"""The local website in a real browser: describe a part on the home page, land in its editor while Claude
works, see the reply, the review render and the model; stop a long turn; attach an image; start a new
chat; resize the panel; go back home and find the project card with its thumbnail."""
from __future__ import annotations

import json

import pytest

from eitri.web.app import Workspace, start_web

from test_web import FakeSession, _png

pw = pytest.importorskip("playwright.sync_api")


def test_home_to_project_and_back(tmp_path):
    sessions: list[FakeSession] = []

    def factory(project, on_event):
        sessions.append(FakeSession(project, on_event))
        return sessions[-1]

    ws = Workspace(tmp_path / "ws", factory, agent={"available": True, "label": "Claude · API key"})
    url, server = start_web(ws, port=0)
    errors: list[str] = []
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: d.accept())

            # home: the agent pill, no projects yet; describe a part and create it
            page.goto(url)
            page.wait_for_selector("text=Claude · API key")
            assert page.is_visible("#empty")
            page.click(".ex >> nth=0")                                            # an example fills the box
            assert "phone stand" in page.input_value("#prompt")
            page.fill("#prompt", "a plate with two holes")
            page.keyboard.press("Meta+Enter" if page.evaluate("navigator.platform").startswith("Mac") else "Control+Enter")
            page.wait_for_url("**/p/plate-with-two-holes/", timeout=30000)

            # the editor: Claude's reply (markdown), the step with its review render, the model
            page.wait_for_function("() => document.querySelectorAll('#messages .msg.agent').length >= 1", timeout=60000)
            assert page.inner_html("#messages .msg.agent >> nth=0").count("<b>plate</b>") == 1
            assert page.locator("#messages .msg.agent li").count() == 2
            page.wait_for_function("() => { const i = document.querySelector('#messages .msg.tool img'); return i && i.complete && i.naturalWidth > 0; }", timeout=30000)
            assert "✓" in page.inner_text("#messages .msg.tool >> nth=0")
            page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0", timeout=60000)
            assert page.title() == "A plate with two holes · eitri" and page.inner_text("#title") == "A plate with two holes"
            assert page.is_visible("#homelink")
            page.wait_for_function("() => document.querySelector('#planusage').textContent.includes('$0.12 of API usage')", timeout=10000)

            # a long turn shows Stop; stopping keeps the conversation
            page.fill("#chatinput", "something slow")
            page.press("#chatinput", "Enter")
            page.wait_for_selector("#stop", state="visible", timeout=10000)
            page.wait_for_function("() => document.querySelector('#chatstatus').textContent.startsWith('Claude is working')", timeout=10000)
            assert page.is_disabled("#send")
            page.click("#stop")
            page.wait_for_function("() => [...document.querySelectorAll('#messages .msg.tool')].some(m => m.textContent === 'stopped')", timeout=20000)
            page.wait_for_selector("#stop", state="hidden", timeout=10000)
            assert len(sessions) == 1 and not sessions[0].closed

            # attach an image, send it with a message
            img = tmp_path / "sketch.png"
            _png(img, size=(40, 30))
            page.set_input_files("#attachfile", str(img))
            page.wait_for_selector("#attachments img", timeout=10000)
            page.fill("#chatinput", "make it look like this")
            page.click("#send")
            page.wait_for_function("() => { const i = document.querySelector('#messages .msg.you .att img'); return i && i.complete && i.naturalWidth === 40; }", timeout=20000)
            assert page.locator("#attachments img").count() == 0
            page.wait_for_function("() => !document.querySelector('#send').disabled", timeout=30000)
            assert sessions[0].images[-1] and sessions[0].images[-1][0].endswith("inputs/sketch.png")

            # a new conversation
            page.click("#newchat")
            page.wait_for_function("() => [...document.querySelectorAll('#messages .msg.divider')].some(m => m.textContent === 'new conversation')", timeout=10000)
            assert sessions[0].closed

            # the side panel can be widened by dragging its edge
            before = page.evaluate("() => document.querySelector('#side').getBoundingClientRect().width")
            box = page.locator("#resizer").bounding_box()
            page.mouse.move(box["x"] + 3, box["y"] + 300)
            page.mouse.down()
            page.mouse.move(box["x"] - 180, box["y"] + 300, steps=6)
            page.mouse.up()
            after = page.evaluate("() => document.querySelector('#side').getBoundingClientRect().width")
            assert after > before + 150, (before, after)

            # the page saved a thumbnail; back home, the card shows it
            page.wait_for_function("() => fetch('/thumb/plate-with-two-holes').then(r => r.status === 200)", timeout=20000)
            page.click("#homelink")
            page.wait_for_selector(".card", timeout=10000)
            page.wait_for_function("() => { const i = document.querySelector('.card .thumb img'); return i && i.complete && i.naturalWidth === 480; }", timeout=20000)
            card = page.inner_text(".card")
            assert "A plate with two holes" in card and "make it look like this" in card
            projects = page.evaluate("() => window.__eitri_home.projects()")
            assert [q["id"] for q in projects] == ["plate-with-two-holes"] and projects[0]["revision"] >= 3
            browser.close()
    finally:
        server.shutdown()
        ws.close()
    assert not errors, errors


def test_live_building_in_the_browser(tmp_path):
    """While Claude works the page shows it: the reply typing, the model code streaming into a panel over
    the 3D view, the build step with a spinner and a timer, the new geometry as a preview before it is
    recorded, and everything settling into the transcript when the turn ends."""
    import shutil
    import time as _time
    from eitri.project import Project
    from test_web import PLATE

    sessions: list[FakeSession] = []

    def factory(project, on_event):
        sessions.append(FakeSession(project, on_event))
        return sessions[-1]

    ws = Workspace(tmp_path / "ws", factory, agent={"available": True, "label": "fake"})
    url, server = start_web(ws, port=0)
    # a built plate elsewhere, to offer as a preview mid-build
    other = Project(tmp_path / "other", name="plate")
    other.write_model(PLATE)
    assert other.build(note="plate", render=False).ok
    errors: list[str] = []
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            pid = json.loads(page.request.post(url + "api/projects", data=json.dumps({"name": "live"})).text())["id"]
            page.goto(url + f"p/{pid}/")
            page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().rev >= 1", timeout=60000)
            page.fill("#chatinput", "stream please")
            page.press("#chatinput", "Enter")

            # thinking, then the reply typing (a live bubble with a caret), then the code panel
            assert "getting started" in page.inner_text("#messages .msg.live")              # at once, before any reply
            assert page.is_visible("#stop") and page.is_disabled("#send")
            page.wait_for_function("() => { const b = document.querySelector('#messages .msg.live'); return b && /thinking.*tokens/.test(b.textContent); }", timeout=10000)
            # Claude's thinking itself, typed into the chat and into the big panel while the view is empty
            page.wait_for_function("() => { const b = document.querySelector('#messages .msg.live.thinking .tbody'); return b && /plate size/.test(b.textContent); }", timeout=10000)
            page.wait_for_function("() => { const s = window.__eitri_live.state(); return s.code && s.code.title === 'Claude is thinking' && /hole positions/.test(s.code.text); }", timeout=10000)
            page.wait_for_function("() => { const b = document.querySelector('#messages .msg.live.text'); return b && b.querySelector('.caret') && b.textContent.length > 3; }", timeout=10000)
            page.wait_for_function("() => { const s = window.__eitri_live.state(); return s.code && s.code.title === 'Claude is writing the model' && s.code.lines >= 2; }", timeout=15000)
            mid = page.evaluate("() => window.__eitri_live.state().code")
            assert PLATE.startswith(mid["text"].rstrip()) or mid["text"].strip() in PLATE
            assert "line" in page.inner_text("#lcmeta")
            assert "writing the model" in page.inner_text("#messages .msg.live")
            # the build step: the code stays up with a timer; the step row spins
            page.wait_for_function("() => { const s = window.__eitri_live.state(); return s.code && s.code.title === 'Building'; }", timeout=15000)
            page.wait_for_function("() => [...document.querySelectorAll('#messages .msg.tool')].some(m => m.querySelector('.spin') && /building/.test(m.textContent) && /\\d+\\.\\d s/.test(m.textContent))", timeout=10000)
            # the code finishes typing out (the animation may still be catching up when the build step starts)
            page.wait_for_function("() => window.__eitri_live.state().code.text.trimEnd().endsWith(\"return {'plate': cut_holes(plate, [(-20, 0), (20, 0)], d=5)}\")", timeout=10000)
            # the build says what it is doing, on its step and on the panel
            (tmp_path / "ws" / pid / "out" / "progress.json").write_text(json.dumps({"stage": "measuring plate: walls, holes", "ts": _time.time(), "done": False}))
            page.wait_for_function("() => [...document.querySelectorAll('#messages .msg.tool')].some(m => /measuring plate: walls, holes · \\d+\\.\\d s/.test(m.textContent))", timeout=10000)
            assert "measuring plate" in page.inner_text("#lcmeta")

            # new geometry not yet recorded: the page shows it at once as a preview
            out = tmp_path / "ws" / pid / "out"
            for f in ("plate.view.glb", "plate.topology.json"):
                shutil.copy(other.out_dir / f, out / ("preview_" + f))
            (out / "preview.json").write_text(json.dumps({"glb": "preview_plate.view.glb", "topology": "preview_plate.topology.json", "ts": _time.time() + 3600}))
            page.wait_for_function("() => String(window.__eitri_live.state().rev).startsWith('p') && window.__eitri_debug().faces > 0", timeout=10000)
            page.wait_for_function("() => window.__eitri_live.state().code && window.__eitri_live.state().code.title.startsWith('Built')", timeout=5000)
            assert "being checked" in page.inner_text("#hud")
            (out / "preview.json").unlink()

            # the build finishes: the recorded revision replaces the preview, the panel goes, the reply types out
            sessions[0].gate.set()
            page.wait_for_function("() => !window.__eitri_live.state().busy && typeof window.__eitri_live.state().rev === 'number'", timeout=60000)
            page.wait_for_function("() => !document.querySelector('#livecode').classList.contains('on')", timeout=10000)
            assert page.locator("#messages .msg.live").count() == 0
            agent = [t.strip() for t in page.locator("#messages .msg.agent").all_inner_texts()]
            assert agent == ["I'll model the plate first, then check it.", "Done: a 60 x 40 x 6 mm plate with two holes."]
            assert page.inner_text("details.msg.think summary").startswith("Thought for")            # the finished thought stays readable
            page.click("details.msg.think summary")
            assert "for 5 mm holes 20 mm off centre" in page.inner_text("details.msg.think .tbody")
            build_row = next(r for r in page.locator("#messages .msg.tool").all_inner_texts() if "building" in r)
            assert build_row.startswith("✓") and " s" in build_row and "a plate" in build_row               # done, with its time
            assert page.inner_text("#messages .msg.tool >> nth=-1").startswith("done · 3 steps")
            page.wait_for_function("() => window.__eitri_debug().faces > 0", timeout=20000)
            # the first geometry of a blank project is framed (not left at the blank ground's zoom)
            fit = page.evaluate("() => { const d = window.__eitri_debug(); const c = d.camera, t = d.target; return { dist: Math.hypot(c[0]-t[0], c[1]-t[1], c[2]-t[2]), radius: d.radius }; }")
            assert fit["radius"] > 35 and fit["dist"] > 2 * fit["radius"], fit
            browser.close()
    finally:
        server.shutdown()
        ws.close()
    assert not errors, errors
