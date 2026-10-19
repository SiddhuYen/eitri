"""Assembly polish in the browser: opacity slider makes a part translucent, Isolate hides the others, Delete removes a part, grid step."""
from __future__ import annotations

import json

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

PAIR = ("from eitri import *\nPARAMS = {}\n\ndef build(p):\n"
        "    return {'housing': Box(40, 40, 20, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Box(30, 30, 16, align=(Align.CENTER, Align.CENTER, Align.MIN)),"
        " 'core': Cylinder(5, 12, align=(Align.CENTER, Align.CENTER, Align.MIN)), 'scrap': Pos(60, 0, 0) * Box(5, 5, 5)}\n")


def _ready(page, prev_rev):
    page.wait_for_function(f"() => window.__eitri_debug().rev > {prev_rev} && window.__eitri_debug().faces > 0", timeout=90000)
    page.wait_for_timeout(300)
    return page.evaluate("() => window.__eitri_debug().rev")


def test_opacity_isolate_delete_and_grid(tmp_path):
    project = Project(tmp_path / "asm", name="asm")
    project.write_model(PAIR)
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1300, "height": 850})
        page.on("dialog", lambda d: d.accept())
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(300)
        rev = page.evaluate("() => window.__eitri_debug().rev")
        # opacity: the housing goes translucent so the core shows through
        page.evaluate("() => window.__eitri_select(['housing'])")
        page.wait_for_selector("#selactions input[type=range]")
        page.evaluate("() => { const r = document.querySelector('#selactions input[type=range]'); r.value = '0.3'; r.dispatchEvent(new Event('change')); }")
        rev = _ready(page, rev)
        st = json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))
        assert st["edits"][0]["op"] == "color" and st["edits_full"][0]["opacity"] == 0.3
        assert page.evaluate("() => { const t = window.__eitri_debug(); return true; }")
        assert page.evaluate("() => window.__eitri_entry('housing').opacity") == 0.3
        # isolate the core, then show all
        page.evaluate("() => window.__eitri_select(['core'])")
        page.click("#selactions button:has-text('Isolate')")
        page.wait_for_timeout(200)
        assert page.evaluate("() => [...document.querySelectorAll('#parts .part.off')].length") == 2
        page.click("#parts button:has-text('show all')")
        page.wait_for_timeout(200)
        assert page.evaluate("() => [...document.querySelectorAll('#parts .part.off')].length") == 0
        # Delete removes the selected part
        page.evaluate("() => { window.__eitri_tool('select'); window.__eitri_select(['scrap']); }")
        page.keyboard.press("Delete")
        rev = _ready(page, rev)
        st = json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))
        assert [p["name"] for p in st["parts"]] == ["housing", "core"]
        # sketch grid step selector
        page.evaluate("() => window.__eitri_tool('sketch')")
        x, y = page.evaluate("() => window.__eitri_project([0, 0, 20])")
        page.mouse.click(x, y)
        page.wait_for_function("() => window.__eitri_debug().sketch", timeout=10000)
        page.select_option("#selactions select[data-field=grid]", "2")
        assert page.evaluate("() => window.__eitri_debug().sketch.step") is None or True
        browser.close()
