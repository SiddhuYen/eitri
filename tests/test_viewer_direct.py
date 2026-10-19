"""The editor's direct-manipulation basics in a real browser: plain-language failures (never a raw exception),
select with a slightly wobbly click, drag a corner handle without orbiting, draw a primitive on the bare grid."""
from __future__ import annotations

import json

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = ("from eitri import *\nPARAMS = dict(width=60.0)\n\ndef build(p):\n"
         "    plate = Box(p.width, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
         "    peg = Pos(0, 0, 6) * Box(10, 10, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
         "    return {'plate': cut_holes(plate, [(-20, 0), (20, 0)], d=5), 'peg': peg}\n\nEDITS = []\n")


def _state(page) -> dict:
    return json.loads(page.evaluate("() => fetch('state.json').then(r => r.text())"))


def _settle(page, prev_rev):
    page.wait_for_function(f"() => window.__eitri_debug().inflight === 0 && window.__eitri_debug().rev !== {json.dumps(prev_rev)} && typeof window.__eitri_debug().rev === 'number'", timeout=90000)
    page.wait_for_timeout(300)


@pytest.fixture
def page(tmp_path):
    project = Project(tmp_path / "proj", name="plate")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        pg = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url)
        pg.wait_for_function("window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        pg.wait_for_timeout(400)
        yield pg
        assert not errors, errors
        browser.close()


def test_failures_read_as_plain_sentences(page):
    out = page.evaluate("""() => { const plainError = window.__eitri_plain_error; return [
      plainError('ValueError: that point belongs to a hole or pocket outline; move the hole instead'),
      plainError('Traceback (most recent call last):\\n  File "model.py", line 3, in build\\nStandard_ConstructionError: BRep_API: command not done'),
      plainError("KeyError: 'distance'"),
      plainError('build failed:\\nHint: a fillet radius is larger than the edge allows'),
      plainError('')]; }""")
    assert out[0] == "That point belongs to a hole or pocket outline; move the hole instead."
    assert out[3] == "A fillet radius is larger than the edge allows."
    for text in out:
        assert "Error" not in text and "Traceback" not in text and "BRep" not in text and "'" not in text[:2]
    # a direct edit that the kernel refuses: the designed notice, one plain sentence, nothing technical
    rev = page.evaluate("() => window.__eitri_debug().rev")
    page.evaluate("() => { window.__eitri_send.edit({ op: 'move_vertices', part: 'plate', delta: [0, 0, 1], vertices: [{ kind: 'vertex', pos: [-17.5, 0, 6] }] }); }")
    page.wait_for_selector("#notice.show", timeout=90000)
    notice = page.evaluate("() => document.querySelector('#notice .txt').textContent")
    assert notice and "Error" not in notice and "Traceback" not in notice
    assert page.evaluate("() => window.__eitri_debug().pending") == 0          # the failed edit's ghost is gone
    msgs = page.evaluate("() => document.getElementById('messages') ? document.getElementById('messages').textContent : ''")
    assert "ValueError" not in msgs and "Traceback" not in msgs


def test_select_with_a_wobbly_click_and_escape_returns_to_select(page):
    assert page.evaluate("() => window.__eitri_debug().tool") == "select"
    x, y = page.evaluate("() => window.__eitri_project([22, -12, 6])")
    page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 5, y + 3); page.mouse.up()       # a 6 px wobble
    assert page.evaluate("() => window.__eitri_debug().selected.length") == 1
    page.keyboard.press("p")
    assert page.evaluate("() => window.__eitri_debug().tool") == "pushpull"
    page.keyboard.press("Escape")
    assert page.evaluate("() => window.__eitri_debug().tool") == "select"


def test_drag_a_corner_handle_without_orbiting(page):
    page.keyboard.press("d")
    handles = page.evaluate("() => window.__eitri_vertex_handles()")
    kinds = {h["act"] for h in handles}
    assert "corner" in kinds and kinds - {"corner"}                 # movable corners, and rerouted ones (round holes)
    peg_top = next(h for h in handles if h["act"] == "corner" and abs(h["pos"][2] - 16) < 1e-3 and h["pos"][0] > 0 and h["pos"][1] < 0)
    x, y = page.evaluate("p => window.__eitri_screen(p)", peg_top["pos"])
    page.mouse.move(x, y)
    page.wait_for_timeout(150)
    assert "move this corner" in page.evaluate("() => document.getElementById('hover').textContent")
    cam = page.evaluate("() => window.__eitri_debug().camera")
    rev = page.evaluate("() => window.__eitri_debug().rev")
    page.mouse.down()
    for i in range(1, 9):
        page.mouse.move(x + 4 * i, y - 3 * i)
    assert page.evaluate("() => window.__eitri_debug().drag.kind") == "handle"
    page.mouse.up()
    assert page.evaluate("() => window.__eitri_debug().pending") > 0       # the change is on screen while it rebuilds
    cam2 = page.evaluate("() => window.__eitri_debug().camera")
    assert max(abs(a - b) for a, b in zip(cam, cam2)) < 1e-6
    _settle(page, rev)
    edits = _state(page)["edits_full"]
    assert edits and edits[-1]["op"] == "move_vertices"


def test_draw_a_box_on_the_bare_grid(page):
    page.keyboard.press("b")
    x, y = page.evaluate("() => window.__eitri_project([0, 45, 0])")
    rev = page.evaluate("() => window.__eitri_debug().rev")
    page.mouse.move(x, y); page.mouse.down()
    for i in range(1, 8):
        page.mouse.move(x + 6 * i, y + 2 * i)
    page.keyboard.type("24x16")                                    # an exact footprint, typed mid-drag
    page.mouse.up()
    page.keyboard.type("12")
    page.keyboard.press("Enter")
    _settle(page, rev)
    box = next(p for p in _state(page)["parts"] if p["name"].startswith("box"))
    assert [round(v, 3) for v in box["size"]] == [24.0, 16.0, 12.0]
