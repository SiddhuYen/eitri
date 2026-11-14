"""`eitri plugin install | uninstall | status | login | logout`: register eitri in the agent apps on this computer.

eitri runs inside the user's own agent (on their own subscription) as one plugin with two MCP servers, both on this computer:
- `eitri`: the local single-domain tools (`eitri mcp`, stdio);
- `eitri-combined`: the combined multi-domain workspace (`eitri mcp --combined`, stdio): projects are folders, builds run here
  with the cross-domain checks on. No account, no token.

What `install` does per agent app it finds (nothing for an app that is not there):
- Claude Code: a copy of the plugin bundle goes to ~/.local/share/eitri/plugin (a stable path: the installed package moves
  on upgrades), which is added as the `eitri` marketplace, and `claude plugin install eitri@eitri` installs it (skills +
  both servers).
- Codex: `[mcp_servers.eitri]` and `[mcp_servers.eitri-combined]` in a marked block of ~/.codex/config.toml, and the skills
  in ~/.agents/skills/eitri-*/ (Codex's plugin manifests cannot carry a local stdio server, so this is the closest equivalent).
- Cursor: both servers in ~/.cursor/mcp.json; `--cursor-rules DIR` also writes DIR/.cursor/rules/eitri.mdc (Cursor has no
  documented file for global rules).

eitri.io's hosted combined workspace (an `eitri-io` server that 0.7.0-0.9.0's `eitri plugin login` could add) closed in
November 2026. `install` (and so `eitri update`) removes that server from every agent app it was added to and forgets the
saved token; `login` only says so; `logout` does the same clean-up. No shell file is edited.

`install` sends one anonymous count (eitri.telemetry: a random install id, the version, the OS, the agent app; off with
`eitri telemetry off`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from .config import config_dir

MARKETPLACE = "eitri"
PLUGIN_ID = "eitri@eitri"
BLOCK_START = "# >>> eitri plugin (written by `eitri plugin install`; `eitri plugin uninstall` removes it) >>>"
BLOCK_END = "# <<< eitri plugin <<<"
OLD_REMOTE = "eitri-io"            # eitri.io's closed hosted server, as `eitri plugin login` (0.7.0-0.9.0) added it
CLOSED = ("eitri.io's hosted combined workspace has closed: everything runs on this computer (the eitri and eitri-combined "
          "servers), with nothing to sign in to.")
SKILLS = ("design", "combined", "cad", "wires", "motion", "pneumatics", "firmware", "electronics")


# ------------------------------------------------------------------------------------------------ paths
def bundle_source() -> Path:
    return Path(__file__).parent / "plugin_bundle"


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "eitri"


def bundle_copy() -> Path:
    return data_dir() / "plugin"


def old_token_path() -> Path:
    """Where 0.7.0-0.9.0 kept the eitri.io token (removed by install, logout and uninstall)."""
    return config_dir() / "plugin-token"


def forget_old_token() -> bool:
    try:
        old_token_path().unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


# ------------------------------------------------------------------------------------------------ the agents
def _claude_bin() -> str | None:
    env = os.environ.get("EITRI_PLUGIN_CLAUDE_BIN")           # tests point this at a stub
    if env:
        return env
    for c in (shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude"),
              str(Path.home() / ".claude" / "local" / "claude")):
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return c
    return None


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def cursor_home() -> Path:
    return Path.home() / ".cursor"


def found_agents() -> dict[str, str]:
    """{agent: why we think it is installed} for Claude Code, Codex and Cursor."""
    out: dict[str, str] = {}
    c = _claude_bin()
    if c:
        out["claude"] = c
    if codex_home().is_dir() or shutil.which("codex"):
        out["codex"] = str(codex_home())
    if cursor_home().is_dir() or shutil.which("cursor") or Path("/Applications/Cursor.app").exists():
        out["cursor"] = str(cursor_home())
    return out


def eitri_command() -> str:
    """The absolute path of this install's `eitri` command (what Codex and Cursor start as the local server)."""
    cand = [Path(sys.executable).parent / "eitri", shutil.which("eitri"), Path.home() / ".local" / "bin" / "eitri"]
    for c in cand:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return str(Path(c))
    return "eitri"


def copy_bundle() -> Path:
    """The bundle at a stable path (the package folder changes on upgrades), with its scripts executable."""
    dst = bundle_copy()
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(bundle_source(), dst, ignore=shutil.ignore_patterns("__pycache__"))
    for f in (dst / "bin").iterdir():
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dst


def _run(cmd: list[str], timeout: float = 180) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return p.returncode, (p.stdout + p.stderr).strip()


# -- Claude Code
def install_claude(bundle: Path) -> tuple[bool, str]:
    claude = _claude_bin()
    if not claude:
        return False, "Claude Code not found"
    rc, out = _run([claude, "plugin", "marketplace", "add", str(bundle)])
    if rc != 0 and "already" not in out.lower():
        return False, f"`claude plugin marketplace add` failed: {out[-400:]}"
    if rc != 0:                               # already added: refresh it from the copy just written
        _run([claude, "plugin", "marketplace", "update", MARKETPLACE])
    rc, out = _run([claude, "plugin", "install", PLUGIN_ID, "--scope", "user"])
    if rc != 0 and "already" not in out.lower():
        return False, f"`claude plugin install {PLUGIN_ID}` failed: {out[-400:]}"
    if rc != 0:
        _run([claude, "plugin", "update", PLUGIN_ID])
    return True, "plugin eitri@eitri (skills /eitri:design, /eitri:combined, /eitri:cad ...; servers eitri, eitri-combined, both on this computer)"


def uninstall_claude() -> tuple[bool, str]:
    claude = _claude_bin()
    if not claude:
        return False, "Claude Code not found"
    rc1, out1 = _run([claude, "plugin", "uninstall", PLUGIN_ID])
    rc2, out2 = _run([claude, "plugin", "marketplace", "remove", MARKETPLACE])
    if rc1 != 0 and "not" not in out1.lower():
        return False, out1[-300:]
    return True, "removed plugin eitri@eitri and the eitri marketplace"


def remove_old_remote_claude() -> bool:
    """Remove eitri.io's closed `eitri-io` server from Claude Code's user config, if 0.9.0's `login` added it (True: it was there)."""
    claude = _claude_bin()
    if not claude:
        return False
    rc, _out = _run([claude, "mcp", "remove", "-s", "user", OLD_REMOTE], timeout=60)
    return rc == 0


# -- Codex
def codex_block(bundle: Path) -> str:
    q = json.dumps                                            # a JSON string is a valid TOML basic string
    return "\n".join([
        BLOCK_START,
        "[mcp_servers.eitri]",
        f"command = {q(eitri_command())}",
        'args = ["mcp"]',
        "startup_timeout_sec = 60",
        "tool_timeout_sec = 900",
        "",
        "[mcp_servers.eitri.env]",
        'EITRI_HOST_AGENT = "Codex"',
        'EITRI_IMAGE_MODE = "inline"',
        'EITRI_LAZY_IMPORT = "1"',
        "",
        "[mcp_servers.eitri-combined]",
        f"command = {q(eitri_command())}",
        'args = ["mcp", "--combined"]',
        "startup_timeout_sec = 60",
        "tool_timeout_sec = 900",
        "",
        "[mcp_servers.eitri-combined.env]",
        'EITRI_HOST_AGENT = "Codex"',
        'EITRI_IMAGE_MODE = "inline"',
        'EITRI_LAZY_IMPORT = "1"',
        BLOCK_END,
        "",
    ])


def _strip_block(text: str) -> str:
    pattern = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.S)
    return pattern.sub("", text)


def install_codex(bundle: Path) -> tuple[bool, str]:
    cfg = codex_home() / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    old = cfg.read_text(encoding="utf-8") if cfg.is_file() else ""
    base = _strip_block(old)
    clash = [n for n in ("eitri", "eitri-combined", '"eitri"', '"eitri-combined"')
             if re.search(rf"^\s*\[mcp_servers\.{re.escape(n)}\]", base, re.M)]
    if clash:
        return False, f"{cfg} already defines [mcp_servers.{clash[0].strip(chr(34))}] outside eitri's block; remove it and run again"
    new = base.rstrip("\n") + ("\n\n" if base.strip() else "") + codex_block(bundle)
    try:
        import tomllib
        tomllib.loads(new)
    except ModuleNotFoundError:                               # Python 3.10: no TOML reader to check with
        pass
    except Exception as exc:                                  # noqa: BLE001 - never leave a config Codex cannot read
        return False, f"{cfg} would not parse with eitri's block ({exc}); left unchanged"
    if old and not (cfg.with_suffix(".toml.eitri-backup")).exists():
        shutil.copy2(cfg, cfg.with_suffix(".toml.eitri-backup"))
    cfg.write_text(new, encoding="utf-8")
    n = install_codex_skills(bundle)
    return True, f"servers eitri + eitri-combined in {_tilde(cfg)}; {n} skills in {_tilde(agents_skills_dir())}"


def agents_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


def install_codex_skills(bundle: Path) -> int:
    """The skills as Codex user skills: ~/.agents/skills/eitri-<name>/SKILL.md (names prefixed: the folder is shared)."""
    n = 0
    for name in SKILLS:
        src = bundle / "skills" / name / "SKILL.md"
        if not src.is_file():
            continue
        text = src.read_text(encoding="utf-8")
        text = re.sub(r"^name: .*$", f"name: eitri-{name}", text, count=1, flags=re.M)
        for other in SKILLS:                                  # cross-references: the **design** skill -> **eitri-design**
            text = text.replace(f"**{other}**", f"**eitri-{other}**")
        dst = agents_skills_dir() / f"eitri-{name}" / "SKILL.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        n += 1
    return n


def uninstall_codex() -> tuple[bool, str]:
    cfg = codex_home() / "config.toml"
    removed = []
    if cfg.is_file():
        old = cfg.read_text(encoding="utf-8")
        new = _strip_block(old)
        if new != old:
            cfg.write_text(new.rstrip("\n") + "\n" if new.strip() else "", encoding="utf-8")
            removed.append(f"servers from {_tilde(cfg)}")
    for name in SKILLS:
        d = agents_skills_dir() / f"eitri-{name}"
        if d.is_dir():
            shutil.rmtree(d)
            removed.append(f"skill eitri-{name}")
    return True, ("removed " + ", ".join(removed)) if removed else "nothing to remove"


# -- Cursor
def cursor_servers(bundle: Path) -> dict:
    env = {"EITRI_HOST_AGENT": "Cursor", "EITRI_IMAGE_MODE": "inline", "EITRI_LAZY_IMPORT": "1"}
    servers: dict = {"eitri": {"type": "stdio", "command": eitri_command(), "args": ["mcp"], "env": dict(env)},
                     "eitri-combined": {"type": "stdio", "command": eitri_command(), "args": ["mcp", "--combined"], "env": dict(env)}}
    return servers


def install_cursor(bundle: Path, rules_dir: str | None = None) -> tuple[bool, str]:
    cfg = cursor_home() / "mcp.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.is_file() else {}
    except ValueError as exc:
        return False, f"{cfg} is not valid JSON ({exc}); left unchanged"
    if not isinstance(data, dict):
        return False, f"{cfg} is not a JSON object; left unchanged"
    servers = data.setdefault("mcpServers", {})
    servers.pop("eitri-combined", None)                     # 0.7.0-0.8.1 wrote eitri.io's URL under this name
    servers.pop(OLD_REMOTE, None)                           # 0.9.0's `login`: eitri.io's closed server
    servers.update(cursor_servers(bundle))
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    msg = f"servers eitri + eitri-combined in {_tilde(cfg)}"
    if rules_dir:
        rule = Path(rules_dir).expanduser() / ".cursor" / "rules" / "eitri.mdc"
        rule.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle / "cursor" / "eitri.mdc", rule)
        msg += f"; rule {_tilde(rule)}"
    return True, msg


def uninstall_cursor() -> tuple[bool, str]:
    cfg = cursor_home() / "mcp.json"
    if not cfg.is_file():
        return True, "nothing to remove"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except ValueError:
        return False, f"{cfg} is not valid JSON; left unchanged"
    servers = data.get("mcpServers") or {}
    gone = [k for k in ("eitri", "eitri-combined", OLD_REMOTE) if servers.pop(k, None) is not None]
    cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True, ("removed " + ", ".join(gone) + f" from {_tilde(cfg)}") if gone else "nothing to remove"


# ------------------------------------------------------------------------------------------------ commands
NO_AGENT = 3                       # `install` found no agent app: nothing failed, there is nowhere to register the plugin yet
INSTALL_AGENT_HINT = ("No agent app found (looked for Claude Code, Codex and Cursor), so the plugin is not registered anywhere yet.\n"
                      "Install Claude Code:  curl -fsSL https://claude.ai/install.sh | bash   (or: npm install -g @anthropic-ai/claude-code)\n"
                      "then run:  eitri plugin install   and then:  claude\n"
                      "(Codex or Cursor work too: install one, then run eitri plugin install.)\n"
                      "Or add the servers by hand: `eitri mcp` (stdio, the six single domains) and `eitri mcp --combined` (stdio, the "
                      "combined workspace).")


def _telemetry_agent(agents: list[str]) -> str:
    return {"claude": "claude-code", "codex": "codex", "cursor": "cursor"}.get(agents[0] if agents else "", "unknown")


def _count_install(agents: list[str]) -> None:
    """The anonymous install count (once per install id) and the one line saying what is sent; never fails the install."""
    try:
        from . import telemetry
        if telemetry.enabled() and not telemetry.first_run_notice(sys.stdout):
            print(telemetry.NOTICE)                           # every install says it, the first one marks it shown
        telemetry.send_install(agent=_telemetry_agent(agents))
    except Exception:                                         # noqa: BLE001
        pass


def remove_old_remote() -> list[str]:
    """eitri.io's closed hosted server, wherever an older eitri put it: Claude Code's user config (`eitri-io`), Cursor's
    mcp.json (`eitri-io`, or 0.8.x's `eitri-combined` pointing at eitri.io), the saved token. Codex's entry lived in eitri's
    own block, which install rewrites. Returns what was removed (for the person to read)."""
    gone = []
    if remove_old_remote_claude():
        gone.append(f"Claude Code's {OLD_REMOTE} server")
    cfg = cursor_home() / "mcp.json"
    data = _read_json(cfg) if cfg.is_file() else {}
    servers = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else {}
    stale = [k for k in (OLD_REMOTE, "eitri-combined") if k in servers and "url" in (servers.get(k) or {})]
    if stale:
        for k in stale:
            servers.pop(k, None)
        cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        gone.append(f"Cursor's {', '.join(stale)} server")
    if forget_old_token():
        gone.append("the saved eitri.io token")
    return gone


def run_install(agents: list[str] | None = None, cursor_rules: str | None = None, quiet: bool = False) -> int:
    found = found_agents()
    want = agents or list(found)
    if not want:
        print(INSTALL_AGENT_HINT)
        return NO_AGENT
    gone = remove_old_remote()                                # before the new configs are written
    bundle = copy_bundle()
    ok_all, done = True, []
    for a in want:
        if a == "claude":
            ok, msg = install_claude(bundle)
        elif a == "codex":
            ok, msg = install_codex(bundle)
        elif a == "cursor":
            ok, msg = install_cursor(bundle, rules_dir=cursor_rules)
        else:
            ok, msg = False, "unknown agent (claude, codex, cursor)"
        ok_all &= ok
        if ok:
            done.append(a)
        print(f"  {'ok  ' if ok else 'FAIL'} {_AGENT_NAMES.get(a, a)}: {msg}")
    if gone:
        print(f"  ok   removed eitri.io's closed hosted workspace: {', '.join(gone)}")
    (data_dir() / "plugin-installed.json").write_text(json.dumps({"agents": want, "cursor_rules": cursor_rules}), encoding="utf-8")
    if not quiet:
        print("")
        print("Both servers run on this computer: the combined workspace (a whole machine: structure, wiring, motion, air, firmware "
              "and its circuit, checked together) and the six single domains. No account, no token, no API key.")
        _count_install(done)
        first = "claude" if "claude" in done else (done[0] if done else None)
        print("")
        print(("Done. Now run  claude  and ask it for a machine" if first == "claude" else
               "Done. Restart your agent and ask it for a machine") +
              ", for example: \"design a desk robot arm with three MG996R servos and an Arduino Uno, powered from a 5 V supply\".")
    return 0 if ok_all else 1


def run_login() -> int:
    """`eitri plugin login` from 0.7.0-0.9.0 added eitri.io's hosted workspace; it has closed. Says so, and cleans up after an
    older login."""
    print(CLOSED)
    for what in remove_old_remote():
        print(f"  removed {what}")
    return 0


def run_logout() -> int:
    gone = remove_old_remote()
    print("removed " + ", ".join(gone) if gone else "nothing to remove: there is no sign-in (" + CLOSED.split(":")[0] + ")")
    return 0


def run_uninstall() -> int:
    remove_old_remote()
    for name, fn in (("claude", uninstall_claude), ("codex", uninstall_codex), ("cursor", uninstall_cursor)):
        if name == "claude" and not _claude_bin():
            continue
        ok, msg = fn()
        print(f"  {'ok  ' if ok else 'FAIL'} {_AGENT_NAMES[name]}: {msg}")
    if bundle_copy().exists():
        shutil.rmtree(bundle_copy())
    try:
        (data_dir() / "plugin-installed.json").unlink()
    except FileNotFoundError:
        pass
    print("eitri itself is still installed (projects in ~/eitri-projects are kept).")
    return 0


def run_status() -> int:
    found = found_agents()
    print("eitri plugin")
    print(f"  agents found: {', '.join(_AGENT_NAMES[a] for a in found) or 'none'}")
    claude = _claude_bin()
    if claude:
        rc, out = _run([claude, "plugin", "list"], timeout=60)
        print(f"  Claude Code: {'installed' if PLUGIN_ID in out or 'eitri@eitri' in out else 'not installed'}")
    cfg = codex_home() / "config.toml"
    if "codex" in found:
        print(f"  Codex: {'installed' if cfg.is_file() and BLOCK_START in cfg.read_text(encoding='utf-8') else 'not installed'}")
    if "cursor" in found:
        print(f"  Cursor: {'installed' if 'eitri' in (_read_json(cursor_home() / 'mcp.json').get('mcpServers') or {}) else 'not installed'}")
    print("  servers: eitri (single domains) and eitri-combined (the combined workspace), both on this computer; nothing to sign in to")
    if old_token_path().is_file():
        print("  an eitri.io token from an older eitri is still saved: `eitri plugin install` (or logout) removes it")
    try:
        from . import telemetry
        why = telemetry.off_reason()
        print(f"  anonymous counts: {'off (' + why + ')' if why else 'on (eitri telemetry off turns them off)'}")
    except Exception:                                         # noqa: BLE001
        pass
    return 0


def refresh_after_update() -> None:
    """After `eitri update`: copy the new bundle and re-register it where it was installed (quietly; best effort). This is also
    where an older install loses eitri.io's closed server."""
    marker = data_dir() / "plugin-installed.json"
    if not marker.is_file():
        return
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
        run_install(info.get("agents"), info.get("cursor_rules"), quiet=True)
    except Exception as exc:                                  # noqa: BLE001
        print(f"could not refresh the eitri plugin ({exc}); run `eitri plugin install`")


def main(args) -> int:
    action = args.action
    if action == "install":
        agents = [a.strip() for a in (args.agent or "").split(",") if a.strip()] or None
        return run_install(agents, args.cursor_rules)
    if action == "login":
        return run_login()
    if action == "logout":
        return run_logout()
    if action == "uninstall":
        return run_uninstall()
    if action == "status":
        return run_status()
    return 2


def add_parser(sub) -> None:
    pl = sub.add_parser("plugin", help="use eitri inside your own agent (Claude Code, Codex, Cursor): install | uninstall | status")
    pl.add_argument("action", choices=["install", "uninstall", "status", "login", "logout"])
    pl.add_argument("--agent", help="only these agents: claude,codex,cursor (default: every one found)")
    pl.add_argument("--cursor-rules", metavar="DIR", help="also write DIR/.cursor/rules/eitri.mdc (a Cursor project rule)")
    # accepted so older scripts keep working; they do nothing now (eitri.io's hosted workspace has closed)
    for flag in ("--login", "--token-stdin", "--no-check", "--no-browser", "--keep-token", "--online"):
        pl.add_argument(flag, action="store_true", help=argparse_suppress())


def argparse_suppress():
    import argparse
    return argparse.SUPPRESS


_AGENT_NAMES = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor"}


def _read_json(f: Path) -> dict:
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _tilde(p: Path | str) -> str:
    p = Path(p)
    try:
        return "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)

