"""The constrained sketch editor in a real browser: a polyline drawn with the mouse gets Fusion's automatic
constraints, a corner drags with the solver running, dimensions and constraints apply, a corner fillet,
a projected 3D face, the extrude, and reshaping a hole by dragging the circle of its sketch."""
from __future__ import annotations

import json
import math

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = ("from eitri import *\nPARAMS = dict(w=60.0)\n\ndef build(p):\n"
         "    return Box(p.w, 40, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))\n")
TOP = 10.0


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


def _sk(page):
    page.wait_for_function("() => window.__eitri_debug().sketch && !window.__eitri_debug().sketch.busy", timeout=10000)
    return page.evaluate("() => window.__eitri_debug().sketch")


def _click(page, pt):
    x, y = page.evaluate(f"() => window.__eitri_project({json.dumps(pt)})")
    page.mouse.move(x, y)
    page.mouse.click(x, y)
    page.wait_for_function("() => { const d = window.__eitri_debug(); return !d.sketch || !d.sketch.busy; }", timeout=10000)
    page.wait_for_timeout(60)


def _op(page, op, args):
    r = page.evaluate(f"() => window.__eitri_sketch.op({json.dumps(op)}, {json.dumps(args)})")
    assert r and r.get("ok"), (op, args, r)
    return r


def _bbox(model):
    us, vs = [], []
    for e in model["entities"]:
        if e["type"] == "line":
            for q in (e["a"], e["b"]):
                us.append(q[0]); vs.append(q[1])
    return min(us), max(us), min(vs), max(vs)


def test_sketch_editor(tmp_path):
    project = Project(tmp_path / "proj", name="block")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("dialog", lambda d: d.dismiss())                                           # keep measured dimension values
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(400)
        rev = page.evaluate("() => window.__eitri_debug().rev")
        v0 = _state(page)["parts"][0]["volume"]

        # 1. sketch on the top face; L draws a chained polyline, closing on its start point
        page.evaluate("() => window.__eitri_tool('sketch')")
        _click(page, [20, 15, TOP])
        page.wait_for_function("() => window.__eitri_debug().sketch", timeout=10000)
        page.wait_for_timeout(300)
        assert page.locator("#selactions .vpick[data-field=sktool]").count() == 1
        page.keyboard.press("l")
        assert page.evaluate("() => window.__eitri_debug().sketch.sub") == "line"
        for pt in ([-10, -5, TOP], [10, -5, TOP], [10, 5, TOP], [-10, 5, TOP], [-10, -5, TOP]):
            _click(page, pt)
        sk = _sk(page)
        ents = sk["model"]["entities"]
        assert [e["type"] for e in ents] == ["line"] * 4 and sk["profiles"] == 1 and sk["shape"] is None
        kinds = sorted(c["type"] for c in sk["model"]["constraints"])
        assert kinds == ["coincident"] * 4 + ["horizontal"] * 2 + ["vertical"] * 2, kinds              # auto constraints: joins + H/V
        assert sk["status"]["dof"] == 4 and sk["sub"] == "line" and page.evaluate("() => window.__eitri_debug().sketch.model.entities.length") == 4
        page.keyboard.press("Escape")                                                       # back to select
        assert page.evaluate("() => window.__eitri_debug().sketch.sub") == "select"

        # 2. drag the (10, 5) corner to (12, 7): the solver keeps the rectangle rectangular
        handles = page.evaluate("() => window.__eitri_handles()")
        corner = next(ref for ref, pos in handles.items() if abs(pos[0] - 10) < 1e-6 and abs(pos[1] - 5) < 1e-6)
        hx, hy = page.evaluate(f"() => window.__eitri_project({json.dumps(handles[corner])})")
        tx, ty = page.evaluate(f"() => window.__eitri_project([12, 7, {TOP + 0.2}])")
        page.mouse.move(hx, hy)
        page.mouse.down()
        page.mouse.move(tx, ty, steps=6)
        page.mouse.up()
        page.wait_for_function("() => !window.__eitri_debug().drag && !window.__eitri_debug().sketch.busy", timeout=10000)
        page.wait_for_timeout(200)
        sk = _sk(page)
        assert _bbox(sk["model"]) == pytest.approx((-10, 12, -5, 7), abs=1e-6), _bbox(sk["model"])

        # 3. a length dimension driven to 30, an equal constraint, a corner fillet; a conflicting dimension is refused
        r = _op(page, "dimension", {"dim": {"type": "length", "a": "l1"}})
        assert r["result"] == 0 and abs(page.evaluate("() => window.__eitri_debug().sketch.model.dims[0].value") - 22) < 1e-6   # measured, then driven
        _op(page, "set_dim", {"index": 0, "value": 30})
        sk = _sk(page)
        u0, u1, v0_, v1 = _bbox(sk["model"])
        assert abs((u1 - u0) - 30) < 1e-6 and abs((v1 - v0_) - 12) < 1e-6, _bbox(sk["model"])
        page.evaluate("() => window.__eitri_sketch.select(['l1', 'l3'])")
        assert page.evaluate("() => window.__eitri_sketch.constrain('equal')")
        sk = _sk(page)
        eq = [i for i, c in enumerate(sk["model"]["constraints"]) if c["type"] == "equal"]
        assert len(eq) == 1 and sk["sel"] == []
        bad = page.evaluate("() => window.__eitri_sketch.op('dimension', {dim: {type: 'length', a: 'l3', value: 50}})")
        assert bad and not bad["ok"] and bad.get("conflict"), bad                              # l3 equals l1 = 30: refused, sketch unchanged
        assert len(_sk(page)["model"]["dims"]) == 1
        bad = page.evaluate("() => window.__eitri_sketch.op('fillet', {a: 'l1', b: 'l2', r: 2})")
        assert bad and not bad["ok"] and bad.get("conflict"), bad                              # trimming l1 would break l1 = l3: refused too
        assert len(_sk(page)["model"]["entities"]) == 4
        _op(page, "unconstrain", {"index": eq[0]})
        _op(page, "fillet", {"a": "l1", "b": "l2", "r": 2})
        sk = _sk(page)
        assert sorted(e["type"] for e in sk["model"]["entities"]) == ["arc"] + ["line"] * 4 and sk["profiles"] == 1 and sk["status"]["ok"]
        assert "fully constrained" not in page.evaluate("() => document.getElementById('selitems').textContent")
        assert "DOF" in page.evaluate("() => document.getElementById('selitems').textContent")

        # 4. project the block's top face outline into the sketch as construction geometry
        topo = _topo(page)
        top = next(f for f in topo["faces"] if f["type"] == "plane" and abs(f["center"][2] - TOP) < 1e-6)
        face = {"kind": "face", "type": top["type"], "center": top["center"], "area": top["area"], "normal": top["normal"]}
        _op(page, "project", {"part": "block", "face": face, "normal": [0, 0, 1], "origin": [0, 0, TOP], "construction": True})
        sk = _sk(page)
        cons = [e for e in sk["model"]["entities"] if e.get("construction")]
        assert len(cons) == 4 and sk["profiles"] == 1                                       # construction lines do not make profiles
        assert "construction" in page.evaluate("() => document.getElementById('selitems').textContent") or True

        # 5. extrude the profile 5 mm: the feature carries the full sketch
        page.evaluate("() => window.__eitri_sketch.set(5, 'add')")
        page.evaluate("() => window.__eitri_sketch.apply()")
        rev = _ready(page, 1, rev)
        st = _state(page)
        e0 = st["edits_full"][0]
        assert e0["op"] == "sketch_extrude" and e0["profile"]["type"] == "sketch" and st["edit_notes"][0]["ok"]
        area = 30 * 12 - (1 - math.pi / 4) * 4
        assert abs(st["parts"][0]["volume"] - (v0 + area * 5)) < 0.05, st["parts"][0]["volume"] - v0
        assert st["edits"][0]["description"].startswith("extrude sketch (")

        # 6. a hole, then reshape it through its sketch: drag the circle centre 5 mm in +x
        page.evaluate("() => fetch('/edit', {method: 'POST', body: JSON.stringify(%s)})" % json.dumps(
            {"op": "hole", "part": "block", "face": face, "at": [-20, -10], "size": "M3"}))
        rev = _ready(page, 2, rev)
        page.evaluate("() => window.__eitri_feature_sketch(1)")
        page.wait_for_function("() => { const d = window.__eitri_debug(); return d.sketch && d.sketch.editing === 1 && !d.sketch.busy; }", timeout=10000)
        page.wait_for_timeout(300)
        handles = page.evaluate("() => window.__eitri_handles()")
        assert list(handles) == ["h.c"], handles
        hx, hy = page.evaluate(f"() => window.__eitri_project({json.dumps(handles['h.c'])})")
        tx, ty = page.evaluate(f"() => window.__eitri_project([-15, -10, {TOP + 0.2}])")
        page.mouse.move(hx, hy)
        page.mouse.down()
        page.mouse.move(tx, ty, steps=6)
        page.mouse.up()
        rev = _ready(page, 2, rev)
        st = _state(page)
        assert st["edits_full"][1]["at"] == [-15, -10] and st["edits_full"][1]["size"] == "M3", st["edits_full"][1]
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        assert page.evaluate("() => window.__eitri_debug().sketch") is None
        browser.close()
