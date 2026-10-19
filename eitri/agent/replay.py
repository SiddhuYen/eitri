"""Play a recorded Claude session back through an agent hub, as if Claude were working now.

A ReplaySession is a drop-in for a real session: the hub calls send(), and it emits the recorded events
(text, tool calls, tool results, the final result) through the same on_event callback, with the same
"live" streaming events the real backend sends, so the chat panel shows Claude typing and the model
code being written. No API call is made. Long waits are compressed (a recorded 11-minute think plays in
a few seconds) and every event says it is a replay; the original timing and cost travel with the
events so the page can show them. When a recorded tool result announces a revision ("[revision 7]
built ..."), `on_revision(7)` steps the project to it, so the 3D view and the checks change on cue.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from .claude_cli import TurnResult

EventHandler = Callable[[str, dict], None]

MAX_GAP = 2.2           # the longest pause a replay makes between two recorded events (seconds)
TYPE_RATE = 420         # characters per second when Claude's recorded text is typed out
CODE_RATE = 2400        # characters per second when model code is "written"
MAX_TYPE = 3.5          # no block takes longer than this to type


class ReplaySession:
    def __init__(self, demo: dict, on_event: EventHandler, on_revision: Callable[[int | None], None] | None = None,
                 code_of: Callable[[int], str | None] | None = None, speed: float = 1.0):
        self.demo = demo
        self.on_event = on_event
        self.on_revision = on_revision or (lambda n: None)
        self.code_of = code_of or (lambda n: None)
        self.speed = max(speed, 0.05)
        self.total_cost = 0.0
        self._stop = threading.Event()

    # the hub's session interface ------------------------------------------------------------------
    def start(self) -> None:
        pass

    def close(self) -> None:
        self._stop.set()

    def interrupt(self) -> bool:
        self._stop.set()
        return True

    def send(self, text: str, images=None, timeout: float = 3600.0) -> TurnResult:
        events = [e for e in self.demo.get("events", []) if e["kind"] != "user"]
        self.on_revision(1)                               # the blank design the session started from
        prev_t = 0.0
        last_text = ""
        tools = 0
        upcoming = self._revision_numbers(events)
        for i, e in enumerate(events):
            if self._wait(min(max(e["t"] - prev_t, 0.0), MAX_GAP)):
                return self._stopped(events, last_text, tools)
            prev_t = e["t"]
            kind, data = e["kind"], dict(e["data"])
            data["replay"] = True
            data["recorded_at"] = e["t"]
            if kind in ("text", "thinking"):
                if self._type(kind, data.get("text") or ""):
                    return self._stopped(events, last_text, tools)
                if kind == "text":
                    last_text = data.get("text") or ""
                self.on_event(kind, data)
            elif kind == "tool_use":
                tools += 1
                code = self.code_of(upcoming.get(i)) if (data.get("name") or "").endswith("build_model") else None
                if self._write_tool(data, code):
                    return self._stopped(events, last_text, tools)
                self.on_event("tool_use", data)
            elif kind == "tool_result":
                rev = _revision_in(data.get("text") or "")
                if rev is not None:
                    self.on_revision(rev)
                self.on_event("tool_result", data)
            elif kind == "result":
                self.on_revision(None)                    # end exactly where the recording ended
                self.on_event("result", dict(data, replay=True, recorded=self.demo.get("recorded")))
                return TurnResult(last_text, cost_usd=0.0, turns=int(data.get("num_turns") or 0),
                                  duration_ms=int(data.get("duration_ms") or 0), is_error=False,
                                  tool_calls=tools, stopped=bool(data.get("stopped")))
            elif kind in ("stopping", "error"):
                self.on_event(kind, data)
        self.on_revision(None)
        return TurnResult(last_text, tool_calls=tools)

    # helpers --------------------------------------------------------------------------------------
    @staticmethod
    def _revision_numbers(events: list[dict]) -> dict[int, int]:
        """For each build_model call, the revision its result announces (to show that revision's code
        being written)."""
        out: dict[int, int] = {}
        pending: list[int] = []
        for i, e in enumerate(events):
            if e["kind"] == "tool_use" and (e["data"].get("name") or "").endswith("build_model"):
                pending.append(i)
            elif e["kind"] == "tool_result" and pending:
                rev = _revision_in(e["data"].get("text") or "")
                if rev is not None:
                    out[pending.pop(0)] = rev
        return out

    def _wait(self, seconds: float) -> bool:
        """Sleep (scaled); True if the replay was stopped meanwhile."""
        return self._stop.wait(seconds / self.speed)

    def _type(self, kind: str, text: str) -> bool:
        self.on_event("live", {"op": "message"})
        self.on_event("live", {"op": "start", "type": kind})
        if text:
            per = min(1.0 / TYPE_RATE, MAX_TYPE / len(text))
            step = max(1, int(0.03 / per))                # ~30 updates a second
            for k in range(0, len(text), step):
                self.on_event("live", {"op": "delta", "text" if kind == "text" else "thinking": text[k:k + step]})
                if self._wait(per * step):
                    return True
        self.on_event("live", {"op": "stop"})
        return False

    def _write_tool(self, data: dict, code: str | None) -> bool:
        self.on_event("live", {"op": "start", "type": "tool_use", "name": data.get("name"), "id": data.get("id")})
        payload = json.dumps({"code": code, "note": (data.get("input") or {}).get("note", "")}) if code else \
            json.dumps(data.get("input") or {})
        per = min(1.0 / CODE_RATE, MAX_TYPE / max(len(payload), 1))
        step = max(1, int(0.03 / per))
        for k in range(0, len(payload), step):
            self.on_event("live", {"op": "delta", "json": payload[k:k + step]})
            if self._wait(per * step):
                return True
        self.on_event("live", {"op": "stop"})
        return False

    def _stopped(self, events, last_text, tools) -> TurnResult:
        self.on_revision(None)
        self.on_event("result", {"subtype": "stopped", "is_error": False, "stopped": True, "replay": True,
                                 "recorded": self.demo.get("recorded")})
        return TurnResult(last_text, tool_calls=tools, stopped=True)


def _revision_in(text: str) -> int | None:
    import re
    m = re.search(r"\[revision (\d+)\]", text[:600])
    return int(m.group(1)) if m else None


def model_code(folder: Path, model: str) -> Callable[[int], str | None]:
    """code_of(n): the model code of revision n in a demo project folder."""
    def code_of(n: int | None) -> str | None:
        if n is None:
            return None
        f = folder / "revisions" / f"{n:03d}" / model
        return f.read_text(encoding="utf-8") if f.is_file() else None
    return code_of
