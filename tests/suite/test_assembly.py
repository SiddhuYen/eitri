"""The assembly: harness connectors placed at the CAD anchors, board pins at their real positions, wire paths that avoid the parts,
the cut-length and pin-wiring checks, and the firmware's sequence as joint poses."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from eitri_suite import assembly as A
from eitri_suite import crossdomain as X
from eitri.anchors import to_json as anchors_json
from eitri.model import load_model

DEMO = Path(__file__).resolve().parents[2] / "eitri_suite" / "demos" / "robot-arm"


def one(checks, needle):
    hits = [c for c in checks if needle in c.name]
    assert len(hits) == 1, [c.name for c in checks]
    return hits[0]


@pytest.fixture(scope="module")
def arm():
    cad = load_model(DEMO / "arm.py")
    parts = cad.build()
    har = load_model(DEMO / "wiring.py").build()
    return parts, anchors_json(parts), har, cad.module.INTEGRATION


def test_the_uno_layout_has_the_real_pin_positions():
    uno = A.BOARDS["uno"]
    assert uno["size"] == [68.58, 53.34, 1.6]
    assert A.board_pin(uno, "D13") == [-5.334, 24.13, 8.5] and A.board_pin(uno, "d9")[0] == pytest.approx(4.826)
    assert A.board_pin(uno, "D7")[0] - A.board_pin(uno, "D8")[0] == pytest.approx(4.064)            # the 160 mil gap
    assert A.board_pin(uno, "A0")[1] == pytest.approx(-24.13) and A.board_pin(uno, "5V")[0] == pytest.approx(3.81)
    assert A.board_pin(uno, "TX") == A.board_pin(uno, "D1") and A.board_pin(uno, "9") == A.board_pin(uno, "D9") and A.board_pin(uno, "D99") is None
    assert A.board_layout("uno") is uno and A.board_layout("nano-every")["key"] == "nano" and A.board_layout("esp32") is None


def test_the_reference_arm_places_every_connector(arm):
    parts, anchors, har, integration = arm
    assert [a["name"] for a in anchors][:3] == ["PCA_I2C", "PCA_PWR", "PCA_CH0"] and len(anchors) == 16      # + F1 (the supply fuse) and GND_REF (uno_wiring.py's ground split)
    place = A.placements(anchors, har, "uno")
    assert place["unplaced"] == []
    assert place["placed"]["ESP32"] == {"anchor": "ESP32", "kind": "connector", "pins": 4, "pitch": 2.54, "part": "controller"}
    assert place["placed"]["SERVO_YAW"]["part"] == "yaw_servo"
    # a connector's pins run along the anchor's x at its pitch, centred; ESP32's x is +y
    pins = place["pins"]
    esp = [pins[f"ESP32.{p}"]["at"] for p in ("3V3", "GND", "21", "22")]
    assert [round(p[1], 3) for p in esp] == [-3.81, -1.27, 1.27, 3.81] and all(p[0] == 50 and p[2] == 21 for p in esp)
    assert pins["SERVO_YAW.SIG"]["dir"] == [-1, 0, 0]


def test_a_board_connector_gets_the_board_layout():
    cad = load_model(DEMO / "arm.py")
    parts = cad.build()
    har = load_model(DEMO / "uno_wiring.py").build()
    place = A.placements(anchors_json(parts), har, "uno", board_connector="UNO")
    assert place["board"] == {"key": "uno", "connector": "UNO"} and place["placed"]["UNO"]["kind"] == "board"
    at = place["pins"]["UNO.D9"]["at"]
    # the anchor's x is +y, so the board's x axis runs along world y: D9 sits 4.826 along it, 24.13 across (-x in the world), 8.5 up
    assert at == pytest.approx([50 - 24.13, 4.826, 21 + 8.5], abs=1e-3)
    assert "UNO.GND" in place["pins"] and place["placed"]["YAW"]["kind"] == "connector"
    # the layout is only for a connector that is the board: ESP32 (3V3, GND, 21, 22) is not
    place2 = A.placements(anchors_json(parts), load_model(DEMO / "wiring.py").build(), "uno")
    assert place2["board"] is None and place2["placed"]["ESP32"]["kind"] == "connector"
    # before the firmware file exists (no board hint) a connector with board pin names is an Uno: the harness is usually built first
    uno_arm = DEMO.parent / "uno-arm"
    cad2 = load_model(uno_arm / "model.py")
    place3 = A.placements(anchors_json(cad2.build()), load_model(uno_arm / "wiring.py").build(), None)
    assert place3["board"] == {"key": "uno", "connector": "UNO"} and place3["placed"]["UNO"]["kind"] == "board" and "UNO.D9" in place3["pins"]
    checks, data = A.run(cad2.build(), anchors_json(cad2.build()), load_model(uno_arm / "wiring.py").build(), cad2.module.INTEGRATION, None)
    c = one(checks, "every harness connector has a CAD anchor")
    assert c.severity == "pass" and "UNO is the Arduino Uno R3" in c.measured and data["unplaced"] == [] and "UNO.D9" in data["pins"]


def test_the_spline_passes_through_its_ends_and_is_deterministic():
    pts = A.catmull_rom([np.array([0.0, 0, 0]), np.array([10.0, 0, 0]), np.array([10.0, 10, 0])], 4)
    assert pts[0].tolist() == [0, 0, 0] and pts[-1].tolist() == [10, 10, 0] and len(pts) == 9
    assert A.catmull_rom([np.array([0.0, 0, 0]), np.array([10.0, 0, 0]), np.array([10.0, 10, 0])], 4)[3].tolist() == pts[3].tolist()


def test_free_wires_leave_their_pins_and_rise_over_parts_in_the_way(arm):
    parts, anchors, har, integration = arm
    place = A.placements(anchors, har, "uno")
    boxes = A.part_boxes(parts)
    paths = A.wire_paths(har, place["pins"], list(boxes.values()), integration["routes"])
    assert set(paths) == {l.id for l in har.links}                                   # every connector is placed, so every wire has a path
    sda = paths["I2C_SDA"]                                                           # ESP32 (x = 50) to the PCA9685 (x = -45): the yaw servo stands between
    assert sda["source"] == "spline" and sda["points"][0] == place["pins"]["ESP32.21"]["at"] and sda["points"][-1] == place["pins"]["PCA_I2C.SDA"]["at"]
    servo_top = boxes["yaw_servo"][1][2]
    assert max(p[2] for p in sda["points"]) > servo_top + A.CLEARANCE_MM             # lifted over it
    assert sda["length_mm"] > 95 and sda["length_mm"] == pytest.approx(A.polyline_length(sda["points"]), abs=0.1)
    # the straight lead out of each pin: the second sample is along the pin's direction (+z here)
    p0, p1 = np.array(sda["points"][0]), np.array(sda["points"][1])
    assert p1[2] > p0[2] and abs(p1[0] - p0[0]) < 1.0
    # wires of the declared route follow its waypoints, pin to pin
    sh = paths["SH_SIG"]
    assert sh["source"] == "route:arm servo leads" and sh["points"][0] == place["pins"]["PCA_CH1.SIG"]["at"] and sh["points"][-1] == place["pins"]["SERVO_SH.SIG"]["at"]
    assert [-8, 0, 30] in [[round(c) for c in p] for p in sh["points"]]


def test_the_assembly_checks_pass_for_the_reference_and_fail_when_a_connector_or_a_wire_is_short(arm, tmp_path):
    parts, anchors, har, integration = arm
    checks, data = A.run(parts, anchors, har, integration, "uno")
    c = one(checks, "every harness connector has a CAD anchor")
    assert c.severity == "pass" and "11 connectors placed" in c.measured           # + F1, the supply fuse
    w = one(checks, "wire cut lengths cover their 3D paths")
    assert w.severity == "pass" and set(w.refs) >= {"I2C_SDA", "PWR_V"} and "SH_SIG" not in w.refs        # routed wires are graded by their route
    assert data["unplaced"] == [] and set(data["paths"]) == {l.id for l in har.links} and "yaw_servo" in data["boxes"] and data["routes"][0]["name"] == "arm servo leads"
    # without the PSU anchor the check fails naming it, and its wires have no path
    without = [a for a in anchors if a["name"] != "PSU"]
    checks, data = A.run(parts, without, har, integration, "uno")
    c = one(checks, "every harness connector has a CAD anchor")
    assert c.severity == "fail" and "no anchor for PSU" in c.measured and c.refs == ["PSU"] and data["unplaced"] == ["PSU"] and "PWR_F" not in data["paths"]
    # a free wire cut shorter than its path fails, naming the wire and both lengths
    folder = tmp_path / "arm"
    shutil.copytree(DEMO, folder, ignore=shutil.ignore_patterns("__pycache__"))
    (folder / "wiring.py").write_text((folder / "wiring.py").read_text().replace('Wire(24, "GN", 200), net="SDA"', 'Wire(24, "GN", 60), net="SDA"'))
    short = load_model(folder / "wiring.py").build()
    checks, data = A.run(parts, anchors, short, integration, "uno")
    w = one(checks, "wire cut lengths cover their 3D paths")
    assert w.severity == "fail" and w.measured.startswith("I2C_SDA: path") and ", cut 60 mm" in w.measured and w.refs == ["I2C_SDA"]


def test_firmware_pins_must_be_wired_in_the_harness(tmp_path):
    fw = load_model(DEMO / "fw.py")
    har = load_model(DEMO / "uno_wiring.py").build()
    built = fw.build()
    c = one(A.pin_wiring_checks(built, har, fw.module.COUPLING), "every firmware pin is wired")
    assert c.severity == "pass" and c.refs == ["UNO.D9", "UNO.D10", "UNO.D11"]
    assert A.pin_wiring_checks(built, har, None)[0].severity == "pass"                     # the board connector is found by its pin names too
    folder = tmp_path / "arm"
    shutil.copytree(DEMO, folder, ignore=shutil.ignore_patterns("__pycache__"))
    (folder / "uno_wiring.py").write_text((folder / "uno_wiring.py").read_text().replace('("D11", servos[2])', ""))
    cut = load_model(folder / "uno_wiring.py").build()
    c = A.pin_wiring_checks(built, cut, fw.module.COUPLING)[0]
    assert c.severity == "fail" and "nothing is wired to UNO.D11 (ELBOW)" in c.measured and c.refs == ["UNO.D11"]
    no_board = load_model(DEMO / "wiring.py").build()                                       # ESP32 + PCA9685: no Uno connector
    c = A.pin_wiring_checks(built, no_board, None)[0]
    assert c.severity == "fail" and "no board connector" in c.measured


def test_the_sequence_becomes_joint_poses_and_is_checked_against_the_limits(tmp_path):
    fw = load_model(DEMO / "fw.py")
    mech = load_model(DEMO / "mech.py").build()
    checks, seq = A.sequence(fw.module, fw.build(), mech)
    c = one(checks, "stays within the servos and the joints")
    assert c.severity == "pass" and "7 steps" in c.measured and "3 limits" in c.measured
    assert [s["name"] for s in seq["steps"]][:3] == ["home", "above pick", "pick"]
    assert seq["steps"][2]["servos"] == {"YAW": 60, "SHOULDER": 135, "ELBOW": 150} and seq["steps"][2]["joints"] == {"yaw": -30, "shoulder": 45, "elbow": 60}
    assert seq["joints"]["YAW"] == {"joint": "yaw", "zero": 90.0, "sign": 1.0} and seq["source"] == "firmware"
    # an angle the servo cannot reach, a joint pushed past its limit, a servo that does not exist
    folder = tmp_path / "arm"
    shutil.copytree(DEMO, folder, ignore=shutil.ignore_patterns("__pycache__"))
    code = (folder / "fw.py").read_text().replace('("pick", dict(YAW=60, SHOULDER=135, ELBOW=150))', '("pick", dict(YAW=190, SHOULDER=175, GRIP=10))')
    (folder / "fw.py").write_text(code)
    bad = load_model(folder / "fw.py")
    checks, seq = A.sequence(bad.module, bad.build(), mech)
    c = one(checks, "stays within the servos and the joints")
    assert c.severity == "fail"
    assert "YAW 190° is outside the servo's 0-180°" in c.measured and "shoulder at 85°, outside its -20..80°" in c.measured and "GRIP is not a servo" in c.measured
    assert seq is not None and seq["steps"][2]["servos"] == {"YAW": 190, "SHOULDER": 175}
    # no SEQUENCE: nothing to check, no data
    assert A.sequence(load_model(DEMO / "uno_wiring.py").module, fw.build(), mech) == ([], None)


def test_the_assembly_data_and_checks_are_in_the_builds(tmp_path):
    from eitri.project import Project
    folder = tmp_path / "arm"
    shutil.copytree(DEMO, folder, ignore=shutil.ignore_patterns("__pycache__"))
    cad = Project(folder, name="arm").build(note="cad", render=False)
    assert cad.ok, cad.error
    asm = cad.extensions["suite"]["assembly"]
    assert asm["unplaced"] == [] and asm["board"] is None and "I2C_SDA" in asm["paths"] and [a["name"] for a in asm["anchors"]] == [a["name"] for a in cad.anchors]
    assert any(c.name == "every harness connector has a CAD anchor" and c.ok for c in cad.report.checks)
    har = Project(folder, name="wiring").build(note="harness", render=False)
    assert har.ok and har.extensions["suite"]["assembly"]["paths"]["SH_SIG"]["source"] == "route:arm servo leads"
    assert any(c["name"] == "wire cut lengths cover their 3D paths" for c in har.domain_report["checks"])
    uno = Project(folder, name="uno_wiring").build(note="uno harness", render=False)
    assert uno.ok and uno.extensions["suite"]["assembly"]["board"] == {"key": "uno", "connector": "UNO"}
    fw = Project(folder, name="fw").build(note="fw", render=False)
    assert fw.ok and [s["name"] for s in fw.extensions["suite"]["sequence"]["steps"]][-1] == "home"
    names = [c["name"] for c in fw.domain_report["checks"]]
    assert "every firmware pin is wired in the harness" in names and "the sequence stays within the servos and the joints" in names
    mech = Project(folder, name="mech").build(note="mech", render=False)
    assert mech.ok and mech.extensions["suite"]["coupling"]["links_map"]["upper"] == ["upper_arm", "elbow_servo"]
