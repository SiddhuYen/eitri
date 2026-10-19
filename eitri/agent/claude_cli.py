"""Drive a headless Claude Code CLI as the LLM backend.

The CLI is started once per session in stream-json mode, with the eitri MCP
server as its only tool source and our system prompt replacing its default. User
turns are written to its stdin; events stream back on stdout.

Authentication is an Anthropic API key and nothing else: the CLI runs with --bare (OAuth and the
keychain are never read) and the key is passed in its environment, so a Claude subscription that
happens to be logged in on this machine is never used. The binary normally comes with the
claude-agent-sdk package (a eitri dependency), so no separate Claude Code install is needed.
"""
from __future__ import annotations

import base64
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from ..project import Project

EventHandler = Callable[[str, dict], None]


_THINKING_DISPLAY: dict[str, bool] = {}
_BARE: dict[str, bool] = {}

# variables that would make the CLI authenticate some other way than the API key it is given
_OTHER_AUTH = ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
               "CLAUDE_CODE_USE_FOUNDRY", "ANTHROPIC_API_KEY")


def supports_bare(binary: str) -> bool:
    """Whether this Claude Code binary has --bare (API-key-only auth); older builds do not."""
    if binary not in _BARE:
        try:
            r = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=20)
            _BARE[binary] = "--bare" in (r.stdout + r.stderr)
        except (OSError, subprocess.SubprocessError):
            _BARE[binary] = False
    return _BARE[binary]


def bundled_claude_binary() -> str | None:
    """The Claude Code binary that ships inside the claude-agent-sdk wheel, if installed."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("claude_agent_sdk")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    for root in spec.submodule_search_locations:
        for name in ("claude", "claude.exe"):
            c = Path(root) / "_bundled" / name
            if c.is_file():
                if os.name != "nt" and not os.access(c, os.X_OK):
                    try:
                        c.chmod(c.stat().st_mode | 0o111)
                    except OSError:
                        continue
                return str(c)
    return None


def supports_thinking_display(binary: str) -> bool:
    """Whether this Claude Code accepts --thinking-display (a hidden option: newer versions stream readable
    thinking summaries with it, instead of empty thinking blocks). An invalid value for a known option
    fails listing the allowed choices; an unknown option is ignored next to --version."""
    if binary in _THINKING_DISPLAY:
        return _THINKING_DISPLAY[binary]
    ok = False
    try:
        r = subprocess.run([binary, "--thinking-display", "__eitri_probe__", "--version"], capture_output=True,
                           text=True, timeout=15)
        ok = r.returncode != 0 and "summarized" in (r.stdout + r.stderr)
    except Exception:
        ok = False
    _THINKING_DISPLAY[binary] = ok
    return ok


def find_claude_binary() -> str | None:
    """Locate the Claude Code CLI: EITRI_CLAUDE_BIN, the one inside Anthropic's claude-agent-sdk package, then PATH and
    the editor extensions. Only builds with --bare qualify (see the module docstring)."""
    env = os.environ.get("EITRI_CLAUDE_BIN")
    if env and Path(env).exists():
        return env
    bundled = bundled_claude_binary()
    if bundled:
        return bundled
    for name in ("claude",):
        p = shutil.which(name)
        if p and supports_bare(p):
            return p
    home = Path.home()
    candidates = [home / ".claude" / "local" / "claude"]
    for ext in sorted(glob.glob(str(home / ".vscode" / "extensions" / "anthropic.claude-code-*")), reverse=True):
        candidates.append(Path(ext) / "resources" / "native-binary" / "claude")
    for ext in sorted(glob.glob(str(home / ".cursor" / "extensions" / "anthropic.claude-code-*")), reverse=True):
        candidates.append(Path(ext) / "resources" / "native-binary" / "claude")
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK) and supports_bare(str(c)):
            return str(c)
    return None


@dataclass
class TurnResult:
    text: str
    cost_usd: float = 0.0
    turns: int = 0
    duration_ms: int = 0
    is_error: bool = False
    tool_calls: int = 0
    usage: dict = field(default_factory=dict)
    stopped: bool = False          # the user stopped the turn (interrupt)


class ClaudeCLISession:
    """One long-lived headless Claude Code process bound to a eitri project."""

    def __init__(self, project: Project, system_prompt: str, model: str = "opus", effort: str | None = "high",
                 max_budget_usd: float | None = None, on_event: EventHandler | None = None,
                 render_quality: float = 1.0, claude_bin: str | None = None, extra_args: list[str] | None = None,
                 stream: bool = True, api_key: str | None = None):
        self.project = project
        self.system_prompt = system_prompt
        self.model = model
        self.effort = effort
        self.max_budget_usd = max_budget_usd
        self.on_event = on_event or (lambda kind, data: None)
        self.render_quality = render_quality
        from ..config import api_key as configured_key
        self.api_key = (api_key or configured_key() or "").strip()
        if not self.api_key:
            raise RuntimeError("no Anthropic API key: add one on eitri's home page, or set ANTHROPIC_API_KEY")
        self.claude_bin = claude_bin or find_claude_binary()
        if not self.claude_bin:
            raise RuntimeError("the Claude Code binary was not found: reinstall eitri (it comes with the "
                               "claude-agent-sdk package) or point EITRI_CLAUDE_BIN at a recent `claude`")
        self.extra_args = extra_args or []
        self.stream = stream                      # token-level "live" events (the website shows Claude typing)
        self.proc: subprocess.Popen | None = None
        self.session_id: str | None = None
        self._lines: "list[str]" = []
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._stderr: list[str] = []
        self.total_cost = 0.0
        self._write_lock = threading.Lock()       # stdin is shared by send() and interrupt()
        self._interrupts = 0
        self._interrupted = False
        self._process_cost = 0.0                  # the CLI reports a running total per process

    # ---------------------------------------------------------------- process
    def _mcp_config(self) -> str:
        cfg = {
            "mcpServers": {
                "eitri": {
                    "command": sys.executable,
                    "args": ["-m", "eitri.agent.mcp_server"],
                    "env": {
                        "EITRI_PROJECT": str(self.project.folder),
                        "EITRI_MODEL_NAME": self.project.name,
                        "EITRI_RENDER_QUALITY": str(self.render_quality),
                        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                    },
                }
            }
        }
        return json.dumps(cfg)

    def start(self) -> None:
        cmd = [
            self.claude_bin, "-p", "--bare",            # API-key auth only: OAuth and the keychain are never read
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--system-prompt", self.system_prompt,
            "--mcp-config", self._mcp_config(), "--strict-mcp-config",
            "--tools", "Read",                       # Read shows our review images as real image blocks
            "--allowedTools", "mcp__eitri__*", "Read",
            "--permission-mode", "dontAsk",
            "--model", self.model,
            "--no-session-persistence",
            "--setting-sources", "",
            "--exclude-dynamic-system-prompt-sections",   # stable prefix: the prompt cache survives across sessions
        ]
        if self.effort:
            cmd += ["--effort", self.effort]
        if self.max_budget_usd:
            cmd += ["--max-budget-usd", str(self.max_budget_usd)]
        if self.stream and "--include-partial-messages" not in self.extra_args:
            cmd.append("--include-partial-messages")
        if self.stream and "--thinking-display" not in self.extra_args and supports_thinking_display(self.claude_bin):
            cmd += ["--thinking-display", "summarized"]      # readable thinking, so the wait is not a blank screen
        cmd += self.extra_args
        self._process_cost = 0.0                  # a new process starts its running total at zero
        env = dict(os.environ)
        for name in _OTHER_AUTH:                  # nothing but the key below may authenticate this process
            env.pop(name, None)
        env["ANTHROPIC_API_KEY"] = self.api_key
        env.pop("CLAUDECODE", None)          # allow nesting inside another Claude Code session
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        env.setdefault("MCP_TIMEOUT", "120000")   # the CAD kernel import can take a while on a loaded machine
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"   # no telemetry, error reports or update checks: API calls only
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1, cwd=str(self.project.folder), env=env)
        self._reader = threading.Thread(target=self._pump_stderr, daemon=True)
        self._reader.start()

    def _pump_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 200:
                del self._stderr[:-200]

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()  # type: ignore[union-attr]
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()

    def _live(self, e: dict) -> None:
        """Forward the streaming API events as compact "live" events: a block starts (text, tool_use with
        its name, thinking), grows (text / partial tool-input JSON / thinking), stops."""
        et = e.get("type")
        if et == "message_start":
            self.on_event("live", {"op": "message"})
        elif et == "content_block_start":
            cb = e.get("content_block") or {}
            self.on_event("live", {"op": "start", "index": e.get("index"), "type": cb.get("type"), "name": cb.get("name"), "id": cb.get("id")})
        elif et == "content_block_delta":
            d = e.get("delta") or {}
            dt = d.get("type")
            if dt == "text_delta":
                self.on_event("live", {"op": "delta", "index": e.get("index"), "text": d.get("text", "")})
            elif dt == "input_json_delta":
                self.on_event("live", {"op": "delta", "index": e.get("index"), "json": d.get("partial_json", "")})
            elif dt == "thinking_delta":
                self.on_event("live", {"op": "delta", "index": e.get("index"), "thinking": d.get("thinking", "")})
        elif et == "content_block_stop":
            self.on_event("live", {"op": "stop", "index": e.get("index")})

    def interrupt(self) -> bool:
        """Stop the current turn. The CLI acknowledges, ends the turn with an error result (send()
        returns it marked stopped) and keeps the session, so the conversation continues."""
        if self.proc is None or self.proc.poll() is not None or self.proc.stdin is None:
            return False
        self._interrupts += 1
        req = {"type": "control_request", "request_id": f"req_{self._interrupts}_eitri", "request": {"subtype": "interrupt"}}
        try:
            with self._write_lock:
                self.proc.stdin.write(json.dumps(req) + "\n")
                self.proc.stdin.flush()
        except Exception:
            return False
        self._interrupted = True
        return True

    # ------------------------------------------------------------------ turns
    def send(self, text: str, images: list[str] | None = None, timeout: float = 3600.0) -> TurnResult:
        """Send one user turn and block until the result event; stream events to on_event."""
        if self.proc is None or self.proc.poll() is not None:
            self.start()
        assert self.proc and self.proc.stdin and self.proc.stdout
        content: list[dict[str, Any]] = []
        for img in images or []:
            p = Path(img)
            if not p.exists():
                continue
            media = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            data = base64.standard_b64encode(p.read_bytes()).decode()
            content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
        content.append({"type": "text", "text": text})
        msg = {"type": "user", "message": {"role": "user", "content": content}}
        self._interrupted = False
        with self._write_lock:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()

        texts: list[str] = []
        tool_calls = 0
        deadline = time.time() + timeout
        while True:
            if time.time() > deadline:
                return TurnResult("\n".join(texts) + "\n[timed out]", is_error=True, tool_calls=tool_calls)
            line = self.proc.stdout.readline()
            if line == "":
                if self.proc.poll() is not None:
                    err = "\n".join(self._stderr[-20:])
                    return TurnResult("\n".join(texts) + f"\n[claude exited with code {self.proc.returncode}]\n{err}",
                                      is_error=True, tool_calls=tool_calls)
                time.sleep(0.05)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if kind == "system":
                if ev.get("session_id"):
                    self.session_id = ev["session_id"]
                if ev.get("subtype") == "thinking_tokens":   # thinking is hidden, its size is reported ~1/s
                    self.on_event("live", {"op": "thinking", "tokens": int(ev.get("estimated_tokens") or 0)})
                    continue
                self.on_event("system", ev)
            elif kind == "assistant":
                msg = ev.get("message", {})
                for block in msg.get("content", []):
                    bt = block.get("type")
                    if bt == "text" and block.get("text"):
                        texts.append(block["text"])
                        self.on_event("text", {"text": block["text"]})
                    elif bt == "tool_use":
                        tool_calls += 1
                        self.on_event("tool_use", {"name": block.get("name", ""), "input": block.get("input", {}),
                                                   "id": block.get("id")})
                    elif bt == "thinking" and block.get("thinking"):
                        self.on_event("thinking", {"text": block["thinking"]})
            elif kind == "user":
                msg = ev.get("message", {})
                for block in msg.get("content", []) if isinstance(msg.get("content"), list) else []:
                    if block.get("type") == "tool_result":
                        c = block.get("content")
                        text = ""
                        if isinstance(c, str):
                            text = c
                        elif isinstance(c, list):
                            text = "\n".join(x.get("text", "") for x in c if x.get("type") == "text")
                        self.on_event("tool_result", {"id": block.get("tool_use_id"), "text": text,
                                                      "is_error": block.get("is_error", False)})
            elif kind == "result":
                stopped = self._interrupted
                self._interrupted = False
                running = float(ev.get("total_cost_usd") or 0.0)
                turn_cost = running - self._process_cost if running >= self._process_cost else running
                self._process_cost = running
                self.total_cost += turn_cost
                self.on_event("result", dict(ev, stopped=stopped, turn_cost_usd=round(turn_cost, 6)))
                final = ev.get("result") or ("\n".join(texts))
                return TurnResult(final or ("[stopped]" if stopped else ""), cost_usd=turn_cost,
                                  turns=int(ev.get("num_turns") or 0), duration_ms=int(ev.get("duration_ms") or 0),
                                  is_error=bool(ev.get("is_error")) and not stopped, tool_calls=tool_calls,
                                  usage=ev.get("usage") or {}, stopped=stopped)
            elif kind == "stream_event":                     # token-level progress of the message being written
                if not ev.get("parent_tool_use_id"):
                    self._live(ev.get("event") or {})
            elif kind == "rate_limit_event":                 # the plan's usage windows (subscription logins)
                self.on_event("rate_limit", ev.get("rate_limit_info") or {})
            elif kind == "control_response":                 # the acknowledgement of an interrupt
                continue
            else:
                self.on_event(kind or "unknown", ev)
