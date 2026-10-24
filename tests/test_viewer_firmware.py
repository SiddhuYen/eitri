"""The firmware editor in a real browser: the pinout replaces the 3D view, a pin is moved and renamed in the inspector, the
board is switched from the pinout table, the code panes are read-only, and a failing check highlights its pin."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = (Path(__file__).resolve().parents[1] / "eitri" / "demos" / "servo-sweep" / "servo_sweep.py").read_text(encoding="utf-8")


def _state(page):
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _until(page, predicate: str):
    # poll with evaluate (it awaits the fetch); wait_for_function would take the returned Promise itself as truthy
    deadline = time.time() + 90
    while not page.evaluate(f"() => fetch('/state.json').then(r => r.json()).then(s => !!({predicate})(s))"):
        assert time.time() < deadline, f"the state never satisfied {predicate}"
        page.wait_for_timeout(150)
    page.wait_for_timeout(900)
    return _state(page)


def _start(tmp_path, code=MODEL):
    project = Project(tmp_path / "proj", name="sweep")
    project.write_model(code)
    assert project.build(note="first", render=False).ok
    return project, start_viewer(project)


def _open(p, url):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1500, "height": 950})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector("#hedit .hpaper svg g[data-kind='pin']", state="attached", timeout=60000)
    page.wait_for_timeout(500)
    return browser, page, errors


def test_edit_a_pin_and_switch_the_board(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        assert page.evaluate("() => document.body.classList.contains('firmware')")
        ins = "#hedit .hinspector"
        assert "Pinout" in page.inner_text(ins) and "PIN_PAN" in page.inner_text(ins)
        code = page.locator(f"{ins} pre.hcode")
        assert "pan.attach(PIN_PAN);" in code.inner_text() and page.evaluate("() => !document.querySelector('#hedit pre.hcode').isContentEditable")
        page.locator(f"{ins} button.small:text('eitri_pins.h')").click()
        page.wait_for_selector(f"{ins} pre.hcode[data-file='eitri_pins.h']")
        assert "const uint8_t PIN_LED = 13;" in page.inner_text(f"{ins} pre.hcode")
        # the inspector: move LED to D12, then rename it
        page.locator(f"{ins} .hpr.fw:has(span:text('PIN_LED'))").click()
        page.wait_for_selector(f"{ins} .hh b:text('Pin D13')")
        page.locator(f"{ins} label.hf:has(span:text('board pin')) select").select_option("D12")
        st = _until(page, "s => s.domain.pins.find(p => p.net === 'LED').canonical === 'D12'")
        assert project.edits() == [{"op": "set_pin", "net": "LED", "pin": "D12"}]
        assert "const uint8_t PIN_LED = 12;" in next(x for x in st["domain"]["sources"] if x["name"] == "eitri_pins.h")["code"]
        page.evaluate("() => window.__domain.select(null)")
        page.locator(f"{ins} label.hf:has(span:text('board')) select").first.select_option("nano")
        _until(page, "s => s.domain.board === 'nano'")
        assert [e["op"] for e in project.edits()] == ["set_pin", "set_board"]
        assert not errors, errors
        browser.close()


def test_a_failing_check_highlights_its_pin(tmp_path):
    project, url = _start(tmp_path, MODEL.replace('fw.pin("D13", "out", "digital_out", net="LED")', 'fw.pin("D13", "out", "pwm", net="LED")'))
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        bad = page.locator("#checks .check.bad.pickable").first
        bad.click()
        page.wait_for_selector("#hedit .hpaper g.hl", state="attached")
        assert "D13" in set(page.eval_on_selector_all("#hedit .hpaper g.hl", "els => els.map(e => e.dataset.id)"))
        assert page.locator("#hedit .hinspector .hpr.fw b.bad").count() >= 1
        assert not errors, errors
        browser.close()
