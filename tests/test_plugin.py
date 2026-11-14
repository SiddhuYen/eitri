"""The eitri plugin: the local MCP server in plugin mode (no agent, no key), the bundle (skills, manifests, scripts), and
`eitri plugin install | uninstall | status | login | logout` against fake agent homes, including the clean-up of eitri.io's
closed hosted server that 0.7.0-0.9.0 could add."""
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
COMBINED_TOOLS = ("combined_reference", "combined_new_project", "combined_list_projects", "combined_read_files",
                  "combined_put_files", "combined_build", "combined_results", "combined_export")

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
    for v in ("XDG_DATA_HOME", "CODEX_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(v, raising=False)
    log = tmp_path / "claude.log"
    stub = tmp_path / "claude"
    # like the real one, `claude mcp remove` fails for a server that is not there (the marker file stands for eitri-io)
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\ncase "$*" in "plugin list"*) echo "eitri@eitri";;\n'
                    f'  "mcp remove"*) if [ -f "{tmp_path}/eitri-io" ]; then rm "{tmp_path}/eitri-io"; exit 0; fi; exit 1;; esac\nexit 0\n')
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
    for tool in COMBINED_TOOLS:                              # the combined skill uses the whole contract
        assert tool in skills["combined"], tool
    # the local combined workspace holds the circuit too (elec.py, checked against the other files)
    assert "circuit" in skills["combined"].split("---")[1] and "elec.py" in skills["design"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8").split("- **Combined workspace (the default):**")[1].split("- **Six local")[0]
    assert "eitri mcp --combined" in readme and "open source" in readme
    for text in (skills["combined"], skills["design"]):                   # the default is local: no token step, no remote URL
        assert "eitri.io/mcp" not in text and "this computer" in text
    router = skills["design"]
    assert "eitri telemetry off" in router
    for text in skills.values():                                          # no hosted workspace, no sign-in left in any skill
        assert "eitri plugin login" not in text and "eitri-io" not in text and "eitri.io/account" not in text
    assert "default" in router.lower()
    for name, (_k, _f, ref, _d) in plugin_files.DOMAINS.items():
        assert f"`{ref}()`" in skills[name] and "PROJECT STATUS" in skills[name]


@pytest.mark.skipif(not shutil.which("claude"), reason="Claude Code is not installed")
def test_claude_validates_plugin_and_marketplace():
    for target in (BUNDLE, ROOT):
        p = subprocess.run(["claude", "plugin", "validate", str(target), "--strict"], capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert p.returncode == 0, p.stdout + p.stderr


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
    assert os.stat(copy / "bin" / "eitri-mcp").st_mode & stat.S_IXUSR
    calls = (home.parent / 'claude.log').read_text(encoding='utf-8')
    assert f"plugin marketplace add {copy}" in calls and "plugin install eitri@eitri --scope user" in calls

    cfg = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    import tomllib
    servers = tomllib.loads(cfg)["mcp_servers"]
    assert servers["eitri"]["args"] == ["mcp"] and servers["eitri"]["tool_timeout_sec"] >= 600
    assert servers["eitri-combined"]["args"] == ["mcp", "--combined"] and "url" not in servers["eitri-combined"]
    assert servers["eitri-combined"]["env"]["EITRI_LAZY_IMPORT"] == "1" and PI.OLD_REMOTE not in servers
    skill = (home / ".agents" / "skills" / "eitri-design" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: eitri-design" in skill and "**eitri-combined**" in skill

    cursor = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert cursor["eitri"]["args"] == ["mcp"] and cursor["eitri-combined"]["args"] == ["mcp", "--combined"]
    assert PI.OLD_REMOTE not in cursor
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


def test_status_names_the_local_servers(home, capsys):
    PI.run_status()
    out = capsys.readouterr().out
    assert "eitri-combined (the combined workspace), both on this computer; nothing to sign in to" in out
    assert "eitri.io" not in out
    assert "anonymous counts: off (EITRI_TELEMETRY=off)" in out          # tests/conftest.py turns them off
    PI.old_token_path().parent.mkdir(parents=True, exist_ok=True)
    PI.old_token_path().write_text("eitri_pat_old0123456789\n")
    PI.run_status()
    assert "token from an older eitri is still saved" in capsys.readouterr().out


def _old_090_install(home):
    """What 0.9.0's `eitri plugin login` left behind: a saved token, Cursor's eitri-io entry (and 0.8.x's eitri-combined at
    eitri.io), Codex's eitri-io inside eitri's block, Claude Code's user-scope eitri-io server (the stub logs the remove)."""
    PI.old_token_path().parent.mkdir(parents=True, exist_ok=True)
    PI.old_token_path().write_text("eitri_pat_old0123456789\n")
    (home / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {
        "mine": {"command": "x"},
        "eitri-io": {"url": "https://eitri.io/mcp", "headers": {"Authorization": "Bearer eitri_pat_old0123456789"}},
        "eitri-combined": {"url": "https://eitri.io/mcp"}}}))
    (home.parent / "eitri-io").write_text("")                                 # Claude Code has the eitri-io server
    (home / ".codex" / "config.toml").write_text('model = "gpt-6"\n\n' + PI.BLOCK_START + '\n[mcp_servers.eitri-io]\n'
                                                 'url = "https://eitri.io/mcp"\n' + PI.BLOCK_END + '\n')


def test_install_removes_the_closed_hosted_server(home, capsys):
    _old_090_install(home)
    assert PI.run_install() == 0
    out = capsys.readouterr().out
    assert "removed eitri.io's closed hosted workspace: Claude Code's eitri-io server, Cursor's eitri-io, eitri-combined server, the saved eitri.io token" in out, out
    assert not PI.old_token_path().exists()
    assert f"mcp remove -s user {PI.OLD_REMOTE}" in (home.parent / "claude.log").read_text(encoding="utf-8")
    cursor = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "eitri-io" not in cursor and cursor["mine"] == {"command": "x"}
    assert cursor["eitri-combined"]["args"] == ["mcp", "--combined"] and "url" not in cursor["eitri-combined"]
    codex = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "eitri-io" not in codex and "eitri.io" not in codex and 'model = "gpt-6"' in codex
    assert not (PI.bundle_copy() / "bin" / "eitri-auth-header").exists()
    # a second install finds nothing left to remove
    PI.run_install()
    assert "closed hosted workspace" not in capsys.readouterr().out


def test_update_refresh_cleans_up_too(home, capsys):
    assert PI.run_install(["cursor"]) == 0
    _old_090_install(home)
    PI.refresh_after_update()
    assert "eitri-io" not in json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert not PI.old_token_path().exists()


def test_login_says_the_hosted_workspace_closed(home, capsys):
    _old_090_install(home)
    assert PI.run_login() == 0
    out = capsys.readouterr().out
    assert out.startswith(PI.CLOSED) and "removed the saved eitri.io token" in out
    assert PI.run_logout() == 0 and "nothing to remove" in capsys.readouterr().out


def test_old_login_flags_still_parse(home, tmp_path):
    """Scripts written for 0.7.0-0.9.0 (`eitri plugin login --token-stdin`, `install --login`) still run, and sign in nowhere."""
    env = {**os.environ, "PYTHONPATH": str(ROOT), "EITRI_LAZY_IMPORT": "1"}
    for args in (["login", "--token-stdin", "--no-check", "--no-browser"], ["logout"], ["status", "--online"]):
        p = subprocess.run([sys.executable, "-m", "eitri", "plugin", *args], input="eitri_pat_piped0123456789\n",
                           capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(tmp_path), timeout=120)
        assert p.returncode == 0, p.stdout + p.stderr
    assert not PI.old_token_path().exists()
    p = subprocess.run([sys.executable, "-m", "eitri", "plugin", "headers"], capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert p.returncode == 2                                                  # gone: argparse refuses it


def test_nothing_points_at_the_closed_server():
    """No eitri.io MCP URL, sign-in or token step left in the package, the bundle, the installer or the docs."""
    files = [p for p in (ROOT / "eitri").rglob("*") if p.suffix in (".py", ".md", ".json", ".mdc", ".html", ".js", "") and p.is_file()
             and "vendor" not in p.parts and "__pycache__" not in p.parts]
    files += [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "scripts" / "install-remote.sh", ROOT / ".claude-plugin" / "marketplace.json"]
    files += list((ROOT / "docs").glob("*.md"))
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        assert "eitri.io/mcp" not in text, p
        assert "eitri.io/account" not in text, p
        assert "eitri-auth-header" not in text, p
    assert not (BUNDLE / "bin" / "eitri-auth-header").exists()
