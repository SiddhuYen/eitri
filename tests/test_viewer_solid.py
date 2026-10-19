"""The solid-completeness tools in a real browser: extrude to the next face from the sketch panel, a variable
fillet and an unequal chamfer from the edge panel, a boss and a lip placed by clicking faces, a pattern along
clicked edges, a wedge primitive, and copy / repair from the part panel."""
from __future__ import annotations

import json
import math

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = ("from eitri import *\nPARAMS = dict(w=40.0)\n\ndef build(p):\n"
         "    body = Box(p.w, 20, 30, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Pos(0, 0, 15) * Box(30, 40, 18)\n"
         "    return body\n")          # a tunnel through Y: floor top at z=6, ceiling at z=24, lid top at z=30


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
    page.wait_for_timeout(150)


def _part(st, name):
    return next(p for p in st["parts"] if p["name"] == name)


def test_solid_tools(tmp_path):
    project = Project(tmp_path / "proj", name="tunnel")
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
        v0 = _state(page)["parts"][0]["volume"]

        # 1. a pillar from the tunnel floor to its ceiling: sketch a rectangle on the floor, extent "to the next face"
        page.evaluate("() => window.__eitri_tool('sketch')")
        page.evaluate("() => window.__eitri_tool('select')")
        page.evaluate("() => { const f = window.__eitri_entry; }")
        floor = next(f for f in _topo(page)["faces"] if f["type"] == "plane" and abs(f["center"][2] - 6) < 1e-6 and f["normal"][2] > 0.9)
        page.evaluate(f"() => {{ window.__eitri_select([{json.dumps(floor['id'])}]); window.__eitri_tool('sketch'); }}")
        page.wait_for_function("() => window.__eitri_debug().sketch && window.__eitri_debug().sketch.face === " + json.dumps(floor["id"]), timeout=10000)
        page.wait_for_timeout(300)
        for pt in ([-5, -3, 6], [5, 3, 6]):
            _click(page, pt)
        page.wait_for_function("() => window.__eitri_debug().sketch.shape && !window.__eitri_debug().sketch.busy", timeout=10000)
        page.select_option("#selactions select[data-field=extent]", "to_next")
        assert page.evaluate("() => window.__eitri_sketch.opts({})")["extent"] == "to_next"
        page.evaluate("() => window.__eitri_sketch.apply()")
        rev = _ready(page, 1, rev)
        st = _state(page)
        assert st["edits_full"][0]["extent"] == "to_next" and st["edit_notes"][0]["ok"]
        assert abs(st["parts"][0]["volume"] - (v0 + 10 * 6 * 18)) < 1e-2, st["parts"][0]["volume"] - v0
        assert "to the next face" in st["edits"][0]["description"]

        # 2. a variable fillet on one vertical outer edge via the edge panel's "end r" field, then an unequal chamfer
        topo = _topo(page)
        edge = next(e for e in topo["edges"] if e["type"] == "line" and abs(e["start"][0] - 20) < 1e-6 and abs(e["end"][0] - 20) < 1e-6 and abs(e["start"][1] + 10) < 1e-6 and abs(e["end"][1] + 10) < 1e-6)
        page.evaluate(f"() => {{ window.__eitri_tool('fillet'); window.__eitri_select([{json.dumps(edge['id'])}]); }}")
        page.wait_for_selector("#selactions input[data-field=second]")
        page.fill("#selactions input[type=number]", "1")
        page.fill("#selactions input[data-field=second]", "3")
        assert page.evaluate("() => window.__eitri_apply()")
        rev = _ready(page, 2, rev)
        st = _state(page)
        assert st["edits_full"][1]["op"] == "fillet" and st["edits_full"][1]["radius_end"] == 3 and st["edit_notes"][1]["ok"] and "r=1->3" in st["edit_notes"][1]["detail"]
        v_fil = st["parts"][0]["volume"]
        assert v_fil < v0 + 10 * 6 * 18
        topo = _topo(page)
        top_edges = [e for e in topo["edges"] if e["type"] == "line" and abs(e["mid"][2] - 30) < 1e-6 and abs(e["mid"][0] + 20) < 1e-6]
        assert len(top_edges) == 1
        page.evaluate(f"() => {{ window.__eitri_tool('chamfer'); window.__eitri_select([{json.dumps(top_edges[0]['id'])}]); }}")
        page.wait_for_selector("#selactions input[data-field=angle]")
        page.fill("#selactions input[type=number]", "2")
        page.fill("#selactions input[data-field=second]", "1")
        assert page.evaluate("() => window.__eitri_apply()")
        rev = _ready(page, 3, rev)
        st = _state(page)
        assert st["edits_full"][2]["size2"] == 1 and st["edit_notes"][2]["ok"] and "2 x 1 mm" in st["edit_notes"][2]["detail"]
        assert abs(st["parts"][0]["volume"] - (v_fil - 2 * 1 / 2 * 20)) < 1e-2

        # 3. a plain boss Ø8 x 6 with a Ø3 hole, placed by clicking the lid's top face
        page.evaluate("() => { window.__eitri_tool('boss'); const b = window.__eitri_spec.boss; b.kind = 'plain'; b.d = 8; b.height = 6; b.hole_d = 3; }")
        lid0 = next(f for f in _topo(page)["faces"] if f["type"] == "plane" and abs(f["center"][2] - 30) < 1e-6 and f["normal"][2] > 0.9)   # its centre moved with the chamfer
        _click(page, [12, 4, 30])
        rev = _ready(page, 4, rev)
        st = _state(page)
        e = st["edits_full"][3]
        assert e["op"] == "boss" and [round(e["at"][0] + lid0["center"][0], 6), round(e["at"][1] + lid0["center"][1], 6)] == [12, 4] and st["edit_notes"][3]["ok"]
        v_boss = st["parts"][0]["volume"]
        assert abs(v_boss - (st["parts"][0]["volume"])) < 1e-9 and v_boss > v_fil - 20 + math.pi * (16 - 2.25) * 6 - 1e-2

        # 4. a lip along the inner edge of the tunnel's lid underside? no: the rim needs an inner outline. Use a groove on the lid top around the boss instead:
        #    the lid top face now has an inner outline (the boss base), so a 1 x 1 "lip" along it is a ring around the boss
        topo = _topo(page)
        lid = next(f for f in topo["faces"] if f["type"] == "plane" and abs(f["center"][2] - 30) < 1e-6 and f["normal"][2] > 0.9)
        page.evaluate(f"() => {{ window.__eitri_tool('lip'); window.__eitri_select([{json.dumps(lid['id'])}]); }}")
        page.wait_for_selector("#selactions button.primary")
        page.evaluate("() => { const td = window.__eitri_tooldata({ mode: 'lip', side: 'inner', width: 1, height: 1 }); }")
        assert page.evaluate("() => window.__eitri_apply()")
        rev = _ready(page, 5, rev)
        st = _state(page)
        assert st["edits_full"][4]["op"] == "lip" and st["edit_notes"][4]["ok"]
        ring = math.pi * (5 ** 2 - 4 ** 2) * 1
        assert abs(st["parts"][0]["volume"] - (v_boss + ring)) < 1e-2, st["parts"][0]["volume"] - v_boss

        # 5. a wedge primitive as a new part, then copies of it along the tunnel's front-bottom edge, then a copy and a repair
        page.evaluate("() => { window.__eitri_tool('primitive'); const s = window.__eitri_spec.primitive; s.kind = 'wedge'; s.size.w = 4; s.size.d = 4; s.size.h = 3; s.size.top_w = 4; s.size.top_h = 0; s.mode = 'new'; s.name = 'ramp'; s.at = [-18, -14, 0]; window.__eitri_tool('primitive'); }")
        page.click("#selactions button.primary")
        rev = _ready(page, 6, rev)
        st = _state(page)
        assert [q["name"] for q in st["parts"]] == ["tunnel", "ramp"] and abs(_part(st, "ramp")["volume"] - 4 * 4 * 3 / 2) < 1e-6
        topo = _topo(page)
        edge = next(e for e in topo["edges"] if e["part"] == "tunnel" and e["type"] == "line" and abs(e["mid"][1] + 10) < 1e-6 and abs(e["mid"][2]) < 1e-6)
        page.evaluate("() => { window.__eitri_tool('pattern'); window.__eitri_tooldata({ part: 'ramp', type: 'path', n: 3 }); }")
        page.evaluate(f"() => {{ const td = window.__eitri_tooldata(); td.pathEdges = [{json.dumps(edge['id'])}]; td.pathPart = 'tunnel'; }}")
        page.evaluate("() => window.__eitri_tooldata({})")
        page.click("#selactions button.primary")
        rev = _ready(page, 7, rev)
        st = _state(page)
        assert st["edits_full"][6]["type"] == "path" and st["edit_notes"][6]["ok"] and [q["name"] for q in st["parts"]] == ["tunnel", "ramp", "ramp_2", "ramp_3"]
        page.evaluate("() => { window.__eitri_tool('select'); window.__eitri_select(['ramp_3']); }")
        answers[:] = ["ramp_copy", "0,30,0"]
        page.click("text=Copy…")
        rev = _ready(page, 8, rev)
        st = _state(page)
        assert st["edits_full"][7] == {"op": "copy_part", "part": "ramp_3", "new_part": "ramp_copy", "delta": [0, 30, 0]} and "ramp_copy" in [q["name"] for q in st["parts"]]
        page.evaluate("() => { window.__eitri_select(['tunnel']); }")
        page.click("text=Repair")
        rev = _ready(page, 9, rev)
        st = _state(page)
        assert st["edits_full"][8] == {"op": "repair", "part": "tunnel"} and "valid yes -> yes" in st["edit_notes"][8]["detail"]
        browser.close()
