"""Form bodies in the browser: the Form tool makes a subdivided box, Edit cage shows the control cage with
pickable vertices, face centres and edge midpoints, and the panel's actions (extrude a face, drag vertices with
the gizmo, crease an edge, change the levels) become form_edit ops that rebuild the body."""
from __future__ import annotations

import json

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")


def _state(page):
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _ready(page, n_edits, prev_rev):
    page.wait_for_function(
        f"() => window.__eitri_debug().rev > {prev_rev} && document.querySelectorAll('#edits .edit').length === {n_edits}"
        " && window.__eitri_debug().faces > 0 && !window.__eitri_debug().drag", timeout=120000)
    page.wait_for_timeout(300)
    return page.evaluate("() => window.__eitri_debug().rev")


def _part(st, name):
    return next(p for p in st["parts"] if p["name"] == name)


def test_form_tool_and_cage_editing(tmp_path):
    project = Project(tmp_path / "proj", name="sculpt")
    project.write_model("from eitri import *\n\nPARAMS = dict()\n\ndef build(p):\n    return {}\n")
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().rev === 1 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(400)
        rev = page.evaluate("() => window.__eitri_debug().rev")

        # 1. a 30 mm box cage, 2 x 2 x 2 quads, two subdivision levels, from the Form tool panel
        page.evaluate("() => { window.__eitri_tool('form'); const s = window.__eitri_spec.form; s.kind = 'box'; Object.assign(s.size, { w: 30, d: 30, h: 30, nx: 2, ny: 2, nz: 2 }); s.levels = 2; s.name = 'blob'; window.__eitri_tool('form'); }")
        page.wait_for_selector("#selpanel button:text-is('Create form (Enter)')")
        assert page.evaluate("() => document.querySelector('select[data-field=formkind]').value") == "box"
        page.click("#selpanel button:text-is('Create form (Enter)')")
        rev = _ready(page, 1, rev)
        st = _state(page)
        e = st["edits_full"][0]
        assert e["op"] == "form" and e["kind"] == "box" and e["size"]["nx"] == 2 and e["levels"] == 2 and e["new_part"] == "blob" and st["edit_notes"][0]["ok"], st["edit_notes"]
        fm = st["forms"]["blob"]
        assert len(fm["cage"]["faces"]) == 24 and len(fm["cage"]["vertices"]) == 26 and fm["levels"] == 2
        assert 30 ** 3 * 0.45 < _part(st, "blob")["volume"] < 30 ** 3 * 0.9 and _part(st, "blob")["size"][2] < 31     # rounded: smaller than its cage

        # 2. Edit cage from the part panel: every vertex, face centre and edge midpoint gets a marker
        page.evaluate("() => { window.__eitri_tool('select'); window.__eitri_select(['blob']); }")
        page.wait_for_selector("#selpanel button:text-is('Edit cage')")
        page.click("#selpanel button:text-is('Edit cage')")
        page.wait_for_function("() => window.__eitri_cage.state() && window.__eitri_cage.state().part === 'blob'", timeout=10000)
        cs = page.evaluate("() => window.__eitri_cage.state()")
        assert cs["markers"] == 26 + 24 + 48 and cs["sel"] == []
        assert "Form cage of blob" in page.inner_text("#selpanel")

        # a click on a cage vertex selects it (the markers are picked before the model)
        v0 = fm["cage"]["vertices"][0]
        page.evaluate("() => window.__eitri_cage.select([])")
        x, y = page.evaluate(f"() => window.__eitri_project({json.dumps(v0)})")
        page.mouse.move(x, y); page.mouse.click(x, y)
        page.wait_for_function("() => window.__eitri_cage.state().sel.length === 1", timeout=10000)
        assert page.evaluate("() => window.__eitri_cage.state().sel") == ["v:0"]

        # 3. extrude a top face of the cage 10 mm, scaled to 0.6: four new quads
        top = next(i for i, f in enumerate(fm["cage"]["faces"]) if all(abs(fm["cage"]["vertices"][v][2] - 15) < 1e-6 for v in f))
        page.evaluate(f"() => window.__eitri_cage.select(['f:{top}'])")
        page.wait_for_selector("#selpanel button:text-is('Extrude face')")
        page.fill("#selpanel input[title='distance along the face normal']", "10")
        page.fill("#selpanel input[title='scale of the new face']", "0.6")
        page.click("#selpanel button:text-is('Extrude face')")
        rev = _ready(page, 2, rev)
        st = _state(page)
        e = st["edits_full"][1]
        assert e["op"] == "form_edit" and e["action"] == "extrude_face" and e["face"] == top and e["distance"] == 10 and e["scale"] == 0.6 and st["edit_notes"][1]["ok"], st["edit_notes"]
        fm = st["forms"]["blob"]
        assert len(fm["cage"]["faces"]) == 28 and len(fm["cage"]["vertices"]) == 30 and max(v[2] for v in fm["cage"]["vertices"]) == 25
        assert _part(st, "blob")["size"][2] > 30 - 2                                                            # the bump shows in the body
        assert page.evaluate("() => window.__eitri_cage.state().part") == "blob"                              # still in cage mode after the rebuild

        # 4. drag two vertices up with the gizmo (moveBy stands in for the pointer): a move_vertices op
        page.evaluate("() => window.__eitri_cage.select(['v:0', 'v:1'])")
        page.wait_for_function("() => window.__eitri_cage.state().sel.length === 2", timeout=10000)
        before = [fm["cage"]["vertices"][0][:], fm["cage"]["vertices"][1][:]]
        assert page.evaluate("() => window.__eitri_cage.moveBy([0, 0, 6])")
        rev = _ready(page, 3, rev)
        st = _state(page)
        e = st["edits_full"][2]
        assert e["op"] == "form_edit" and e["action"] == "move_vertices" and sorted(e["vertices"]) == [0, 1] and e["delta"] == [0, 0, 6] and st["edit_notes"][2]["ok"]
        fm = st["forms"]["blob"]
        assert [round(fm["cage"]["vertices"][i][2] - before[i][2], 6) for i in (0, 1)] == [6, 6]

        # 5. crease an edge, then raise the levels: both rebuild and are recorded
        a, b = sorted(fm["cage"]["faces"][0][:2])
        page.evaluate(f"() => window.__eitri_cage.select(['e:{a}-{b}'])")
        page.wait_for_selector("#selpanel button:text-is('Crease')")
        page.click("#selpanel button:text-is('Crease')")
        rev = _ready(page, 4, rev)
        st = _state(page)
        assert st["edits_full"][3]["action"] == "crease" and st["edits_full"][3]["edges"] == [[a, b]] and st["forms"]["blob"]["cage"]["creases"] == [[a, b]]
        page.evaluate("() => window.__eitri_cage.action('levels', { levels: 3 })")
        rev = _ready(page, 5, rev)
        st = _state(page)
        assert st["edits_full"][4]["action"] == "levels" and st["forms"]["blob"]["levels"] == 3 and _part(st, "blob")["volume"] > 0
        assert "levels to 3" in page.inner_text("#edits")

        # 6. Escape leaves cage mode (first clears the selection, then exits); the markers go away
        page.evaluate("() => window.__eitri_cage.select(['v:2'])")
        page.keyboard.press("Escape")
        page.wait_for_function("() => window.__eitri_cage.state() && window.__eitri_cage.state().sel.length === 0", timeout=5000)
        page.keyboard.press("Escape")
        page.wait_for_function("() => window.__eitri_cage.state() === null", timeout=5000)
        browser.close()
