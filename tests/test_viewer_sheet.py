"""Sheet metal in the browser: a base flange from a ground sketch, an edge flange from the edge panel, the
flat pattern laid out beside it with bend lines drawn, its DXF, and hiding it again."""
from __future__ import annotations

import json

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")


def _state(page):
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _topo(page):
    return json.loads(page.evaluate("() => fetch('/topology.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _ready(page, n_edits, prev_rev):
    page.wait_for_function(
        f"() => window.__eitri_debug().rev > {prev_rev} && document.querySelectorAll('#edits .edit').length === {n_edits}"
        " && window.__eitri_debug().faces > 0 && !window.__eitri_debug().drag", timeout=90000)
    page.wait_for_timeout(300)
    return page.evaluate("() => window.__eitri_debug().rev")


def _click(page, pt):
    x, y = page.evaluate(f"() => window.__eitri_project({json.dumps(pt)})")
    page.mouse.move(x, y)
    page.mouse.click(x, y)
    page.wait_for_function("() => { const d = window.__eitri_debug(); return !d.sketch || !d.sketch.busy; }", timeout=10000)
    page.wait_for_timeout(80)


def _part(st, name):
    return next(p for p in st["parts"] if p["name"] == name)


def test_sheet_metal_tools(tmp_path):
    project = Project(tmp_path / "proj", name="channel")
    project.write_model("from eitri import *\n\nPARAMS = dict()\n\ndef build(p):\n    return {}\n")
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        answers = []
        page.on("dialog", lambda d: d.accept(answers.pop(0)) if answers else d.dismiss())
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().rev === 1 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(400)
        rev = page.evaluate("() => window.__eitri_debug().rev")

        # 1. base flange: a 120 x 50 rectangle on the ground, 1.5 mm steel
        page.evaluate("() => window.__eitri_tool('sketch')")
        x, y = page.evaluate("() => window.__eitri_project([0, 0, 0])")
        page.mouse.click(x, y)
        page.wait_for_function("() => window.__eitri_debug().sketch && window.__eitri_debug().sketch.face === 'ground'", timeout=10000)
        page.wait_for_timeout(300)
        cx, cy = page.evaluate("() => window.__eitri_project([0, 0, 0])")
        page.mouse.move(cx, cy)
        for _ in range(5):                                                   # zoom out: a 120 mm sheet is larger than the blank grid (and clear of the sketch panel)
            page.mouse.wheel(0, 400)
            page.wait_for_timeout(120)
        xs = [page.evaluate(f"() => window.__eitri_project({json.dumps(pt)})") for pt in ([-60, -25, 0], [60, 25, 0])]
        assert all(0 < x < 1000 and 0 < y < 900 for x, y in xs), xs
        for pt in ([-60, -25, 0], [60, 25, 0]):
            _click(page, pt)
        page.wait_for_function("() => window.__eitri_debug().sketch.shape && !window.__eitri_debug().sketch.busy", timeout=10000)
        assert page.evaluate("() => window.__eitri_sketch.opts({ method: 'base_flange', sheetT: 1.5, newPart: 'channel' })")["method"] == "base_flange"
        page.evaluate("() => window.__eitri_sketch.apply()")
        page.wait_for_function("() => window.__eitri_debug().rev > 1 && window.__eitri_debug().faces > 0", timeout=60000)
        rev = _ready(page, 1, 1)
        st = _state(page)
        e = st["edits_full"][0]
        assert e["op"] == "base_flange" and e["thickness"] == 1.5 and e["new_part"] == "channel" and st["edit_notes"][0]["ok"]
        assert abs(_part(st, "channel")["volume"] - 120 * 50 * 1.5) < 1e-3 and st["sheets"]["channel"]["thickness"] == 1.5 and st["sheets"]["channel"]["bends"] == []

        # 2. a 25 mm flange up along the +Y edge of the top face, from the edge panel
        topo = _topo(page)
        edge = next(e for e in topo["edges"] if e["part"] == "channel" and e["type"] == "line" and abs(e["mid"][1] - 25) < 1e-6 and abs(e["mid"][2] - 1.5) < 1e-6)
        page.evaluate(f"() => {{ window.__eitri_tool('select'); window.__eitri_select([{json.dumps(edge['id'])}]); }}")
        page.wait_for_selector("text=Flange…")
        answers[:] = ["25 90"]
        page.click("text=Flange…")
        rev = _ready(page, 2, rev)
        st = _state(page)
        assert st["edits_full"][1]["op"] == "flange" and st["edits_full"][1]["height"] == 25 and st["edit_notes"][1]["ok"]
        assert len(st["sheets"]["channel"]["bends"]) == 1 and _part(st, "channel")["size"][2] > 25

        # 2b. a contour flange (a hat step: 12 up, 8 out) along the -Y edge of the top face
        topo = _topo(page)
        edge = next(e for e in topo["edges"] if e["part"] == "channel" and e["type"] == "line" and abs(e["mid"][1] + 25) < 1e-6 and abs(e["mid"][2] - 1.5) < 1e-6)
        page.evaluate(f"() => {{ window.__eitri_tool('select'); window.__eitri_select([{json.dumps(edge['id'])}]); }}")
        page.wait_for_selector("#selpanel button:text-is('Contour flange…')")
        answers[:] = ["12@90, 8@-90"]
        page.click("#selpanel button:text-is('Contour flange…')")
        rev = _ready(page, 3, rev)
        st = _state(page)
        assert st["edits_full"][2]["op"] == "contour_flange" and st["edits_full"][2]["legs"] == [[12, 90], [8, -90]] and st["edit_notes"][2]["ok"], st["edit_notes"]
        assert len(st["sheets"]["channel"]["bends"]) == 3 and _part(st, "channel")["size"][1] > 50 + 8

        # 3. the flat pattern beside it, with its bend lines drawn, its DXF, then hidden again
        page.evaluate("() => window.__eitri_select(['channel'])")
        page.wait_for_selector("text=Flat pattern")
        page.click("text=Flat pattern")
        rev = _ready(page, 4, rev)
        st = _state(page)
        assert "channel_flat" in st["flats"] and st["flats"]["channel_flat"]["source"] == "channel" and len(st["flats"]["channel_flat"]["bends"]) == 3
        assert [q["name"] for q in st["parts"]] == ["channel", "channel_flat"]
        page.wait_for_function("() => window.__eitri_render.bends() === 3", timeout=10000)
        dxf = page.evaluate("() => fetch('/download?fmt=flat&part=channel').then(r => r.text())")
        assert "BEND" in dxf and "OUTLINE" in dxf
        page.evaluate("() => window.__eitri_select(['channel'])")
        page.wait_for_selector("text=Hide flat pattern")
        page.click("text=Hide flat pattern")
        page.wait_for_function(f"() => window.__eitri_debug().rev > {rev} && document.querySelectorAll('#edits .edit').length === 3", timeout=60000)
        page.wait_for_timeout(300)
        st = _state(page)
        assert [q["name"] for q in st["parts"]] == ["channel"] and not st["flats"]
        browser.close()
