"""The Fusion-style tools in a real browser: drag push/pull, sketch + extrude, hole, fillet, measure, undo."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = ("from eitri import *\nPARAMS = dict(width=60.0)\n\ndef build(p):\n"
         "    plate = Box(p.width, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
         "    return cut_holes(plate, [(-20, 0), (20, 0)], d=5)\n")


def _state(page) -> dict:
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _ready(page, n_edits, prev_rev):
    """Wait until the page has loaded a newer revision that carries n_edits; returns that revision.

    (Page-side state only: wait_for_function does not await a fetch promise.)"""
    page.wait_for_function(
        f"() => window.__eitri_debug().rev > {prev_rev} && document.querySelectorAll('#edits .edit').length === {n_edits}"
        " && window.__eitri_debug().faces > 0 && !window.__eitri_debug().drag", timeout=60000)
    page.wait_for_timeout(300)
    return page.evaluate("() => window.__eitri_debug().rev")


def _proj(page, pt):
    return page.evaluate(f"() => window.__eitri_project({json.dumps(pt)})")


def test_direct_modeling_tools(tmp_path):
    project = Project(tmp_path / "proj", name="plate")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function("window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(500)

        # 1. push/pull by dragging the top face 10 mm along its normal (screen-space projection of the axis)
        page.evaluate("() => window.__eitri_tool('pushpull')")
        x0, y0 = _proj(page, [10, 10, 6])
        x1, y1 = _proj(page, [10, 10, 16])
        page.mouse.move(x0, y0)
        page.mouse.down()
        page.mouse.move(x1, y1, steps=8)
        assert abs(page.evaluate("() => window.__eitri_debug().drag.value") - 10) <= 0.5
        page.mouse.up()
        rev = _ready(page, 1, 1)
        st = _state(page)
        assert st["edits"][0]["description"].startswith("push/pull plate plane +") and 15.5 <= st["parts"][0]["size"][2] <= 16.5
        top_z = st["parts"][0]["size"][2]

        # 2. sketch a 20 x 10 rectangle on the new top face with two clicks, extrude it 5 mm
        page.evaluate("() => window.__eitri_tool('sketch')")
        x, y = _proj(page, [0, 0, top_z])
        page.mouse.click(x, y)
        page.wait_for_function("() => window.__eitri_debug().sketch", timeout=10000)
        page.wait_for_timeout(400)
        ax, ay = _proj(page, [-10, -5, top_z])
        page.mouse.click(ax, ay)
        bx, by = _proj(page, [10, 5, top_z])
        page.mouse.move(bx, by)
        page.mouse.click(bx, by)
        page.wait_for_function("() => window.__eitri_debug().sketch.shape && !window.__eitri_debug().sketch.busy", timeout=10000)
        sk = page.evaluate("() => window.__eitri_debug().sketch")
        assert sk["shape"] == {"type": "rect", "center": [0, 0], "w": 20, "h": 10}, sk
        assert len(sk["model"]["entities"]) == 4 and sk["profiles"] == 1 and sk["status"]["dof"] == 2     # the decomposed, constrained sketch behind the quick shape
        page.evaluate("() => window.__eitri_sketch.set(5, 'add')")
        page.evaluate("() => window.__eitri_sketch.apply()")
        rev = _ready(page, 2, rev)
        st = _state(page)
        assert st["edits"][1]["description"].startswith("extrude 20x10 rectangle 5") and abs(st["parts"][0]["size"][2] - (top_z + 5)) < 1e-6
        # (top_z stays the plate's own top; the pad is a 20 x 10 island in the middle)

        # 3. hole tool: an M3 counterbore at (-20, 10) on the plate's top face (the pad sits in the middle)
        page.evaluate("() => window.__eitri_tool('hole')")
        page.click("#selactions .vpick[data-field=head] .vp[data-value=counterbore]")
        x, y = _proj(page, [-20, -12, top_z])           # on the plate top, on the camera side of the pad (not occluded)
        page.mouse.move(x, y)
        page.mouse.click(x, y)
        rev = _ready(page, 3, rev)
        st = _state(page)
        assert st["edits"][2]["description"] == "hole M3 counterbore in plate"
        hole_lines = [line.strip() for line in st["summary"].splitlines() if "Ø" in line]
        assert any("Ø6.5 blind" in line and "(-20,-12," in line for line in hole_lines), (st["edits"], hole_lines)

        # 4. fillet: click a vertical corner edge, type the radius, Enter
        page.evaluate("() => window.__eitri_tool('fillet')")
        x, y = _proj(page, [30, 20, 3])
        page.mouse.move(x, y)
        page.mouse.click(x, y)
        page.wait_for_timeout(300)
        sel = page.evaluate("() => window.__eitri_debug().selected")
        assert len(sel) == 1 and ":e" in sel[0]
        page.fill("#selactions input[type=number]", "3")
        page.press("#selactions input[type=number]", "Enter")
        rev = _ready(page, 4, rev)
        st = _state(page)
        assert st["edits"][3]["description"].startswith("fillet r3 on 1 edge") and not [n for n in st["edit_notes"] if not n["ok"]]

        # 5. measure between two bottom corners (snaps to edge endpoints): 60 mm apart
        page.click("[data-view=iso]")
        page.wait_for_timeout(300)
        page.evaluate("() => window.__eitri_tool('measure')")
        for pt in ([-29.5, -20, top_z], [29.5, -20, top_z]):   # near the top-front corners: the tool snaps to the vertices
            x, y = _proj(page, pt)
            page.mouse.move(x, y)
            page.mouse.click(x, y)
            page.wait_for_timeout(300)
        page.wait_for_timeout(600)
        labels = page.eval_on_selector_all(".lbl.measure", "els => els.map(e => e.textContent)")
        assert any(l.startswith("60.00 mm") for l in labels), labels

        # 6. undo removes the last change (the fillet): back to 3 edits
        page.keyboard.press("Escape")
        page.keyboard.press("Meta+z")
        page.wait_for_function(f"() => window.__eitri_debug().rev > {rev} && document.querySelectorAll('#edits .edit').length === 3", timeout=60000)
        assert len(project.edits()) == 3
        browser.close()
    assert not errors, errors
