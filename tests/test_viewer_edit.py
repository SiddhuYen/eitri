"""The Edit tool in a real browser: corner handles are pickable, a corner is dragged up, a face is
moved and tilted through the gizmo commit path, and a hole is deleted with Delete."""
from __future__ import annotations

import json

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = ("from eitri import *\nPARAMS = dict(size=20.0)\n\ndef build(p):\n"
         "    box = Box(p.size, p.size, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
         "    return box - Pos(-5, 0, 3) * Rot(90, 0, 0) * Cylinder(1.5, 30)   # a side hole along Y: the top face stays a plain quad\n")


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


def _bbox(page, part="box"):
    return next(p for p in _topo(page)["parts"] if p["name"] == part)["bbox"]


def test_edit_tool(tmp_path):
    project = Project(tmp_path / "proj", name="box")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(400)
        rev = page.evaluate("() => window.__eitri_debug().rev")

        # corner handles appear with the tool; click the top corner nearest the camera
        page.evaluate("() => window.__eitri_tool('edit')")
        n_vertices = len(_topo(page)["vertices"])
        assert n_vertices >= 8 and page.evaluate("() => window.__eitri_debug().vertices") == n_vertices   # 8 corners + the hole seam's 2
        x, y = page.evaluate("() => window.__eitri_project([10, -10, 10])")
        page.mouse.click(x, y)
        page.wait_for_timeout(200)
        sel = page.evaluate("() => window.__eitri_debug().selected")
        assert len(sel) == 1 and sel[0].startswith("box:v") and page.evaluate(f"() => window.__eitri_entry({json.dumps(sel[0])}).pos") == [10, -10, 10]
        assert page.evaluate("() => window.__eitri_debug().egizmo")

        # typed move: the corner goes up 6 mm, the box is no longer a box
        page.evaluate("() => window.__eitri_edit.move([0, 0, 6], null)")
        rev = _ready(page, 1, rev)
        st = _state(page)
        assert st["edits"][0]["op"] == "move_vertices" and st["edit_notes"][0]["ok"]
        assert abs(_bbox(page)[5] - 16) < 1e-3
        assert page.evaluate("() => window.__eitri_debug().vertices") == len(_topo(page)["vertices"])   # handles rebuilt on the new model

        # move a flat side face through the gizmo commit path (+x face by 2 mm), then tilt it
        topo = _topo(page)
        side = next(f for f in topo["faces"] if f["type"] == "plane" and f["normal"] == [1, 0, 0])
        page.evaluate(f"() => {{ window.__eitri_select([{json.dumps(side['id'])}]); window.__eitri_edit.mode('extend'); }}")
        page.evaluate("() => window.__eitri_edit.commit([2, 0, 0], null)")
        rev = _ready(page, 2, rev)
        assert abs(_bbox(page)[3] - 12) < 1e-6 and _state(page)["edits"][1]["op"] == "move_face"
        topo = _topo(page)
        side = next(f for f in topo["faces"] if f["type"] == "plane" and f["normal"] == [1, 0, 0])
        page.evaluate(f"() => window.__eitri_select([{json.dumps(side['id'])}])")
        page.evaluate("() => window.__eitri_edit.commit([0, 0, 0], {axis: [0, 1, 0], angle: 10, about: [12, 0, 5]})")
        rev = _ready(page, 3, rev)
        st = _state(page)
        assert st["edit_notes"][2]["ok"] and "tilted 10" in st["edit_notes"][2]["detail"]
        assert not any(f["type"] == "plane" and f["normal"] == [1, 0, 0] for f in _topo(page)["faces"])   # that face is no longer axis-aligned

        # delete the hole: select its wall in the Edit tool and press Delete
        topo = _topo(page)
        wall = next(f for f in topo["faces"] if f["type"] == "cylinder" and f.get("concave"))
        v0 = next(p for p in topo["parts"] if p["name"] == "box")["volume"]
        page.evaluate(f"() => window.__eitri_select([{json.dumps(wall['id'])}])")
        page.keyboard.press("Delete")
        rev = _ready(page, 4, rev)
        topo = _topo(page)
        assert not topo["holes"] and next(p for p in topo["parts"] if p["name"] == "box")["volume"] > v0
        assert page.evaluate("() => window.__eitri_debug().timeline") == ["1 move_vertices", "2 move_face", "3 move_face", "4 delete_faces"]
        browser.close()


MODEL2 = ("from eitri import *\nPARAMS = dict(size=20.0)\n\ndef build(p):\n"
          "    return Box(p.size, p.size, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)) + Pos(5, 5, 10) * Cylinder(3, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))\n")


def _open(p, tmp_path, model, name="box"):
    project = Project(tmp_path / name, name="box")
    project.write_model(model)
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(url)
    page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
    page.wait_for_timeout(400)
    return browser, page


def test_window_select_and_labels(tmp_path):
    with pw.sync_playwright() as p:
        browser, page = _open(p, tmp_path, MODEL)
        rev = page.evaluate("() => window.__eitri_debug().rev")
        page.click("#labelsbtn")
        page.wait_for_timeout(200)
        dims = page.evaluate("() => [...document.querySelectorAll('.lbl.dim')].map(e => e.textContent)")
        assert sorted(dims) == ["10.0 mm", "20.0 mm", "20.0 mm"]
        page.click("#labelsbtn")
        # front view: a shift-drag box around the top edge grabs the four top corners (front and back pairs overlap)
        page.click("[data-view=front]")
        page.wait_for_timeout(300)
        page.evaluate("() => { window.__eitri_tool('edit'); window.__eitri_filter.edge = false; window.__eitri_filter.face = false; }")
        x1, y1 = page.evaluate("() => window.__eitri_project([-16, 0, 12.5])")
        x2, y2 = page.evaluate("() => window.__eitri_project([16, 0, 7.5])")
        page.keyboard.down("Shift")
        page.mouse.move(x1, y1)
        page.mouse.down()
        page.mouse.move(x2, y2, steps=6)
        page.mouse.up()
        page.keyboard.up("Shift")
        page.wait_for_timeout(200)
        sel = page.evaluate("() => window.__eitri_debug().selected")
        assert len(sel) == 4 and all(s.startswith("box:v") for s in sel), sel
        assert all(page.evaluate(f"() => window.__eitri_entry({json.dumps(s)}).pos[2]") == 10 for s in sel)
        page.evaluate("() => window.__eitri_edit.move([0, 0, 6], null)")
        rev = _ready(page, 1, rev)
        st = _state(page)
        assert st["edit_notes"][0]["ok"] and "4 corner(s)" in st["edit_notes"][0]["detail"] and abs(_bbox(page)[5] - 16) < 1e-3
        browser.close()


def test_rim_drag_pushes_the_face(tmp_path):
    with pw.sync_playwright() as p:
        browser, page = _open(p, tmp_path, MODEL2, "boss")
        rev = page.evaluate("() => window.__eitri_debug().rev")
        topo = _topo(page)
        rim = next(e for e in topo["edges"] if e["type"] == "circle" and abs(e["mid"][2] - 15) < 1e-6)
        page.evaluate(f"() => {{ window.__eitri_tool('edit'); window.__eitri_select([{json.dumps(rim['id'])}]); }}")
        page.evaluate("() => window.__eitri_edit.move([0, 0, 3], null)")
        rev = _ready(page, 1, rev)
        st = _state(page)
        assert st["edits"][0]["op"] == "push_pull" and st["edit_notes"][0]["ok"] and abs(_bbox(page)[5] - 18) < 1e-3
        browser.close()
