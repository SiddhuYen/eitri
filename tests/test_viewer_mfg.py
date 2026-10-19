"""Manufacturing in the browser: toolpaths generated from the Manufacture panel and drawn over the model,
the G-code download, the cut file and the print-prep report."""
from __future__ import annotations

import json

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = ("from eitri import *\nPARAMS = dict(w=60.0)\n\ndef build(p):\n"
         "    body = Box(p.w, 40, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Pos(0, 0, 10) * Box(30, 20, 6, align=(Align.CENTER, Align.CENTER, Align.MAX))\n"
         "    return cut_holes(body, [(22, 12), (-22, -12)], d=5)\n\nMATERIALS = {'part': 'aluminum'}\n")


def test_toolpaths_cut_and_print_prep(tmp_path):
    project = Project(tmp_path / "proj", name="part")
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
        page.wait_for_timeout(1500)
        assert page.evaluate("() => document.getElementById('mfgpart').value") == "part"
        page.click("#mfgrun")
        page.wait_for_function("() => window.__eitri_render.toolpathCount() > 0", timeout=60000)
        box = page.evaluate("() => document.getElementById('mfgbox').textContent")
        assert "contour outside" in box and "pocket" in box and "drill" in box and "min" in page.evaluate("() => document.getElementById('mfgtime').textContent")
        gcode = page.evaluate("() => fetch(document.getElementById('mfggcode').getAttribute('href')).then(r => r.text())")
        assert gcode.startswith("; eitri 2.5-D job") and "M30" in gcode and "2 hole(s)" in gcode
        page.click("#mfgclear")
        assert page.evaluate("() => window.__eitri_render.toolpathCount()") == 0
        dxf = page.evaluate("() => fetch('/download?fmt=cut&part=part&kerf=0.3&kind=dxf').then(r => r.text())")
        assert "CUT_OUTER" in dxf and "CUT_INNER" in dxf
        answers[:] = ["150x150"]
        page.click("#mfgprint")
        page.wait_for_function("() => document.getElementById('mfgbox').textContent.includes('best orientation')", timeout=60000)
        rep = page.evaluate("() => document.getElementById('mfgbox').textContent")
        assert "bed 150 x 150" in rep and "1 part(s) placed" in rep
        stl = page.evaluate("() => fetch('/download?fmt=bed&kind=stl').then(r => r.arrayBuffer()).then(b => b.byteLength)")
        assert stl > 1000
        browser.close()
