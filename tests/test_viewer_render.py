"""Rendering and parameter extras in the browser: material appearances, studio lighting toggle, focal length,
named views, configurations, and comparing a revision as an overlay."""
from __future__ import annotations

import json

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = '''from eitri import *

PARAMS = dict(width=40.0, depth="width / 2")
CONFIGS = {"small": {"width": 20.0}, "large": {"width": 80.0}}
MATERIALS = {"plate": "brass", "block": "pla"}

def build(p):
    return {"plate": Box(p.width, p.depth, 4, align=(Align.CENTER, Align.CENTER, Align.MIN)), "block": Pos(0, 0, 4) * Box(10, 10, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))}
'''


def _state(page):
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def test_render_and_param_features(tmp_path):
    project = Project(tmp_path / "proj", name="plate")
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
        page.wait_for_timeout(600)
        # appearances by material: brass is a metal, PLA is not
        ap = page.evaluate("() => ({ plate: window.__eitri_render.appearance('plate'), block: window.__eitri_render.appearance('block') })")
        assert ap["plate"]["metalness"] > 0.8 and ap["block"]["metalness"] < 0.1
        page.click("#envlight")
        page.wait_for_timeout(100)
        # focal length
        page.evaluate("() => { const f = document.getElementById('fov'); f.value = 60; f.dispatchEvent(new Event('input')); }")
        assert page.evaluate("() => window.__eitri_render.fov()") == 60 and page.evaluate("() => document.getElementById('fovlabel').textContent") == "60°"
        # named views
        answers[:] = ["my iso"]
        page.click("#saveview")
        assert "my iso" in page.evaluate("() => document.getElementById('namedviews').textContent")
        # configurations: the select applies a config and rebuilds
        page.wait_for_selector("#configsel")
        st = _state(page)
        assert st["configs"] == ["small", "large"] and st["params"] == {"width": 40.0, "depth": "width / 2"}
        page.select_option("#configsel", "large")
        page.wait_for_function("() => window.__eitri_debug().rev > 1 && window.__eitri_debug().faces > 0", timeout=60000)
        page.wait_for_timeout(300)
        st = _state(page)
        assert st["params"]["width"] == 80.0 and st["params"]["depth"] == "width / 2"
        assert abs(next(q for q in st["parts"] if q["name"] == "plate")["size"][1] - 40) < 1e-6          # the expression followed
        # compare the first revision against the current one: an overlay appears and the summary names the change
        page.evaluate("() => window.__eitri_render.compare(1)")
        page.wait_for_function("() => window.__eitri_render.compareCount() > 0", timeout=30000)
        box = page.evaluate("() => document.getElementById('comparebox').textContent")
        assert "revision 1 -> 2" in box and "plate changed" in box and "block" not in box.split("\n")[0].replace("plate changed", "")
        page.evaluate("() => window.__eitri_render.compare(null)")
        assert page.evaluate("() => window.__eitri_render.compareCount()") == 0
        browser.close()
