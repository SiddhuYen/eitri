"""One agent session shared by the terminal REPL, the browser viewer and the local website.

The hub owns the LLM session, serialises turns (one at a time), keeps an event log the viewer polls,
and turns a viewer selection into text the agent can act on ("Selected in the viewer: face plate:f7 ...").
With `history`, the log is also saved to the project's .eitri/chat.jsonl, so the conversation shows
again after a restart. A turn can be stopped (the session survives), the conversation can be started
afresh, and an idle session can be closed to free memory: the next message then starts a new session
that is handed the recent conversation, so the user does not notice the switch.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..project import Project
from ..viewglb import describe_selection

EventHandler = Callable[[str, dict], None]

_RESULT_KEYS = ("subtype", "is_error", "num_turns", "duration_ms", "turn_cost_usd", "stopped")
_QUIET = ("system", "unknown", "tool_progress")    # passed to the printer, not kept in the transcript
_CODE_FIELDS = {"build_model": ("code",), "edit_model": ("old", "new"), "set_params": ("params",), "sketch_op": ("op",)}


def partial_json_string(s: str, key: str) -> str | None:
    """The (possibly unfinished) string value of `key` in a partial JSON object: what Claude has typed
    of a tool input so far."""
    import re
    m = re.search(r'"%s"\s*:\s*"' % re.escape(key), s)
    if not m:
        return None
    out: list[str] = []
    i, n = m.end(), len(s)
    esc = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", '"': '"', "\\": "\\", "/": "/"}
    while i < n:
        c = s[i]
        if c == '"':
            break
        if c == "\\":
            if i + 1 >= n:
                break
            e = s[i + 1]
            if e == "u":
                if i + 6 > n:
                    break
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                except ValueError:
                    pass
                i += 6
                continue
            out.append(esc.get(e, e))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


class AgentHub:
    def __init__(self, project: Project, make_session: Callable[[EventHandler], Any],
                 printer: EventHandler | None = None, max_events: int = 2000, history: bool | str | Path = False):
        self.project = project
        self._make_session = make_session
        self.session = None
        self.printer = printer or (lambda kind, data: None)
        self.events: list[dict] = []
        self._next_id = 1
        self._lock = threading.Lock()         # one turn at a time
        self._events_lock = threading.Lock()
        self.busy = False
        self.max_events = max_events
        self.total_cost = 0.0
        self.turn_started: float | None = None
        self.turn_tools = 0
        self.last_activity = time.time()
        self.rate_limit: dict | None = None   # the plan's usage windows, from the CLI's rate-limit events
        self._carry_context = False           # hand the recent conversation to the next new session
        self._fresh = False                   # the session exists but has not had a message yet (prewarmed)
        self._prewarming = False
        self._guest = None                    # a session running one turn instead of ours (a replay)
        # what Claude is doing right now, for the page: phase + the block being written (token by token)
        self._live_lock = threading.Lock()
        self.phase: str | None = None         # starting | thinking | writing | tool_input | running
        self.phase_since = 0.0
        self.live: dict | None = None         # {"seq", "type", "name", "id", "text", "json", "done", "since"}
        self.running: dict | None = None      # the tool being executed: {"name", "id", "since"}
        self._seq = 0
        self._appended = 0
        if history is True:
            self.history_path: Path | None = project.folder / ".eitri" / "chat.jsonl"
        else:
            self.history_path = Path(history) if history else None
        if self.history_path:
            self._load_history()

    # ------------------------------------------------------------------ events
    def _record(self, kind: str, data: dict, source: str = "agent") -> None:
        with self._events_lock:
            slim = dict(data)
            if kind == "tool_result":
                from ..cli import _tool_result_text
                slim["text"] = _tool_result_text(slim.get("text") or "")[:4000]
            if kind == "tool_use" and isinstance(slim.get("input"), dict):
                slim["input"] = {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in slim["input"].items()}
            if kind == "result":
                slim = {k: slim.get(k) for k in _RESULT_KEYS if slim.get(k) is not None}
            if kind == "thinking" and len(slim.get("text") or "") > 8000:
                slim["text"] = slim["text"][:8000] + "…"
            event = {"id": self._next_id, "kind": kind, "data": slim, "ts": time.time(), "source": source}
            self.events.append(event)
            self._next_id += 1
            if len(self.events) > self.max_events:
                del self.events[: len(self.events) - self.max_events]
            self._save(event)

    def _save(self, event: dict) -> None:
        if not self.history_path:
            return
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self._appended += 1
            if self._appended > 4 * self.max_events:          # keep the file bounded: rewrite the tail
                self._appended = 0
                self.history_path.write_text("".join(json.dumps(e, default=str) + "\n" for e in self.events), encoding="utf-8")
            else:
                with self.history_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(event, default=str) + "\n")
        except OSError:
            pass

    def _load_history(self) -> None:
        try:
            lines = self.history_path.read_text(encoding="utf-8").splitlines()   # type: ignore[union-attr]
        except OSError:
            return
        events = []
        for line in lines[-self.max_events:]:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(e, dict) and isinstance(e.get("id"), int) and e.get("kind"):
                events.append(e)
        if not events:
            return
        self.events = events
        self._next_id = max(e["id"] for e in events) + 1
        self._carry_context = any(e["kind"] in ("user", "text") for e in events)
        if events[-1]["kind"] != "divider":
            self._record("divider", {"text": "eitri restarted · your next message starts a new Claude session, "
                                             "which is given the recent conversation"}, source="system")

    def _set_phase(self, phase: str | None) -> None:
        if phase != self.phase:
            self.phase, self.phase_since = phase, time.time()

    def _on_live(self, d: dict) -> None:
        with self._live_lock:
            self._seq += 1
            op = d.get("op")
            if op == "message":
                self.live = None
                self._set_phase("thinking")
            elif op == "start":
                kind = d.get("type") or ""
                self.live = {"type": kind, "name": d.get("name"), "id": d.get("id"), "text": "", "json": "",
                             "done": False, "since": time.time()}
                self._set_phase("writing" if kind == "text" else "tool_input" if kind == "tool_use" else "thinking")
            elif op == "delta" and self.live is not None:
                if "text" in d:
                    self.live["text"] += d["text"]
                elif "json" in d:
                    self.live["json"] += d["json"]
                elif "thinking" in d:
                    self.live["text"] = (self.live["text"] + d["thinking"])[-12000:]
            elif op == "stop" and self.live is not None:
                self.live["done"] = True
            elif op == "thinking":                       # hidden thinking: how far it has got
                if self.live is None or self.live.get("type") != "thinking":
                    self.live = {"type": "thinking", "name": None, "id": None, "text": "", "json": "", "done": False, "since": time.time()}
                self.live["tokens"] = int(d.get("tokens") or 0)
                self._set_phase("thinking")

    def live_view(self) -> dict | None:
        """The block being written, ready for the page: text so far, or for a tool call its name and the
        code/edit typed so far (decoded from the partial JSON)."""
        with self._live_lock:
            live = dict(self.live) if self.live else None
            seq = self._seq
        if not live:
            return None
        out = {"seq": seq, "type": live["type"], "name": live.get("name"), "id": live.get("id"), "done": live["done"],
               "elapsed": round(time.time() - live["since"], 1)}
        if live["type"] == "tool_use":
            short = (live.get("name") or "").split("__")[-1]
            fields = {}
            for key in _CODE_FIELDS.get(short, ()):
                v = partial_json_string(live["json"], key)
                if v is not None:
                    fields[key] = v
            if short == "set_params" and "params" not in fields:
                fields["params"] = live["json"]
            out["fields"] = fields
            note = partial_json_string(live["json"], "note")
            if note:
                out["note"] = note
            out["chars"] = len(live["json"])
        else:
            out["text"] = live["text"]
            if live.get("tokens"):
                out["tokens"] = live["tokens"]
        return out

    def _on_event(self, kind: str, data: dict) -> None:
        self.last_activity = time.time()
        if kind == "live":
            self._on_live(data)
            return
        if kind == "rate_limit":
            self.rate_limit = dict(data, seen=time.time())
            return
        if kind == "thinking":                         # a finished thought (a readable summary): how long it took
            with self._live_lock:
                if self.live and self.live.get("type") == "thinking":
                    data = dict(data, seconds=round(time.time() - self.live["since"], 1))
            if not (data.get("text") or "").strip():
                return
        if kind == "tool_use":
            self.turn_tools += 1
            with self._live_lock:
                self.running = {"name": data.get("name"), "id": data.get("id"), "since": time.time()}
                self._set_phase("running")
        elif kind == "tool_result":
            with self._live_lock:
                self.running = None
                self._set_phase("thinking")
        elif kind == "result":
            with self._live_lock:
                self.running = None
                self.live = None
                self._set_phase(None)
        if kind not in _QUIET:
            self._record(kind, data)
        try:
            self.printer(kind, data)
        except Exception:
            pass

    def events_since(self, since: int) -> list[dict]:
        with self._events_lock:
            return [e for e in self.events if e["id"] > since]

    @property
    def last_event_id(self) -> int:
        return self._next_id - 1

    def status(self) -> dict:
        """What the chat panel shows besides the messages: progress, what Claude is doing right now (phase,
        the block being written, the tool running) and the plan usage."""
        now = time.time()
        running = dict(self.running) if self.running else None
        if running:
            running["elapsed"] = round(now - running.pop("since"), 1)
        return {"busy": self.busy, "elapsed": round(now - self.turn_started, 1) if self.turn_started else None,
                "tools": self.turn_tools, "usage": self.rate_limit, "cost": round(self.total_cost, 4),
                "session": self.session is not None, "phase": self.phase if self.busy else None,
                "phase_elapsed": round(now - self.phase_since, 1) if self.busy and self.phase else None,
                "live": self.live_view() if self.busy else None, "running": running if self.busy else None}

    # ------------------------------------------------------------------- turns
    def _ensure_session(self):
        if self.session is None:
            self.session = self._make_session(self._on_event)
            self._fresh = True
        return self.session

    def prewarm(self) -> None:
        """Start Claude now (its process and tools take a few seconds to load), so the first message is
        answered at once. Costs no usage: nothing is sent until the user writes."""
        if self.session is not None or self.busy or self._prewarming:
            return
        self._prewarming = True

        def run():
            try:
                if not self._lock.acquire(timeout=1):
                    return
                try:
                    if self.session is None:
                        session = self._make_session(self._on_event)
                        if hasattr(session, "start"):
                            session.start()
                        self.session, self._fresh = session, True
                        self.last_activity = time.time()
                finally:
                    self._lock.release()
            except Exception:
                pass
            finally:
                self._prewarming = False

        threading.Thread(target=run, daemon=True).start()

    def compose(self, text: str, selection: list[str] | None) -> str:
        """Prepend the viewer selection (resolved against the latest topology) to a message."""
        if not selection:
            return text
        rev = self.project.last_ok_revision()
        topo = None
        if rev and rev.outputs.get("topology") and Path(rev.outputs["topology"]).exists():
            try:
                topo = json.loads(Path(rev.outputs["topology"]).read_text(encoding="utf-8"))
            except Exception:
                topo = None
        geometry = [s for s in selection if not str(s).startswith("edit:")]
        features = [s for s in selection if str(s).startswith("edit:")]
        lines = []
        if geometry:
            lines.append(describe_selection(topo, geometry) if topo else "\n".join(f"- {s}" for s in geometry))
        if features:
            from ..edits import describe
            edits = self.project.edits()
            for s in features:
                try:
                    i = int(str(s).split(":", 1)[1])
                    e = edits[i]
                    lines.append(f"- direct edit #{i + 1} (EDITS[{i}]): {describe(e)}\n  {json.dumps(e)}")
                except (ValueError, IndexError):
                    lines.append(f"- {s} (no longer exists)")
        return f"Selected in the viewer (revision {rev.number if rev else '?'}):\n" + "\n".join(lines) + f"\n\n{text}"

    def _context_preamble(self, limit: int = 8, chars: int = 600) -> str:
        """The recent conversation, for a new session that replaces one that was closed or lost."""
        msgs = []
        for e in self.events:
            if e["kind"] == "user":
                msgs.append(("User", e["data"].get("text") or ""))
            elif e["kind"] == "text":
                msgs.append(("You", e["data"].get("text") or ""))
        msgs = [(who, t.strip()) for who, t in msgs if t.strip()][-limit:]
        if not msgs:
            return ""
        lines = "\n".join(f"{who}: {t[:chars] + ('…' if len(t) > chars else '')}" for who, t in msgs)
        return ("[Context: you are continuing an earlier conversation about this project in a new session. The model "
                "file, the direct edits and DESIGN.md on disk are current. The most recent messages were:\n"
                f"{lines}\n]\n\n")

    def send(self, text: str, images: list[str] | None = None, source: str = "terminal",
             selection: list[str] | None = None, session=None):
        """One turn. `session` runs it on another session than the hub's own (a replay of a recorded
        conversation), leaving the hub's Claude session untouched."""
        message = self.compose(text, selection)
        with self._lock:
            self.busy = True
            self.turn_started = self.last_activity = time.time()
            self.turn_tools = 0
            with self._live_lock:
                self.live, self.running = None, None
                self._set_phase("starting")
            preamble = "" if session is not None else \
                (self._context_preamble() if ((self.session is None or self._fresh) and self._carry_context) else "")
            if session is None:
                self._carry_context = False
            shown = []
            for img in images or []:
                try:
                    shown.append(str(Path(img).resolve().relative_to(self.project.folder.resolve())))
                except ValueError:
                    shown.append(Path(img).name)
            self._record("user", {"text": text, "selection": selection or [], "images": shown}, source=source)
            if source != "terminal":
                try:
                    self.printer("user", {"text": text, "selection": selection or [], "source": source})
                except Exception:
                    pass
            try:
                if session is None:
                    session = self._ensure_session()
                    self._fresh = False
                    result = session.send(preamble + message, images=images)
                    self.total_cost = getattr(session, "total_cost", self.total_cost)
                else:
                    self._guest = session
                    try:
                        result = session.send(message, images=images)
                    finally:
                        self._guest = None
                return result
            finally:
                self.busy = False
                self.turn_started = None
                self.last_activity = time.time()
                with self._live_lock:
                    self.live, self.running = None, None
                    self._set_phase(None)

    def send_async(self, text: str, images: list[str] | None = None, source: str = "viewer",
                   selection: list[str] | None = None, session=None) -> None:
        threading.Thread(target=self._safe_send, args=(text, images, source, selection, session), daemon=True).start()

    def _safe_send(self, text, images, source, selection, session=None) -> None:
        try:
            self.send(text, images, source, selection, session=session)
        except Exception as exc:
            self._record("error", {"text": f"{type(exc).__name__}: {exc}"})
            try:
                self.printer("error", {"text": f"{type(exc).__name__}: {exc}"})
            except Exception:
                pass
            self.close()                       # a broken session is replaced on the next message
            self._carry_context = True

    def interrupt(self) -> bool:
        """Stop the turn in progress; the session (and its memory of the conversation) is kept."""
        session = getattr(self, "_guest", None) or self.session
        if not self.busy or session is None or not hasattr(session, "interrupt"):
            return False
        ok = bool(session.interrupt())
        if ok:
            self._record("stopping", {"text": "stopping…"}, source="system")
        return ok

    def reset(self, wait: float = 15.0) -> None:
        """A new conversation: stop any turn, close the session, and carry nothing over."""
        if self.busy:
            self.interrupt()
            deadline = time.time() + wait
            while self.busy and time.time() < deadline:
                time.sleep(0.1)
        self.close()
        self._carry_context = False
        self._record("divider", {"text": "new conversation"}, source="system")

    def close_if_idle(self, max_idle: float) -> bool:
        """Close the session after `max_idle` seconds without activity (the next message starts a new
        session with the recent conversation). Never while a turn runs."""
        if self.session is None or self.busy or time.time() - self.last_activity < max_idle:
            return False
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if self.busy or self.session is None:
                return False
            self.close()
            self._carry_context = True
            return True
        finally:
            self._lock.release()

    def close(self) -> None:
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
            self._fresh = False
