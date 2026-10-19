"""Surface bodies in the browser: an open chain sketched on a face becomes a sheet (surface extrude), the
part panel extends it and thickens it into a solid."""
from __future__ import annotations

import json

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = ("from eitri import *\nPARAMS = dict(w=60.0)\n\ndef build(p):\n"
         "    return Box(p.w, 40, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))\n")
TOP = 4.0


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


def test_surface_tools(tmp_path):
    project = Project(tmp_path / "proj", name="plate")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        answers = []
        page.on("dialog", lambda d: d.accept(answers.pop(0)) if answers else d.dismiss())
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(400)
        rev = page.evaluate("() => window.__eitri_debug().rev")

        # 1. a line on the plate top, extruded as a surface 15 mm up: a 20 x 15 sheet, a new surface part
        page.evaluate("() => window.__eitri_tool('sketch')")
        _click(page, [20, 15, TOP])
        page.wait_for_function("() => window.__eitri_debug().sketch", timeout=10000)
        page.wait_for_timeout(300)
        page.keyboard.press("l")
        for pt in ([-10, -10, TOP], [10, -10, TOP]):
            _click(page, pt)
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.__eitri_debug().sketch.open === 1 && !window.__eitri_debug().sketch.busy", timeout=10000)
        assert page.evaluate("() => window.__eitri_sketch.opts({ method: 'surface_extrude', distance: 15, newPart: 'fin' })")["method"] == "surface_extrude"
        assert page.locator("#selactions input[data-field=newpart]").input_value() == "fin"
        page.evaluate("() => window.__eitri_sketch.apply()")
        rev = _ready(page, 1, rev)
        st = _state(page)
        e = st["edits_full"][0]
        assert e["op"] == "surface" and e["kind"] == "extrude" and e["new_part"] == "fin" and st["edit_notes"][0]["ok"]
        assert [q["name"] for q in st["parts"]] == ["plate", "fin"] and _part(st, "fin")["volume"] == 0
        fin_faces = [f for f in _topo(page)["faces"] if f["part"] == "fin"]
        assert len(fin_faces) == 1 and abs(fin_faces[0]["area"] - 300) < 1e-3 and "surface" in st["edits"][0]["description"]

        # 2. the part panel of a surface body: extend all free edges by 5 (30 x 25), then thicken by 2 into a solid
        page.evaluate("() => { window.__eitri_tool('select'); window.__eitri_select(['fin']); }")
        page.wait_for_selector("text=Thicken…")
        answers[:] = ["5"]
        page.click("text=Extend…")
        rev = _ready(page, 2, rev)
        st = _state(page)
        assert st["edits_full"][1]["op"] == "surface_extend" and st["edit_notes"][1]["ok"]
        fin_faces = [f for f in _topo(page)["faces"] if f["part"] == "fin"]
        assert abs(sum(f["area"] for f in fin_faces) - 750) < 1e-3
        page.evaluate("() => window.__eitri_select(['fin'])")
        page.wait_for_selector("text=Thicken…")
        answers[:] = ["2"]
        page.click("text=Thicken…")
        rev = _ready(page, 3, rev)
        st = _state(page)
        assert st["edits_full"][2] == {"op": "thicken", "part": "fin", "thickness": 2} and st["edit_notes"][2]["ok"]
        assert abs(_part(st, "fin")["volume"] - 1500) < 1e-2
        browser.close()
