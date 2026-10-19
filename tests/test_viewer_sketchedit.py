"""Tangent-chain selection by double-click, and reshaping an existing sketch feature by dragging a corner of its (now constrained) sketch."""
from __future__ import annotations

import json

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = ("from eitri import *\nPARAMS = dict(size=40.0)\n\ndef build(p):\n"
         "    return extrude(RectangleRounded(p.size, 30, 5), 10)\n")      # a rounded block: the top outline is a tangent chain of 8 edges


def _state(page):
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _ready(page, n_edits, prev_rev):
    page.wait_for_function(
        f"() => window.__eitri_debug().rev > {prev_rev} && document.querySelectorAll('#edits .edit').length === {n_edits}"
        " && window.__eitri_debug().faces > 0 && !window.__eitri_debug().drag", timeout=90000)
    page.wait_for_timeout(300)
    return page.evaluate("() => window.__eitri_debug().rev")


def test_tangent_chain_and_sketch_handles(tmp_path):
    project = Project(tmp_path / "proj", name="block")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    topo = json.loads(open(project.last_ok_revision().outputs["topology"], encoding="utf-8").read())
    top_edges = [e for e in topo["edges"] if abs(e["mid"][2] - 10) < 1e-6]
    assert len(top_edges) == 8 and all(len(e["tangent"]) == 2 for e in top_edges)       # lines and arcs alternate, all tangent
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(400)
        rev = page.evaluate("() => window.__eitri_debug().rev")

        # double-click the front top edge: the whole rounded outline is selected, then fillet it
        page.evaluate("() => window.__eitri_tool('fillet')")
        x, y = page.evaluate("() => window.__eitri_project([0, -15, 10])")
        page.mouse.dblclick(x, y)
        page.wait_for_timeout(300)
        sel = page.evaluate("() => window.__eitri_debug().selected")
        assert len(sel) == 8 and all(s.startswith("block:e") for s in sel), sel
        page.fill("#selactions input[type=number]", "1")
        page.press("#selactions input[type=number]", "Enter")
        rev = _ready(page, 1, rev)
        st = _state(page)
        assert st["edits"][0]["op"] == "fillet" and "8 edge(s)" in st["edits"][0]["description"] and st["edit_notes"][0]["ok"]

        # a pocket, then reopen its sketch and drag a corner handle: 20x8 -> wider and taller
        top = {"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [0, 0, 10], "area": 1000}
        page.evaluate("() => fetch('/edit', {method: 'POST', body: JSON.stringify(%s)})" % json.dumps(
            {"op": "sketch_extrude", "part": "block", "face": top, "origin": [0, 0, 10], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [0, 0], "w": 20, "h": 8}, "distance": 3, "mode": "cut"}))
        rev = _ready(page, 2, rev)
        v0 = _state(page)["parts"][0]["volume"]
        page.evaluate("() => window.__eitri_feature_sketch(1)")
        page.wait_for_function("() => { const d = window.__eitri_debug(); return d.sketch && d.sketch.editing === 1 && d.sketch.model.entities.length === 4 && !d.sketch.busy; }", timeout=10000)
        page.wait_for_timeout(300)
        sk = page.evaluate("() => window.__eitri_debug().sketch")
        assert sk["shape"]["type"] == "rect" and len(sk["model"]["dims"]) == 2 and sk["status"]["dof"] == 2   # a quick shape reopens as a constrained sketch
        handles = page.evaluate("() => window.__eitri_handles()")
        corner = [ref for ref, pos in handles.items() if abs(pos[0] - 10) < 1e-6 and abs(pos[1] - 4) < 1e-6]   # the (+u, +v) corner: two coincident line ends
        assert len(corner) == 2 and len(handles) == 8, handles
        # the width/height dimensions would make a corner drag move the whole rectangle; drop them, then drag the corner
        page.evaluate("() => window.__eitri_sketch.op('undimension', {index: 1})")
        page.evaluate("() => window.__eitri_sketch.op('undimension', {index: 0})")
        assert page.evaluate("() => window.__eitri_debug().sketch.model.dims.length") == 0
        hx, hy = page.evaluate(f"() => window.__eitri_project({json.dumps(handles[corner[0]])})")
        tx, ty = page.evaluate("() => window.__eitri_project([14, 6, 10.2])")
        page.mouse.move(hx, hy)
        page.mouse.down()
        page.mouse.move(tx, ty, steps=8)
        page.mouse.up()
        rev = _ready(page, 2, rev)
        st = _state(page)
        pr = st["edits_full"][1]["profile"]
        assert pr["type"] == "sketch" and len(pr["entities"]) == 4
        us = [q[0] for e in pr["entities"] for q in (e["a"], e["b"])]; vs = [q[1] for e in pr["entities"] for q in (e["a"], e["b"])]
        assert abs(min(us) + 10) < 1e-6 and abs(max(us) - 14) < 1e-6 and abs(min(vs) + 4) < 1e-6 and abs(max(vs) - 6) < 1e-6, pr   # H/V kept: 24 x 10 at (2, 1)
        assert st["parts"][0]["volume"] < v0 - 100                        # a bigger pocket
        page.wait_for_function("() => { const d = window.__eitri_debug(); return d.sketch && d.sketch.editing === 1 && !d.sketch.busy && d.sketch.model.entities.length === 4; }", timeout=10000)
        assert page.evaluate("() => window.__eitri_debug().sketch.shape") is None   # the sketch reopened on the new model, now a real sketch
        browser.close()
