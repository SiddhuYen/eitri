"""CAD <-> harness (route fit, mating faces) and harness <-> firmware (pinout) consistency checks."""
from __future__ import annotations

import copy
import json
import math
import shutil
from pathlib import Path

import build123d as bd
import pytest

from eitri_suite import crossdomain as X
from eitri.harness import Connector, Harness, Wire
from eitri.harness.catalog import Gauge
from eitri.model import Part, Parts, load_model

DEMO = Path(__file__).resolve().parents[2] / "eitri_suite" / "demos" / "robot-arm"


def one(checks, needle):
    hits = [c for c in checks if needle in c.name]
    assert len(hits) == 1, [c.name for c in checks]
    return hits[0]


@pytest.fixture(scope="module")
def arm():
    cad = load_model(DEMO / "arm.py")
    har = load_model(DEMO / "wiring.py")
    return cad.build(), har.build(), cad.module.INTEGRATION


def test_bundle_diameter_numbers():
    # 22 AWG: conductor 0.6438 mm, wall 0.30 mm each side -> OD 1.2438 mm (an estimate)
    od = X.wire_od_mm(Gauge.parse(22))
    assert od == pytest.approx(0.6438 + 0.6, abs=1e-3)
    # six of them: sqrt(6 * od^2 / 0.7)
    assert X.bundle_diameter_mm([od] * 6) == pytest.approx(math.sqrt(6 * od * od / 0.7), rel=1e-9) == pytest.approx(3.642, abs=2e-3)
    assert X.wire_od_mm(Gauge.parse(12)) == pytest.approx(2.053 + 1.0, abs=2e-3)         # heavier wire: the thicker wall
    assert X.wire_od_mm(Gauge.parse("0.5mm2")) == pytest.approx(math.sqrt(4 * 0.5 / math.pi) + 0.6, abs=1e-6)


# ---------------------------------------------------------------------------- routes


def test_the_reference_routes_fit(arm):
    parts, h, integ = arm
    checks = X.route_checks(parts, h, integ["routes"])
    fit = one(checks, "fits turret cable hole")
    assert fit.severity == "pass" and "Ø8.0 mm hole in turret" in fit.measured and "bundle of 6 wire(s) 3.6 mm" in fit.measured and "ESTIMATE" in fit.measured
    assert one(checks, "wire length").severity == "pass"


def test_a_hole_too_small_fails_naming_the_feature_and_the_dimensions(arm):
    parts, h, integ = arm
    small = Parts([p if p.name != "turret" else Part("turret", p.shape.fuse(bd.Pos(-8, 0, 53.4) * bd.Cylinder(2.5, 6)) - bd.Pos(-8, 0, 53.4) * bd.Cylinder(2.0, 20), "#888") for p in parts])
    c = one(X.route_checks(small, h, integ["routes"]), "fits turret cable hole")
    assert c.severity == "fail" and "Ø4.0 mm hole in turret at (-8, 0, 53.4) is too small" in c.measured and "short by" in c.measured and ">= 4.6" in c.expected
    assert "turret" in c.refs and "SH_SIG" in c.refs


def test_more_wire_needs_a_bigger_hole(arm):
    parts, h, integ = arm
    thick = copy.deepcopy(h)
    for l in thick.links:
        if l.id.startswith(("SH_", "EL_")):
            l.gauge = Gauge.parse(14)                       # 6 x 14 AWG: 6 * (1.628 + 1.0)^2 / 0.7 -> 7.7 mm bundle
    c = one(X.route_checks(parts, thick, integ["routes"]), "fits turret cable hole")
    assert c.severity == "fail" and "7.7 mm" in c.measured and "Ø8.0 mm hole" in c.measured
    wide = copy.deepcopy(integ["routes"])
    wide[0]["clearance_mm"] = 6.0                           # the clearance counts: 3.6 + 6 = 9.6 mm > 8
    assert one(X.route_checks(parts, h, wide), "fits turret cable hole").severity == "fail"


def test_wire_length_against_the_routed_path(arm):
    parts, h, integ = arm
    path = sum(math.dist(a, b) for a, b in zip(integ["routes"][0]["waypoints"], integ["routes"][0]["waypoints"][1:]))
    assert path == pytest.approx(206.0, abs=1.0)
    short = copy.deepcopy(h)
    short.link("SH_SIG").length_mm = 180
    c = one(X.route_checks(parts, short, integ["routes"]), "wire length")
    assert c.severity == "fail" and "SH_SIG is 180 mm" in c.measured and "too short to reach" in c.measured
    tight = copy.deepcopy(h)
    tight.link("SH_SIG").length_mm = 215                    # reaches, but under the 10 % service loop (227 mm)
    c = one(X.route_checks(parts, tight, integ["routes"]), "wire length")
    assert c.severity == "warn" and "SH_SIG (215 mm)" in c.measured and "service loop" in c.measured


def test_route_declaration_errors(arm):
    parts, h, integ = arm
    r = copy.deepcopy(integ["routes"])
    r[0]["wires"] = ["SH_SIG", "NOPE"]
    c = one(X.route_checks(parts, h, r), "wires exist")
    assert c.severity == "fail" and "no wire NOPE" in c.measured
    r = copy.deepcopy(integ["routes"])
    r[0]["through"][0]["at"] = [60, 30, 30]                 # nowhere near a hole
    c = one(X.route_checks(parts, h, r), "fits turret cable hole")
    assert c.severity == "fail" and "no hole in turret near (60, 30, 30)" in c.measured
    r = copy.deepcopy(integ["routes"])
    r[0]["through"][0]["part"] = "ghost"
    assert "not in the model" in one(X.route_checks(parts, h, r), "fits").measured
    r = copy.deepcopy(integ["routes"])
    r[0]["through"][0] = dict(part="base", at=[0, 0, 4], label="slot", channel_mm=3.0)        # a slot given by its width
    c = one(X.route_checks(parts, h, r), "fits slot")
    assert c.severity == "fail" and "declared 3 mm channel" in c.measured


def test_a_blind_hole_is_not_a_pass_through():
    plate = bd.Box(40, 40, 10) - bd.Pos(0, 0, 2.5) * bd.Cylinder(4, 5)            # 5 mm deep from the top: blind
    parts = Parts([Part("plate", plate, "#888")])
    h = Harness()
    a, b = h.add(Connector("A", "BARE")), h.add(Connector("B", "BARE"))
    h.connect(a, b, Wire(22, "RD", 200), name="W")
    c = one(X.route_checks(parts, h, [dict(name="r", wires=["W"], through=[dict(part="plate", at=[0, 0, 2.5], label="hole")])]), "fits hole")
    assert c.severity == "fail" and "blind" in c.measured


# ---------------------------------------------------------------------------- mating faces


def test_mating_faces_are_reachable_in_the_reference(arm):
    parts, h, integ = arm
    checks = X.mating_checks(parts, h, integ["mating"])
    assert len(checks) == 5 and all(c.severity == "pass" for c in checks), [c.line() for c in checks]
    assert "20 mm deep" in checks[0].measured


def test_a_blocked_mating_face_names_the_blocker_and_the_free_length(arm):
    parts, h, integ = arm
    cover = Part("cover", bd.Pos(-45, 0, 8 + 3 + 12) * bd.Box(70, 70, 2), "#888")           # a lid 12 mm over the board
    blocked = Parts(list(parts) + [cover])
    c = one(X.mating_checks(blocked, h, integ["mating"]), "mating face PCA_CH0")
    assert c.severity == "fail" and "cover blocks" in c.measured and "needs 20 mm" in c.measured and "cover" in c.refs
    free = float(c.measured.split(": ")[1].split(" mm free")[0])
    assert 10.5 < free < 12.0                                                        # 12 mm minus the 0.2 mm start gap, found by bisection


def test_mating_declaration_errors(arm):
    parts, h, integ = arm
    m = copy.deepcopy(integ["mating"])
    m[0]["connector"] = "NOPE"
    assert "no connector 'NOPE'" in one(X.mating_checks(parts, h, m), "mating face NOPE").measured
    m = copy.deepcopy(integ["mating"])
    m[0]["at"] = [-45, 0, 40]                                                         # floating in the air
    c = one(X.mating_checks(parts, h, m), "mating face PCA_CH0")
    assert c.severity == "fail" and "not on the part" in c.measured
    m = copy.deepcopy(integ["mating"])
    m[0]["part"] = "ghost"
    assert "no part 'ghost'" in one(X.mating_checks(parts, h, m), "mating face PCA_CH0").measured


# ---------------------------------------------------------------------------- harness <-> firmware pinout


@pytest.fixture(scope="module")
def uno():
    har = load_model(DEMO / "uno_wiring.py")
    fw = load_model(DEMO / "fw.py")
    return har.build(), fw.build(), dict(fw.module.COUPLING["pins"])


def test_the_reference_firmware_matches_the_harness(uno):
    h, fw, pins = uno
    checks = X.firmware_pin_checks(h, fw, pins)
    assert len(checks) == 3 and all(c.severity == "pass" for c in checks), [c.line() for c in checks]


def test_a_firmware_pin_that_the_harness_wires_elsewhere_fails_naming_both_sides(uno):
    h, fw, pins = uno
    wrong = dict(pins, YAW="UNO.D10")
    c = one(X.firmware_pin_checks(h, fw, wrong), "firmware pin YAW")
    assert c.severity == "fail" and "the firmware drives D9 for YAW, the harness wires it to D10 (UNO.D10)" in c.measured


def test_unwired_unknown_missing_and_unmapped_pins(uno):
    h, fw, pins = uno
    hh = copy.deepcopy(h)
    hh.connectors["UNO"].add_pin("D5")
    c = one(X.firmware_pin_checks(hh, fw, dict(pins, YAW="UNO.D5")), "firmware pin YAW")
    assert c.severity == "fail" and "is not wired to anything" in c.measured
    assert "not in the harness" in one(X.firmware_pin_checks(h, fw, dict(pins, YAW="GHOST.D9")), "firmware pin YAW").measured
    assert "does not declare" in one(X.firmware_pin_checks(h, fw, dict(pins, NOPE="UNO.D9")), "firmware pin NOPE").measured
    unmapped = {k: v for k, v in pins.items() if k != "ELBOW"}
    c = one(X.firmware_pin_checks(h, fw, unmapped), "firmware pin ELBOW is in the harness")
    assert c.severity == "warn"


# ---------------------------------------------------------------------------- run on project builds, from either side


def test_the_checks_run_on_a_cad_build_and_on_a_harness_build(tmp_path):
    folder = tmp_path / "arm"
    shutil.copytree(DEMO, folder)
    cad = load_model(folder / "arm.py")
    checks, data = X.project_checks(folder / "arm.py", cad.build(), "cad", cad.module)
    names = [c.name for c in checks]
    assert all(c.severity == "pass" for c in checks), [c.line() for c in checks if c.severity != "pass"]
    assert data == {"routes": 1, "mating": 5} and any("fits turret cable hole" in n for n in names)
    har = load_model(folder / "wiring.py")
    hchecks, hdata = X.project_checks(folder / "wiring.py", har.build(), "harness")
    assert any("fits turret cable hole" in c.name for c in hchecks)                  # a harness change is checked against the CAD too
    assert all(c.severity == "pass" for c in hchecks)
    fw = load_model(folder / "fw.py")
    fchecks, fdata = X.project_checks(folder / "fw.py", fw.build(), "firmware", fw.module)
    assert len(fchecks) == 3 and fdata["pins"] == 3 and fdata["harness"] == "uno_wiring.py"


def test_a_harness_edit_that_breaks_the_fit_is_caught_on_the_harness_build(tmp_path):
    folder = tmp_path / "arm"
    shutil.copytree(DEMO, folder)
    wiring = (folder / "wiring.py").read_text()
    (folder / "wiring.py").write_text(wiring.replace("PARAMS = dict(servo_a=1.0, lead_mm=260)", "PARAMS = dict(servo_a=1.0, lead_mm=150)"))
    har = load_model(folder / "wiring.py")
    checks, _ = X.project_checks(folder / "wiring.py", har.build(), "harness")
    c = one(checks, "wire length")
    assert c.severity == "fail" and "SH_SIG is 150 mm" in c.measured


def test_nothing_to_compare_means_no_checks(tmp_path):
    (tmp_path / "plate.py").write_text("from eitri import *\nPARAMS = dict()\ndef build(p):\n    return Box(10, 10, 10)\n")
    m = load_model(tmp_path / "plate.py")
    assert X.project_checks(tmp_path / "plate.py", m.build(), "cad", m.module) == ([], {})
    (tmp_path / "wiring.py").write_text((DEMO / "wiring.py").read_text())
    h = load_model(tmp_path / "wiring.py")
    assert X.project_checks(tmp_path / "wiring.py", h.build(), "harness") == ([], {})              # no CAD model: nothing to compare
    assert X.project_checks(tmp_path / "plate.py", object(), "pneumatics", m.module) == ([], {})   # no cross-domain check for pneumatics


def test_the_checks_are_in_the_build_report_and_the_guard_covers_them(tmp_path):
    from eitri.project import Project
    folder = tmp_path / "arm"
    shutil.copytree(DEMO, folder)
    pr = Project(folder, name="arm")
    res = pr.build(note="first", render=False)
    assert res.ok and res.extensions["suite"]["declares"]["integration"] == {"routes": 1, "mating": 5}
    assert any("fits turret cable hole" in c.name for c in res.report.checks)
    # an agent that deletes INTEGRATION fails the guard
    code = (folder / "arm.py").read_text()
    pr.write_model(code[:code.index("INTEGRATION = dict(")])
    res = pr.build(note="drop integration", render=False)
    assert any(c.name == "suite: declared checks are kept" and not c.ok and "integration.routes dropped from 1 to 0" in c.measured for c in res.report.checks)


def test_the_firmware_pinout_check_runs_and_is_guarded_on_the_firmware_build(tmp_path):
    from eitri.project import Project
    folder = tmp_path / "arm"
    shutil.copytree(DEMO, folder, ignore=shutil.ignore_patterns("__pycache__"))
    pr = Project(folder, name="fw")
    res = pr.build(note="first", render=False)
    assert res.extensions["suite"]["declares"]["pinout"] == {"pins": 3}
    assert sum(1 for c in res.domain_report["checks"] if c["name"].startswith("firmware pin ")) == 3
    code = (folder / "fw.py").read_text()
    pr.write_model(code.replace('"YAW": "UNO.D9", ', ""))
    res = pr.build(note="drop a pin", render=False)
    assert any(c["name"] == "suite: declared checks are kept" and not c["ok"] and "pinout.pins dropped from 3 to 2" in c["measured"] for c in res.domain_report["checks"])
