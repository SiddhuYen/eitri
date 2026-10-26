"""The pneumatics editor in a real browser: the schematic replaces the 3D view, a cylinder and a tube are edited in the
inspector, a component is dragged into a LAYOUT hint, and a failing check highlights its cylinder."""
from __future__ import annotations

import json
import time

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = '''from eitri.pneumatics import *

PARAMS = dict(p_bar=6.0, bore_mm=32, load_N=250)


def build(p):
    c = Circuit("Pusher")
    air = c.supply("AIR", pressure_bar=7.0, flow_l_min=600)
    frl = c.frl("FRL", set_bar=p.p_bar, C=2.5, b=0.35)
    v = c.valve("V1", "5/2", C=1.2, b=0.3)
    cyl = c.cylinder("C1", bore_mm=p.bore_mm, rod_mm=12, stroke_mm=100, load_N=p.load_N, load_retract_N=100, speed_mm_s=200, cycles_per_min=20)
    c.tube("T1", air["OUT"], frl["IN"], id_mm=8, length_m=2.0, C=3.0)
    c.tube("T2", frl["OUT"], v["P"], id_mm=6, length_m=1.0, C=2.0)
    c.tube("T3", v["A"], cyl["CAP"], id_mm=6, length_m=1.5, C=1.8)
    c.tube("T4", v["B"], cyl["ROD"], id_mm=6, length_m=1.5, C=1.8)
    c.silencer(v["R"], C=3, b=0.3)
    c.silencer(v["S"], C=3, b=0.3)
    return c


def verify(c, p, chk):
    chk.at_least("force margin", cylinder_forces(c.get("C1"), p.p_bar)["extend_usable_N"] / p.load_N, 1.3)
'''


def _state(page):
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _until(page, predicate: str):
    # poll with evaluate (it awaits the fetch); wait_for_function would take the returned Promise itself as truthy
    deadline = time.time() + 60
    while not page.evaluate(f"() => fetch('/state.json').then(r => r.json()).then(s => !!({predicate})(s))"):
        assert time.time() < deadline, f"the state never satisfied {predicate}"
        page.wait_for_timeout(150)
    page.wait_for_timeout(900)
    return _state(page)


def _start(tmp_path, code=MODEL):
    project = Project(tmp_path / "proj", name="pusher")
    project.write_model(code)
    assert project.build(note="first", render=False).ok
    return project, start_viewer(project)


def _open(p, url):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1500, "height": 950})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector("#hedit .hpaper svg g[data-kind='component']", state="attached", timeout=60000)
    page.wait_for_timeout(500)
    return browser, page, errors


def test_edit_a_cylinder_and_a_tube(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        assert page.evaluate("() => document.body.classList.contains('pneumatics')") and "Components" in page.inner_text("#domaininsp .hinspector")
        page.locator("#domaininsp .hinspector .hnr.pneu:has(b:text('C1'))").click()
        page.wait_for_selector("#domaininsp .hinspector .hh b:text('Cylinder C1')")
        bore = page.locator("#domaininsp .hinspector label.hf:has(span:text('bore (mm)')) input")
        bore.fill("40")
        bore.press("Enter")
        _until(page, "s => s.domain.components.find(c => c.name === 'C1').bore_mm === 40")
        assert project.edits()[0]["bore_mm"] == 40
        page.evaluate("() => window.__domain.select({kind: 'tube', id: 'T3'})")
        page.wait_for_selector("#domaininsp .hinspector .hh b:text('Tube T3')")
        length = page.locator("#domaininsp .hinspector label.hf:has(span:text('length (m)')) input")
        length.fill("4")
        length.press("Enter")
        _until(page, "s => s.domain.tubes.find(t => t.name === 'T3').length_m === 4")
        assert [e["op"] for e in project.edits()] == ["set_component", "set_tube"]
        assert not errors, errors
        browser.close()


def test_drag_a_component_and_highlight_a_failure(tmp_path):
    project, url = _start(tmp_path, MODEL.replace("load_N=250)", "load_N=600)"))
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        bad = page.locator("#checks .check.bad.pickable").first
        bad.click()
        page.wait_for_selector("#hedit .hpaper g.hl", state="attached")
        assert "C1" in set(page.eval_on_selector_all("#hedit .hpaper g.hl", "els => els.map(e => e.dataset.id)"))
        bad.click()
        before = _state(page)["domain"]["scene"]["nodes"]["V1"]
        r = page.evaluate("""() => { const g = [...document.querySelectorAll('g[data-kind="component"][data-id="V1"]')].find(g => g.querySelector('rect')); const b = g.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; }""")
        page.mouse.move(*r)
        page.mouse.down()
        page.mouse.move(r[0] + 60, r[1] + 20, steps=5)
        page.mouse.up()
        st = _until(page, "s => s.domain.layout && s.domain.layout.V1")
        assert st["domain"]["scene"]["nodes"]["V1"]["x"] > before["x"] + 20 and "LAYOUT = " in project.read_model() and project.edits() == []
        assert not errors, errors
        browser.close()
