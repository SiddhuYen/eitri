"""Two anonymous counts: how many people install eitri's plugin, and how many projects they make with it.

What is sent, and nothing else (no prompts, file contents, paths, names, errors or account): one small JSON POST to
https://eitri.io/api/ping

    {"event": "install" | "project_created", "id": "<a random uuid4 made once per install>", "version": "<eitri version>",
     "os": "darwin" | "linux", "agent": "claude-code" | "codex" | "cursor" | "unknown",
     "kind": "combined" | "cad" | "wires" | "motion" | "pneumatics" | "firmware" | "electronics"}     (kind: project_created only)

The id is stored in eitri's config folder (install-id); it is not tied to any account or token. "install" is sent once per id,
by `eitri plugin install`; "project_created" by the plugin's servers when the agent makes a project (combined_new_project), or
at the first build of a project new_project made (a single-domain project's kind is known only then). The request carries
nothing else; like any request it reaches the server from this computer's network address, which the payload does not include.

Fire-and-forget: a background thread, a 2 s timeout, no retry; a failure is ignored and never stops a build, an install or a tool.

Off with any of: `eitri telemetry off` (saved in config.json), EITRI_TELEMETRY=off, EITRI_NO_PING=1, DO_NOT_TRACK=1. Nothing is
sent under pytest, inside a sandbox build (eitri.sandbox sets EITRI_TELEMETRY=off), or from builds (model code never calls this).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid

URL = "https://eitri.io/api/ping"
TIMEOUT_S = 2.0
EVENTS = ("install", "project_created")
KINDS = ("combined", "cad", "wires", "motion", "pneumatics", "firmware", "electronics")
AGENTS = ("claude-code", "codex", "cursor", "unknown")
KIND_OF_CORE = {"cad": "cad", "harness": "wires", "mechanism": "motion", "pneumatics": "pneumatics", "firmware": "firmware",
                "electronics": "electronics", "combined": "combined"}
NOTICE = ("eitri's plugin sends two anonymous counts (an install, a project created: a random id, the eitri version, the OS, the agent "
          "app, the project's domain; nothing else). Turn them off with:  eitri telemetry off")
_OFF = ("0", "off", "false", "no")
_ON = ("1", "true", "yes", "on")


def _env_off() -> str | None:
    """Why the environment turns the counts off, or None."""
    if os.environ.get("EITRI_TELEMETRY", "").strip().lower() in _OFF:
        return "EITRI_TELEMETRY=off"
    if os.environ.get("EITRI_NO_PING", "").strip().lower() in _ON:
        return "EITRI_NO_PING=1"
    if os.environ.get("DO_NOT_TRACK", "").strip().lower() in _ON:
        return "DO_NOT_TRACK=1"
    if os.environ.get("EITRI_SANDBOX_WORK"):
        return "inside a sandbox"
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return "running under tests"
    return None


def off_reason() -> str | None:
    """Why nothing is sent (None: the counts are on)."""
    why = _env_off()
    if why:
        return why
    from .config import get_setting
    if str(get_setting("telemetry", "on")).lower() in _OFF:
        return "turned off with `eitri telemetry off`"
    return None


def enabled() -> bool:
    return off_reason() is None


def install_id(create: bool = True) -> str | None:
    """The random id of this install (uuid4, lowercase), made on first use and kept in eitri's config folder."""
    from .config import config_dir
    f = config_dir() / "install-id"
    try:
        v = f.read_text(encoding="utf-8").strip().lower()
        uuid.UUID(v)
        return v
    except (OSError, ValueError):
        pass
    if not create:
        return None
    v = str(uuid.uuid4())
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(v + "\n", encoding="utf-8")
    except OSError:
        return None
    return v


def agent_name(raw: str | None = None) -> str:
    """claude-code | codex | cursor | unknown, from the plugin's EITRI_HOST_AGENT (or a name given)."""
    a = (raw if raw is not None else os.environ.get("EITRI_HOST_AGENT", "")).strip().lower()
    if "claude" in a:
        return "claude-code"
    if "codex" in a:
        return "codex"
    if "cursor" in a:
        return "cursor"
    return "unknown"


def os_name() -> str | None:
    return {"darwin": "darwin", "linux": "linux"}.get(sys.platform)


def payload(event: str, kind: str | None = None, agent: str | None = None) -> dict | None:
    """The exact body sent, or None when this event cannot be sent (unknown event or kind, another OS, no id)."""
    from . import __version__
    if event not in EVENTS:
        return None
    osn = os_name()
    pid = install_id()
    if osn is None or pid is None:
        return None
    version = re.sub(r"[^0-9A-Za-z.+-]", "", str(__version__))[:32]
    body = {"event": event, "id": pid, "version": version, "os": osn, "agent": agent if agent in AGENTS else agent_name(agent)}
    if event == "project_created":
        k = KIND_OF_CORE.get(str(kind or ""), str(kind or ""))
        if k not in KINDS:
            return None
        body["kind"] = k
    return body


def _post(body: dict) -> None:
    import urllib.request
    data = json.dumps(body, separators=(",", ":")).encode()
    req = urllib.request.Request(URL, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "eitri"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            r.read(64)
    except Exception:  # noqa: BLE001 - fire and forget: never retried, never reported
        pass


_sender = _post                      # tests replace this (no network in tests)


def _send_quietly(body: dict) -> None:
    try:
        _sender(body)
    except Exception:  # noqa: BLE001 - fire and forget
        pass


def send(event: str, kind: str | None = None, agent: str | None = None, wait: bool = False) -> threading.Thread | None:
    """Send one count in the background (wait=True: wait for it, at most TIMEOUT_S + a little; a CLI about to exit). Returns the
    thread, or None when nothing is sent."""
    try:
        if not enabled():
            return None
        body = payload(event, kind, agent)
        if body is None:
            return None
        t = threading.Thread(target=_send_quietly, args=(body,), daemon=True, name="eitri-ping")
        t.start()
        if wait:
            t.join(TIMEOUT_S + 0.5)
        return t
    except Exception:  # noqa: BLE001 - counting never breaks anything
        return None


def send_install(agent: str | None = None) -> threading.Thread | None:
    """The install count: once per install id (a refresh after `eitri update` re-runs the install and sends nothing)."""
    from .config import get_setting, set_setting
    try:
        if not enabled():
            return None
        pid = install_id()
        if pid is None or get_setting("install_ping") == pid:
            return None
        t = send("install", agent=agent, wait=True)
        if t is not None:
            set_setting("install_ping", pid)
        return t
    except Exception:  # noqa: BLE001
        return None


def first_run_notice(stream=None) -> bool:
    """Print the one-line notice once per config folder (on stderr: an MCP server's stdout is the protocol). True if printed."""
    from .config import config_dir
    if not enabled():
        return False
    f = config_dir() / "telemetry-notice-shown"
    if f.exists():
        return False
    try:
        print(NOTICE, file=stream or sys.stderr, flush=True)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("1\n", encoding="utf-8")
    except OSError:
        pass
    return True


def notice_once() -> str | None:
    """The one-line notice, the first time anything shows it on this install (then None): the first plugin result that makes a
    project carries it for the agent to pass on, unless `eitri plugin install` already printed it in the person's terminal."""
    import io
    buf = io.StringIO()
    return buf.getvalue().strip() if first_run_notice(buf) else None


def main(action: str = "status") -> int:
    from .config import set_setting
    if action == "off":
        set_setting("telemetry", "off")
        print("eitri sends no anonymous counts from now on (saved in eitri's config). `eitri telemetry on` turns them back on.")
        return 0
    if action == "on":
        set_setting("telemetry", "on")
        why = _env_off()
        print("eitri's anonymous counts are on" + (f", but {why} keeps them off here." if why else "."))
        return 0
    why = off_reason()
    print(f"anonymous counts: {'off (' + why + ')' if why else 'on'}")
    print(f"  sent to {URL}: event (install | project_created), id {install_id(create=False) or '(none yet)'}, version, os, agent, "
          "kind (project_created only). Nothing else.")
    print("  off: eitri telemetry off  (or EITRI_TELEMETRY=off, EITRI_NO_PING=1, DO_NOT_TRACK=1)")
    return 0
