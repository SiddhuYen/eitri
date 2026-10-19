"""Insert a screw into a clicked hole, sweep a sketched profile along a clicked edge, redo."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")
BRACKET = Path(__file__).resolve().parent.parent / "examples" / "bracket.py"


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


def _click_at(page, pt):
    x, y = page.evaluate(f"() => window.__eitri_project({json.dumps(pt)})")
    page.mouse.click(x, y)
    page.wait_for_timeout(150)


def test_insert_sweep_redo(tmp_path):
    project = Project(tmp_path / "proj", name="bracket")
    project.write_model(BRACKET.read_text(encoding="utf-8"))
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(400)
        rev = page.evaluate("() => window.__eitri_debug().rev")

        # insert an M3x8 screw by clicking the far wall of the plate hole at (15, 18): head on the plate top (z=4)
        page.evaluate("() => { window.__eitri_tool('insert'); const s = window.__eitri_spec.insert; s.kind = 'screw'; s.size = 'M3'; s.length = 8; s.head = 'socket_head'; s.flip = false; }")
        _click_at(page, [15 - 2.75 * 0.707, 18 + 2.75 * 0.707, 2])
        rev = _ready(page, 1, rev)
        st = _state(page)
        names = [p["name"] for p in st["parts"]]
        assert names == ["bracket", "screw_M3"] and st["edits"][0]["op"] == "insert"
        sb = next(p for p in _topo(page)["parts"] if p["name"] == "screw_M3")["bbox"]
        assert abs(sb[5] - 7) < 0.2 and abs(sb[2] - (-4)) < 0.2 and abs(sb[0] + sb[3] - 30) < 0.2     # head z 4..7, shank to z=-4, centred at x=15

        # sweep: a Ø3 circle sketched on the plate's right end face at its top-front corner, swept along the top-front edge
        page.evaluate("() => window.__eitri_tool('sketch')")
        _click_at(page, [30, 10, 2])
        page.wait_for_function("() => window.__eitri_debug().sketch", timeout=10000)
        page.wait_for_timeout(300)
        page.evaluate("() => window.__eitri_sketch.sub('circle')")
        for pt in ([30, 0, 4], [30, 1.5, 4]):
            _click_at(page, pt)
        page.wait_for_function("() => window.__eitri_debug().sketch.shape && window.__eitri_debug().sketch.shape.type === 'circle' && !window.__eitri_debug().sketch.busy", timeout=10000)
        edge = next(e for e in _topo(page)["edges"] if e["part"] == "bracket" and e["type"] == "line" and e["mid"] == [0, 0, 4])
        page.evaluate(f"() => window.__eitri_sketch.method('sweep', [{json.dumps(edge['id'])}])")
        v0 = next(p for p in st["parts"] if p["name"] == "bracket")["volume"]
        page.evaluate("() => window.__eitri_sketch.apply()")
        rev = _ready(page, 2, rev)
        st = _state(page)
        assert st["edits"][1]["op"] == "sweep" and st["edit_notes"][1]["ok"]
        assert next(p for p in st["parts"] if p["name"] == "bracket")["volume"] > v0 + 100

        # undo the sweep, then redo it
        page.keyboard.press("Meta+z")
        rev = _ready(page, 1, rev)
        page.keyboard.press("Meta+Shift+z")
        rev = _ready(page, 2, rev)
        assert _state(page)["edits"][1]["op"] == "sweep"
        browser.close()
