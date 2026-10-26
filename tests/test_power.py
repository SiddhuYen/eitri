"""The power budget, shared by the wire-harness and firmware domains: a board's 5V / 3V3 supply pins against what they can supply
(sourced limits in eitri/power.py), and a shared ground for everything that takes a signal from a board. The backlog's examples:
an MG996R servo (2.5 A stall at 6 V, TowerPro MG996R datasheet) on the Uno's 5V pin must fail; the same servo on its own 6 V supply,
ground shared with the Uno, must pass."""
from __future__ import annotations

import pytest

from eitri import power
from eitri.firmware import Firmware, FirmwareError
from eitri.firmware.report import analyze as analyze_fw
from eitri.harness import Connector, Harness, HarnessError, Wire
from eitri.harness.outputs import bom
from eitri.harness.report import analyze as analyze_h

MG996R_STALL_A = 2.5        # TowerPro MG996R datasheet: stall current 2.5 A (6 V)


def test_limits_and_their_sources():
    usb = power.supply_limit("uno", "5V")
    assert usb.limit_A == 0.5 and "polyfuse" in usb.basis and "MF-MSMF050-2" in usb.source
    assert power.supply_limit("Arduino Uno", "+3.3V").limit_A == 0.050 and "A000066-full-pinout" in power.supply_limit("uno", "3V3").source
    assert power.supply_limit("nano", "3V3").limit_A == 0.050 and "A000005-full-pinout" in power.supply_limit("nano", "3V3").source
    assert power.supply_limit("nano", "5V").limit_A == 0.5 and "USB 2.0" in power.supply_limit("nano", "5V").source
    assert power.supply_limit("nano", "5V", "vin", 9).limit_A is None                     # not published: ungraded, never guessed
    assert power.supply_limit("esp32", "3V3").limit_A is None
    # the Uno on VIN: the regulator's rating, or its heat (PD = (VIN - 5) x I, 46 C/W, 125 C junction, 25 C ambient)
    assert power.supply_limit("uno", "5V", "vin").limit_A == 0.8
    hot = power.supply_limit("uno", "5V", "vin", vin_V=12)
    assert hot.limit_A == pytest.approx((125 - 25) / (46 * 7), abs=1e-3) and "heat" in hot.basis
    assert power.supply_limit("uno", "5V", "vin", vin_V=6.5).limit_A == 0.8
    assert power.supply_limit("uno", "5V", "vin", vin_V=5).limit_A == 0.0
    for lim in (usb, hot, power.supply_limit("nano", "5V")):
        assert lim.source, "every graded limit names its source"
    assert power.grade(0.6, usb) == "fail" and power.grade(0.45, usb) == "warn" and power.grade(0.2, usb) == "pass"


def _uno_direct(current=MG996R_STALL_A, **board):
    h = Harness("MG996R on the Uno 5V pin")
    uno = h.add(Connector("UNO", "ARDUINO-UNO", pins=["5V", "GND", "D9"], **board))
    srv = h.add(Connector("SERVO", "SOLDER", pins=["GND", "V+", "SIG"]))
    h.net("SERVO_5V", current_A=current, voltage_V=5)
    h.net("SIG", current_A=0.001)
    h.connect(uno["5V"], srv["V+"], Wire(22, "RD", 200), net="SERVO_5V")
    h.connect(srv["GND"], uno["GND"], Wire(22, "BK", 200), net="SERVO_5V")
    h.connect(uno["D9"], srv["SIG"], Wire(26, "OG", 200), net="SIG")
    return h


def _separate(ground_shared=True):
    h = Harness("MG996R on its own 6 V supply")
    uno = h.add(Connector("UNO", "ARDUINO-UNO", pins=["GND", "D9"] if ground_shared else ["D9"]))
    psu = h.add(Connector("PSU", "BARE", pins=["+6V", "GND"]))
    srv = h.add(Connector("SERVO", "SOLDER", pins=["GND", "V+", "SIG"]))
    g = h.splice("GND_JOIN")
    h.net("SERVO_6V", current_A=MG996R_STALL_A, voltage_V=6)
    h.net("GND", current_A=MG996R_STALL_A, voltage_V=6, round_trip=False)
    h.net("SIG", current_A=0.001)
    h.connect(psu["+6V"], srv["V+"], Wire(20, "RD", 300), net="SERVO_6V")
    h.connect(srv["GND"], g, Wire(20, "BK", 300), net="GND")
    h.connect(g, psu["GND"], Wire(20, "BK", 100), net="GND")
    if ground_shared:
        h.connect(g, uno["GND"], Wire(24, "BK", 150), net="GND", current_A=0.01)
    h.connect(uno["D9"], srv["SIG"], Wire(26, "OG", 200), net="SIG")
    return h


def _power_checks(rep):
    return {c.name: c for c in rep.checks if c.category == "power"}


def test_harness_servo_on_the_uno_5v_pin_fails():
    pc = _power_checks(analyze_h(_uno_direct()))
    c = pc["supply UNO.5V (Arduino Uno on USB)"]
    assert c.severity == "fail" and "2.5 A drawn from UNO.5V" in c.measured and "can supply 0.5 A" in c.measured
    assert "separate supply" in c.measured and "ground" in c.measured and "source:" in c.measured and c.refs[0] == "UNO"
    assert pc["shared ground SERVO - UNO"].severity == "pass"


def test_harness_servo_on_its_own_supply_passes():
    rep = analyze_h(_separate())
    pc = _power_checks(rep)
    assert not any(c.severity == "fail" for c in pc.values()), pc
    assert pc["shared ground SERVO - UNO"].severity == "pass" and not any(n.startswith("supply ") for n in pc)
    assert not rep.failed, [(c.name, c.measured) for c in rep.failed]


def test_harness_shared_ground_missing_fails():
    pc = _power_checks(analyze_h(_separate(ground_shared=False)))
    c = pc["shared ground SERVO - UNO"]
    assert c.severity == "fail" and "no GND pin" in c.measured


def test_harness_grades_by_power_path_and_warns_near_the_limit():
    near = _power_checks(analyze_h(_uno_direct(current=0.45)))["supply UNO.5V (Arduino Uno on USB)"]
    assert near.severity == "warn" and "own draw" in near.measured
    ok = _power_checks(analyze_h(_uno_direct(current=0.2)))["supply UNO.5V (Arduino Uno on USB)"]
    assert ok.severity == "pass"
    vin = _power_checks(analyze_h(_uno_direct(current=0.6, powered="vin", vin_V=12)))["supply UNO.5V (Arduino Uno on VIN 12 V)"]
    assert vin.severity == "fail" and "heat at 12 V" in vin.measured
    h = _uno_direct()
    h.nets["SERVO_5V"].current_A = None                                     # no current: say so, never pass silently
    assert _power_checks(analyze_h(h))["supply UNO.5V (Arduino Uno on USB)"].severity == "warn"
    with pytest.raises(HarnessError, match="powered"):
        Connector("UNO", "ARDUINO-UNO", pins=["5V"], powered="battery")


def test_harness_board_in_the_bom():
    rows = bom(_uno_direct())
    assert any(r["description"].startswith("Arduino Uno R3 board") for r in rows)


SKETCH = '#include <Servo.h>\n#include "eitri_pins.h"\nServo s;\nvoid setup(){ s.attach(PIN_ARM); }\nvoid loop(){ s.write(90); }\n'


def _fw(**servo):
    fw = Firmware("arm", board="uno")
    fw.servo("ARM", pin="D9", **servo)
    fw.source("arm.ino", SKETCH)
    return fw


def test_firmware_servo_on_the_5v_pin_fails_and_on_its_own_supply_passes():
    bad = _power_checks(analyze_fw(_fw(power="5V", current_A=MG996R_STALL_A)))
    c = bad["supply 5V pin (uno, on USB)"]
    assert c.severity == "fail" and "can supply 0.5 A" in c.measured and 'power="external"' in c.measured
    good = _power_checks(analyze_fw(_fw(power="external", current_A=MG996R_STALL_A)))
    assert list(good) == ["loads on their own supply"] and good["loads on their own supply"].severity == "pass"
    assert "GND" in good["loads on their own supply"].measured
    assert not _power_checks(analyze_fw(_fw()))                             # nothing declared: nothing graded


def test_firmware_loads_add_up_and_unknowns_warn():
    fw = _fw(power="5V", current_A=0.3)
    fw.load("OLED", "3V3", 0.02)
    fw.load("STRIP", "5V", 0.3)
    pc = _power_checks(analyze_fw(fw))
    assert pc["supply 5V pin (uno, on USB)"].severity == "fail" and "0.6 A drawn" in pc["supply 5V pin (uno, on USB)"].measured
    assert pc["supply 3V3 pin (uno, on USB)"].severity == "pass"
    assert _power_checks(analyze_fw(_fw(power="5V")))["supply 5V pin (uno, on USB)"].severity == "warn"
    with pytest.raises(FirmwareError, match="power must be"):
        _fw(power="9V", current_A=1)
    with pytest.raises(FirmwareError, match="needs power"):
        _fw(current_A=1)
