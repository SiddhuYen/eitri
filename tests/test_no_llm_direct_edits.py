"""Deterministic editor actions never involve Claude: every direct edit, parameter change, feature edit, undo,
redo, restore, sketch solve, measurement and export goes editor -> EDITS/PARAMS -> kernel, with no agent turn,
no model call and no network. Claude stays in the loop only for chat (and the explicit "fold into code" chat).

The server runs with a real AgentHub whose session factory is a tripwire, the Anthropic client and the
Claude Code binary are booby-trapped, and every route below must leave all three untouched."""
from __future__ import annotations

import json
import subprocess
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from eitri.agent.hub import AgentHub
from eitri.project import Project
from eitri.viewer.server import make_handler

MODEL = ("from eitri import *\nPARAMS = dict(width=60.0)\n\ndef build(p):\n"
         "    plate = Box(p.width, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
         "    return cut_holes(plate, [(-20, 0), (20, 0)], d=5)\n\nEDITS = []\n")
TOP = {"kind": "face", "type": "plane", "center": [0, 10, 6], "normal": [0, 0, 1], "area": 2360.0}


@pytest.fixture
def server(tmp_path, monkeypatch):
    tripped: list[str] = []
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: tripped.append("anthropic client") or (_ for _ in ()).throw(RuntimeError("LLM")))
    real_popen = subprocess.Popen

    def popen(cmd, *a, **k):
        text = " ".join(map(str, cmd)) if isinstance(cmd, (list, tuple)) else str(cmd)
        if "claude" in text.split("/")[-1] or " -p " in text or "--bare" in text:
            tripped.append("claude binary: " + text[:80])
            raise RuntimeError("LLM")
        return real_popen(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", popen)

    project = Project(tmp_path / "proj", name="plate")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok

    def no_session(on_event):
        tripped.append("agent session")
        raise RuntimeError("LLM")
    hub = AgentHub(project, no_session)
    sends = []
    real_send = hub.send
    monkeypatch.setattr(hub, "send", lambda *a, **k: sends.append(a) or real_send(*a, **k))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(project, threading.Lock(), hub))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    yield url, project, tripped, sends
    srv.shutdown()


def _post(url, path, body):
    req = urllib.request.Request(url + path, data=json.dumps(body).encode(), method="POST", headers={"Origin": url.rstrip("/")})
    return json.load(urllib.request.urlopen(req, timeout=120))


def test_direct_edits_never_reach_claude(server):
    url, project, tripped, sends = server
    edits = [
        {"op": "push_pull", "part": "plate", "face": TOP, "distance": 2},
        {"op": "primitive", "kind": "box", "size": {"w": 10, "d": 10, "h": 10}, "mode": "new", "new_part": "peg", "at": [0, 30, 0]},
        {"op": "primitive", "kind": "cylinder", "size": {"r": 3, "h": 8}, "mode": "add", "part": "plate", "face": TOP, "origin": [0, 10, 6], "normal": [0, 0, 1], "at": [0, 0]},
        {"op": "hole", "part": "plate", "face": TOP, "origin": [0, 10, 6], "normal": [0, 0, 1], "at": [10, 0], "size": "M3"},
        {"op": "fillet", "part": "plate", "edges": [{"kind": "edge", "type": "line", "mid": [0, 20, 8], "length": 60}], "radius": 1},
        {"op": "move", "part": "peg", "delta": [5, 0, 0]},
        {"op": "move_vertices", "part": "peg", "delta": [0, 0, 1], "vertices": [{"kind": "vertex", "pos": [10, 35, 10]}]},
        {"op": "pattern", "part": "peg", "type": "rect", "nx": 2, "ny": 1, "dx": 15, "dy": 0},
    ]
    for e in edits:
        r = _post(url, "edit", e)
        assert "ok" in r, r
    assert _post(url, "set", {"width": 64})["ok"]
    assert _post(url, "edits/update", {"index": 0, "edit": dict(edits[0], distance=3)})["ok"]
    _post(url, "edits/move", {"index": 1, "to": 0})
    assert _post(url, "edits/remove", {"index": 0})["ok"] is not None
    assert _post(url, "undo", {})["ok"] is not None
    assert _post(url, "redo", {})["ok"] is not None
    assert _post(url, "restore", {"number": 1})["ok"] is not None
    _post(url, "sketch/solve", {"sketch": {"entities": [], "constraints": []}})
    _post(url, "measure", {"ids": ["plate:f0", "plate:f1"]})
    urllib.request.urlopen(url + "download?fmt=step", timeout=120).read()
    urllib.request.urlopen(url + "state.json", timeout=30).read()
    assert tripped == [], tripped
    assert sends == [], sends
