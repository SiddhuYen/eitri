"""The electronics editor in a real browser: the schematic replaces the 3D view, the parts and nets tables, a resistor's value
edited in the inspector, a pin moved to another net, a part dragged (LAYOUT), and a failing check highlighting its parts."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = (Path(__file__).resolve().parents[1] / "eitri" / "demos" / "uno-arm-circuit" / "uno_arm_circuit.py").read_text(encoding="utf-8")
INS = "#domaininsp .hinspector"


def _state(page):
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _until(page, predicate: str):
    deadline = time.time() + 90
    while not page.evaluate(f"() => fetch('/state.json').then(r => r.json()).then(s => !!({predicate})(s))"):
        assert time.time() < deadline, f"the state never satisfied {predicate}"
        page.wait_for_timeout(150)
    page.wait_for_timeout(900)
    return _state(page)


def _start(tmp_path, code=MODEL):
    project = Project(tmp_path / "proj", name="arm")
    project.write_model(code)
    assert project.build(note="first", render=False).ok
    return project, start_viewer(project)


def _open(p, url):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1500, "height": 950})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector("#hedit .hpaper svg g[data-kind='part']", state="attached", timeout=60000)
    page.wait_for_timeout(500)
    return browser, page, errors


def test_tables_value_edit_and_rewire(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        assert page.evaluate("() => document.body.classList.contains('electronics')")
        text = page.inner_text(INS)
        assert "Circuit" in text and "PARTS" in text.upper() and "NETS" in text.upper() and "SERVO_5V" in text
        assert page.locator(f"{INS} [data-table='parts'] .hnr.elec").count() == 18
        # a resistor's value from the parts table
        page.locator(f"{INS} [data-part='R1']").click()
        page.wait_for_selector(f"{INS} .hh b:text('R1 · resistor')")
        inp = page.locator(f"{INS} label.hf:has(span:text('resistance')) input")
        inp.fill("120"); inp.press("Enter")
        st = _until(page, "s => s.domain.parts.find(p => p.name === 'R1').spec.ohm === 120")
        assert project.edits() == [{"op": "set_part", "part": "R1", "ohm": 120}]
        assert any(c["name"] == "pin current UNO.D13" and c["severity"] == "fail" for c in st["checks"])
        # move the LED's cathode onto another net: a connect edit
        page.evaluate("() => window.__domain.select({kind: 'part', id: 'LED1'})")
        page.wait_for_selector(f"{INS} .hh b:text('LED1 · led')")
        page.locator(f"{INS} label.hf:has(span:text('K')) select").select_option("COIL_LOW")
        _until(page, "s => s.domain.nets.find(n => n.name === 'COIL_LOW').pins.includes('LED1.K')")
        assert project.edits()[-1] == {"op": "connect", "pin": "LED1.K", "net": "COIL_LOW"}
        # a net from the schematic
        page.evaluate("() => window.__domain.select({kind: 'net', id: 'SDA'})")
        page.wait_for_selector(f"{INS} .hh b:text('Net SDA')")
        assert "I2C clock" in page.inner_text(INS)
        assert not errors, errors
        browser.close()


def test_drag_saves_a_layout_hint(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        box = page.locator("#hedit .hpaper svg g[data-kind='part'][data-id='SOL1'] rect").first.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + 60, box["y"] + box["height"] / 2 + 80, steps=8)
        page.mouse.up()
        deadline = time.time() + 60
        while "SOL1" not in (project.layout() or {}):
            assert time.time() < deadline, "no layout hint saved"
            page.wait_for_timeout(200)
        assert not errors, errors
        browser.close()


def test_a_failing_check_highlights_its_parts(tmp_path):
    project, url = _start(tmp_path, MODEL.replace('servo_supply="psu", flyback=True', 'servo_supply="psu", flyback=False'))
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        bad = page.locator("#checks .check.bad.pickable").first
        bad.click()
        page.wait_for_timeout(400)
        lit = page.evaluate("() => [...document.querySelectorAll('#hedit g.hl')].map(g => g.dataset.id)")
        assert "K1" in lit and "Q1" in lit
        assert not errors, errors
        browser.close()
