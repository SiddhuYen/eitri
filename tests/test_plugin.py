"""The eitri plugin: the local MCP server in plugin mode (no agent, no key), the bundle (skills, manifests, scripts),
`eitri plugin install | login | uninstall` against fake agent homes, and the combined-workspace contract against a stub
of eitri.io's remote server (tests/plugin_stub_remote.py)."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from eitri import __version__, plugin_files
from eitri import plugin_install as PI

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "eitri" / "plugin_bundle"
sys.path.insert(0, str(Path(__file__).parent))
from plugin_stub_remote import NO_TOKEN, TOOLS, StubRemote  # noqa: E402

MECH_OK = '''from eitri.mechanism import *

PARAMS = dict(arm=60.0)

def build(p):
    m = Mechanism("Lever", "one servo, one arm")
    m.link("base", fixed=True)
    m.link("arm", length_mm=p.arm, mass_g=20)
    servo = Actuator("SG90", stall_kgcm=1.8, speed_deg_s=500, travel_deg=180, mass_g=9)
    m.joint("pivot", "base", "arm", axis=(0, -1, 0), at=(0, 0, 0), limit_deg=(0, 60), actuator=servo)
    m.tip("arm")
    return m
'''

def _call(server, name, args=None):
    """A tool call through the MCP server, as an agent makes it: (text, is_error)."""
    from mcp.server.mcpserver.exceptions import ToolError
    try:
        res = asyncio.run(server.call_tool(name, args or {}))
    except ToolError as exc:
        return str(exc), True
    return "\n".join(getattr(c, "text", "") for c in res.content), bool(res.is_error)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home folder with Codex and Cursor present and a stub `claude` that logs its arguments."""
    h = tmp_path / "home"
    (h / ".codex").mkdir(parents=True)
    (h / ".cursor").mkdir()
    monkeypatch.setenv("HOME", str(h))
    for v in ("XDG_DATA_HOME", "CODEX_HOME", "EITRI_MCP_URL", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(v, raising=False)
    log = tmp_path / "claude.log"
    stub = tmp_path / "claude"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\ncase "$*" in "plugin list"*) echo "eitri@eitri";; esac\nexit 0\n')
    stub.chmod(0o755)
    monkeypatch.setenv("EITRI_PLUGIN_CLAUDE_BIN", str(stub))
    monkeypatch.setattr(PI.shutil, "which", lambda name: None)   # no real codex/cursor/eitri on PATH
    return h


# ------------------------------------------------------------------------------------------------ the bundle
def test_generated_files_are_current():
    assert plugin_files.stale() == [], "run: python scripts/gen_plugin.py"
    assert json.loads((BUNDLE / ".claude-plugin" / "plugin.json").read_text())["version"] == __version__


def test_mcp_config_has_both_servers():
    cfg = json.loads((BUNDLE / ".mcp.json").read_text())["mcpServers"]
    assert set(cfg) == {"eitri", "eitri-combined"}
    assert cfg["eitri"]["type"] == "stdio" and cfg["eitri"]["args"] == ["${CLAUDE_PLUGIN_ROOT}/bin/eitri-mcp"]
    remote = cfg["eitri-combined"]
    assert remote["type"] == "http" and remote["url"] == "${EITRI_MCP_URL:-https://eitri.io/mcp}"
    assert "eitri-auth-header" in remote["headersHelper"] and "headers" not in remote     # no token in the file
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert market["name"] == "eitri" and market["plugins"][0]["source"] == "./eitri/plugin_bundle"


def test_skills():
    skills = {d.name: (d / "SKILL.md").read_text() for d in (BUNDLE / "skills").iterdir()}
    assert set(skills) == set(PI.SKILLS)
    for name, text in skills.items():
        head = text.split("---")[1]
        assert f"name: {name}" in head and "description:" in head
        # skills describe the contract, never how the private suite works inside
        for word in ("eitri_suite", "cadhost", "eitri-hosted", "sandbox container"):
            assert word not in text, (name, word)
    for tool in TOOLS:                                       # the combined skill uses the whole contract
        assert tool in skills["combined"], tool
    router = skills["design"]
    assert "eitri plugin login" in router and "train" in router and "telemetry" in router
    assert "default" in router.lower()
    for name, (_k, _f, ref, _d) in plugin_files.DOMAINS.items():
        assert f"`{ref}()`" in skills[name] and "PROJECT STATUS" in skills[name]


@pytest.mark.skipif(not shutil.which("claude"), reason="Claude Code is not installed")
def test_claude_validates_plugin_and_marketplace():
    for target in (BUNDLE, ROOT):
        p = subprocess.run(["claude", "plugin", "validate", str(target), "--strict"], capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, p.stdout + p.stderr


def test_auth_header_script(tmp_path):
    script = BUNDLE / "bin" / "eitri-auth-header"
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "EITRI_CONFIG_DIR": str(tmp_path / "cfg")}
    run = lambda e: json.loads(subprocess.run(["sh", str(script)], env=e, capture_output=True, text=True).stdout)  # noqa: E731
    assert run(env) == {}
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "plugin-token").write_text('eitri_pat_abc123"; rm -rf /\n')
    assert run(env) == {"Authorization": "Bearer eitri_pat_abc123rm-rf/"}            # quotes and spaces cannot break the JSON
    (tmp_path / "cfg" / "plugin-token").unlink()
    assert run({**env, "EITRI_TOKEN": "fromenv_0123456789"}) == {"Authorization": "Bearer fromenv_0123456789"}


def test_launcher_script(tmp_path):
    script = BUNDLE / "bin" / "eitri-mcp"
    good, old = tmp_path / "good", tmp_path / "old"
    good.write_text('#!/bin/sh\nif [ "$1" = --help ]; then printf "  {chat,mcp}\\n    mcp       the plugin server\\n"; exit 0; fi\necho "ran $*"\n')
    old.write_text('#!/bin/sh\nif [ "$1" = --help ]; then printf "    chat   the REPL\\n"; exit 0; fi\necho "ran $*"\n')
    good.chmod(0o755)
    old.chmod(0o755)
    base = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    p = subprocess.run(["sh", str(script), "x"], env={**base, "EITRI_BIN": str(good)}, capture_output=True, text=True)
    assert p.stdout.strip() == "ran mcp x"
    p = subprocess.run(["sh", str(script)], env={**base, "EITRI_BIN": str(old)}, capture_output=True, text=True)
    assert p.returncode == 1 and "eitri update" in p.stderr
    p = subprocess.run(["sh", str(script)], env=base, capture_output=True, text=True)
    assert p.returncode == 1 and "eitri.io/install" in p.stderr


# ------------------------------------------------------------------------------------------------ the local server
def test_local_tools_without_an_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("EITRI_NO_WARM", "1")
    from eitri.agent.mcp_server import make_plugin_server
    from eitri.agent.plugin import PluginSession
    from eitri.viewer import server as viewer_server
    session = PluginSession(root=tmp_path / "projects", open_browser=False)
    server = make_plugin_server(session)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"new_project", "open_project", "list_projects", "project_status", "rebuild", "open_viewer", "cad_reference",
            "domain_guide", "build_model", "mechanism_reference"} <= names
    assert not any(n.startswith("combined_") for n in names)

    text, err = _call(server, "build_model", {"code": MECH_OK})            # no project yet: says how to open one
    assert err and "new_project" in text
    text, err = _call(server, "mechanism_reference")                        # references need no project
    assert not err and "Mechanism" in text
    text, err = _call(server, "domain_guide", {"domain": "harness"})
    assert not err and "wire" in text.lower() and "new_project" in text

    text, err = _call(server, "new_project", {"name": "Lever test", "goal": "a servo lever"})
    assert not err and "lever-test" in text
    folder = tmp_path / "projects" / "lever-test"
    assert "Goal: a servo lever" in (folder / "DESIGN.md").read_text()

    text, err = _call(server, "build_model", {"code": MECH_OK})
    assert not err, text
    assert "PROJECT STATUS" in text and "lever_test.py (mechanism): revision 1 built" in text
    assert "Every model file is built and every check passes" in text

    # a failing requirement: the status names it and says what to do
    failing = MECH_OK + "\ndef verify(m, p, c):\n    c.at_least('arm reach', p.arm, 100)\n"
    text, err = _call(server, "build_model", {"code": failing})
    assert "FAIL arm reach" in text and "STILL TO DO" in text and "never delete or loosen" in text

    # an edit made with the agent's own file tools is reported unbuilt until rebuild
    model = folder / "lever_test.py"
    model.write_text(failing.replace("arm=60.0", "arm=120.0"))
    text, _ = _call(server, "project_status")
    assert "NOT BUILT" in text and "rebuild" in text
    text, err = _call(server, "rebuild")
    assert not err and "0 FAIL" in text and "every check passes" in text and "STILL TO DO" not in text

    # the viewer follows the project, captioned for the agent and without the API-key note
    url = session.viewer_url()
    assert url.startswith("http://127.0.0.1:")
    st = json.loads(urllib.request.urlopen(url + "state.json", timeout=10).read())
    assert st["plugin"] and st["needs_key"] is False and st["revision"] == 3 and st["has_agent"] is False
    viewer_server.PLUGIN_AGENT = None

    text, _ = _call(server, "list_projects")
    assert "lever-test" in text and "revision 3 built" in text
    session2 = PluginSession(root=tmp_path / "projects", open_browser=False)
    text = session2.open_project("lever-test").text
    assert "revision 3 built" in text and session2.project.folder == folder
    assert "already an eitri project" in session2.new_project("x", folder=str(folder)).text


def test_lazy_import_gives_the_same_namespace():
    """EITRI_LAZY_IMPORT=1 (the plugin launcher) defers the CAD kernel; `from eitri import *` still gives every name."""
    code = ("import sys; import eitri; heavy = 'build123d' in sys.modules; ns = {}; exec('from eitri import *', ns);"
            "import types; names = [k for k, v in ns.items() if not k.startswith('_') and not isinstance(v, types.ModuleType)];"
            "print(heavy, len(names), 'Box' in ns, 'cut_holes' in ns, 'bd' in ns)")
    run = lambda lazy: subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT),  # noqa: E731
                                      env={**os.environ, "EITRI_LAZY_IMPORT": "1" if lazy else "0"}).stdout.split()
    lazy, eager = run(True), run(False)
    assert lazy[0] == "False" and eager[0] == "True"
    assert lazy[2:] == eager[2:] == ["True", "True", "True"]
    assert lazy[1] == eager[1], (lazy, eager)                           # the same model-file namespace


def test_plugin_mode_never_starts_claude(monkeypatch, tmp_path):
    """Plugin mode must not start `claude` or need a key: the session and server import nothing that drives Claude."""
    monkeypatch.setenv("EITRI_NO_WARM", "1")
    code = ("import sys; from eitri.agent.mcp_server import make_plugin_server; from eitri.agent.plugin import PluginSession;"
            f"make_plugin_server(PluginSession(root={str(tmp_path)!r}, open_browser=False));"
            "bad=[m for m in sys.modules if m in ('eitri.agent.claude_cli','eitri.agent.api_backend','eitri.agent.hub','claude_agent_sdk','anthropic')];"
            "print(bad); sys.exit(1 if bad else 0)")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT))
    assert p.returncode == 0, p.stdout + p.stderr


def test_eitri_mcp_speaks_stdio(tmp_path):
    """`eitri mcp` as an agent starts it: a real stdio MCP session, no key in the environment."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env.update(EITRI_NO_WARM="1", EITRI_NO_BROWSER="1", EITRI_WORKSPACE=str(tmp_path), PYTHONPATH=str(ROOT),
               EITRI_LAZY_IMPORT="1")                     # as the plugin's launcher starts it

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "eitri", "mcp"], env=env, cwd=str(tmp_path))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                res = await s.call_tool("list_projects", {})
                return init, tools, res
    init, tools, res = asyncio.run(go())
    assert "new_project" in tools and "build_model" in tools
    assert "combined workspace" in (init.instructions or "")
    assert "No projects" in res.content[0].text


# ------------------------------------------------------------------------------------------------ install / login
def test_install_everywhere_and_uninstall(home):
    assert PI.run_install(cursor_rules=str(home / "proj")) == 0
    copy = home / ".local" / "share" / "eitri" / "plugin"
    assert (copy / ".claude-plugin" / "plugin.json").is_file()
    assert os.stat(copy / "bin" / "eitri-auth-header").st_mode & stat.S_IXUSR
    calls = (home.parent / 'claude.log').read_text()
    assert f"plugin marketplace add {copy}" in calls and "plugin install eitri@eitri --scope user" in calls

    cfg = (home / ".codex" / "config.toml").read_text()
    import tomllib
    servers = tomllib.loads(cfg)["mcp_servers"]
    assert servers["eitri"]["args"] == ["mcp"] and servers["eitri"]["tool_timeout_sec"] >= 600
    assert servers["eitri-combined"]["url"] == "https://eitri.io/mcp"
    assert servers["eitri-combined"]["http_headers_helper"] == str(copy / "bin" / "eitri-auth-header")
    skill = (home / ".agents" / "skills" / "eitri-design" / "SKILL.md").read_text()
    assert "name: eitri-design" in skill and "**eitri-combined**" in skill

    cursor = json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]
    assert cursor["eitri"]["args"] == ["mcp"] and "eitri-combined" not in cursor      # no token yet
    assert (home / "proj" / ".cursor" / "rules" / "eitri.mdc").read_text().startswith("---\ndescription:")

    # idempotent, and the user's own Codex settings survive
    (home / ".codex" / "config.toml").write_text('model = "gpt-6"\n\n[mcp_servers.other]\ncommand = "x"\n\n' + cfg)
    assert PI.run_install(["codex"]) == 0
    cfg2 = (home / ".codex" / "config.toml").read_text()
    assert cfg2.count(PI.BLOCK_START) == 1 and 'model = "gpt-6"' in cfg2 and "[mcp_servers.other]" in cfg2

    assert PI.run_uninstall() == 0
    cfg3 = (home / ".codex" / "config.toml").read_text()
    assert PI.BLOCK_START not in cfg3 and "[mcp_servers.other]" in cfg3
    assert not (home / ".agents" / "skills" / "eitri-design").exists()
    assert "eitri" not in json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]
    assert "plugin uninstall eitri@eitri" in (home.parent / 'claude.log').read_text() and not copy.exists()


def test_codex_refuses_a_hand_written_eitri_server(home):
    (home / ".codex" / "config.toml").write_text('[mcp_servers.eitri]\ncommand = "mine"\n')
    ok, msg = PI.install_codex(PI.copy_bundle())
    assert not ok and "outside eitri's block" in msg
    assert (home / ".codex" / "config.toml").read_text() == '[mcp_servers.eitri]\ncommand = "mine"\n'


def test_no_agent_found(home, monkeypatch, capsys):
    shutil.rmtree(home / ".codex")
    shutil.rmtree(home / ".cursor")
    monkeypatch.setenv("EITRI_PLUGIN_CLAUDE_BIN", "")
    monkeypatch.setattr(PI, "_claude_bin", lambda: None)
    monkeypatch.setattr(PI.Path, "exists", lambda self: False if str(self) == "/Applications/Cursor.app" else os.path.exists(self))
    assert PI.run_install() == 1 and "No agent app found" in capsys.readouterr().out


def test_login_against_the_stub(home, monkeypatch, capsys):
    stub = StubRemote()
    try:
        monkeypatch.setenv("EITRI_MCP_URL", stub.url)
        assert PI.account_url() == stub.url.rsplit("/", 1)[0] + "/account"
        PI.run_install(["cursor"])
        assert PI.run_login(token="eitri_pat_wrongtoken000000") == 1           # eitri.io rejects it: not saved
        assert "rejected" in capsys.readouterr().out and PI.load_token() is None
        assert PI.run_login(token="not a token") == 1
        assert PI.run_login(token="Bearer " + stub.token) == 0
        assert PI.load_token() == stub.token
        assert stat.S_IMODE(os.stat(PI.token_path()).st_mode) == 0o600
        cursor = json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]
        assert cursor["eitri-combined"] == {"url": stub.url, "headers": {"Authorization": f"Bearer {stub.token}"}}
        # the bundle's helper now sends that token
        out = subprocess.run(["sh", str(BUNDLE / "bin" / "eitri-auth-header")], capture_output=True, text=True,
                             env={"PATH": os.environ["PATH"], "HOME": str(home), "EITRI_CONFIG_DIR": os.environ["EITRI_CONFIG_DIR"]}).stdout
        assert json.loads(out) == {"Authorization": f"Bearer {stub.token}"}
        assert PI.run_logout() == 0 and PI.load_token() is None
        assert "eitri-combined" not in json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]
    finally:
        stub.close()
    # eitri.io unreachable: saved anyway, with a note
    monkeypatch.setenv("EITRI_MCP_URL", "http://127.0.0.1:9/mcp")
    assert PI.run_login(token="eitri_pat_offline0123456789") == 0 and "could not reach" in capsys.readouterr().out


def test_login_from_stdin(home, monkeypatch):
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("eitri_pat_fromstdin012345\n"))
    assert PI.run_login(from_stdin=True, check=False) == 0 and PI.load_token() == "eitri_pat_fromstdin012345"


# ------------------------------------------------------------------------------------------------ the remote contract
def _remote(url: str, headers: dict, steps):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    async def go():
        async with create_mcp_http_client(headers=headers) as http:
            async with streamable_http_client(url, http_client=http) as (r, w, *_):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await steps(s)
    return asyncio.run(go())


def test_combined_loop_against_the_stub(tmp_path):
    """The loop the combined skill describes, through a real MCP client with the bundle's auth helper."""
    stub = StubRemote()
    try:
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        (cfg / "plugin-token").write_text(stub.token)
        headers = json.loads(subprocess.run(["sh", str(BUNDLE / "bin" / "eitri-auth-header")], capture_output=True, text=True,
                                            env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "EITRI_CONFIG_DIR": str(cfg)}).stdout)

        async def steps(s):
            tools = {t.name for t in (await s.list_tools()).tools}
            t = lambda r: r.content[0].text  # noqa: E731
            ref = t(await s.call_tool("combined_reference", {}))
            pid = json.loads(t(await s.call_tool("combined_new_project", {"name": "arm", "goal": "a 2-DOF arm"})))["project_id"]
            await s.call_tool("combined_put_files", {"project_id": pid, "files": {"motion/arm.py": 'servo = "SG90"'}})
            first = json.loads(t(await s.call_tool("combined_build", {"project_id": pid})))
            await s.call_tool("combined_put_files", {"project_id": pid, "files": {"motion/arm.py": 'servo = "MG996R"',
                                                                                   "wiring/harness.py": "..."}})
            second = json.loads(t(await s.call_tool("combined_build", {"project_id": pid})))
            again = json.loads(t(await s.call_tool("combined_results", {"project_id": pid})))
            links = json.loads(t(await s.call_tool("combined_export", {"project_id": pid, "formats": ["step"]})))
            return tools, ref, first, second, again, links
        tools, ref, first, second, again, links = _remote(stub.url, headers, steps)
        assert tools == set(TOOLS)
        assert "layout" in ref
        assert not first["all_pass"] and any("wiring not built yet" in s for s in first["next_steps"])
        assert any(c["severity"] == "fail" and c["source"] for c in first["checks"])
        assert second["all_pass"] and second["next_steps"] == [] and again == second
        assert links["step"].endswith(".step")
    finally:
        stub.close()


def test_remote_without_token_says_how_to_sign_in():
    stub = StubRemote()
    try:
        state, detail = PI.check_token(None, stub.url)
        assert state == "rejected" and "eitri plugin login" in detail and detail == NO_TOKEN
        assert PI.check_token(stub.token, stub.url)[0] == "ok"
    finally:
        stub.close()
