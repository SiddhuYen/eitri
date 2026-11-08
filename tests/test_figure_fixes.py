"""The figure fixes from the component database (2026-11-07), the two new electronics checks, and checks switched off by an
unset input (they must show in the report, not pass silently)."""
from __future__ import annotations

import pytest

from eitri.checks import checks_not_run


def _by_name(checks, name):
    hits = [c for c in checks if c.name == name]
    assert len(hits) == 1, (name, [c.name for c in checks])
    return hits[0]


# --------------------------------------------------------------------------- fasteners and bearings


def test_countersinks_take_the_iso_10642_head():
    from eitri.lib.fasteners import countersink_dims, heat_set_insert_dims
    # ISO 10642:2020 dk max (= nominal): a countersink this size takes the head flush
    expected = {"M2": 4.7, "M2.5": 5.88, "M3": 6.72, "M4": 8.96, "M5": 11.2, "M6": 13.44, "M8": 17.92, "M10": 22.4, "M12": 26.88}
    assert {k: countersink_dims(k)[0] for k in expected} == expected
    # ruthex D3 hole diameters
    assert heat_set_insert_dims("M2.5")[0] == 4.0 and heat_set_insert_dims("M8")[0] == 9.6 and heat_set_insert_dims("M3")[0] == 4.0


def test_miniature_bearings_are_open_unless_named_shielded():
    from eitri.lib.bearings import bearing_dims
    # NSK E126i: 683 is 2 mm wide open, 3 mm shielded (ZZ); 608 is 7 mm either way
    assert bearing_dims("683") == (3, 7, 2) and bearing_dims("683ZZ") == (3, 7, 3) and bearing_dims("683-2RS") == (3, 7, 3)
    assert bearing_dims("MR85") == (5, 8, 2) and bearing_dims("mr85zz") == (5, 8, 2.5)
    assert bearing_dims("688") == (8, 16, 4) and bearing_dims("688ZZ") == (8, 16, 5)
    assert bearing_dims("608") == bearing_dims("608ZZ") == (8, 22, 7)
    assert bearing_dims("MR115") == bearing_dims("MR115ZZ") == (5, 11, 4)          # listed shielded only


# --------------------------------------------------------------------------- pneumatics


def test_pneumatic_defaults_follow_smc():
    from eitri.pneumatics.flow import DEFAULT_C, SMC_FLOW_CONTROL_C, SMC_SILENCER_C
    from eitri.pneumatics.model import LOAD_RATIOS, PRESSURE_RATINGS
    assert DEFAULT_C["silencer"][0] == 1.0 and SMC_SILENCER_C["M5"] == 1.0 and SMC_SILENCER_C["1/4"] == 3.0     # AN05, AN15
    assert DEFAULT_C["flow_control"][0] == 0.3 and SMC_FLOW_CONTROL_C["1/4"] == 1.4                            # AS: S / 5
    assert LOAD_RATIOS == {"moving": 0.5, "static": 0.7, "guided": 1.0}
    assert PRESSURE_RATINGS["regulator"][0] == 10.0


def test_a_speed_controllers_free_flow_is_no_more_than_its_throttle():
    from eitri.pneumatics import Circuit
    from eitri.pneumatics.checks import _comp_step
    c = Circuit("fc")
    fc = c.flow_control("FC1", meter="out", C=0.6, b=0.3)
    free = _comp_step(fc, "A", "B") if not fc.throttles("A", "B") else _comp_step(fc, "B", "A")
    assert free.C == 0.6 and "free flow" in free.note
    bare = c.flow_control("FC2", meter="out")
    free = _comp_step(bare, "A", "B") if not bare.throttles("A", "B") else _comp_step(bare, "B", "A")
    assert free.C == 0.3 and free.estimated


# --------------------------------------------------------------------------- electronics: unregulated supply into a board


def _vin(make):
    from eitri.electronics import Circuit
    from eitri.electronics.checks import run_checks
    c = Circuit("t")
    u = c.board("U1", "uno", powered="vin")
    b = make(c)
    c.connect(b["+"], u["VIN"])
    c.connect(b["-"], u["GND"])
    return run_checks(c)[0]


def test_a_car_battery_straight_on_vin_fails_at_its_charging_voltage():
    checks = _vin(lambda c: c.battery("BATT", "lead-acid", 6))
    assert _by_name(checks, "VIN U1 (powered on VIN)").severity == "pass"              # 12 V nominal: inside 7-12 V
    hi = _by_name(checks, "VIN U1 at BATT's highest voltage")
    assert hi.severity == "fail" and "14.5 V" in hi.measured and "Yuasa NP7-12" in hi.measured and "regulator" in hi.measured


def test_a_battery_inside_the_vin_range_passes():
    checks = _vin(lambda c: c.battery("BATT", "lipo", 2))                              # 7.2 V nominal, 8.4 V full
    hi = _by_name(checks, "VIN U1 at BATT's highest voltage")
    assert hi.severity == "pass" and "8.4 V" in hi.measured and "Molicel" in hi.measured


def test_a_supply_that_looks_like_a_battery_says_its_full_charge_is_not_modelled():
    checks = _vin(lambda c: c.supply("BATT", 12.0))
    w = _by_name(checks, "VIN U1 at BATT's highest voltage")
    assert w.severity == "warn" and w.not_run == ["BATT.v_max"] and w.measured.startswith("not run: ")
    checks = _vin(lambda c: c.supply("PSU", 9.0))                                       # a bench supply: nothing to add
    assert not [x for x in checks if "highest voltage" in x.name]


def test_a_battery_on_the_5v_pin_fails():
    from eitri.electronics import Circuit
    from eitri.electronics.checks import run_checks
    c = Circuit("t")
    u = c.board("U1", "uno")
    b = c.supply("PACK", 6.0, v_max=6.4, battery=True)                                 # 4 x AA straight on 5V
    c.connect(b["+"], u["5V"])
    c.connect(b["-"], u["GND"])
    chk = _by_name(run_checks(c)[0], "5V pin U1 fed by PACK")
    assert chk.severity == "fail" and "5.5 V" in chk.measured and "bypasses the board's regulator" in chk.measured


def test_battery_api():
    from eitri.electronics import Circuit
    from eitri.electronics.model import CircuitError
    c = Circuit("t")
    b = c.battery("B", "li-ion", 3)
    assert b.spec["volts"] == 10.8 and b.spec["v_max"] == 12.6 and b.srcs["v_max"] == "molicel-p26a" and "up to 12.6 V" in b.value_text
    with pytest.raises(CircuitError, match="unknown chemistry"):
        c.battery("X", "nicad", 4)
    with pytest.raises(CircuitError, match="below its volts"):
        c.supply("S", 12, v_max=10)


# --------------------------------------------------------------------------- electronics: gate pull-down


def _gate(pulldown: bool):
    from eitri.electronics import Circuit
    from eitri.electronics.checks import run_checks
    c = Circuit("pump")
    u = c.board("U1", "uno")
    q = c.mosfet("Q1", "IRLZ44N")
    rg = c.resistor("RG", 220)
    psu = c.supply("PSU", 12, 2)
    m = c.motor("M1", 12, 1.0)
    d = c.diode("D1", "1N4007")
    c.gpio(u["D9"], "out")
    c.connect(u["D9"], rg["1"])
    c.connect(rg["2"], q["G"])
    c.connect(q["S"], u["GND"], psu["-"])
    c.connect(psu["+"], m["+"], d["K"])
    c.connect(m["-"], q["D"], d["A"])
    if pulldown:
        rp = c.resistor("RPD", 100_000)
        c.connect(rp["1"], q["G"])
        c.connect(rp["2"], u["GND"])
    return _by_name(run_checks(c)[0], "gate pull-down Q1 (IRLZ44N)")


def test_a_gate_driven_from_an_avr_pin_needs_a_pull_down():
    bad = _gate(False)
    assert bad.severity == "warn" and "high impedance" in bad.measured and "ATmega328P" in bad.measured and "Q1.G to GND" in bad.measured
    good = _gate(True)
    assert good.severity == "pass" and "RPD" in good.measured and "tri-stated through reset" in good.measured


# --------------------------------------------------------------------------- switched-off checks


def _arm(**act):
    from eitri.mechanism import Actuator, Mechanism
    m = Mechanism("arm")
    m.mount("edge")
    m.link("base", fixed=True)
    m.link("a", length_mm=100, mass_g=20)
    m.joint("j1", "base", "a", axis=(0, -1, 0), limit_deg=(0, 180), actuator=Actuator("MG996R", stall_kgcm=9.4, mass_g=55, **act))
    return m


def test_an_actuator_without_travel_is_a_visible_check_not_run():
    from eitri.mechanism.report import analyze, summarize
    good = analyze(_arm(travel_deg=180))
    assert _by_name(good.checks, "joint limits j1").severity == "pass" and good.to_dict()["checks_not_run"] == []
    # the bench miss: travel_deg=None switched the joint-travel check off and every check passed
    off = analyze(_arm(travel_deg=None))
    c = _by_name(off.checks, "joint limits j1")
    assert c.severity == "warn" and c.not_run == ["travel_deg"] and c.measured.startswith("not run: joint travel: MG996R declares no travel_deg")
    listed = off.to_dict()["checks_not_run"]
    assert listed == [{"name": "joint limits j1", "category": "limits", "severity": "warn", "inputs": ["travel_deg"], "refs": ["j1"],
                       "why": c.measured}]
    assert "Not run (an input is unset): joint limits j1 (travel_deg not set)" in summarize(off)
    # gearing past the travel still fails as before
    geared = analyze(_arm(travel_deg=180, gear_ratio=12))
    assert _by_name(geared.checks, "joint limits j1").severity == "fail"


def test_a_declared_continuous_actuator_passes_but_stays_listed():
    from eitri.mechanism.report import analyze
    rep = analyze(_arm(continuous=True))
    c = _by_name(rep.checks, "joint limits j1")
    assert c.severity == "pass" and "declared continuous" in c.measured
    assert rep.to_dict()["checks_not_run"][0]["inputs"] == ["travel_deg (declared continuous)"]


def test_other_domains_list_their_switched_off_checks():
    from eitri.electronics import Circuit
    from eitri.electronics.report import analyze as e_analyze
    c = Circuit("t")
    psu = c.supply("PSU", 5.0)                                    # no current_A: the rail budget is not graded
    ld = c.load("FAN", 0.2)                                       # no v_min / v_max: the supply range is not graded
    c.connect(psu["+"], ld["+"])
    c.connect(psu["-"], ld["-"])
    names = {x["name"]: x["inputs"] for x in e_analyze(c).to_dict()["checks_not_run"]}
    assert names["supply voltage FAN.+"] == ["FAN.v_min", "FAN.v_max"] and any(v == ["PSU.current_A"] for v in names.values())

    from eitri.pneumatics import Circuit as PCircuit
    from eitri.pneumatics.report import analyze as p_analyze
    pc = PCircuit("p")
    air = pc.supply("AIR", pressure_bar=6.0)                      # no flow_l_min
    v = pc.valve("V1", "3/2", C=0.8, b=0.3)
    cyl = pc.cylinder("C1", 25, 10, 25, acting="single", load_N=50, cycles_per_min=10)       # no spring_N
    pc.tube("T1", air["OUT"], v["P"], id_mm=6, length_m=1, C=2.0)
    pc.tube("T2", v["A"], cyl["CAP"], id_mm=6, length_m=1, C=2.0)
    pc.silencer(v["R"], C=1, b=0.3)
    inputs = sorted(i for x in p_analyze(pc).to_dict()["checks_not_run"] for i in x["inputs"])
    assert inputs == ["AIR.flow_l_min", "C1.spring_N"]

    from eitri.harness import Connector, Harness, Wire
    from eitri.harness.report import analyze as h_analyze
    h = Harness("h")
    a = h.add(Connector("A", "ACME-9", pins=["1", "2"]))          # unknown type, no rating_a
    b = h.add(Connector("B", "JST-XH", pins=["1", "2"]))
    h.net("V", current_A=1.0)                                     # no voltage_V: the drop percentage is not graded
    h.net("G", current_A=1.0, round_trip=False)
    h.connect(a["1"], b["1"], Wire(22, "RD", 300), net="V")
    h.connect(a["2"], b["2"], Wire(22, "BK", 300), net="G")
    inputs = {i for x in h_analyze(h).to_dict()["checks_not_run"] for i in x["inputs"]}
    assert "A.rating_a" in inputs and any(i.endswith(".voltage_V") for i in inputs)
