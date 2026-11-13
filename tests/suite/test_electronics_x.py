"""The electronics file of a combined project against the other domains (eitri_suite.electronics): a pass and a fail case for each
check, and "not run" (never a pass) for every missing input. The reference is the Uno servo arm (eitri_suite/demos/uno-arm), whose
elec.py runs every check and passes them all (one warning: a Dupont pin at 83% of its rating at the servos' stall)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eitri.model import load_model
from eitri_suite import electronics as E
from eitri_suite import elec_sources as es

DEMO = Path(__file__).resolve().parents[2] / "eitri_suite" / "demos" / "uno-arm"


def variant(tmp_path: Path, name: str, edits: dict[str, list[tuple[str, str]]] | None = None, drop: tuple[str, ...] = ()) -> Path:
    d = tmp_path / name
    shutil.copytree(DEMO, d)
    for f in drop:
        (d / f).unlink()
    for f, subs in (edits or {}).items():
        text = (d / f).read_text(encoding="utf-8")
        for a, b in subs:
            assert a in text, (f, a)
            text = text.replace(a, b)
        (d / f).write_text(text, encoding="utf-8")
    return d


def run(folder: Path):
    lm = load_model(folder / "elec.py")
    return E.run(lm.module, lm.build(), folder / "elec.py")


def one(checks, needle):
    hits = [c for c in checks if needle in c.name]
    assert len(hits) == 1, (needle, [c.name for c in checks])
    return hits[0]


@pytest.fixture(scope="module")
def demo():
    return run(DEMO)


# ---------------------------------------------------------------------------- the reference example

def test_the_uno_arm_runs_every_check_and_passes(demo):
    checks, data = demo
    assert data["not_run"] == 0
    assert not [c for c in checks if c.severity == "fail"], [(c.name, c.measured) for c in checks if c.severity == "fail"]
    warns = [c.name for c in checks if c.severity == "warn"]
    assert warns == []                          # core 0.8.0: the servo plugs are Micro-Fit 3.0 (7 A/pin): no 83 % Dupont warning any more
    assert len(checks) == 24 and all(c.category == "integration" for c in checks)      # 23 + power: what moves together (circuit)
    for needle in ("pins: harness", "pins: firmware", "harness power: wire ampacity", "harness power: voltage at the loads",
                   "harness power: PSU supplies", "mounting: UNO hole pattern", "mounting: UNO outline", "mounting: UNO clearance",
                   "actuators: every actuated joint", "actuators: stall current", "dissipation:",
                   "power: the supply covers the actuators that move together (circuit)"):
        assert any(needle in c.name for c in checks), needle


def test_every_number_says_where_it_comes_from(demo):
    checks, _ = demo
    by = {c.name: c for c in checks}
    assert "TowerPro MG996R" in by["actuators: stall current vs PSU.+"].measured and "7.5 A" in by["actuators: stall current vs PSU.+"].measured
    v = one(checks, "voltage at the loads on V5")
    assert "NEC Chapter 9 Table 8" in v.measured and "TowerPro MG996R" in v.measured and "SERVO_EL" in v.measured
    assert "PowerStream" in one(checks, "wire ampacity on V5").measured
    h = one(checks, "UNO hole pattern")
    assert "UNO-TH_Rev3e.brd" in h.measured and "Ø3.2" in h.measured and "ISO 68-1" in es.SOURCES["iso-metric-thread"]["quote"]


def test_the_uno_outline_is_its_board_file(demo):
    ol = es.OUTLINES["uno"]
    assert ol["size"][:2] == (68.58, 53.34) and ol["hole_d"] == 3.2
    assert ol["holes"] == [(-20.32, -24.13), (-19.05, 24.13), (31.75, 8.89), (31.75, -19.05)]
    from eitri_suite.assembly import BOARDS                       # the Assembly view draws the same holes
    assert [tuple(round(v, 2) for v in h) for h in BOARDS["uno"]["holes"]] == ol["holes"]


# ---------------------------------------------------------------------------- pins: harness

def test_a_harness_wire_the_circuit_does_not_have_fails(tmp_path):
    d = variant(tmp_path, "miswire", {"wiring.py": [('h.connect(uno[pin], sv["SIG"], Wire(22, "OG", length), net=f"SIG_{tag}", name=f"{tag}_SIG")',
                                                     'h.connect(uno[pin], sv["SIG"] if tag != "EL" else v5, Wire(22, "OG", length), net=f"SIG_{tag}", name=f"{tag}_SIG")')]})
    checks, _ = run(d)
    bad = [c for c in checks if c.name.startswith("pins: harness joins only one circuit net")]
    assert len(bad) == 1 and bad[0].severity == "fail" and "UNO.D11 on SIG_EL" in bad[0].measured and "on V5" in bad[0].measured
    sig = one(checks, "circuit net SIG_EL is carried")
    assert sig.severity == "fail" and "SERVO_EL.SIG (unwired)" in sig.measured


def test_a_circuit_connection_the_harness_lacks_fails(tmp_path):
    d = variant(tmp_path, "noground", {"wiring.py": [('    h.connect(gnd, uno["GND"], Wire(22, "BK", 120), net="GND_PWR", current_A=0.05, name="UNO_GND")      # the common ground\n', '')]})
    c = one(run(d)[0], "circuit net GND is carried")
    assert c.severity == "fail" and "UNO.GND (unwired)" in c.measured and "Fix: a wire" in c.measured


def test_a_harness_pin_the_part_does_not_have_fails(tmp_path):
    d = variant(tmp_path, "badpin", {"wiring.py": [('pins={"D9": "D9", "D10": "D10", "D11": "D11", "GND": "GND"}',
                                                    'pins={"D9": "D9", "D10": "D10", "D11": "D11", "GND": "GND", "D99": "D99"}')]})
    c = one(run(d)[0], "harness connectors are circuit parts")
    assert c.severity == "fail" and "UNO.D99 is not a pin of circuit part UNO" in c.measured


def test_no_harness_is_not_run(tmp_path):
    checks, data = run(variant(tmp_path, "noharness", drop=("wiring.py",)))
    for needle in ("harness connectors are circuit parts", "rail currents in the harness"):
        c = one(checks, needle)
        assert c.severity == "warn" and c.measured == "not run: COUPLING harness='wiring.py' is not a file of the project"
    assert data["not_run"] >= 2


# ---------------------------------------------------------------------------- pins: firmware

def test_a_firmware_pin_on_an_unwired_board_pin_fails(tmp_path):
    d = variant(tmp_path, "fwpin", {"fw.py": [('fw.servo("ELBOW", "D11", power="external")', 'fw.servo("ELBOW", "D6", power="external")'),
                                              ('"ELBOW": "UNO.D11"', '"ELBOW": "UNO.D6"')]})
    checks, _ = run(d)
    c = one(checks, "firmware pin ELBOW matches")
    assert c.severity == "fail" and "nothing is wired to UNO.D6" in c.measured
    g = one(checks, "every circuit GPIO is in the firmware")
    assert g.severity == "warn" and "D11 (net SIG_EL)" in g.measured


def test_a_firmware_output_the_circuit_calls_an_input_fails(tmp_path):
    d = variant(tmp_path, "mode", {"elec.py": [('c.gpio(uno[pin], "out")', 'c.gpio(uno[pin], "in" if tag == "YAW" else "out")')]})
    c = one(run(d)[0], "firmware pin YAW matches")
    assert c.severity == "fail" and "declares it an input" in c.measured


def test_servo_power_must_be_where_the_firmware_says(tmp_path):
    d = variant(tmp_path, "power", {"fw.py": [('fw.servo("YAW", "D9", power="external")', 'fw.servo("YAW", "D9", power="5V", current_A=2.5)')]})
    c = one(run(d)[0], "firmware pin YAW matches")
    assert c.severity == "fail" and "runs from the board's 5V; the circuit powers SERVO_YAW from V5" in c.measured


def test_a_different_board_fails(tmp_path):
    d = variant(tmp_path, "board", {"elec.py": [('c.board("UNO", "uno", powered="usb")', 'c.board("UNO", "nano", powered="usb")')]})
    c = one(run(d)[0], "firmware board is the circuit's board")
    assert c.severity == "fail" and "firmware board uno, circuit board UNO (nano)" in c.measured


def test_no_firmware_is_not_run(tmp_path):
    c = one(run(variant(tmp_path, "nofw", drop=("fw.py",)))[0], "firmware pins match the circuit")
    assert c.severity == "warn" and c.measured == "not run: COUPLING firmware='fw.py' is not a file of the project"


# ---------------------------------------------------------------------------- harness power

def test_a_lead_too_thin_for_the_stall_current_fails_ampacity(tmp_path):
    d = variant(tmp_path, "thin", {"wiring.py": [('h.connect(fz["LOAD"], sv["V+"], Wire(18, "RD", length)', 'h.connect(fz["LOAD"], sv["V+"], Wire(28, "RD", length)')]})
    c = one(run(d)[0], "wire ampacity on V5")
    assert c.severity == "fail" and "YAW_V 28 AWG 2.5 A of 1.4 A" in c.measured and "Fix: a heavier gauge" in c.measured


def test_a_long_lead_leaves_the_servo_under_its_minimum_voltage(tmp_path):
    d = variant(tmp_path, "long", {"wiring.py": [("PARAMS = dict(servo_a=2.5, lead_mm=320)", "PARAMS = dict(servo_a=2.5, lead_mm=900)")]})
    c = one(run(d)[0], "voltage at the loads on V5")
    assert c.severity == "fail" and "SERVO_EL gets 4.7" in c.measured and "it needs 4.8 V" in c.measured     # 18 AWG leads now
    assert "fuse drop" in c.measured and "Littelfuse" in c.measured                                       # the MINI fuse's drop counts


def test_the_harness_declaring_less_than_the_circuit_draws_warns(tmp_path):
    d = variant(tmp_path, "declared", {"wiring.py": [("PARAMS = dict(servo_a=2.5, lead_mm=320)", "PARAMS = dict(servo_a=1.0, lead_mm=320)")]})
    c = one(run(d)[0], "the harness grades V5 on the circuit's currents")
    assert c.severity == "warn" and "YAW_V 1 A declared, 2.5 A in the circuit" in c.measured


def test_a_supply_too_small_for_what_the_harness_carries_fails(tmp_path):
    d = variant(tmp_path, "small", {"elec.py": [("PARAMS = dict(psu_A=10.0)", "PARAMS = dict(psu_A=6.0)")]})
    checks, _ = run(d)
    c = one(checks, "PSU supplies what the harness carries")
    assert c.severity == "fail" and "7.5 A leaves PSU.+" in c.measured and "6 A available" in c.measured
    a = one(checks, "actuators: stall current vs PSU.+")
    assert a.severity == "fail" and "7.5 A" in a.measured and "of 6 A" in a.measured


def test_the_bom_supply_is_the_tighter_limit(tmp_path):
    d = variant(tmp_path, "bom", {"elec.py": [('part_number="PSU-5V-10A"', 'part_number="PSU-5V-4A"')],
                                  "model.py": [('("PSU-5V-10A", 1)', '("PSU-5V-4A", 1)')]})
    c = one(run(d)[0], "PSU supplies what the harness carries")
    assert c.severity == "fail" and "4 A available" in c.measured and "the BOM's Adafruit 1466" in c.measured and "adafruit.com/product/1466" in c.measured


def test_a_bom_supply_the_circuit_does_not_name_is_not_run(tmp_path):
    d = variant(tmp_path, "bomname", {"elec.py": [('part_number="PSU-5V-10A"', 'part_number="bench supply"')]})
    c = one(run(d)[0], "the BOM's power supply is a circuit supply")
    assert c.severity == "warn" and c.measured.startswith("not run: the CAD file buys PSU-5V-10A")


def test_a_supply_with_no_rating_is_not_run(tmp_path):
    d = variant(tmp_path, "norating", {"elec.py": [('c.supply("PSU", 5.0, current_A=p.psu_A, part_number="PSU-5V-10A")', 'c.supply("PSU", 5.0)')]})
    checks, _ = run(d)
    c = one(checks, "PSU supplies what the harness carries")
    assert c.severity == "warn" and c.measured.startswith("not run: PSU has no current rating")
    assert one(checks, "actuators: stall current").measured.startswith("not run:")


def test_wire_currents_follow_the_tree():
    class L:
        def __init__(self, i, a, b):
            self.id, self.a, self.b = i, a, b
    links = [L("trunk", "src", "bus"), L("a", "bus", "x"), L("b", "bus", "y")]
    cur = E.wire_currents(links, {"src"}, {"x": 2.0, "y": 0.5})
    assert cur == {"trunk": 2.5, "a": 2.0, "b": 0.5}
    loop = E.wire_currents(links + [L("loop", "x", "y")], {"src"}, {"x": 2.0, "y": 0.5})
    assert loop["a"] == loop["b"] == loop["loop"] == 2.5                  # a loop: each wire may carry everything


# ---------------------------------------------------------------------------- mounting

def test_standoffs_off_the_board_pattern_fail(tmp_path):
    d = variant(tmp_path, "holes", {"model.py": [("UNO_HOLES = [(-20.32, -24.13), (-19.05, 24.13), (31.75, 8.89), (31.75, -19.05)]",
                                                  "UNO_HOLES = [(-27.94, -24.13), (-26.67, 24.13), (24.13, 8.89), (24.13, -19.05)]")]})
    c = one(run(d)[0], "UNO hole pattern matches the CAD")
    assert c.severity == "fail" and "4 of 4 UNO holes" in c.measured and "7.62 mm off" in c.measured


def test_solid_standoffs_have_no_hole_and_fail(tmp_path):
    d = variant(tmp_path, "solid", {"model.py": [("* (Cylinder(2.5, STANDOFF_H) - Cylinder(TAP_D / 2, STANDOFF_H))", "* Cylinder(2.5, STANDOFF_H)")]})
    c = one(run(d)[0], "UNO hole pattern matches the CAD")
    assert c.severity == "fail" and "no hole in the carrier" in c.measured


def test_a_screw_too_big_for_the_board_holes_fails(tmp_path):
    d = variant(tmp_path, "screw", {"elec.py": [('screw="M3"', 'screw="M4"')]})
    c = one(run(d)[0], "UNO hole pattern matches the CAD")
    assert c.severity == "fail" and "M4 screw (4 mm) does not pass UNO's Ø3.2 mm holes" in c.measured


def test_a_board_outline_inside_another_body_fails(tmp_path):
    d = variant(tmp_path, "overlap", {"model.py": [('    return {"base": base,', '    turret = turret + box(UNO[0], UNO[1], UNO_TOP + 2, 20, 20, 6)\n    return {"base": base,')]})
    c = one(run(d)[0], "UNO outline is clear of the CAD bodies")
    assert c.severity == "fail" and "overlaps turret" in c.measured


def test_clearance_under_the_mount_s_figure_fails_and_without_one_is_not_run(tmp_path):
    c = one(run(variant(tmp_path, "tight", {"elec.py": [("clearance_mm=1.0", "clearance_mm=3.0")]}))[0], "UNO clearance")
    assert c.severity == "fail" and "1.4 mm to yaw_servo" in c.measured and ">= 3 mm" in c.expected
    c = one(run(variant(tmp_path, "noclear", {"elec.py": [(', clearance_mm=1.0)', ')')]}))[0], "UNO clearance")
    assert c.severity == "warn" and c.measured.startswith("not run: no clearance_mm")


def test_a_module_without_an_outline_or_a_mount_is_not_run(tmp_path):
    d = variant(tmp_path, "module", {"elec.py": [('    return c\n', '    bme = c.module("ENV", "BME280")\n    c.connect(bme["VCC"], "UNO.3V3")\n'
                                                  '    c.connect(bme["GND"], "UNO.GND")\n    pwm = c.module("PWM", "PCA9685")\n    return c\n')]})
    checks, _ = run(d)
    assert one(checks, "mounting: ENV fits").measured == "not run: eitri has no published outline and hole pattern for BME280"
    assert one(checks, "mounting: PWM fits").measured.startswith("not run: PWM has no mount")


def test_without_file_names_the_project_s_only_file_is_used_and_none_is_not_run(tmp_path):
    d = variant(tmp_path, "nonames", {"elec.py": [('    harness="wiring.py", firmware="fw.py", mechanism="mech.py", cad="model.py",\n', '')]}, drop=("fw.py",))
    checks, _ = run(d)
    assert one(checks, "firmware pins match the circuit").measured == "not run: the project has no firmware file"
    assert one(checks, "UNO hole pattern").severity == "pass"


def test_no_cad_is_not_run(tmp_path):
    c = one(run(variant(tmp_path, "nocad", drop=("model.py",)))[0], "boards and modules fit their CAD mounts")
    assert c.measured == "not run: COUPLING cad='model.py' is not a file of the project"


# ---------------------------------------------------------------------------- actuators

def test_a_different_servo_model_than_the_mechanism_fails(tmp_path):
    d = variant(tmp_path, "model", {"elec.py": [('s = c.servo(name, "MG996R")', 's = c.servo(name, "SG90" if tag == "EL" else "MG996R")')]})
    c = one(run(d)[0], "every actuated joint has its actuator")
    assert c.severity == "fail" and "joint elbow is driven by a MG996R in the mechanism and a SG90 in the circuit" in c.measured


def test_a_joint_without_its_actuator_in_the_circuit_fails(tmp_path):
    d = variant(tmp_path, "count", {"elec.py": [('(("SERVO_YAW", "D9", "YAW"), ("SERVO_SH", "D10", "SH"), ("SERVO_EL", "D11", "EL"))',
                                                 '(("SERVO_YAW", "D9", "YAW"), ("SERVO_SH", "D10", "SH"))'),
                                                ('"elbow": "SERVO_EL"', '"elbow": "SERVO_X"')]})
    c = one(run(d)[0], "every actuated joint has its actuator")
    assert c.severity == "fail" and "joint elbow is mapped to 'SERVO_X', which is not a servo" in c.measured


def test_bindings_come_through_the_firmware_without_a_coupling(tmp_path):
    d = variant(tmp_path, "fwbind", {"elec.py": [('    actuators={"yaw": "SERVO_YAW", "shoulder": "SERVO_SH", "elbow": "SERVO_EL"},\n', '')]})
    c = one(run(d)[0], "every actuated joint has its actuator")
    assert c.severity == "pass" and "yaw -> SERVO_YAW through the firmware (YAW on D9)" in c.measured


def test_no_mechanism_is_not_run(tmp_path):
    checks, _ = run(variant(tmp_path, "nomech", drop=("mech.py",)))
    for needle in ("every actuated joint", "stall current vs the drivers", "dissipation:"):
        assert one(checks, needle).measured == "not run: COUPLING mechanism='mech.py' is not a file of the project"


def _motor_circuit(gate="IRLZ44N", amps=2.0):
    from eitri.electronics import Circuit
    c = Circuit("motor")
    uno = c.board("UNO", "uno")
    psu = c.supply("PSU12", 12.0, current_A=5.0)
    m = c.motor("M1", 12.0, amps)
    q = c.mosfet("Q1", gate)
    r = c.resistor("R1", 220)
    c.gpio(uno["D5"], "out")
    c.connect(uno["D5"], r["1"])
    c.connect(r["2"], q["G"])
    c.connect(psu["+"], m["+"])
    c.net("LOW", m["-"], q["D"])
    c.connect(q["S"], uno["GND"], psu["-"])
    return c


def _one_joint(name="ACT"):
    from eitri.mechanism import Actuator, Mechanism
    mech = Mechanism("m")
    mech.link("base", fixed=True)
    mech.link("arm", length_mm=50, mass_g=10)
    mech.joint("j", "base", "arm", axis=(0, 0, 1), at=(0, 0, 0), limit_deg=(-90, 90), actuator=Actuator(name, stall_kgcm=5, speed_deg_s=100))
    return mech


def test_motor_stall_against_its_mosfet_and_its_dissipation():
    from eitri.electronics.analysis import analyze
    c = _motor_circuit(amps=2.0)
    an = analyze(c)
    checks, stall, _ = E.actuator_checks(c, an, _one_joint(), {"j": "M1"}, [])
    d = one(checks, "stall current vs Q1")
    assert d.severity == "pass" and "of 47 A" in d.measured and "Infineon IRLZ44N" in d.measured
    diss = one(E.dissipation_checks(c, an, stall), "dissipation: Q1")
    # the gate is driven through R1 at the Uno's VOH min (4.2 V): RDS(on) at VGS 4 V, 35 mΩ; 2 A^2 x 35 mΩ = 0.14 W
    assert diss.severity == "pass" and "35 mΩ" in diss.measured and "0.14 W" in diss.measured
    hot = one(E.dissipation_checks(c, an, {"M1": 40.0}), "dissipation: Q1")         # 40 A stall: 40 W in a TO-220 with no heatsink
    assert hot.severity == "fail" and "limit 175 C" in hot.measured


def test_a_relay_whose_contacts_are_under_the_stall_current_fails():
    from eitri.electronics import Circuit
    from eitri.electronics.analysis import analyze
    c = Circuit("relay")
    uno = c.board("UNO", "uno")
    psu = c.supply("PSU12", 12.0, current_A=20.0)
    k = c.relay("K1")
    m = c.motor("M1", 12.0, 9.0)
    c.connect(psu["+"], k["COM"])
    c.connect(k["NO"], m["+"])
    c.connect(m["-"], psu["-"], uno["GND"])
    checks, _s, _ = E.actuator_checks(c, analyze(c), _one_joint(), {"j": "M1"}, [])
    d = one(checks, "stall current vs K1")
    assert d.severity == "fail" and "9 A" in d.measured and "contacts 7 A" in d.measured


def test_servos_through_a_pca9685_are_not_run_for_want_of_a_published_rating():
    from eitri.electronics import Circuit
    from eitri.electronics.analysis import analyze
    c = Circuit("pca")
    uno = c.board("UNO", "uno")
    pwm = c.module("PWM", "PCA9685")
    psu = c.supply("PSU", 5.0, current_A=10.0)
    c.connect(psu["+"], pwm["V+"])
    s = c.servo("S1", "MG996R")
    c.connect(s["V+"], pwm["V+"])
    c.connect(s["GND"], psu["-"], uno["GND"])
    checks, _s, _ = E.actuator_checks(c, analyze(c), _one_joint("MG996R"), {"j": "S1"}, [])
    d = one(checks, "stall current vs PWM")
    assert d.severity == "warn" and d.measured.startswith("not run: PWM (PCA9685) passes 2.5 A") and "publishes no current rating" in d.measured


def test_a_regulator_feeding_stalled_servos_overheats():
    from eitri.electronics import Circuit
    from eitri.electronics.analysis import analyze
    c = Circuit("reg")
    uno = c.board("UNO", "uno")
    psu = c.supply("PSU12", 12.0, current_A=5.0)
    reg = c.regulator("U1", "L7805")
    c.connect(psu["+"], reg["IN"])
    c.connect(reg["GND"], psu["-"], uno["GND"])
    s = c.servo("S1", "SG90")
    c.connect(s["V+"], reg["OUT"])
    c.connect(s["GND"], uno["GND"])
    an = analyze(c)
    d = one(E.dissipation_checks(c, an, {"S1": 0.5}), "dissipation: U1")
    # (12 - 5) V x 0.5 A + 12 V x 8 mA = 3.6 W; x 50 C/W = 180 C over 25 C ambient: far over 125 C
    assert d.severity == "fail" and "3.60 W" in d.measured and "junction 205 C" in d.measured and "limit 125 C" in d.measured and "L7805" in d.name
    ok = one(E.dissipation_checks(c, an, {"S1": 0.05}), "dissipation: U1")
    assert ok.severity == "pass"


def test_nothing_to_dissipate_says_so(demo):
    c = one(demo[0], "dissipation:")
    assert c.severity == "pass" and "no regulator or switching driver" in c.measured


# ---------------------------------------------------------------------------- the guard

def test_what_the_electronics_file_declares_is_guarded():
    from eitri_suite import projectchecks
    lm = load_model(DEMO / "elec.py")
    assert projectchecks.flat_declared(lm.module) == {"electronics.connectors": 5, "electronics.actuators": 3, "electronics.mounts": 1}
