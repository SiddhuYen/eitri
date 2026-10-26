"""Firmware kernel: boards, the typed pinout, every static check firing on a crafted bad project and passing on the fixed one,
the generated pin header, the usage scan, the real arduino-cli compile (skipped where arduino-cli is not installed), the explicit
"compiler unavailable" failure, direct edits, the pinout diagram, the reference example and the demo."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from eitri.firmware import BOARDS, Firmware, FirmwareError, board, find_cli, scan_calls
from eitri.firmware import diagram, edits as E, outputs
from eitri.firmware.build import compile_fw, stage_sketch
from eitri.firmware.compile import CompileResult, compile_sketch
from eitri.firmware.report import analyze, firmware_data, summarize

HAVE_CLI = find_cli() is not None
needs_cli = pytest.mark.skipif(not HAVE_CLI, reason="arduino-cli is not installed (set EITRI_ARDUINO_CLI or put it on PATH)")

GOOD = r'''
#include <Servo.h>
#include "eitri_pins.h"
Servo pan;
void setup() {
  pan.attach(PIN_PAN);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_M_DIR, OUTPUT);
}
void loop() {
  int pot = analogRead(PIN_POT);
  analogWrite(PIN_M_PWM, pot / 4);
  digitalWrite(PIN_M_DIR, digitalRead(PIN_BUTTON));
  digitalWrite(PIN_LED, HIGH);
  pan.write(map(pot, 0, 1023, 0, 180));
}
'''


def project(code=GOOD, board_key="uno") -> Firmware:
    fw = Firmware("test", board=board_key)
    fw.servo("PAN", pin="D9", power="external")
    fw.limit_switch("BUTTON", pin="D2")
    fw.pin("D13", "out", "digital_out", net="LED")
    fw.pin("A0", "in", "analog_in", net="POT")
    fw.pwm_motor("M", pwm="D5", dir="D4")
    fw.source("test.ino", code)
    return fw


def one(rep, needle):
    hits = [c for c in rep.checks if needle in c.name]
    assert len(hits) == 1, f"{needle!r} matched {[c.name for c in hits]} in {[c.name for c in rep.checks]}"
    return hits[0]


# ---------------------------------------------------------------------------- boards and the model


def test_boards():
    uno, nano, esp = board("uno"), board("nano"), board("esp32")
    assert uno.fqbn == "arduino:avr:uno" and nano.fqbn == "arduino:avr:nano" and esp.fqbn == "esp32:esp32:esp32" and board("esp32dev") is esp and board("x") is None
    assert {p.name for p in uno.pins.values() if p.pwm} == {"D3", "D5", "D6", "D9", "D10", "D11"}
    assert {p.name for p in uno.pins.values() if p.interrupt} == {"D2", "D3"}
    assert uno.resolve("9").name == "D9" and uno.resolve(9).name == "D9" and uno.resolve("A0").number == 14 and uno.resolve("d13").name == "D13" and uno.resolve("D20") is None
    assert "A6" in nano.pins and not nano.pins["A6"].digital_out and "A6" not in uno.pins
    assert esp.resolve("GPIO7").forbidden and not esp.resolve("34").digital_out and esp.resolve(25).dac and esp.resolve("IO21").name == "GPIO21"
    assert any("strapping" in n for n in esp.resolve("GPIO0").notes)


def test_model_api():
    fw = Firmware("x")
    fw.pin("D3", "out", "pwm", net="LAMP")
    with pytest.raises(FirmwareError, match="already used"):
        fw.pin("D4", net="LAMP")
    with pytest.raises(FirmwareError, match="role must be"):
        fw.pin("D5", role="laser")
    with pytest.raises(FirmwareError, match="C identifier"):
        fw.pin("D6", net="bad name")
    with pytest.raises(FirmwareError, match="eitri_pins.h"):
        fw.source("eitri_pins.h", "")
    with pytest.raises(FirmwareError, match="file name"):
        fw.source("../evil.ino", "")
    fw.source("x.ino", "void setup(){} void loop(){}")
    with pytest.raises(FirmwareError, match="already defined"):
        fw.source("x.ino", "")
    assert fw.get_pin("LAMP").symbol == "PIN_LAMP" and fw.main_sketch.name == "x.ino" and fw.sketch_name == "x"


def test_pins_header():
    fw = project()
    fw.serial(9600)
    h = fw.pins_header()
    assert "const uint8_t PIN_PAN = 9;" in h and "const uint8_t PIN_POT = 14;" in h and "const unsigned long SERIAL_BAUD = 9600UL;" in h and "#pragma once" in h
    bad = Firmware("y")
    bad.pin("D42", net="NOPE")
    assert "// PIN_NOPE: D42 is not a pin of this board" in bad.pins_header()


# ---------------------------------------------------------------------------- the static checks


def test_good_project_passes_statically():
    rep = analyze(project())
    assert rep.ok and not rep.warnings, summarize(rep)
    pins = {p.net: p for p in rep.pins}
    assert pins["PAN"].canonical == "D9" and pins["PAN"].uses == ["attach @ test.ino:6"] and pins["POT"].number == 14
    assert pins["BUTTON"].uses == ["pinMode(INPUT_PULLUP) @ test.ino:7", "digitalRead @ test.ino:14"]
    assert not any(c.category == "compile" for c in rep.checks)                 # analyze() without a compile result: no compile check


def test_unknown_board_and_missing_pins():
    fw = project(board_key="mega2560")
    c = one(analyze(fw), "target board")
    assert c.severity == "fail" and "mega2560" in c.measured
    fw = project()
    fw.pin("D20", net="GHOST")
    c = one(analyze(fw), "pin D20 (GHOST)")
    assert c.severity == "fail" and "not a pin of the uno" in c.measured


def test_capability_failures():
    fw = project()
    fw.get_pin("M_PWM").pin = "D4"                                               # no PWM on D4 (and now D4 is taken twice)
    rep = analyze(fw)
    c = one(rep, "pin D4 (M_PWM) can be pwm")
    assert c.severity == "fail" and "D4 has no PWM (PWM-capable: D3, D5, D6, D9, D10, D11)" in c.measured and c.refs == ["D4"]
    d = one(rep, "no pin assigned twice")
    assert d.severity == "fail" and "D4 is declared 2 times (M_PWM, M_DIR)" in d.measured
    fw2 = project()
    fw2.get_pin("POT").pin = "D7"
    assert "D7 has no ADC" in one(analyze(fw2), "pin D7 (POT) can be analog_in").measured
    fw3 = project()
    fw3.limit_switch("FAST", pin="D8", interrupt=True)
    assert one(analyze(fw3), "pin D8 (FAST) can be interrupt").severity == "fail"
    fw4 = Firmware("n", board="nano")
    fw4.pin("A6", "out", "digital_out", net="X")
    assert "cannot drive an output" in one(analyze(fw4), "pin A6 (X)").measured
    fw5 = project()
    fw5.get_pin("LED").direction = "in"
    assert "role digital_out is an output but the pin is declared in" in one(analyze(fw5), "pin D13 (LED)").measured


def test_esp32_rules():
    fw = Firmware("e", board="esp32", wifi=True)
    fw.pin("GPIO7", "out", net="FLASH")
    fw.pin("GPIO34", "out", "digital_out", net="IN_ONLY")
    fw.pin("GPIO25", "in", "analog_in", net="ADC2")
    fw.pin("GPIO0", "out", net="STRAP")
    fw.pin("GPIO32", "in", "analog_in", net="ADC1")
    rep = analyze(fw)
    assert "must not be used: wired to the module's SPI flash" in one(rep, "pin GPIO7 (FLASH)").measured
    assert one(rep, "pin GPIO34 (IN_ONLY)").severity == "fail"
    w = one(rep, "pin GPIO25 (ADC2)")
    assert w.severity == "warn" and "Wi-Fi" in w.measured
    assert one(rep, "pin GPIO0 (STRAP)").severity == "warn" and one(rep, "pin GPIO32 (ADC1)").severity == "pass"


def test_serial_pins_are_reserved():
    fw = project()
    fw.pin("D1", "out", net="TXLED")
    fw.serial(115200)
    c = one(analyze(fw), "no pin assigned twice")
    assert c.severity == "fail" and "D1 (TXLED) is a Serial pin and Serial is in use" in c.measured
    fw2 = project(GOOD.replace("void setup() {", "void setup() {\n  Serial.begin(9600);"))      # Serial in the code counts too
    fw2.pin("D0", "in", net="RXBTN")
    assert one(analyze(fw2), "no pin assigned twice").severity == "fail"


def test_usage_scan():
    calls = scan_calls(project())
    assert ("pinMode", "PIN_BUTTON", "INPUT_PULLUP") in {(c["fn"], c["arg"], c["mode"]) for c in calls}
    assert any(c["fn"] == "attach" and c["arg"] == "PIN_PAN" for c in calls)
    commented = project(GOOD + "\n// digitalWrite(7, HIGH);\n/* analogWrite(6, 1); */\nconst char *s = \"pinMode(8, OUTPUT)\";\n")
    assert len(scan_calls(commented)) == len(calls)                              # comments and strings are not code


@pytest.mark.parametrize("change,needle,expect", [
    (("pinMode(PIN_LED, OUTPUT);", "pinMode(PIN_LED, INPUT);"), "PIN_LED used as declared", "pinMode(INPUT) on a pin declared out"),
    (("pinMode(PIN_LED, OUTPUT);", ""), "PIN_LED used as declared", "no pinMode(PIN_LED, OUTPUT)"),
    (("analogWrite(PIN_M_PWM, pot / 4);", "analogWrite(PIN_LED, pot / 4); analogWrite(PIN_M_PWM, 1);"), "PIN_LED used as declared", "analogWrite on a pin declared digital_out"),
    (("int pot = analogRead(PIN_POT);", "int pot = analogRead(PIN_BUTTON) + analogRead(PIN_POT);"), "PIN_BUTTON used as declared", "analogRead on a pin declared digital_in"),
    (("digitalWrite(PIN_M_DIR, digitalRead(PIN_BUTTON));", "digitalWrite(PIN_BUTTON, HIGH); digitalWrite(PIN_M_DIR, digitalRead(PIN_BUTTON));"), "PIN_BUTTON used as declared", "digitalWrite on a pin declared in"),
    (("pan.attach(PIN_PAN);", ""), "PIN_PAN used as declared", "declared but the sources never use it"),
    (("pinMode(PIN_BUTTON, INPUT_PULLUP);", "pinMode(PIN_BUTTON, INPUT);"), "PIN_BUTTON used as declared", "declared with a pull-up but pinMode is INPUT"),
])
def test_usage_failures(change, needle, expect):
    rep = analyze(project(GOOD.replace(*change)))
    c = one(rep, needle)
    assert c.severity == "fail" and expect in c.measured, c.measured


def test_undeclared_and_unresolved_pins():
    rep = analyze(project(GOOD.replace("digitalWrite(PIN_LED, HIGH);", "digitalWrite(PIN_LED, HIGH); digitalWrite(7, LOW); int p = 12; pinMode(p, OUTPUT);")))
    c = one(rep, "no undeclared pins")
    assert c.severity == "fail" and "digitalWrite(7 = D7) at test.ino:15" in c.measured
    w = one(rep, "pin calls resolved")
    assert w.severity == "warn" and "pinMode(p)" in w.measured
    # a literal that IS declared resolves to the declaration
    assert analyze(project(GOOD.replace("digitalWrite(PIN_LED, HIGH);", "digitalWrite(13, HIGH);"))).ok


def test_no_sources_or_no_sketch():
    fw = Firmware("x")
    assert one(analyze(fw), "sources").severity == "fail"
    fw.source("lib.h", "")
    assert "no .ino file" in one(analyze(fw), "sources").measured


# ---------------------------------------------------------------------------- compile


def test_compiler_unavailable_is_an_explicit_failed_check(tmp_path, monkeypatch):
    monkeypatch.setenv("EITRI_ARDUINO_CLI", str(tmp_path / "no-such-arduino-cli"))
    folder, res = compile_fw(project(), tmp_path)
    assert res is not None and not res.available and res.message.startswith("compiler unavailable: arduino-cli was not found")
    assert "arduino-cli core install arduino:avr" in res.message
    c = one(analyze(project(), None, res), "compiles (arduino-cli)")
    assert c.severity == "fail" and "compiler unavailable" in c.measured
    assert (folder / "eitri_pins.h").is_file() and (folder / "test.ino").is_file() and folder.name == "test"


def test_compile_error_parsing_without_a_compiler(tmp_path, monkeypatch):
    """The diagnostics parser on arduino-cli's real output format, via a fake binary."""
    fake = tmp_path / "arduino-cli"
    fake.write_text("#!/bin/sh\necho \"/x/sketch/test/test.ino:15:9: error: 'class Servo' has no member named 'wrte'\" >&2\n"
                    "echo \"/x/sketch/test/test.ino:3:1: warning: unused variable 'q'\" >&2\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setenv("EITRI_ARDUINO_CLI", str(fake))
    folder = stage_sketch(project(), tmp_path)
    res = compile_sketch(folder, "arduino:avr:uno", "arduino:avr", tmp_path / "b")
    assert res.available and not res.ok and res.errors == [{"file": "test.ino", "line": 15, "col": 9, "kind": "error", "msg": "'class Servo' has no member named 'wrte'"}]
    assert res.warnings[0]["line"] == 3
    c = one(analyze(project(), None, res), "compiles (arduino-cli)")
    assert c.severity == "fail" and "1 error(s): test.ino:15: 'class Servo' has no member named 'wrte'" in c.measured and c.refs == ["test.ino"]
    ok = CompileResult(True, True, "compiled", flash_bytes=4000, flash_pct=12, ram_bytes=1900, ram_pct=92, warnings=[{"file": "test.ino", "line": 3, "col": 1, "kind": "warning", "msg": "unused"}])
    rep = analyze(project(), None, ok)
    assert one(rep, "compiles (arduino-cli)").severity == "warn" and "RAM 1900 bytes (92%)" in one(rep, "compiles (arduino-cli)").measured
    assert one(rep, "compiler warnings in the sources").severity == "warn"


@needs_cli
def test_real_compile_passes_and_is_cached(tmp_path):
    fw = project()
    _f, res = compile_fw(fw, tmp_path)
    assert res.available and res.ok, res.log[-2000:]
    assert res.flash_bytes and 0 < res.flash_pct < 50 and res.ram_pct is not None and not res.cached
    rep = analyze(fw, None, res)
    assert rep.ok and "compiled for arduino:avr:uno" in one(rep, "compiles (arduino-cli)").measured
    _f, again = compile_fw(fw, tmp_path)
    assert again.cached and again.ok


@needs_cli
def test_real_compile_reports_errors(tmp_path):
    fw = project(GOOD.replace("pan.write(", "pan.wrte("))
    _f, res = compile_fw(fw, tmp_path)
    assert res.available and not res.ok and res.errors and res.errors[0]["file"] == "test.ino" and res.errors[0]["line"] == 16 and "wrte" in res.errors[0]["msg"]
    c = one(analyze(fw, None, res), "compiles (arduino-cli)")
    assert c.severity == "fail" and "test.ino:16:" in c.measured


@needs_cli
def test_a_missing_core_is_unavailable_not_a_crash(tmp_path):
    fw = Firmware("e", board="esp32")
    fw.source("e.ino", "void setup(){}\nvoid loop(){}\n")
    _f, res = compile_fw(fw, tmp_path)
    if res.available and res.ok:
        pytest.skip("the esp32 core is installed here")
    assert not res.available and "esp32:esp32 core is not installed" in res.message and "package_esp32_index.json" in res.message


# ---------------------------------------------------------------------------- outputs, data, diagram, edits


def test_tables_data_and_diagram(tmp_path):
    fw = project()
    fw.get_pin("M_PWM").pin = "D4"
    rep = analyze(fw)
    rows = outputs.pinout_rows(fw, rep)
    assert rows[0]["pin"] == "D9" and rows[0]["symbol"] == "PIN_PAN" and any(r["status"] == "fail" for r in rows)
    assert "| D9 | 9 | PIN_PAN |" in outputs.pinout_markdown(fw, rep) and outputs.pinout_csv(rows).startswith("pin,number,symbol")
    data = firmware_data(fw, rep)
    assert data["fqbn"] == "arduino:avr:uno" and data["sources"][-1]["name"] == "eitri_pins.h" and data["sources"][-1]["generated"] and "servo" in data["roles"]
    sc = diagram.build_scene(fw, rep, diagram.status_from(rep))
    root = ET.fromstring(diagram.scene_to_svg(sc))
    ns = "{http://www.w3.org/2000/svg}"
    ids = {(g.get("data-kind"), g.get("data-id")) for g in root.iter(ns + "g") if g.get("data-id")}
    assert {("pin", "D9"), ("pin", "D4"), ("pin", "A0"), ("peripheral", "PAN"), ("peripheral", "M")} <= ids
    diagram.scene_to_png(diagram.build_scene(fw, rep, diagram.status_from(rep), footer=diagram.footer_from(rep)), tmp_path / "p.png")


def test_edits():
    fw = project()
    notes = E.apply_edits(fw, [
        {"op": "set_pin", "net": "LED", "pin": "D12"},
        {"op": "set_pin", "net": "M_PWM", "role": "digital_out"},
        {"op": "add_pin", "pin": "D7", "net": "BUZZER", "direction": "out", "role": "digital_out"},
        {"op": "set_pin", "net": "BUZZER", "rename": "BEEP"},
        {"op": "remove_pin", "net": "POT"},
        {"op": "set_board", "board": "nano"},
        {"op": "set_board", "board": "mega"},
        {"op": "set_pin", "net": "NOPE", "pin": "D3"},
    ])
    assert [n.ok for n in notes] == [True, True, True, True, True, True, False, False]
    assert fw.get_pin("LED").pin == "D12" and fw.get_pin("BEEP").pin == "D7" and fw.board_key == "nano" and not any(p.net == "POT" for p in fw.pins)
    assert E.describe({"op": "set_pin", "net": "LED", "pin": "D12"}) == "pin LED: pin = D12"
    assert E.merge_edit({"op": "set_pin", "net": "LED", "pin": "D12"}, {"op": "set_pin", "net": "LED", "role": "pwm"}) == {"op": "set_pin", "net": "LED", "pin": "D12", "role": "pwm"}
    from eitri.edits import describe
    assert describe({"op": "set_board", "board": "nano"}) == "board = nano"
    # the header follows the edit: the code keeps using PIN_LED
    assert "const uint8_t PIN_LED = 12;" in fw.pins_header()


# ---------------------------------------------------------------------------- the reference example and the demo


def _ref_example(tmp_path):
    from eitri.agent.prompt import domain_reference_text
    blocks = re.findall(r"```python\n(.*?)```", domain_reference_text("firmware", {"BOARDS": ""}), re.S)
    assert len(blocks) == 1
    f = tmp_path / "ex" / "model.py"
    f.parent.mkdir()
    f.write_text(blocks[0], encoding="utf-8")
    return f


def test_reference_example_passes_statically(tmp_path):
    from eitri.model import load_model
    lm = load_model(_ref_example(tmp_path))
    fw = lm.build()
    rep = analyze(fw, lm.verify(fw))
    assert rep.ok and not rep.warnings, summarize(rep)


@needs_cli
def test_reference_example_and_demo_compile_green(tmp_path):
    from eitri.model import load_model
    demo = Path(__file__).resolve().parents[1] / "eitri" / "demos" / "servo-sweep" / "servo_sweep.py"
    for i, path in enumerate((_ref_example(tmp_path), demo)):
        lm = load_model(path)
        fw = lm.build()
        _f, res = compile_fw(fw, tmp_path / f"out{i}")
        rep = analyze(fw, lm.verify(fw), res)
        assert rep.ok and not rep.warnings, summarize(rep)


def test_demo_passes_statically():
    from eitri.model import load_model
    demo = Path(__file__).resolve().parents[1] / "eitri" / "demos" / "servo-sweep" / "servo_sweep.py"
    lm = load_model(demo)
    fw = lm.build()
    assert analyze(fw, lm.verify(fw)).ok
