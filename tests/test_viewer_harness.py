"""The wire-harness editor in a real browser: the diagram replaces the 3D view, wires and connectors are edited in the
inspector, connectors are dragged, a failing check highlights its wires, and every change lands in the model file
(EDITS / LAYOUT) where the agent sees it."""
from __future__ import annotations

import json
import time

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = '''from eitri.harness import *

PARAMS = dict(length_mm=1500)


def build(p):
    h = Harness("Bench lead")
    a = h.add(Connector("J1", "JST-XH", pins={"1": "GND", "2": "5V"}))
    b = h.add(Connector("J2", "JST-XH", pins={"1": "GND", "2": "5V"}))
    h.net("5V", current_A=2.0, voltage_V=5.0)
    h.net("GND", current_A=2.0, voltage_V=5.0, round_trip=False)
    h.connect(a["2"], b["2"], Wire(24, "RD", p.length_mm), net="5V")
    h.connect(a["1"], b["1"], Wire(24, "BK", p.length_mm), net="GND")
    return h
'''


def _state(page):
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _until(page, predicate: str):
    """Wait until the server's state satisfies a JS predicate on it (`s => ...`), then let the page's poll redraw."""
    # poll with evaluate (it awaits the fetch); wait_for_function would take the returned Promise itself as truthy
    deadline = time.time() + 60
    while not page.evaluate(f"() => fetch('/state.json').then(r => r.json()).then(s => !!({predicate})(s))"):
        assert time.time() < deadline, f"the state never satisfied {predicate}"
        page.wait_for_timeout(150)
    page.wait_for_timeout(900)
    return _state(page)


def _wire_point(page, wire_id, t=0.12):
    """Screen coordinates of a point on a wire's curve (near its start: clear of labels and boxes)."""
    return page.evaluate("""([id, t]) => {
      const p = document.querySelector(`g[data-kind="wire"][data-id="${id}"] path.core`);
      const pt = p.getPointAtLength(p.getTotalLength() * t); const m = p.getScreenCTM();
      return [pt.x * m.a + pt.y * m.c + m.e, pt.x * m.b + pt.y * m.d + m.f];
    }""", [wire_id, t])


def _conn_point(page, name):
    """The middle of a connector's header strip."""
    return page.evaluate("""(name) => {
      const r = document.querySelector(`g[data-kind="conn"][data-id="${name}"] rect.head`).getBoundingClientRect();
      return [r.x + r.width / 2, r.y + r.height / 2];
    }""", name)


def _start(tmp_path):
    project = Project(tmp_path / "proj", name="bench")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    return project, start_viewer(project)


def _open(p, url):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1500, "height": 950})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector("#hedit .hpaper svg g[data-kind='wire'] path.core", state="attached", timeout=60000)
    page.wait_for_timeout(500)
    return browser, page, errors


def test_the_harness_editor_replaces_the_3d_view(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        assert page.evaluate("() => document.body.classList.contains('harness')")
        assert page.evaluate("() => getComputedStyle(document.getElementById('tools')).display") == "none"      # no CAD toolbar
        assert page.locator("#chat").is_visible() and page.locator("#chatinput").is_visible()                    # chat is unchanged
        wires = page.locator("#hedit g[data-kind='wire']:has(path.core)")
        assert wires.count() == 2 and page.locator("#hedit g[data-kind='conn']").count() == 2
        # the checks card: red / amber / green rows from the report
        st = _state(page)
        assert st["kind"] == "harness" and st["harness"]["scene"]["nodes"]["J1"] and st["net_stats"]
        rows = page.locator("#checks .check")
        assert rows.count() == len(st["checks"])
        assert page.locator("#checks .check.ok").count() >= 5
        assert "check" in page.inner_text("#checktitle").lower()
        assert page.locator("#sheet").get_attribute("src") is None and not page.is_visible("#sheet")   # built without the review image: none is asked for (it would 404)
        assert not errors, errors
        browser.close()


def test_edit_a_wire_in_the_inspector(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        x, y = _wire_point(page, "W1")
        page.mouse.click(x, y)
        page.wait_for_selector("#domaininsp .hinspector .hp .hh b:text('Wire W1')")
        assert "J1.2 → J2.2" in page.inner_text("#domaininsp .hinspector .hsub")

        # gauge: 24 AWG -> 30 AWG is far too thin for 2 A over 1.5 m: the ampacity and drop checks must turn red
        gauge = page.locator("#domaininsp .hinspector input[list='hgauges']")
        gauge.fill("30")
        gauge.press("Enter")
        _until(page, "s => s.harness.wires.find(w => w.id === 'W1').gauge === 30")
        assert project.edits() == [{"op": "set_wire", "wire": "W1", "gauge": 30, "by": project.edits()[0].get("by")}] or project.edits()[0]["gauge"] == 30
        st = _state(page)
        assert st["edits"][0]["description"] == "wire W1: gauge = 30" and st["edit_notes"][0]["ok"]
        bad = [c["name"] for c in st["checks"] if c["severity"] == "fail"]
        assert any(n.startswith("ampacity W1") for n in bad) or any(n.startswith("voltage drop 5V") for n in bad), bad
        assert page.locator("#checks .check.bad").count() >= 1
        assert "30 AWG" in page.evaluate("() => [...document.querySelectorAll(\"#hedit g[data-kind='wire'][data-id='W1']\")].map(g => g.textContent).join(' ')")   # the diagram redrew
        assert page.inner_text("#domaininsp .hinspector .hh b") == "Wire W1"                          # the selection survived the rebuild

        # colour, length and net from the same panel, and each one is merged into the one edit for W1
        page.locator("#domaininsp .hinspector select").nth(0).select_option("YE")
        _until(page, "s => s.harness.wires.find(w => w.id === 'W1').color === 'YE'")
        length = page.locator("#domaininsp .hinspector input[type=number]").first
        length.fill("900")
        length.press("Enter")
        _until(page, "s => s.harness.wires.find(w => w.id === 'W1').length_mm === 900")
        edits = project.edits()
        assert len(edits) == 1 and edits[0]["wire"] == "W1" and edits[0]["gauge"] == 30 and edits[0]["color"] == "YE" and edits[0]["length_mm"] == 900
        wires = {w["id"]: w for w in _state(page)["harness"]["wires"]}
        assert wires["W1"]["gauge"] == 30 and wires["W1"]["color"] == "YE" and wires["W1"]["length_mm"] == 900 and wires["W2"]["length_mm"] == 1500

        # the agent sees it: the tool the agent calls lists the user's edit
        from eitri.agent.tools import Tools
        assert "wire W1: gauge = 30" in Tools(project).list_edits().text
        assert not errors, errors
        browser.close()


def test_edit_a_connector_pins_and_type(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        x, y = _conn_point(page, "J2")
        page.mouse.click(x, y)
        page.wait_for_selector("#domaininsp .hinspector .hh b:text('Connector J2')")
        # the type, from the catalog
        typ = page.locator("#domaininsp .hinspector input[list='htypes']")
        typ.fill("JST-PH")
        typ.press("Enter")
        _until(page, "s => s.harness.connectors[1].type === 'JST-PH'")
        conns = {c["name"]: c for c in _state(page)["harness"]["connectors"]}
        assert conns["J2"]["type"] == "JST-PH" and conns["J2"]["rating_a"] == 2.0
        assert "2 A per pin" in page.inner_text("#domaininsp .hinspector .hsub")
        # rename pin 2 -> VCC
        pin2 = page.locator("#domaininsp .hinspector .hpr input[type=text]").nth(2)     # row 2: [pin, function] x 2 rows
        pin2.fill("VCC")
        pin2.press("Enter")
        _until(page, "s => s.harness.connectors[1].pins[1] === 'VCC'")
        assert _state(page)["harness"]["connectors"][1]["pins"] == ["1", "VCC"]
        w = next(w for w in _state(page)["harness"]["wires"] if w["id"] == "W1")
        assert w["b"] == {"connector": "J2", "pin": "VCC"}
        # add a pin, mark it NC; then it can be removed again
        page.get_by_role("button", name="+ pin").click()
        _until(page, "s => s.harness.connectors[1].pins.length === 3")
        assert _state(page)["harness"]["connectors"][1]["pins"] == ["1", "VCC", "3"]
        assert any(c["name"] == "all pins connected or declared NC" and c["severity"] == "fail" for c in _state(page)["checks"])   # the new pin is open
        page.locator("#domaininsp .hinspector .hpr input[type=checkbox]").nth(2).check()
        _until(page, "s => s.harness.connectors[1].nc.includes('3')")
        assert any(c["name"] == "all pins connected or declared NC" and c["severity"] == "pass" for c in _state(page)["checks"])
        ops = [e["op"] for e in project.edits()]
        assert ops == ["set_connector", "rename_pin", "add_pin", "nc"]
        assert not errors, errors
        browser.close()


def test_drag_a_connector_persists_a_layout_hint(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        before = _state(page)["harness"]["scene"]["nodes"]["J2"]
        x, y = _conn_point(page, "J2")
        k = page.evaluate("() => document.querySelector('.hpaper').getBoundingClientRect().width / parseFloat(document.querySelector('.hpaper svg').style.width)")
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 60, y + 40, steps=4)
        page.mouse.move(x + 120, y + 80, steps=4)
        # while dragging, its wires follow
        mid = page.evaluate("() => document.querySelector('g[data-kind=\"wire\"][data-id=\"W1\"] path.core').getAttribute('d')")
        page.mouse.up()
        _until(page, "s => s.harness.layout && s.harness.layout.J2")
        after = _state(page)["harness"]["scene"]["nodes"]["J2"]
        assert abs((after["x"] - before["x"]) - 120 / k) < 12 and abs((after["y"] - before["y"]) - 80 / k) < 12
        layout = project.layout()
        assert set(layout) == {"J2"} and abs(layout["J2"][0] - after["x"]) < 1
        assert "LAYOUT = " in project.read_model() and project.edits() == []          # a layout hint, not an edit
        # wiring is untouched
        assert _state(page)["harness"]["wires"][0]["a"] == {"connector": "J1", "pin": "2"}
        assert not errors, errors
        browser.close()


def test_click_a_failing_check_highlights_its_wires(tmp_path):
    project, url = _start(tmp_path)
    project.set_params({"length_mm": 4000})
    assert project.build(note="long", render=False).ok
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        page.wait_for_selector("#checks .check.bad")
        bad = page.locator("#checks .check.bad.pickable").first
        name = bad.locator(".nm").inner_text()
        refs = next(c["refs"] for c in _state(page)["checks"] if c["name"] == name)
        assert refs
        bad.click()
        page.wait_for_selector("#hedit .hpaper g.hl", state="attached")
        lit = set(page.eval_on_selector_all("#hedit .hpaper g.hl", "els => els.map(e => e.dataset.id)"))
        assert lit and lit <= set(refs) | {"W1", "W2"}
        assert page.evaluate("() => document.querySelector('#hedit .hpaper svg').classList.contains('dimmed')")
        bad.click()                                                    # the same check again: cleared
        page.wait_for_timeout(200)
        assert page.locator("#hedit .hpaper g.hl").count() == 0
        # the diagram itself marks failing wires (red halo) so the review image and the editor agree
        assert page.locator("#hedit .hpaper path.halo").count() >= 1
        assert not errors, errors
        browser.close()


def test_add_and_remove_a_wire(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        # first free two pins to wire (a third pin on each connector)
        project.add_edit({"op": "add_pin", "connector": "J1", "pin": "3", "label": "SIG"})
        project.add_edit({"op": "add_pin", "connector": "J2", "pin": "3", "label": "SIG"})
        assert project.build(note="pins", render=False).ok
        _until(page, "s => s.harness.connectors[0].pins.length === 3")
        page.get_by_role("button", name="Add wire").click()
        pin = lambda c, n: page.locator(f"g[data-kind='conn'][data-id='{c}'] rect.pinhit[data-pin='{n}']")
        pin("J1", "3").click(force=True)
        pin("J2", "3").click(force=True)
        _until(page, "s => s.harness.wires.length === 3")
        added = project.edits()[-1]
        assert added["op"] == "add_wire" and (added["a"], added["b"]) == ("J1.3", "J2.3")
        ids = [w["id"] for w in _state(page)["harness"]["wires"]]
        assert ids == ["W1", "W2", "W3"]
        page.wait_for_selector("#domaininsp .hinspector .hh b:text('Wire W3')")              # the new wire is selected for editing
        page.get_by_role("button", name="Remove wire").click()
        _until(page, "s => s.harness.wires.length === 2")
        assert [w["id"] for w in _state(page)["harness"]["wires"]] == ["W1", "W2"]
        assert not errors, errors
        browser.close()


def test_a_cad_project_still_opens_the_3d_editor(tmp_path):
    project = Project(tmp_path / "cad", name="box")
    project.write_model("from eitri import *\nPARAMS = dict(w=10)\n\ndef build(p):\n    return Box(p.w, 10, 10)\n")
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0", timeout=60000)
        assert not page.evaluate("() => document.body.classList.contains('harness')") and page.locator("#hedit").count() == 0
        browser.close()
