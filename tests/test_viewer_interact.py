"""Clicking geometry in the viewer, direct edits, and chat with the selection attached (real browser)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eitri.agent.hub import AgentHub
from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = ("from eitri import *\nPARAMS = dict(width=60.0)\n\ndef build(p):\n"
         "    plate = Box(p.width, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
         "    return cut_holes(plate, [(-20, 0), (20, 0)], d=5)\n")


class FakeSession:
    def __init__(self, on_event):
        self.on_event = on_event
        self.total_cost = 0.0
        self.received: list[str] = []

    def send(self, text, images=None):
        self.received.append(text)
        self.on_event("text", {"text": "got it"})
        self.on_event("result", {"num_turns": 1, "total_cost_usd": 0.0})
        from eitri.agent.claude_cli import TurnResult
        return TurnResult("ok")

    def close(self):
        pass


def test_click_edit_and_chat(tmp_path):
    project = Project(tmp_path / "proj", name="plate")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    sessions: list[FakeSession] = []
    hub = AgentHub(project, lambda cb: (sessions.append(FakeSession(cb)) or sessions[-1]))
    url = start_viewer(project, hub=hub)
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function("window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(500)
        # click the top face
        page.evaluate("() => window.__eitri_tool('select')")
        x, y = page.evaluate("() => window.__eitri_project([10, 10, 6])")
        page.mouse.move(x, y)
        page.mouse.click(x, y)
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.__eitri_debug().selected") == ["plate:f2"]
        assert "normal (0, 0, 1)" in page.text_content("#selitems")
        # push/pull it by 4 mm through the panel
        page.fill("#selactions input[type=number]", "4")
        page.click("#selactions button.primary")
        page.wait_for_function("() => document.querySelectorAll('#edits .edit').length === 1", timeout=60000)
        page.wait_for_function("() => window.__eitri_debug().rev >= 2 && window.__eitri_debug().topo && window.__eitri_debug().faces > 0", timeout=60000)   # the page has loaded the new build
        page.wait_for_timeout(300)
        state = json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))
        assert [e["description"] for e in state["edits"]] == ["push/pull plate plane +4 mm"]
        assert state["parts"][0]["size"][2] == 10
        assert len(project.edits()) == 1 and "EDITS = [" in project.read_model()
        # select a hole face and ask the agent about it: the selection travels with the message
        topo = json.loads(Path(project.last_ok_revision().outputs["topology"]).read_text(encoding="utf-8"))
        hole_face = next(f for f in topo["faces"] if f["type"] == "cylinder" and f["concave"])["id"]
        page.evaluate(f"() => window.__eitri_select(['{hole_face}'])")
        page.fill("#chatinput", "make this hole 8 mm")
        page.click("#send")
        page.wait_for_function("() => document.querySelectorAll('#messages .msg.agent').length >= 1", timeout=30000)
        assert sessions and sessions[0].received
        msg = sessions[0].received[0]
        assert msg.startswith("Selected in the viewer") and hole_face in msg and "radius 2.5" in msg and msg.rstrip().endswith("make this hole 8 mm")
        # a failing edit is refused and rolled back
        r = page.evaluate("() => fetch('/edit', {method: 'POST', body: JSON.stringify({op: 'fillet', part: 'plate', edges: [{kind: 'edge', type: 'line', mid: [999, 999, 999], length: 1}], radius: 2})}).then(r => r.json())")
        assert r["ok"] is False and len(project.edits()) == 1
        browser.close()
    assert not errors, errors
