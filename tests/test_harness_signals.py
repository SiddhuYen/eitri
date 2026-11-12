"""Signal nets graded against the devices at their ends (eitri.harness.signals): a microcontroller pin, a PLC digital input of an
IEC 61131-2 type, a sensor's switching output. No declared end: the check does not run."""
from __future__ import annotations

import pytest

from eitri.harness import Connector, Harness, HarnessError, Wire
from eitri.harness.report import analyze
from eitri.harness.signals import IEC_TYPES


def _h(cur=0.015, volts=24.0, iec_type=None, sensor=None, sensor_kw=None):
    h = Harness("plc")
    plc = h.add(Connector("K1", "BARE", pins=["DI0", "M"]))
    b1 = h.add(Connector("B1", "BARE", pins=["1", "3", "4"]))
    h.net("B1_OUT", current_A=cur, voltage_V=volts, signal=True)
    h.connect(b1["4"], plc["DI0"], Wire("0.25mm2", "BK", 5000), net="B1_OUT")
    if iec_type is not None:
        h.plc_input(plc["DI0"], iec_type=iec_type, part="customer PLC")
    if sensor is not None or sensor_kw:
        h.sensor_output(b1["4"], part=sensor or "", **(sensor_kw or {}))
    return h


def _c(h, name="signal net B1_OUT"):
    return next(c for c in analyze(h).checks if c.name == name)


def _input(h):
    return next(c for c in analyze(h).checks if c.name.startswith("signal net B1_OUT input"))


def test_iec_types_as_read_from_the_ti_chart():
    assert (IEC_TYPES[1].on_V_min, IEC_TYPES[1].on_mA_max, IEC_TYPES[1].off_mA_max) == (15.2, 15.0, 0.4)
    assert (IEC_TYPES[2].on_V_min, IEC_TYPES[2].on_mA_max, IEC_TYPES[2].off_mA_max) == (11.2, 30.0, 1.9)
    assert (IEC_TYPES[3].on_V_min, IEC_TYPES[3].on_mA_max, IEC_TYPES[3].off_mA_max) == (11.2, 15.0, 1.3)
    assert all(t.on_V_max == 30.0 for t in IEC_TYPES.values())


def test_no_declared_end_is_not_run_never_the_arduino_figure():
    c = _c(_h())
    assert c.status == "not_run" and "no device is declared" in c.measured and "20 mA" not in c.measured
    assert c.source and c.source[0].basis == "rule" and c.rule == "harness.signal-net"


def test_plc_input_types_grade_current_and_voltage():
    c = _c(_h(cur=0.015, iec_type=1))
    assert c.severity == "pass" and "IEC 61131-2 type 1" in c.measured and "Arduino" not in c.measured
    assert any(s.basis == "document" and "iso1228" in s.url for s in c.source), c.source
    assert _c(_h(cur=0.020, iec_type=3)).severity == "fail"              # more than a type 3 input draws when ON (15 mA)
    assert _c(_h(cur=0.020, iec_type=2)).severity == "pass"              # type 2 inputs draw up to 30 mA
    assert _input(_h(volts=24, iec_type=1)).severity == "pass"
    assert _input(_h(volts=12, iec_type=1)).severity == "fail"           # 12 V never reads ON on a type 1 input (15.2 V)
    assert _input(_h(volts=12, iec_type=3)).severity == "pass"           # ... but does on type 3 (11.2 V)
    assert _input(_h(volts=36, iec_type=3)).severity == "fail"
    nv = _input(_h(volts=None, iec_type=3))
    assert nv.status == "not_run" and nv.not_run == ["B1_OUT.voltage_V"]
    assert _input(_h(iec_type=3)).rule == "harness.signal-net-input"
    with pytest.raises(HarnessError, match="1, 2 or 3"):
        _h(iec_type=4)


def test_catalogue_sensor_outputs_use_their_datasheets():
    c = _c(_h(cur=0.015, sensor="Festo SMT-8M-A-PS-24V-E-0,3-M8D"))
    assert c.severity == "pass" and "100 mA" in c.measured
    assert any(s.basis == "document" and "574334" in s.url for s in c.source)
    assert _c(_h(cur=0.12, sensor="Festo SMT-8M-A-PS-24V-E-0,3-M8D")).severity == "fail"
    assert _c(_h(cur=0.045, sensor="SMC D-M9B")).severity == "fail"       # the D-M9B carries 40 mA at most
    with pytest.raises(HarnessError, match="datasheet"):
        _h(sensor="SMC D-M9B", sensor_kw={"max_output_A": 0.1})          # a catalogue figure cannot be overridden
    # both ends declared: the lower limit binds (a type 3 input's 15 mA under the sensor's 100 mA)
    both = _c(_h(cur=0.016, iec_type=3, sensor="Festo SMT-8M-A-PS-24V-E-0,3-M8D"))
    assert both.severity == "fail" and "15 mA" in both.measured


def test_two_wire_sensor_leakage_and_drop_against_the_input_type():
    # D-M9B: 0.8 mA leakage. A type 1 input is sure to read OFF only up to 0.4 mA: it can stay ON. Type 3 (1.3 mA) is fine.
    bad = _input(_h(cur=0.01, iec_type=1, sensor="SMC D-M9B"))
    assert bad.severity == "fail" and "leaks 0.8 mA" in bad.measured
    ok = _input(_h(cur=0.01, iec_type=3, sensor="SMC D-M9B"))
    assert ok.severity == "pass" and "20 V" in ok.measured                # 24 V less its 4 V drop
    low = _input(_h(cur=0.01, volts=15, iec_type=3, sensor="SMC D-M9B"))
    assert low.severity == "fail" and "11 V at the input" in low.measured
    unset = _input(_h(cur=0.01, iec_type=3, sensor_kw={"wires": 2, "max_output_A": 0.04}))
    assert unset.status == "not_run" and "B1.4.drop_V" in unset.not_run


def test_model_given_sensor_rating_and_a_missing_one():
    c = _c(_h(cur=0.015, sensor_kw={"max_output_A": 0.2}))
    assert c.severity == "pass" and c.source[0].basis == "model"
    nr = _c(_h(cur=0.015, sensor_kw={"wires": 3}))
    assert nr.status == "not_run"


def test_mcu_pin_declaration_and_board_connectors():
    h = Harness("m")
    uno = h.add(Connector("UNO", "ARDUINO-UNO", pins=["D9", "GND"]))
    led = h.add(Connector("L1", "BARE", pins=["A"]))
    h.net("SIG", current_A=0.03, voltage_V=5, signal=True)
    h.connect(uno["D9"], led["A"], Wire(22, "OG", 100), net="SIG")
    c = _c(h, "signal net SIG")
    assert c.severity == "fail" and "20 mA" in c.measured                # a board connector is declared by its type
    with pytest.raises(HarnessError, match="unknown board"):
        Harness("x").mcu_pin(Connector("X", "BARE", pins=["1"])["1"], board="esp32")


def test_renaming_keeps_the_declaration():
    h = _h(iec_type=3)
    h.rename_connector("K1", "PLC")
    h.rename_pin("PLC", "DI0", "I0.0")
    assert "PLC.I0.0" in [str(r) for r in h.signal_ends]
    assert _input(h).severity == "pass"
    with pytest.raises(HarnessError, match="declared device"):
        h2 = _h(iec_type=3)
        h2.remove_link(h2.links[0].id)
        h2.remove_pin("K1", "DI0")
