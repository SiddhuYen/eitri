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
    assert json.loads((BUNDLE / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"] == __version__


def test_icon():
    """A square PNG of 512-2048 px under 2 MB (what Claude Code's plugin directory accepts), named in plugin.json."""
    from PIL import Image
    manifest = json.loads((BUNDLE / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    icon = BUNDLE / manifest["icon"]
    assert manifest["icon"].startswith("./") and icon.is_file() and icon.stat().st_size < 2_000_000
    with Image.open(icon) as im:
        assert im.format == "PNG" and im.size[0] == im.size[1] and 512 <= im.size[0] <= 2048
    assert (BUNDLE / "assets" / "icon.svg").is_file()


def test_mcp_config_has_both_servers():
    cfg = json.loads((BUNDLE / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(cfg) == {"eitri", "eitri-combined"}
    assert cfg["eitri"]["type"] == "stdio" and cfg["eitri"]["args"] == ["${CLAUDE_PLUGIN_ROOT}/bin/eitri-mcp"]
    combined = cfg["eitri-combined"]                                   # the combined workspace runs locally too: no URL, no token
    assert combined["type"] == "stdio" and combined["args"] == ["${CLAUDE_PLUGIN_ROOT}/bin/eitri-mcp", "--combined"]
    assert "url" not in combined and "headersHelper" not in combined
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["name"] == "eitri" and market["plugins"][0]["source"] == "./eitri/plugin_bundle"


def test_skills():
    skills = {d.name: (d / "SKILL.md").read_text(encoding="utf-8") for d in (BUNDLE / "skills").iterdir()}
    assert set(skills) == set(PI.SKILLS)
    for name, text in skills.items():
        head = text.split("---")[1]
        assert f"name: {name}" in head and "description:" in head
        # skills describe the contract, never how the private suite works inside
        for word in ("eitri_suite", "cadhost", "eitri-hosted", "sandbox container"):
            assert word not in text, (name, word)
    for tool in TOOLS:                                       # the combined skill uses the whole contract
        assert tool in skills["combined"], tool
    # the local combined workspace holds the circuit too (elec.py, checked against the other files)
    assert "circuit" in skills["combined"].split("---")[1] and "elec.py" in skills["design"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8").split("- **Combined workspace (the default):**")[1].split("- **Six local")[0]
    assert "eitri mcp --combined" in readme and "open source" in readme
    for text in (skills["combined"], skills["design"]):                   # the default is local: no token step, no remote URL
        assert "eitri.io/mcp" not in text and "this computer" in text
    router = skills["design"]
    assert "eitri plugin login" in router and "train" in router and "eitri telemetry off" in router
    assert router.index("eitri plugin login") > router.index("## eitri.io (optional)")
    assert "default" in router.lower()
    for name, (_k, _f, ref, _d) in plugin_files.DOMAINS.items():
        assert f"`{ref}()`" in skills[name] and "PROJECT STATUS" in skills[name]


@pytest.mark.skipif(not shutil.which("claude"), reason="Claude Code is not installed")
def test_claude_validates_plugin_and_marketplace():
    for target in (BUNDLE, ROOT):
        p = subprocess.run(["claude", "plugin", "validate", str(target), "--strict"], capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert p.returncode == 0, p.stdout + p.stderr


def test_auth_header_script(tmp_path):
    script = BUNDLE / "bin" / "eitri-auth-header"
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "EITRI_CONFIG_DIR": str(tmp_path / "cfg")}
    run = lambda e: json.loads(subprocess.run(["sh", str(script)], env=e, capture_output=True, text=True, encoding="utf-8").stdout)  # noqa: E731
    assert run(env) == {}
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "plugin-token").write_text('eitri_pat_abc123"; rm -rf /\n')
    assert run(env) == {"Authorization": "Bearer eitri_pat_abc123rm-rf/"}            # quotes and spaces cannot break the JSON
    (tmp_path / "cfg" / "plugin-token").unlink()
    assert run({**env, "EITRI_TOKEN": "fromenv_0123456789"}) == {"Authorization": "Bearer fromenv_0123456789"}


def test_launcher_script(tmp_path):
    script = BUNDLE / "bin" / "eitri-mcp"
    good, old = tmp_path / "good", tmp_path / "old"
    good.write_text('#!/bin/sh\nif [ "$2" = --help ]; then printf "  --plugin\\n  --combined  the combined workspace\\n"; exit 0; fi\necho "ran $*"\n')
    old.write_text('#!/bin/sh\nif [ "$2" = --help ]; then printf "  -h, --help\\n"; exit 0; fi\n'
                   'if [ "$1" = --version ]; then echo "eitri 0.8.1"; exit 0; fi\necho "ran $*"\n')
    good.chmod(0o755)
    old.chmod(0o755)
    base = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    p = subprocess.run(["sh", str(script), "x"], env={**base, "EITRI_BIN": str(good)}, capture_output=True, text=True, encoding="utf-8")
    assert p.stdout.strip() == "ran mcp x"
    p = subprocess.run(["sh", str(script), "--combined"], env={**base, "EITRI_BIN": str(good)}, capture_output=True, text=True, encoding="utf-8")
    assert p.stdout.strip() == "ran mcp --combined"
    p = subprocess.run(["sh", str(script)], env={**base, "EITRI_BIN": str(old)}, capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 1 and "eitri update" in p.stderr
    assert "needs eitri 0.9.0 or newer" in p.stderr and "eitri 0.8.1" in p.stderr and "not released yet" in p.stderr
    p = subprocess.run(["sh", str(script)], env=base, capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 1 and "eitri.io/install" in p.stderr


def _installer(tmp_path, version: str, combined: bool, plugin_rc: int = 0, plugin_out: str = "  ok   Claude Code: plugin eitri@eitri"):
    """`install-remote.sh --plugin` over a stub eitri (uv is a stub too): (returncode, stdout, stderr)."""
    tools, home = tmp_path / "tools", tmp_path / "home"
    tools.mkdir()
    home.mkdir()
    flag = "  --combined  the combined workspace\\n" if combined else ""
    (tools / "eitri").write_text(
        '#!/bin/sh\ncase "$1" in\n'
        f'  mcp) printf "  -h, --help\\n{flag}";;\n'
        f'  --version) echo "eitri {version}";;\n'
        f'  plugin) echo "PLUGIN $*"; echo "{plugin_out}"; exit {plugin_rc};;\n'
        '  *) exit 0;;\nesac\n', encoding="utf-8")
    (tools / "uv").write_text(f'#!/bin/sh\ncase "$*" in "tool dir --bin") echo "{tools}";; "tool dir") echo "{tmp_path}/none";; esac\nexit 0\n',
                              encoding="utf-8")
    for f in ("eitri", "uv"):
        (tools / f).chmod(0o755)
    env = {"PATH": f"{tools}:/usr/bin:/bin", "HOME": str(home), "EITRI_INSTALL_SOURCE": "stub", "EITRI_NO_PATH_EDIT": "1"}
    p = subprocess.run(["sh", str(ROOT / "scripts" / "install-remote.sh"), "--plugin"], env=env, capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    return p.returncode, p.stdout, p.stderr


@pytest.mark.parametrize("version,ok", [("0.8.1", False), ("0.9.0", True)])
def test_installer_plugin_flag_needs_0_9(tmp_path, version, ok):
    """`--plugin` over an eitri without the local combined workspace stops with the 0.9.0 message; a new one registers the
    plugin and ends with what to run next: no sign-in step, no token."""
    rc, out, err = _installer(tmp_path, version, combined=ok)
    if ok:
        assert rc == 0 and "PLUGIN plugin install" in out, out + err
        assert "Done. Now run   claude" in out and "login" not in out and "token" not in out.replace("no token", "")
    else:
        assert rc == 1 and "PLUGIN" not in out
        assert "needs eitri 0.9.0 or newer" in err and "eitri 0.8.1" in err


def test_installer_without_an_agent_app_succeeds_and_says_what_to_install(tmp_path):
    """No Claude Code, Codex or Cursor: eitri is installed, so the installer exits 0 and says how to get Claude Code."""
    rc, out, err = _installer(tmp_path, "0.9.0", combined=True, plugin_rc=PI.NO_AGENT, plugin_out=PI.INSTALL_AGENT_HINT.splitlines()[1])
    assert rc == 0, out + err
    assert "claude.ai/install.sh" in out and "eitri plugin install" in out and "Done. Now run" not in out


# ------------------------------------------------------------------------------------------------ the local server
def test_local_tools_without_an_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("EITRI_NO_WARM", "1")
    from eitri.agent.mcp_server import make_plugin_server
    from eitri.agent.plugin import PluginSession
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
    assert "Goal: a servo lever" in (folder / "DESIGN.md").read_text(encoding="utf-8")

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
    run = lambda lazy: subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),  # noqa: E731
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
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
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
    calls = (home.parent / 'claude.log').read_text(encoding='utf-8')
    assert f"plugin marketplace add {copy}" in calls and "plugin install eitri@eitri --scope user" in calls

    cfg = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    import tomllib
    servers = tomllib.loads(cfg)["mcp_servers"]
    assert servers["eitri"]["args"] == ["mcp"] and servers["eitri"]["tool_timeout_sec"] >= 600
    assert servers["eitri-combined"]["args"] == ["mcp", "--combined"] and "url" not in servers["eitri-combined"]
    assert servers["eitri-combined"]["env"]["EITRI_LAZY_IMPORT"] == "1" and PI.REMOTE_NAME not in servers     # no token: no eitri.io
    skill = (home / ".agents" / "skills" / "eitri-design" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: eitri-design" in skill and "**eitri-combined**" in skill

    cursor = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert cursor["eitri"]["args"] == ["mcp"] and cursor["eitri-combined"]["args"] == ["mcp", "--combined"]
    assert PI.REMOTE_NAME not in cursor                                                 # eitri.io only after `login`
    assert (home / "proj" / ".cursor" / "rules" / "eitri.mdc").read_text(encoding="utf-8").startswith("---\ndescription:")

    # idempotent, and the user's own Codex settings survive
    (home / ".codex" / "config.toml").write_text('model = "gpt-6"\n\n[mcp_servers.other]\ncommand = "x"\n\n' + cfg)
    assert PI.run_install(["codex"]) == 0
    cfg2 = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert cfg2.count(PI.BLOCK_START) == 1 and 'model = "gpt-6"' in cfg2 and "[mcp_servers.other]" in cfg2

    assert PI.run_uninstall() == 0
    cfg3 = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert PI.BLOCK_START not in cfg3 and "[mcp_servers.other]" in cfg3
    assert not (home / ".agents" / "skills" / "eitri-design").exists()
    assert "eitri" not in json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "plugin uninstall eitri@eitri" in (home.parent / 'claude.log').read_text(encoding='utf-8') and not copy.exists()


def test_codex_refuses_a_hand_written_eitri_server(home):
    (home / ".codex" / "config.toml").write_text('[mcp_servers.eitri]\ncommand = "mine"\n')
    ok, msg = PI.install_codex(PI.copy_bundle())
    assert not ok and "outside eitri's block" in msg
    assert (home / ".codex" / "config.toml").read_text(encoding="utf-8") == '[mcp_servers.eitri]\ncommand = "mine"\n'


def test_no_agent_found(home, monkeypatch, capsys):
    shutil.rmtree(home / ".codex")
    shutil.rmtree(home / ".cursor")
    monkeypatch.setenv("EITRI_PLUGIN_CLAUDE_BIN", "")
    monkeypatch.setattr(PI, "_claude_bin", lambda: None)
    monkeypatch.setattr(PI.Path, "exists", lambda self: False if str(self) == "/Applications/Cursor.app" else os.path.exists(self))
    assert PI.run_install() == PI.NO_AGENT
    out = capsys.readouterr().out
    assert "No agent app found" in out and "curl -fsSL https://claude.ai/install.sh | bash" in out and "eitri plugin install" in out


def test_login_against_the_stub(home, monkeypatch, capsys):
    stub = StubRemote()
    try:
        monkeypatch.setenv("EITRI_MCP_URL", stub.url)
        assert PI.account_url() == stub.url.rsplit("/", 1)[0] + "/account"
        PI.run_install(["cursor"])
        capsys.readouterr()
        assert PI.run_login(token="eitri_pat_wrongtoken000000") == 1           # eitri.io rejects it: not saved
        out = capsys.readouterr().out
        assert "rejected" in out and PI.load_token() is None and PI.SIGNED_IN_NOTICE not in out
        assert PI.run_login(token="not a token") == 1
        assert PI.SIGNED_IN_NOTICE not in capsys.readouterr().out
        n_calls = len(stub.calls)
        assert PI.run_login(token="Bearer " + stub.token) == 0
        out = capsys.readouterr().out
        assert len(stub.calls) == n_calls + 1                                   # checked with eitri.io first ...
        assert out.index("saved eitri") < out.index(PI.SIGNED_IN_NOTICE)       # ... then stored, then the notice
        assert PI.load_token() == stub.token
        assert stat.S_IMODE(os.stat(PI.token_path()).st_mode) == 0o600
        cursor = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        assert cursor[PI.REMOTE_NAME] == {"url": stub.url, "headers": {"Authorization": f"Bearer {stub.token}"}}
        assert cursor["eitri-combined"]["args"] == ["mcp", "--combined"]                 # the local one stays
        # the bundle's helper now sends that token
        out = subprocess.run(["sh", str(BUNDLE / "bin" / "eitri-auth-header")], capture_output=True, text=True, encoding="utf-8",
                             env={"PATH": os.environ["PATH"], "HOME": str(home), "EITRI_CONFIG_DIR": os.environ["EITRI_CONFIG_DIR"]}).stdout
        assert json.loads(out) == {"Authorization": f"Bearer {stub.token}"}
        assert PI.run_logout() == 0 and PI.load_token() is None
        servers = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        assert PI.REMOTE_NAME not in servers and "eitri-combined" in servers
    finally:
        stub.close()
    # eitri.io unreachable: saved anyway, with a note
    monkeypatch.setenv("EITRI_MCP_URL", "http://127.0.0.1:9/mcp")
    assert PI.run_login(token="eitri_pat_offline0123456789") == 0 and "could not reach" in capsys.readouterr().out


def test_status_names_the_servers_and_the_optional_remote(home, monkeypatch, capsys):
    PI.run_status()
    out = capsys.readouterr().out
    assert "eitri-combined (the combined workspace), both on this computer" in out
    assert f"eitri.io ({PI.REMOTE_NAME}, optional): not added (eitri plugin login adds it)" in out
    assert "anonymous counts: off (EITRI_TELEMETRY=off)" in out          # tests/conftest.py turns them off
    monkeypatch.setenv("EITRI_MCP_URL", "http://127.0.0.1:8078/mcp")
    PI.save_token("eitri_pat_status0123456789")
    PI.run_status()
    assert "http://127.0.0.1:8078/mcp (from EITRI_MCP_URL)" in capsys.readouterr().out


def test_login_adds_the_remote_server_to_claude_code(home, capsys):
    """`eitri plugin login` adds eitri.io as a user-scope Claude Code server (header helper, no token in the config) and
    `logout` removes it again."""
    PI.run_install(["claude"])
    assert PI.run_login(token="eitri_pat_claude0123456789", check=False) == 0
    calls = (home.parent / "claude.log").read_text(encoding="utf-8")
    line = next(l for l in calls.splitlines() if l.startswith(f"mcp add-json -s user {PI.REMOTE_NAME} "))
    cfg = json.loads(line.split(f"{PI.REMOTE_NAME} ", 1)[1])
    assert cfg["type"] == "http" and cfg["url"] == "https://eitri.io/mcp" and "eitri-auth-header" in cfg["headersHelper"]
    assert "eitri_pat_claude" not in line
    assert PI.run_logout() == 0
    assert f"mcp remove -s user {PI.REMOTE_NAME}" in (home.parent / "claude.log").read_text(encoding="utf-8").splitlines()[-1]


def test_login_without_a_terminal_prints_the_instruction_first(home, tmp_path):
    """Piped (no terminal): the "Make a personal token" line comes before the prompt, and the token is read from stdin."""
    env = {**os.environ, "PYTHONPATH": str(ROOT), "EITRI_LAZY_IMPORT": "1"}
    p = subprocess.run([sys.executable, "-m", "eitri", "plugin", "login", "--no-check", "--no-browser"],
                       input="eitri_pat_piped0123456789\n", stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", env=env, cwd=str(tmp_path), start_new_session=True, timeout=120)
    out = p.stdout
    assert p.returncode == 0, out
    assert out.index("Make a personal token") < out.index("eitri.io token (hidden") < out.index("saved eitri")
    notice = PI.unverified_notice("--no-check was given")                    # this run passes --no-check
    assert out.index("saved eitri") < out.index(notice)                       # printed to the person's terminal
    assert "GetPassWarning" not in out and PI.load_token() == "eitri_pat_piped0123456789"


def test_login_flushes_the_instruction_before_the_terminal_prompt(home, monkeypatch):
    """The reported case: stdout piped (block-buffered) while getpass prompts on the terminal. By the time the prompt
    shows, the instruction must already have left stdout's buffer."""
    import getpass
    import io
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="utf-8", line_buffering=False, write_through=False)
    seen = {}

    class TTY(io.StringIO):
        def isatty(self):
            return True

    def fake_getpass(prompt):
        seen["before_prompt"] = raw.getvalue().decode("utf-8")
        return "eitri_pat_terminal012345678"
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stdin", TTY())
    monkeypatch.setattr(getpass, "getpass", fake_getpass)
    assert PI.run_login(check=False, open_browser=False) == 0
    assert "Make a personal token" in seen["before_prompt"]


def test_signed_in_notice_wording():
    assert PI.SIGNED_IN_NOTICE == (
        "Signed in to eitri.io. In the combined workspace, the model files your agent writes are sent to eitri.io, kept as "
        "projects in your account, and used to train eitri's models unless you turn that off at https://eitri.io/account. "
        "Single-domain work stays on this machine.")
    # the installer's --plugin path no longer signs in (the combined workspace is local): no login call in it
    sh = (ROOT / "scripts" / "install-remote.sh").read_text(encoding="utf-8")
    assert '"$eitri" plugin login' not in sh


def test_unverified_token_is_not_called_signed_in(home, monkeypatch, capsys):
    """--no-check, or eitri.io unreachable: "Token saved (not checked yet: <reason>)." and the same privacy sentence."""
    assert PI.SIGNED_IN_NOTICE == "Signed in to eitri.io. " + PI.PRIVACY_NOTICE
    capsys.readouterr()
    assert PI.run_login(token="eitri_pat_nocheck0123456789", check=False) == 0
    out = capsys.readouterr().out
    assert "Signed in to eitri.io" not in out
    assert "Token saved (not checked yet: --no-check was given). " + PI.PRIVACY_NOTICE in out
    assert PI.load_token() == "eitri_pat_nocheck0123456789"

    monkeypatch.setenv("EITRI_MCP_URL", "http://127.0.0.1:9/mcp")           # nothing listens there
    assert PI.run_login(token="eitri_pat_offline0123456789") == 0
    out = capsys.readouterr().out
    assert "Signed in to eitri.io" not in out
    assert "Token saved (not checked yet: could not reach http://127.0.0.1:9/mcp (" in out
    assert ("). " + PI.PRIVACY_NOTICE) in out
    assert out.index("saved eitri") < out.index("Token saved (not checked yet")
    assert PI.load_token() == "eitri_pat_offline0123456789"


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
        headers = json.loads(subprocess.run(["sh", str(BUNDLE / "bin" / "eitri-auth-header")], capture_output=True, text=True, encoding="utf-8",
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
        assert PI.check_token("eitri_pat_revoked000000000", stub.url)[0] == "rejected"
        assert PI.check_token(stub.token, stub.url) == ("ok", "signed in")
        # signed out, the tools still list (so the agent sees them) and a call says how to sign in
        async def steps(s):
            tools = {t.name for t in (await s.list_tools()).tools}
            res = await s.call_tool("combined_list_projects", {})
            return tools, res
        tools, res = _remote(stub.url, {}, steps)
        assert tools == set(TOOLS) and res.is_error and "eitri plugin login" in res.content[0].text
    finally:
        stub.close()
