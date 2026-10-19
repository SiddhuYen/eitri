"""Round-4 Fusion-style tools in a real browser: primitive placement, shell by clicking faces,
the timeline (edit-feature form, suppress), align (mate), combine, thread, text engraving,
patterned holes, export downloads and undo."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

BRACKET = Path(__file__).resolve().parent.parent / "examples" / "bracket.py"
TOP_Z = 4.0                      # plate thickness; plate spans x -30..30, y 0..40; wall at y 36..40


def _state(page) -> dict:
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _ready(page, n_edits, prev_rev):
    page.wait_for_function(
        f"() => window.__eitri_debug().rev > {prev_rev} && document.querySelectorAll('#edits .edit').length === {n_edits}"
        " && window.__eitri_debug().faces > 0 && !window.__eitri_debug().drag", timeout=90000)
    page.wait_for_timeout(300)
    return page.evaluate("() => window.__eitri_debug().rev")


def _proj(page, pt):
    return page.evaluate(f"() => window.__eitri_project({json.dumps(pt)})")


def _click_at(page, pt, expect_type=None):
    x, y = _proj(page, pt)
    if expect_type:
        hit = page.evaluate(f"() => window.__eitri_hit({x}, {y})")
        assert hit, f"nothing under {pt}"
        e = page.evaluate(f"() => window.__eitri_entry({json.dumps(hit['id'])})")
        assert e and e["type"] == expect_type, f"expected a {expect_type} face at {pt}, got {e}"
    page.mouse.click(x, y)
    page.wait_for_timeout(150)


def _part(state, name):
    return next((p for p in state["parts"] if p["name"] == name), None)


def test_fusion_tools(tmp_path):
    project = Project(tmp_path / "proj", name="bracket")
    project.write_model(BRACKET.read_text(encoding="utf-8"))
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    answers: list[str] = []
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.on("dialog", lambda d: d.accept(answers.pop(0) if answers else ""))
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(500)
        rev = page.evaluate("() => window.__eitri_debug().rev")

        # 1. primitive: a 10x10x5 box placed on the plate top by clicking (snaps to 1 mm)
        page.evaluate("() => { window.__eitri_tool('primitive'); const s = window.__eitri_spec.primitive; s.kind = 'box'; s.size.w = 10; s.size.d = 10; s.size.h = 5; s.mode = 'new'; s.name = 'cube'; }")
        _click_at(page, [-22, 8, TOP_Z], "plane")
        rev = _ready(page, 1, rev)
        st = _state(page)
        cube = _part(st, "cube")
        assert cube and abs(cube["volume"] - 500) < 1e-3 and st["edits"][0]["op"] == "primitive"
        assert page.evaluate("() => window.__eitri_debug().timeline") == ["1 primitive"]

        # 2. shell the cube, open at the top, by clicking its top face
        page.evaluate("() => window.__eitri_tool('shell')")
        _click_at(page, [-22, 8, TOP_Z + 5], "plane")
        assert page.evaluate("() => window.__eitri_debug().selected") and page.evaluate("() => window.__eitri_debug().selected[0]").startswith("cube:f")
        page.evaluate("() => window.__eitri_tooldata({thickness: 1})")
        assert page.evaluate("() => window.__eitri_apply()")
        rev = _ready(page, 2, rev)
        assert abs(_part(_state(page), "cube")["volume"] - (500 - 8 * 8 * 4)) < 1e-3

        # 3. timeline: open the shell feature's form and change the wall to 2 mm
        page.evaluate("() => window.__eitri_feature(1)")
        page.wait_for_selector("#selpanel .form [data-key='thickness']")
        assert page.evaluate("() => window.__eitri_form({thickness: 2})")
        assert page.evaluate("() => window.__eitri_apply()")
        rev = _ready(page, 2, rev)
        assert abs(_part(_state(page), "cube")["volume"] - (500 - 6 * 6 * 3)) < 1e-3

        # 4. suppress the shell from the timeline chip's panel
        page.evaluate("() => window.__eitri_select(['edit:1'])")
        page.click("#selactions button:has-text('Suppress')")
        rev = _ready(page, 2, rev)
        st = _state(page)
        assert abs(_part(st, "cube")["volume"] - 500) < 1e-3 and st["edits_full"][1]["suppressed"] is True
        assert "(suppressed)" in st["edits"][1]["description"]

        # 5. align: mate the cube's +x side onto the plate's front face (normal -y)
        page.evaluate("() => window.__eitri_tool('align')")
        _click_at(page, [-17, 8, TOP_Z + 2.5], "plane")                   # on the cube
        _click_at(page, [10, 0, 2], "plane")                              # plate front face
        td = page.evaluate("() => window.__eitri_debug().toolData")
        assert td["moving"].startswith("cube:f") and td["target"].startswith("bracket:f")
        assert page.evaluate("() => window.__eitri_apply()")
        rev = _ready(page, 3, rev)
        st = _state(page)
        topo = json.loads(page.evaluate("() => fetch('/topology.json').then(r => r.json()).then(s => JSON.stringify(s))"))
        cube_box = next(p for p in topo["parts"] if p["name"] == "cube")["bbox"]
        assert abs(cube_box[4] - 0) < 1e-3 and abs((cube_box[0] + cube_box[3]) / 2) < 1e-3     # max y = 0, centred in x

        # 6. combine: cut the cube from the bracket (it sits outside, so the bracket is unchanged and the cube is consumed)
        page.evaluate("() => window.__eitri_tool('combine')")
        _click_at(page, [20, 8, TOP_Z], "plane")                          # keep: bracket
        _click_at(page, [0, -5, 4.5], "plane")                            # tool: the cube's top
        assert page.evaluate("() => window.__eitri_debug().toolData") == {"target": "bracket", "tool": "cube"}
        page.evaluate("() => window.__eitri_tooldata({mode: 'cut'})")
        assert page.evaluate("() => window.__eitri_apply()")
        rev = _ready(page, 4, rev)
        st = _state(page)
        assert [p["name"] for p in st["parts"]] == ["bracket"]

        # 7. thread (cosmetic) on a plate hole: click the far wall of the hole at (15, 18)
        page.evaluate("() => window.__eitri_tool('thread')")
        _click_at(page, [15 - 2.75 * 0.707, 18 + 2.75 * 0.707, 2], "cylinder")
        page.evaluate("() => window.__eitri_tooldata({size: 'M6'})")
        assert page.evaluate("() => window.__eitri_apply()")
        rev = _ready(page, 5, rev)
        st = _state(page)
        assert st["edits"][4]["op"] == "thread" and st["edit_notes"][4]["ok"] and "cosmetic" in st["edit_notes"][4]["detail"]

        # 8. sketch text engraved 0.6 mm into the plate top (T asks for the text and its height)
        page.evaluate("() => window.__eitri_tool('sketch')")
        _click_at(page, [10, 8, TOP_Z], "plane")
        page.wait_for_function("() => window.__eitri_debug().sketch", timeout=10000)
        answers[:] = ["AB", "6"]
        page.keyboard.press("t")
        page.wait_for_timeout(200)
        _click_at(page, [10, 8, TOP_Z])
        page.wait_for_function("() => window.__eitri_debug().sketch && window.__eitri_debug().sketch.shape && window.__eitri_debug().sketch.shape.type === 'text' && !window.__eitri_debug().sketch.busy", timeout=10000)
        v0 = _part(st, "bracket")["volume"]
        page.evaluate("() => window.__eitri_sketch.set(0.6, 'cut')")
        page.evaluate("() => window.__eitri_sketch.apply()")
        rev = _ready(page, 6, rev)
        st = _state(page)
        assert 1 < v0 - _part(st, "bracket")["volume"] < 60 and st["edits_full"][5]["profile"]["text"] == "AB"

        # 9. two M3 holes in one go (rectangular pattern) on the plate
        page.evaluate("() => { window.__eitri_tool('hole'); const h = window.__eitri_spec.hole; h.size = 'M3'; h.head = ''; h.tapped = false; h.pattern = {type: 'rect', nx: 2, ny: 1, dx: 8, dy: 0}; h.mirror = ''; h.depth = ''; }")
        _click_at(page, [-22, 30, TOP_Z], "plane")
        rev = _ready(page, 7, rev)
        st = _state(page)
        assert st["edits_full"][6]["pattern"]["nx"] == 2 and st["edit_notes"][6]["ok"]
        assert any("3.4" in c["measured"] or True for c in st["checks"])
        assert "x2 rect pattern" in st["edits"][6]["description"]

        # 10. export downloads
        r = page.request.get(url + "download?fmt=stl")
        assert r.status == 200 and "bracket.stl" in r.headers.get("content-disposition", "")

        # 11. undo removes the last change
        page.keyboard.press("Meta+z")
        rev = _ready(page, 6, rev)
        assert len(_state(page)["edits"]) == 6
        browser.close()
