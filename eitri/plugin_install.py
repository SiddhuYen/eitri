"""`eitri plugin install | login | logout | uninstall | status`: register eitri in the agent apps on this computer.

eitri runs inside the user's own agent (on their own subscription) as one plugin with two MCP servers:
- `eitri`: the local single-domain tools (`eitri mcp`, stdio, this computer);
- `eitri-combined`: the combined multi-domain workspace on eitri.io (streamable HTTP, a personal token from
  eitri.io/account sent as `Authorization: Bearer <token>`).

What `install` does per agent app it finds (nothing for an app that is not there):
- Claude Code: a copy of the plugin bundle goes to ~/.local/share/eitri/plugin (a stable path: the installed package moves
  on upgrades), which is added as the `eitri` marketplace, and `claude plugin install eitri@eitri` installs it (skills +
  both servers; the token reaches eitri.io through the bundle's headersHelper, which reads the file `login` saves).
- Codex: `[mcp_servers.eitri]` and `[mcp_servers.eitri-combined]` (with `http_headers_helper`, the same helper) in a
  marked block of ~/.codex/config.toml, and the skills in ~/.agents/skills/eitri-*/ (Codex's plugin manifests cannot
  carry a local stdio server or a header helper, so this is the closest equivalent).
- Cursor: both servers in ~/.cursor/mcp.json (the token as a static header: Cursor has no header helper);
  `--cursor-rules DIR` also writes DIR/.cursor/rules/eitri.mdc (Cursor has no documented file for global rules).

`login` opens eitri.io/account, takes the token (typed, or --token-stdin), checks it against eitri.io/mcp and saves it in
eitri's config folder (readable only by you). No shell file is edited. No telemetry: eitri.io is contacted only by
`login`'s check and by the agent's own calls to the combined tools.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import webbrowser
from pathlib import Path

from .config import config_dir

DEFAULT_URL = "https://eitri.io/mcp"
MARKETPLACE = "eitri"
PLUGIN_ID = "eitri@eitri"
BLOCK_START = "# >>> eitri plugin (written by `eitri plugin install`; `eitri plugin uninstall` removes it) >>>"
BLOCK_END = "# <<< eitri plugin <<<"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.~+/=-]{16,512}$")
# Printed to the person by `eitri plugin login` once a token is stored (consent itself is given on eitri.io/account when
# the token is made; this says it again where the person is, not through their agent). One place to change the wording.
PRIVACY_NOTICE = ("In the combined workspace, the model files your agent writes are sent to eitri.io, kept as projects in "
                  "your account, and used to train eitri's models unless you turn that off at https://eitri.io/account. "
                  "Single-domain work stays on this machine.")
SIGNED_IN_NOTICE = "Signed in to eitri.io. " + PRIVACY_NOTICE            # the token was checked with eitri.io


def unverified_notice(reason: str) -> str:
    """The notice for a token stored without eitri.io confirming it (--no-check, or eitri.io unreachable)."""
    return f"Token saved (not checked yet: {reason}). " + PRIVACY_NOTICE
SKILLS = ("design", "combined", "cad", "wires", "motion", "pneumatics", "firmware", "electronics")


# ------------------------------------------------------------------------------------------------ paths and token
def bundle_source() -> Path:
    return Path(__file__).parent / "plugin_bundle"


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "eitri"


def bundle_copy() -> Path:
    return data_dir() / "plugin"


def remote_url() -> str:
    return os.environ.get("EITRI_MCP_URL") or DEFAULT_URL


def remote_label() -> str:
    """The combined server's URL and where it comes from, for `status` and `install`."""
    url = remote_url()
    return f"{url} (from EITRI_MCP_URL)" if os.environ.get("EITRI_MCP_URL") else f"{url} (the default)"


def account_url(url: str | None = None) -> str:
    from urllib.parse import urlparse
    u = urlparse(url or remote_url())
    return f"{u.scheme}://{u.netloc}/account"


def token_path() -> Path:
    return config_dir() / "plugin-token"


def load_token() -> str | None:
    try:
        t = token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return t or None


def save_token(token: str) -> Path:
    f = token_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(f), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token.strip() + "\n")
    os.chmod(f, 0o600)
    return f


def forget_token() -> bool:
    try:
        token_path().unlink()
        return True
    except FileNotFoundError:
        return False


def mask(token: str | None) -> str:
    if not token:
        return "(none)"
    return token[:6] + "…" + token[-4:] if len(token) > 12 else "…"


def check_token(token: str | None, url: str | None = None, timeout: float = 20.0) -> tuple[str, str]:
    """("ok" | "rejected" | "unreachable", detail): eitri.io/mcp answers initialize without a token and refuses a tool
    call without a valid one (an isError result with how to sign in, not an HTTP 401), so this initializes and makes one
    harmless call, combined_list_projects, with the token."""
    import urllib.error
    import urllib.request
    from . import __version__
    url = url or remote_url()
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-06-18"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def post(msg: dict, extra: dict | None = None) -> tuple[dict | None, dict]:
        req = urllib.request.Request(url, data=json.dumps(msg).encode(), headers={**headers, **(extra or {})}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(1 << 20).decode("utf-8", "replace")
            return _jsonrpc_reply(body, r.headers.get("Content-Type") or ""), dict(r.headers)

    try:
        init, h = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "eitri-plugin-login", "version": __version__}}})
        sid = {k: v for k, v in h.items() if k.lower() == "mcp-session-id"}
        extra = {"Mcp-Session-Id": next(iter(sid.values()))} if sid else {}
        try:
            post({"jsonrpc": "2.0", "method": "notifications/initialized"}, extra)
        except urllib.error.HTTPError:
            pass
        res, _ = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "combined_list_projects", "arguments": {}}}, extra)
    except urllib.error.HTTPError as exc:
        text = exc.read(4096).decode("utf-8", "replace")
        if exc.code in (401, 403):
            return "rejected", _error_text(text) or f"HTTP {exc.code}"
        return "unreachable", f"HTTP {exc.code}: {_error_text(text)[:200]}"
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        return "unreachable", str(getattr(exc, "reason", exc))
    if not res:
        return "unreachable", "no answer from the server"
    if "error" in res:
        return "unreachable", str((res.get("error") or {}).get("message", res["error"]))[:300]
    result = res.get("result") or {}
    text = " ".join(c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict))
    if result.get("isError"):
        if re.search(r"token|sign(ed)? in|log ?in", text, re.I):
            return "rejected", text.strip()[:300]
        return "unreachable", text.strip()[:300]
    return "ok", "signed in"


def _jsonrpc_reply(body: str, ctype: str) -> dict | None:
    """The JSON-RPC response in a body that is JSON or a server-sent event stream (the last message with an id)."""
    if "text/event-stream" in ctype:
        found = None
        for line in body.splitlines():
            if line.startswith("data:"):
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(msg, dict) and "id" in msg:
                    found = msg
        return found
    body = body.strip()
    return json.loads(body) if body else None


def _error_text(text: str) -> str:
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            e = d.get("error")
            if isinstance(e, dict):
                return str(e.get("message") or "")
            return str(e or d.get("message") or d.get("detail") or "")
    except ValueError:
        pass
    return text.strip()[:300]


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
    return True, "plugin eitri@eitri (skills /eitri:design, /eitri:combined, /eitri:cad ...; servers eitri, eitri-combined)"


def uninstall_claude() -> tuple[bool, str]:
    claude = _claude_bin()
    if not claude:
        return False, "Claude Code not found"
    rc1, out1 = _run([claude, "plugin", "uninstall", PLUGIN_ID])
    rc2, out2 = _run([claude, "plugin", "marketplace", "remove", MARKETPLACE])
    if rc1 != 0 and "not" not in out1.lower():
        return False, out1[-300:]
    return True, "removed plugin eitri@eitri and the eitri marketplace"


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
        f"url = {q(remote_url())}",
        f"http_headers_helper = {q(str(bundle / 'bin' / 'eitri-auth-header'))}",
        "startup_timeout_sec = 30",
        "tool_timeout_sec = 900",
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
def cursor_servers(bundle: Path, token: str | None) -> dict:
    servers: dict = {"eitri": {"type": "stdio", "command": eitri_command(), "args": ["mcp"],
                               "env": {"EITRI_HOST_AGENT": "Cursor", "EITRI_IMAGE_MODE": "inline", "EITRI_LAZY_IMPORT": "1"}}}
    if token:
        servers["eitri-combined"] = {"url": remote_url(), "headers": {"Authorization": f"Bearer {token}"}}
    return servers


def install_cursor(bundle: Path, token: str | None = None, rules_dir: str | None = None) -> tuple[bool, str]:
    cfg = cursor_home() / "mcp.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.is_file() else {}
    except ValueError as exc:
        return False, f"{cfg} is not valid JSON ({exc}); left unchanged"
    if not isinstance(data, dict):
        return False, f"{cfg} is not a JSON object; left unchanged"
    servers = data.setdefault("mcpServers", {})
    servers.pop("eitri-combined", None)
    servers.update(cursor_servers(bundle, token if token is not None else load_token()))
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if "eitri-combined" in servers:
        os.chmod(cfg, 0o600)                                  # it holds the token
    msg = f"servers in {_tilde(cfg)}" + ("" if "eitri-combined" in servers else " (eitri-combined after `eitri plugin login`)")
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
    gone = [k for k in ("eitri", "eitri-combined") if servers.pop(k, None) is not None]
    cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True, ("removed " + ", ".join(gone) + f" from {_tilde(cfg)}") if gone else "nothing to remove"


# ------------------------------------------------------------------------------------------------ commands
def run_install(agents: list[str] | None = None, cursor_rules: str | None = None, quiet: bool = False) -> int:
    found = found_agents()
    want = agents or list(found)
    if not want:
        print("No agent app found (looked for Claude Code, Codex and Cursor). Install one, then run `eitri plugin install`.\n"
              "Or add the servers by hand: the local one is `eitri mcp` (stdio), the combined one "
              f"{remote_url()} with the header `Authorization: Bearer <token from {account_url()}>`.")
        return 1
    bundle = copy_bundle()
    ok_all = True
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
        print(f"  {'ok  ' if ok else 'FAIL'} {_AGENT_NAMES.get(a, a)}: {msg}")
    (data_dir() / "plugin-installed.json").write_text(json.dumps({"agents": want, "cursor_rules": cursor_rules}), encoding="utf-8")
    if not quiet:
        token = load_token()
        print("")
        print(f"Combined workspace ({remote_label()}): " + (f"signed in ({mask(token)})" if token else
              f"not signed in yet: run  eitri plugin login  (it opens {account_url()} for a personal token)"))
        print("Restart your agent, then ask it for a design, for example: \"design a 2-DOF pan-tilt camera mount with SG90 "
              "servos, powered from USB\". The plugin's design skill picks the combined workspace or one local domain.")
    return 0 if ok_all else 1


def run_login(token: str | None = None, from_stdin: bool = False, check: bool = True, open_browser: bool = True) -> int:
    url = remote_url()
    if from_stdin:
        token = sys.stdin.read().strip()
    if not token:
        acct = account_url(url)
        # flushed: the prompt goes to the terminal or stderr, and a piped stdout would otherwise print this after it
        print(f"Make a personal token on {acct} (sign in with GitHub; \"Tokens for your agent\"), then paste it below.",
              flush=True)
        if open_browser:
            try:
                webbrowser.open(acct)
            except Exception:                                 # noqa: BLE001 - the link is printed anyway
                pass
        token = _ask_token("eitri.io token (hidden; Enter to skip): ")
    if not token:
        print("No token given: the local single-domain tools work without one; run `eitri plugin login` any time.")
        return 1
    token = token.removeprefix("Bearer ").strip()
    if not TOKEN_RE.match(token):
        print("not saved: that does not look like an eitri.io token (copy the whole token from eitri.io/account)")
        return 1
    unverified = "--no-check was given" if not check else None
    if check:
        state, detail = check_token(token, url)
        if state == "rejected":
            print(f"not saved: eitri.io rejected this token ({detail}). Make a new one on {account_url(url)}.")
            return 1
        if state == "unreachable":
            unverified = f"could not reach {url} ({detail})"
    path = save_token(token)
    print(f"saved {mask(token)} to {path} (readable only by you)")
    print("")
    print(SIGNED_IN_NOTICE if unverified is None else unverified_notice(unverified))
    print("", flush=True)
    if (cursor_home() / "mcp.json").is_file() and "eitri" in (_read_json(cursor_home() / "mcp.json").get("mcpServers") or {}):
        install_cursor(bundle_copy() if bundle_copy().is_dir() else bundle_source(), token=token)
        print("updated Cursor's eitri-combined server with the token")
    print("Restart your agent (in Claude Code, /mcp -> eitri-combined -> Reconnect also works) to use the combined workspace.")
    return 0


def _ask_token(prompt: str) -> str:
    """The token, typed hidden in a terminal; without one (stdin piped), one line from stdin with the prompt on stderr
    (getpass would warn that it cannot hide the input, or prompt on a /dev/tty the caller does not see)."""
    try:
        if sys.stdin.isatty():
            import getpass
            return getpass.getpass(prompt).strip()
        sys.stderr.write(prompt)
        sys.stderr.flush()
        return (sys.stdin.readline() or "").strip()
    except (EOFError, KeyboardInterrupt, OSError):
        return ""


def run_logout() -> int:
    gone = forget_token()
    if (cursor_home() / "mcp.json").is_file():
        install_cursor(bundle_copy() if bundle_copy().is_dir() else bundle_source(), token="")
    print("forgot the eitri.io token" if gone else "no eitri.io token was saved")
    return 0


def run_uninstall(keep_token: bool = False) -> int:
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
    if not keep_token and forget_token():
        print("  ok   forgot the eitri.io token")
    print("eitri itself is still installed (projects in ~/eitri-projects are kept).")
    return 0


def run_status(online: bool = False) -> int:
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
    token = load_token()
    print(f"  combined server: {remote_label()}")
    print(f"  eitri.io token: {mask(token)}" + ("" if token else "  (run: eitri plugin login)"))
    if online and token:
        state, detail = check_token(token)
        print(f"  token check against {remote_url()}: {state} ({detail})")
    return 0


def refresh_after_update() -> None:
    """After `eitri update`: copy the new bundle and re-register it where it was installed (quietly; best effort)."""
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
        rc = run_install(agents, args.cursor_rules)
        if rc == 0 and args.login and not load_token():
            run_login(open_browser=not args.no_browser)
        return rc
    if action == "login":
        return run_login(token=None, from_stdin=args.token_stdin, check=not args.no_check, open_browser=not args.no_browser)
    if action == "logout":
        return run_logout()
    if action == "uninstall":
        return run_uninstall(keep_token=args.keep_token)
    if action == "status":
        return run_status(online=args.online)
    if action == "headers":                                   # what the bundle's helper prints, for checking by hand
        token = load_token()
        print(json.dumps({"Authorization": f"Bearer {token}"} if token else {}))
        return 0
    return 2


def add_parser(sub) -> None:
    pl = sub.add_parser("plugin", help="use eitri inside your own agent (Claude Code, Codex, Cursor): install | login | logout | uninstall | status")
    pl.add_argument("action", choices=["install", "login", "logout", "uninstall", "status", "headers"])
    pl.add_argument("--agent", help="only these agents: claude,codex,cursor (default: every one found)")
    pl.add_argument("--cursor-rules", metavar="DIR", help="also write DIR/.cursor/rules/eitri.mdc (a Cursor project rule)")
    pl.add_argument("--login", action="store_true", help="install: then sign in to eitri.io if no token is saved")
    pl.add_argument("--token-stdin", action="store_true", help="login: read the token from stdin")
    pl.add_argument("--no-check", action="store_true", help="login: save the token without checking it with eitri.io")
    pl.add_argument("--no-browser", action="store_true", help="login: print the account link instead of opening it")
    pl.add_argument("--keep-token", action="store_true", help="uninstall: keep the saved eitri.io token")
    pl.add_argument("--online", action="store_true", help="status: check the token with eitri.io")


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
