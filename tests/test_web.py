"""The local website (eitri web) over HTTP: creating a project with a prompt starts Claude, the project's
editor and API live under /p/<id>/, attachments, stop, new chat, plan usage, the transcript surviving a
restart (and handed to the next session), imports, linked folders, trash, thumbnails, and the local-only
guard. A fake session stands in for headless Claude."""
from __future__ import annotations

import base64
import io
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from eitri.agent.claude_cli import TurnResult
from eitri.project import Project
from eitri.web.app import Workspace, start_web

PLATE = ("from eitri import *\nPARAMS = dict(width=60.0)\n\ndef build(p):\n"
         "    plate = Box(p.width, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
         "    return {'plate': cut_holes(plate, [(-20, 0), (20, 0)], d=5)}\n")
USAGE = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.12, "resetsAt": 2000000000},
                                                 "seven_day": {"utilization": 0.4, "resetsAt": 2000500000}}}


class FakeSession:
    """Stands in for headless Claude: writes a plate model, builds it, and reports events like the real
    session (a message containing "slow" waits until it is interrupted)."""

    def __init__(self, project, on_event):
        self.project, self.on_event = project, on_event
        self.received: list[str] = []
        self.images: list[list[str]] = []
        self.total_cost = 0.0
        self.closed = False
        self.stop = threading.Event()
        self.gate = threading.Event()                 # a "stream" turn waits here before building

    def _stream(self, index, kind, pieces, name=None, tid=None, delay=0.06):
        self.on_event("live", {"op": "start", "index": index, "type": kind, "name": name, "id": tid})
        for piece in pieces:
            self.on_event("live", {"op": "delta", "index": index, ("json" if kind == "tool_use" else "text"): piece})
            time.sleep(delay)
        self.on_event("live", {"op": "stop", "index": index})

    def send(self, text, images=None):
        self.total_cost += 0.12                       # what an API-key session reports as it goes
        self.received.append(text)
        self.images.append(list(images or []))
        self.on_event("rate_limit", USAGE)
        if "stream" in text:                          # token by token, like headless Claude with partial messages
            self.on_event("live", {"op": "message"})
            self.on_event("live", {"op": "start", "index": 0, "type": "thinking"})
            thought = ["Weighing the plate size ", "against the two hole positions; ", "60 x 40 leaves room ", "for 5 mm holes 20 mm off centre."]
            for k, piece in enumerate(thought, 1):     # a readable thinking summary, plus its growing size
                self.on_event("live", {"op": "thinking", "tokens": 50 * k})
                self.on_event("live", {"op": "delta", "index": 0, "thinking": piece})
                time.sleep(0.25)
            self.on_event("live", {"op": "stop", "index": 0})
            self.on_event("thinking", {"text": "".join(thought)})
            intro = "I'll model the plate first, then check it."
            self._stream(0, "text", [w + " " for w in intro.split(" ")], delay=0.15)
            self.on_event("text", {"text": intro})
            self.on_event("live", {"op": "message"})
            js = json.dumps({"code": PLATE, "note": "a plate"})
            self._stream(1, "tool_use", [js[i:i + 8] for i in range(0, len(js), 8)], "mcp__eitri__build_model", "s1", 0.05)
            self.on_event("tool_use", {"name": "mcp__eitri__build_model", "input": {"code": PLATE, "note": "a plate"}, "id": "s1"})
            self.on_event("tool_progress", {"tool_use_id": "s1-heartbeat-0", "elapsed_time_seconds": 30, "heartbeat": True})
            self.gate.wait(60)                        # the test looks at the page mid-build
            self.gate.clear()
            self.project.write_model(PLATE)
            res = self.project.build(note="a plate", render=False)
            self.on_event("tool_result", {"id": "s1", "is_error": not res.ok, "text": f"[revision {self.project.revision}] built"})
            self.on_event("live", {"op": "message"})
            outro = "Done: a **60 x 40 x 6 mm** plate with two holes."
            self._stream(0, "text", [w + " " for w in outro.split(" ")])
            self.on_event("text", {"text": outro})
            self.on_event("result", {"num_turns": 3, "duration_ms": 4000})
            return TurnResult(outro, turns=3)
        if "slow" in text:
            self.on_event("tool_use", {"name": "mcp__eitri__build_model", "input": {"note": "a slow build"}, "id": "slow1"})
            if self.stop.wait(30):
                self.stop.clear()
                self.on_event("result", {"subtype": "error_during_execution", "is_error": True, "num_turns": 1, "stopped": True})
                return TurnResult("[stopped]", stopped=True)
        self.on_event("tool_use", {"name": "mcp__eitri__write_model", "input": {"note": "a plate"}, "id": f"w{len(self.received)}"})
        self.project.write_model(PLATE)
        res = self.project.build(note="fake agent", render=False)
        review = self.project.out_dir / "review.png"
        _png(review)
        self.on_event("tool_result", {"id": f"w{len(self.received)}", "is_error": not res.ok,
                                      "text": f"[revision {self.project.revision}] built\n\nReview image: {review}  <- Read this file now and check it."})
        self.on_event("text", {"text": "Made a **plate**:\n- 60 x 40 x 6 mm\n- two Ø5 holes"})
        self.on_event("result", {"num_turns": 2, "duration_ms": 1500, "total_cost_usd": 0.01})
        return TurnResult("ok", turns=2)

    def interrupt(self):
        self.stop.set()
        return True

    def close(self):
        self.closed = True


def _png(path: Path | None = None, size=(8, 6), color=(200, 30, 30)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(buf.getvalue())
    return buf.getvalue()


def _call(url, path, payload=None, headers=None):
    req = urllib.request.Request(url + path.lstrip("/"), data=None if payload is None else json.dumps(payload).encode(),
                                 method="POST" if payload is not None else "GET", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _js(url, path, payload=None, headers=None):
    return json.loads(_call(url, path, payload, headers)[2])


def _wait(pred, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


def _idle(url, pid):
    return lambda: not _js(url, f"/p/{pid}/events?since=0")["busy"]


def test_projects_chat_stop_new_chat_and_history(tmp_path):
    sessions: list[FakeSession] = []

    def factory(project, on_event):
        sessions.append(FakeSession(project, on_event))
        return sessions[-1]

    ws = Workspace(tmp_path / "ws", factory, agent={"available": True, "label": "fake Claude"})
    url, server = start_web(ws, port=0)
    try:
        status, _, body = _call(url, "/")
        assert status == 200 and b"What do you want to make?" in body
        r = _js(url, "/api/projects")
        assert r["projects"] == [] and r["agent"]["label"] == "fake Claude" and r["workspace"] == str((tmp_path / "ws").resolve())

        # a prompt creates the project and Claude starts on it
        r = _js(url, "/api/projects", {"prompt": "A plate with two holes, please"})
        assert r["ok"] and r["id"] == "plate-with-two-holes-please" and r["url"] == "/p/plate-with-two-holes-please/" and r["warning"] is None
        pid = r["id"]
        _wait(lambda: sessions and sessions[0].received)
        _wait(_idle(url, pid))
        ev = _js(url, f"/p/{pid}/events?since=0")
        kinds = [e["kind"] for e in ev["events"]]
        assert kinds == ["user", "tool_use", "tool_result", "text", "result"], kinds
        assert ev["usage"]["unifiedWindows"]["five_hour"]["utilization"] == 0.12 and ev["tools"] == 1 and ev["session"] is True
        assert sessions[0].received[0] == "A plate with two holes, please"

        # the project's editor and API under /p/<id>/ (the bare id redirects to the trailing slash)
        status, _, page = _call(url, f"/p/{pid}")
        assert status == 200 and b"Chat with Claude" in page
        st = _js(url, f"/p/{pid}/state.json")
        assert st["home"] == "/" and st["glb"].startswith("view.glb?r=") and st["topology"].startswith("topology.json?r=") and st["title"] == "A plate with two holes, please"
        assert [p["name"] for p in st["parts"]] == ["plate"]
        status, _, glb = _call(url, f"/p/{pid}/" + st["glb"])
        assert status == 200 and glb[:4] == b"glTF"
        card = _js(url, "/api/projects")["projects"][0]
        assert card["id"] == pid and card["name"] == "plate_with_two_holes_please" and card["title"] == "A plate with two holes, please"
        assert card["revision"] >= 2 and card["last_message"].startswith("A plate")
        assert _js(url, "/api/rename", {"id": pid, "title": "  wall plate  "})["title"] == "Wall plate"
        assert _js(url, "/api/projects")["projects"][0]["title"] == "Wall plate" and _js(url, f"/p/{pid}/state.json")["title"] == "Wall plate"
        assert not _js(url, "/api/rename", {"id": pid, "title": "   "})["ok"]
        review = next(e for e in ev["events"] if e["kind"] == "tool_result")["data"]["text"]
        review_path = review.split("Review image: ")[1].split()[0]
        status, headers, img = _call(url, f"/p/{pid}/file?path=" + quote(review_path))
        assert status == 200 and headers["Content-Type"] == "image/png" and img[:4] == b"\x89PNG"

        # attach an image and send it with a message
        png = _png()
        up = _js(url, f"/p/{pid}/upload", {"name": "sketch photo.png", "data": "data:image/png;base64," + base64.b64encode(png).decode()})
        assert up["ok"] and up["path"] == "inputs/sketch_photo.png"
        assert _call(url, f"/p/{pid}/file?path=" + up["path"])[2] == png
        assert not _js(url, f"/p/{pid}/upload", {"name": "notes.txt", "data": base64.b64encode(b"hi").decode()})["ok"]
        assert _js(url, f"/p/{pid}/chat", {"text": "make it like this", "images": [up["path"]]})["ok"]
        _wait(lambda: len(sessions[0].received) == 2)
        _wait(_idle(url, pid))
        assert sessions[0].images[1] == [str((tmp_path / "ws" / pid / "inputs" / "sketch_photo.png").resolve())]
        user = [e for e in _js(url, f"/p/{pid}/events?since=0")["events"] if e["kind"] == "user"][-1]
        assert user["data"]["images"] == ["inputs/sketch_photo.png"]
        assert not _js(url, f"/p/{pid}/chat", {"text": "x", "images": ["../../etc/passwd"]})["ok"]

        # stop a long turn: the session (and its conversation) survives
        assert _js(url, f"/p/{pid}/chat", {"text": "something slow"})["ok"]
        _wait(lambda: _js(url, f"/p/{pid}/events?since=0")["busy"])
        status, _, body = _call(url, f"/p/{pid}/chat", {"text": "another one"})
        assert status == 409 and b"still working" in body
        assert _js(url, f"/p/{pid}/stop", {})["ok"]
        _wait(_idle(url, pid))
        evs = _js(url, f"/p/{pid}/events?since=0")["events"]
        assert evs[-1]["kind"] == "result" and evs[-1]["data"]["stopped"] is True and evs[-2]["kind"] == "stopping"
        assert len(sessions) == 1 and not sessions[0].closed
        assert _call(url, f"/p/{pid}/stop", {})[0] == 409                       # nothing to stop now

        # a new conversation: the session closes and the next message starts a clean one
        assert _js(url, f"/p/{pid}/newchat", {})["ok"]
        assert sessions[0].closed and _js(url, f"/p/{pid}/events?since=0")["events"][-1]["data"]["text"] == "new conversation"
        assert _js(url, f"/p/{pid}/chat", {"text": "fresh start"})["ok"]
        _wait(lambda: len(sessions) == 2 and sessions[1].received)
        _wait(_idle(url, pid))
        assert sessions[1].received[0] == "fresh start"

        # the page's thumbnail
        assert _js(url, f"/p/{pid}/thumb", {"data": "data:image/png;base64," + base64.b64encode(png).decode()})["ok"]
        assert _call(url, f"/thumb/{pid}")[2] == png and _js(url, "/api/projects")["projects"][0]["thumb"] is True
        assert not _js(url, f"/p/{pid}/thumb", {"data": base64.b64encode(b"not a png").decode()})["ok"]

        # only this machine, only our own pages
        assert _call(url, f"/p/{pid}/chat", {"text": "x"}, {"Origin": "http://evil.example"})[0] == 403
        assert _call(url, "/api/projects", {"prompt": "x"}, {"Origin": "http://evil.example"})[0] == 403
        assert _call(url, "/api/projects", {"prompt": "x"}, {"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert _call(url, "/api/projects", headers={"Host": "evil.example:8765"})[0] == 403
        assert _call(url, f"/p/{pid}/state.json", headers={"Host": "attacker.test"})[0] == 403
        assert _call(url, f"/p/{pid}/file?path=../../../../etc/passwd")[0] == 404
        _png(tmp_path / "outside.png")
        assert _call(url, f"/p/{pid}/file?path=" + quote(str(tmp_path / "outside.png")))[0] == 404
        assert _call(url, "/p/no-such-project/state.json")[0] == 404
        assert len(_js(url, "/api/projects")["projects"]) == 1                   # the refused requests created nothing
    finally:
        server.shutdown()
        ws.close()

    # after a restart the conversation is still there, and the next new session is handed the recent messages
    sessions.clear()
    ws2 = Workspace(tmp_path / "ws", factory, agent={"available": True, "label": "fake Claude"})
    url2, server2 = start_web(ws2, port=0)
    try:
        evs = _js(url2, f"/p/{pid}/events?since=0")["events"]
        assert evs[-1]["kind"] == "divider" and any(e["kind"] == "user" and e["data"]["text"] == "fresh start" for e in evs)
        assert _js(url2, "/api/projects")["usage"]["unifiedWindows"]["seven_day"]["utilization"] == 0.4   # remembered
        assert _js(url2, f"/p/{pid}/chat", {"text": "continue please"})["ok"]
        _wait(lambda: sessions and sessions[0].received)
        _wait(_idle(url2, pid))
        msg = sessions[0].received[0]
        assert msg.startswith("[Context: you are continuing") and "User: fresh start" in msg and "You: Made a **plate**" in msg
        assert msg.rstrip().endswith("continue please")
    finally:
        server2.shutdown()
        ws2.close()


def test_import_link_trash_and_unavailable_claude(tmp_path):
    import build123d as bd
    ws = Workspace(tmp_path / "ws", lambda project, cb: None, agent={"available": False, "label": "Claude not found", "detail": "install it"})
    url, server = start_web(ws, port=0)
    try:
        # a STEP file becomes a project with the part inserted as a component
        step = tmp_path / "bracket.step"
        bd.export_step(bd.Box(30, 20, 10), str(step))
        r = _js(url, "/api/import", {"filename": "bracket.step", "data": base64.b64encode(step.read_bytes()).decode()})
        assert r["ok"] and r["id"] == "bracket" and r["note"] == "imported bracket.step", r
        st = _js(url, "/p/bracket/state.json")
        assert len(st["parts"]) == 1 and abs(st["parts"][0]["volume"] - 6000) < 1 and st["edits"][0]["description"].startswith("insert")
        # a model .py; other files are refused
        r = _js(url, "/api/import", {"filename": "plate.py", "data": base64.b64encode(PLATE.encode()).decode()})
        assert r["ok"] and r["id"] == "plate" and _js(url, "/p/plate/state.json")["parts"][0]["name"] == "plate"
        assert not _js(url, "/api/import", {"filename": "notes.txt", "data": base64.b64encode(b"hi").decode()})["ok"]
        assert not _js(url, "/api/import", {"filename": "x.py", "data": base64.b64encode(b"print(1)").decode()})["ok"]

        # a project folder kept elsewhere: listed, served, and only unlisted by trash
        other = tmp_path / "elsewhere" / "clip"
        proj = Project(other, name="clip")
        proj.write_model(PLATE)
        assert proj.build(note="first", render=False).ok
        r = _js(url, "/api/link", {"path": str(other)})
        assert r["ok"]
        lid = r["id"]
        assert _js(url, "/api/link", {"path": str(other)})["id"] == lid                   # linking twice is one entry
        assert lid in [p["id"] for p in _js(url, "/api/projects")["projects"]]
        assert _js(url, f"/p/{lid}/state.json")["name"] == "clip"
        assert not _js(url, "/api/link", {"path": str(tmp_path / "nowhere")})["ok"]
        knob = tmp_path / "elsewhere" / "knob"                                        # a generic model.py is named after its folder
        kp = Project(knob, name="model")
        kp.write_model(PLATE)
        assert kp.build(note="first", render=False).ok
        kid = _js(url, "/api/link", {"path": str(knob)})["id"]
        assert next(p for p in _js(url, "/api/projects")["projects"] if p["id"] == kid)["title"] == "knob"
        assert _js(url, f"/p/{kid}/state.json")["title"] == "knob"
        r = _js(url, "/api/trash", {"id": lid})
        assert r["ok"] and r["unlisted"] and (other / "clip.py").exists()
        assert _js(url, "/api/trash", {"id": kid})["ok"]
        assert lid not in [p["id"] for p in _js(url, "/api/projects")["projects"]]

        # trashing a workspace project moves it into .trash
        r = _js(url, "/api/trash", {"id": "bracket"})
        assert r["ok"] and Path(r["trashed"]).parent == (tmp_path / "ws" / ".trash").resolve() and Path(r["trashed"], "eitri.json").exists()
        assert [p["id"] for p in _js(url, "/api/projects")["projects"]] == ["plate"]
        assert _js(url, "/api/projects")["projects"][0]["title"] == "Plate"
        assert _js(url, "/api/trash", {"id": "bracket"})["ok"] is False

        # without Claude a prompt still creates the project, with a warning; a blank one needs no Claude
        r = _js(url, "/api/projects", {"prompt": "a cube"})
        assert r["ok"] and r["id"] == "cube" and "not available" in r["warning"]
        r = _js(url, "/api/projects", {"name": "My Part"})
        assert r["ok"] and r["id"] == "my-part" and r["warning"] is None
        assert _js(url, "/api/projects", {"name": "My Part"})["id"] == "my-part-2"
    finally:
        server.shutdown()
        ws.close()


def test_live_progress_prewarm_preview_and_timings(tmp_path):
    """What the page sees while Claude works: a session started before the first message (prewarm), the
    reply and the model code as they are typed, the step that is running with its time, a preview of
    geometry not yet recorded, and the build's stage timings."""
    sessions: list[FakeSession] = []

    def factory(project, on_event):
        sessions.append(FakeSession(project, on_event))
        return sessions[-1]

    ws = Workspace(tmp_path / "ws", factory, agent={"available": True, "label": "fake"})
    url, server = start_web(ws, port=0)
    try:
        pid = _js(url, "/api/projects", {"name": "live plate"})["id"]
        assert not sessions
        assert _call(url, f"/p/{pid}/")[0] == 200                  # opening the page starts Claude...
        _wait(lambda: len(sessions) == 1, 10)
        assert sessions[0].received == []                            # ...without sending anything
        assert _js(url, f"/p/{pid}/events?since=0")["session"] is True

        assert _js(url, f"/p/{pid}/chat", {"text": "stream please"})["ok"]
        seen = {"text": [], "code": [], "phases": set(), "tokens": [], "thought": []}

        def look():
            st = _js(url, f"/p/{pid}/events?since=0")
            seen["phases"].add(st.get("phase"))
            live = st.get("live") or {}
            if live.get("type") == "thinking" and live.get("tokens"):
                seen["tokens"].append(live["tokens"])
            if live.get("type") == "thinking" and live.get("text"):
                seen["thought"].append(live["text"])
            if live.get("type") == "text" and live.get("text"):
                seen["text"].append(live["text"])
            if live.get("type") == "tool_use" and (live.get("fields") or {}).get("code") is not None:
                seen["code"].append(live["fields"]["code"])
            return st.get("phase") == "running" and (st.get("running") or {}).get("name", "").endswith("build_model")
        _wait(look, 30)
        assert len(sessions) == 1 and sessions[0].received == ["stream please"]           # the prewarmed session answered
        assert {"thinking", "writing", "tool_input", "running"} <= seen["phases"], seen["phases"]
        assert seen["tokens"] and max(seen["tokens"]) >= 100                               # the thinking's size, as it grows
        assert any(0 < len(x) < 90 for x in seen["thought"])                               # and its summary, mid-thought
        assert any(0 < len(x) < len("I'll model the plate first, then check it.") for x in seen["text"])     # caught mid-sentence
        partial = [c for c in seen["code"] if 0 < len(c) < len(PLATE)]
        assert partial and all(PLATE.startswith(c) for c in seen["code"])                  # the code as typed, decoded
        st = _js(url, f"/p/{pid}/events?since=0")
        assert st["live"]["done"] is True and st["live"]["fields"]["code"] == PLATE and st["live"]["note"] == "a plate"
        assert st["running"]["elapsed"] >= 0 and st["busy"] is True
        # what the running build is doing (the runner's out/progress.json)
        out = tmp_path / "ws" / pid / "out"
        (out / "progress.json").write_text(json.dumps({"stage": "measuring plate: walls, holes", "ts": time.time() - 2, "done": False}))
        build = _js(url, f"/p/{pid}/events?since=0")["build"]
        assert build["stage"] == "measuring plate: walls, holes" and build["elapsed"] >= 1.5

        # a preview: geometry newer than every revision is offered to the page, then withdrawn
        glb = next(out.glob("*.view.glb"))
        (out / "preview.json").write_text(json.dumps({"glb": glb.name, "topology": glb.name.replace(".view.glb", ".topology.json"), "ts": time.time() + 3600}))
        pv = _js(url, f"/p/{pid}/state.json")["preview"]
        assert pv and pv["glb"].startswith("preview.glb?t=")
        assert _call(url, f"/p/{pid}/" + pv["glb"])[2][:4] == b"glTF" and _js(url, f"/p/{pid}/" + pv["topology"])["parts"] is not None
        (out / "preview.json").unlink()

        sessions[0].gate.set()
        _wait(_idle(url, pid), 60)
        st = _js(url, f"/p/{pid}/events?since=0")
        assert st["phase"] is None and st["live"] is None and st["running"] is None
        assert [e["data"]["text"] for e in st["events"] if e["kind"] == "text"] == ["I'll model the plate first, then check it.", "Done: a **60 x 40 x 6 mm** plate with two holes."]
        thoughts = [e["data"] for e in st["events"] if e["kind"] == "thinking"]
        assert len(thoughts) == 1 and thoughts[0]["text"].startswith("Weighing the plate") and thoughts[0]["seconds"] >= 0.8
        # the build announced its geometry first, and timed its stages
        pv = json.loads((out / "preview.json").read_text())
        assert pv["glb"].endswith(".view.glb") and pv["topology"].endswith(".topology.json")
        assert _js(url, f"/p/{pid}/state.json")["preview"] is None                          # recorded since: no longer a preview
        timings = json.loads((out / "result.json").read_text())["timings"]
        assert {"build", "view", "verify", "export", "analyze"} <= set(timings), timings
        assert json.loads((out / "progress.json").read_text()) | {"ts": 0} == {"stage": "done", "done": True, "ts": 0}
        assert "build" not in _js(url, f"/p/{pid}/events?since=0")                           # nothing running now
        assert not any(e["kind"] == "tool_progress" for e in st["events"])                      # Claude Code's heartbeats stay out of the chat
    finally:
        server.shutdown()
        ws.close()


def test_partial_json_decoding():
    from eitri.agent.hub import partial_json_string as P
    s = json.dumps({"old": "a = 1", "new": 'w = 40.0  # \u00d8 hole, "quoted"\nx = 2'})
    for cut in range(len(s) + 1):
        v = P(s[:cut], "new")
        assert v is None or 'w = 40.0  # \u00d8 hole, "quoted"\nx = 2'.startswith(v), (cut, v)
    assert P(s, "new") == 'w = 40.0  # \u00d8 hole, "quoted"\nx = 2' and P(s, "old") == "a = 1" and P(s, "code") is None
