"""Blank design in the browser: the grid is there, S + a click on empty space sketches on the ground, extrude makes the first part."""
from __future__ import annotations

import json

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")


def test_sketch_on_the_ground(tmp_path):
    project = Project(tmp_path / "blank", name="blank")
    assert project.ensure_model() and project.build(note="blank", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().rev === 1 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(400)
        d = page.evaluate("() => window.__eitri_debug()")
        assert d["faces"] == 0 and d["radius"] > 1
        assert "blank design" in page.evaluate("() => document.getElementById('hud').textContent")
        page.evaluate("() => window.__eitri_tool('sketch')")
        x, y = page.evaluate("() => window.__eitri_project([0, 0, 0])")
        page.mouse.click(x, y)
        page.wait_for_function("() => window.__eitri_debug().sketch && window.__eitri_debug().sketch.face === 'ground'", timeout=10000)
        page.wait_for_timeout(300)
        for pt in ([-10, -5, 0], [10, 5, 0]):
            px, py = page.evaluate(f"() => window.__eitri_project({json.dumps(pt)})")
            page.mouse.move(px, py)
            page.mouse.click(px, py)
            page.wait_for_timeout(150)
        page.wait_for_function("() => window.__eitri_debug().sketch.shape && window.__eitri_debug().sketch.shape.type === 'rect' && !window.__eitri_debug().sketch.busy", timeout=10000)
        page.evaluate("() => window.__eitri_sketch.set(5, 'add')")
        page.evaluate("() => window.__eitri_sketch.apply()")
        page.wait_for_function("() => window.__eitri_debug().rev > 1 && window.__eitri_debug().faces > 0", timeout=60000)
        page.wait_for_timeout(300)
        st = json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))
        assert [p["name"] for p in st["parts"]] == ["part"] and abs(st["parts"][0]["volume"] - 20 * 10 * 5) < 1e-3
        assert st["edits"][0]["op"] == "sketch_extrude" and st["edits_full"][0].get("face") is None
        browser.close()
