"""Inspect ▸ Interference overlay and the live drawing's views field in the browser."""
from __future__ import annotations

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

PAIR = ("from eitri import *\nPARAMS = {}\n\ndef build(p):\n"
        "    return {'a': Box(20, 20, 20), 'b': Pos(15, 0, 0) * Box(20, 20, 20)}\n")


def test_interference_overlay_and_drawing_views(tmp_path):
    project = Project(tmp_path / "pair", name="pair")
    project.write_model(PAIR)
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1300, "height": 850})
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(300)
        page.click("#interf")
        page.wait_for_function("() => document.getElementById('hud').textContent.includes('interference')", timeout=30000)
        assert "a × b 2000.00 mm³" in page.evaluate("() => document.getElementById('hud').textContent")
        page.wait_for_function("() => document.getElementById('interf').classList.contains('on')", timeout=5000)
        page.click("#interf")                                   # toggles off
        page.wait_for_timeout(200)
        assert not page.evaluate("() => document.getElementById('interf').classList.contains('on')")
        # the live drawing follows the views field
        page.fill("#drawviews", "front, section:XZ@0")
        page.check("#showdrawing")
        page.wait_for_function("() => document.getElementById('drawing').src.includes('section%3AXZ%400') && document.getElementById('drawing').naturalWidth > 0", timeout=30000)
        browser.close()
