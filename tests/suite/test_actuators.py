"""Every actuator bound to the joint it drives, at it, and carried by its link (eitri_suite.kinematics.actuator_bindings and the
checks "every actuator is at the joint it drives" / "every body is attached"): the reference arms (three and five joints), the
agent's way of writing COUPLING (servo bodies listed under no link, bound by name and added to the parent link's bodies for the
Assembly view), a servo built on the ground next to the arm (the floating-servo bug: it fails, at rest and at every keyframe), a
servo whose shaft is off the joint axis, and the harness connectors and the board counted as bodies."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eitri.anchors import to_json as anchors_json
from eitri.model import load_model
from eitri_suite import assembly as A
from eitri_suite import coupling as C
from eitri_suite import kinematics as K
from eitri_suite import projectchecks

DEMOS = Path(__file__).resolve().parents[2] / "eitri_suite" / "demos"
UNO = DEMOS / "uno-arm"
DESK = DEMOS / "desk-gripper"
LISTED = 'links={"base": ["base", "yaw_servo"], "turret": ["turret", "shoulder_servo"], "upper": ["upper_arm", "elbow_servo"], "fore": ["forearm"]}'
UNLISTED = 'links={"base": ["base"], "turret": ["turret"], "upper": ["upper_arm"], "fore": ["forearm"]}'


def one(checks, needle):
    hits = [c for c in checks if needle in c.name]
    assert len(hits) == 1, [c.name for c in checks]
    return hits[0]


def variant(tmp_path, name, edits: dict[str, list[tuple[str, str]]], src=UNO) -> Path:
    d = tmp_path / name
    shutil.copytree(src, d, ignore=shutil.ignore_patterns("__pycache__"))
    for f, subs in edits.items():
        text = (d / f).read_text(encoding="utf-8")
        for a, b in subs:
            assert a in text, (f, a)
            text = text.replace(a, b)
        (d / f).write_text(text, encoding="utf-8")
    return d


def mech_checks(folder: Path):
    lm = load_model(folder / "mech.py")
    return projectchecks.extras("mechanism", lm.module, lm.build(), folder / "mech.py")


def cad_checks(folder: Path, model="model.py"):
    lm = load_model(folder / model)
    return projectchecks.extras("cad", lm.module, lm.build(), folder / model)


def every_keyframe_attached(folder: Path, bodies: list[str]) -> dict:
    """The attachment of each body at every keyframe the view shows (zero, home, every SEQUENCE step), through the bound links."""
    cad, mlm = load_model(folder / "model.py"), load_model(folder / "mech.py")
    parts, mech = cad.build(), mlm.build()
    bindings = K.actuator_bindings(parts, mech, mlm.module.COUPLING, dict(getattr(cad.module, "HARDWARE", None) or {}))
    links = K.effective_links(mlm.module.COUPLING["links"], bindings)
    seq = C.firmware_sequence(folder, mech, "mech.py")
    frames = K.keyframes(mech, seq)
    assert len(frames) >= 4                                                            # the zero pose, home and the sequence
    c, info = K.attachment_check(parts, mech, links, poses=frames)
    return {"check": c, "info": info, "bindings": bindings, "links": links, "frames": frames, "parts": parts, "mech": mech}


# ---------------------------------------------------------------------------- the reference arms: three and five joints

def test_every_actuator_of_the_three_joint_arm_is_at_its_joint_and_stays_attached_across_all_keyframes():
    r = every_keyframe_attached(UNO, ["yaw_servo", "shoulder_servo", "elbow_servo"])
    js = r["bindings"]["joints"]
    assert {j: b["part"] for j, b in js.items()} == {"yaw": "yaw_servo", "shoulder": "shoulder_servo", "elbow": "elbow_servo"}
    assert {j: b["link"] for j, b in js.items()} == {"yaw": "base", "shoulder": "turret", "elbow": "upper"}      # each on its joint's parent link
    assert all(b["ok"] and b["distance_mm"] == 0.0 and b["axis_deg"] == 0.0 and b["source"] == "named after the joint" for b in js.values()), js
    assert r["bindings"]["unbound"] == [] and r["bindings"]["missing"] == []
    c = r["check"]
    assert c.severity == "pass" and "9 bodies attached at the zero pose, home and 7 sequence steps" in c.measured, c.measured
    assert r["info"]["poses_checked"] == 8 and all(b["attached"] for b in r["info"]["bodies"].values())
    a = K.actuator_check(r["bindings"])
    assert a.severity == "pass" and "yaw_servo drives yaw (on base, shaft along the axis within 0°)" in a.measured


def test_every_actuator_of_the_five_joint_desk_arm_including_the_prismatic_jaw():
    r = every_keyframe_attached(DESK, [])
    js = r["bindings"]["joints"]
    assert {j: b["part"] for j, b in js.items()} == {"yaw": "yaw_servo", "shoulder": "shoulder_servo", "elbow": "elbow_servo", "wrist": "wrist_servo", "jaw": "gripper_servo"}
    assert all(b["ok"] for b in js.values()), js
    assert js["wrist"]["axis_deg"] == 0.0 and js["wrist"]["link"] == "fore"
    jaw = js["jaw"]                                                                     # a slider's servo drives it through a horn: near the stroke axis, not on the point
    assert jaw["source"] == "listed under link grip" and jaw["axis_deg"] is None and 0 < jaw["line_mm"] < K.DRIVE_REACH_MM and jaw["link"] == "grip"
    assert r["check"].severity == "pass" and "15 bodies attached at the zero pose, home and 10 sequence steps" in r["check"].measured, r["check"].measured
    assert one(mech_checks(DESK)[0], "every actuator is at the joint it drives").severity == "pass"


# ---------------------------------------------------------------------------- the agent's COUPLING: servo bodies listed under no link

def test_servo_bodies_left_out_of_coupling_are_bound_by_name_and_ride_the_parent_link(tmp_path):
    d = variant(tmp_path, "unlisted", {"mech.py": [(LISTED, UNLISTED)]})
    r = every_keyframe_attached(d, [])
    assert r["links"] == {"base": ["base", "yaw_servo"], "turret": ["turret", "shoulder_servo"], "upper": ["upper_arm", "elbow_servo"], "fore": ["forearm"]}
    assert r["check"].severity == "pass" and all(b["attached"] for b in r["info"]["bodies"].values())
    assert r["info"]["bodies"]["elbow_servo"]["link"] == "upper" and "elbow_servo" not in r["info"]["implicit_base"]
    # the build result carries the bound map and the bindings (the Assembly view poses the servo bodies with it) ...
    checks, data = mech_checks(d)
    assert data["coupling"]["links_map"] == r["links"] and data["coupling"]["actuators"]["elbow"]["part"] == "elbow_servo"
    assert one(checks, "every actuator is at the joint it drives").severity == "pass" and one(checks, "every body is attached").severity == "pass"
    # ... the servo masses count on their links (the Actuator declares no mass_g, so nothing is counted twice) ...
    assert any(x.startswith("elbow_servo = catalog MG996R 80 g [source: TowerPro") for x in data["coupling"]["sources"]["upper"]) and data["coupling"]["link_masses_g"]["upper"] > 60
    # ... and the CAD build of the same project says the same
    cchecks, cdata = cad_checks(d)
    assert one(cchecks, "every actuator is at the joint it drives").severity == "pass" and cdata["placement"]["links_map"] == r["links"]


def test_an_actuator_with_a_declared_mass_is_not_counted_twice(tmp_path):
    d = variant(tmp_path, "mass", {"mech.py": [(LISTED, UNLISTED), ('Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=400, travel_deg=180)',
                                                                    'Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=400, travel_deg=180, mass_g=55)')]})
    checks, data = mech_checks(d)
    assert "elbow_servo" not in " ".join(data["coupling"]["sources"].get("upper", []))      # the Actuator carries the 55 g
    assert data["coupling"]["links_map"]["upper"] == ["upper_arm", "elbow_servo"]            # the body still rides the link in the view
    assert one(checks, "every actuator is at the joint it drives").severity == "pass"


def test_coupling_actuators_names_the_body_outright(tmp_path):
    d = variant(tmp_path, "explicit", {"mech.py": [(LISTED, UNLISTED), ("cad=\"model.py\",", "cad=\"model.py\", actuators={\"elbow\": \"elbow_servo\", \"yaw\": \"ghost\"},")]})
    checks, data = mech_checks(d)
    js = data["coupling"]["actuators"]
    assert js["elbow"]["source"] == "COUPLING actuators" and js["elbow"]["ok"]
    c = one(checks, "every actuator is at the joint it drives")
    assert c.severity == "fail" and "COUPLING actuators names 'ghost' for joint yaw, which the CAD model does not build" in c.measured


# ---------------------------------------------------------------------------- the floating servo

def test_a_servo_built_on_the_ground_next_to_the_arm_fails_at_rest_and_at_every_keyframe(tmp_path):
    """The bug: the CAD put the elbow servo on the base plate next to the arm (not at the elbow), COUPLING did not list it, and the old
    check took an unlisted body resting on the table as fixed to the base. Now it is the elbow's actuator, so it rides the upper link,
    and it is 136 mm from the joint it drives: the actuator check names the distance, the attachment check names it floating."""
    d = variant(tmp_path, "ground", {"mech.py": [(LISTED, UNLISTED)],
                                     "model.py": [("elbow_servo = box(20 + p.upper_mm, sh_y + p.plate_t + H / 2, SHOULDER_Z, L, H, W)", "elbow_servo = box(60, -70, H / 2, L, W, H)")]})
    checks, data = mech_checks(d)
    a = one(checks, "every actuator is at the joint it drives")
    assert a.severity == "fail" and a.measured.startswith("elbow_servo is 136.4 mm from the point of joint elbow at (120, 68.35, 65.75): its output face must be centred there")
    assert a.refs == ["elbow_servo", "elbow"]
    b = one(checks, "every body is attached")
    assert b.severity == "fail" and "elbow_servo (link upper) floats" in b.measured and "elbow_servo" in b.refs
    assert data["coupling"]["attachment"]["elbow_servo"]["attached"] is False
    # at every keyframe too: carried by the upper link it is nowhere near the arm (home is the zero pose here, so the first step shows it)
    r = every_keyframe_attached(d, [])
    assert r["info"]["bodies"]["elbow_servo"]["attached"] is False
    # the CAD and the harness builds of the same project say the same, and the servo's plug (anchored on the body) is off with it
    cchecks, _ = cad_checks(d)
    assert one(cchecks, "every actuator is at the joint it drives").severity == "fail"
    assert "connector SERVO_EL is" in one(cchecks, "every body is attached").measured
    hlm = load_model(d / "wiring.py")
    hchecks, _ = projectchecks.extras("harness", hlm.module, hlm.build(), d / "wiring.py")
    assert one(hchecks, "every actuator is at the joint it drives").severity == "fail"


def test_a_body_carried_away_by_its_link_floats_at_a_keyframe_not_at_rest(tmp_path):
    """A bracket resting on the arm at the zero pose but listed under the base: at rest it touches the table, so the old check passed;
    at home and every step the arm has moved away from it, and it hangs in the air."""
    d = variant(tmp_path, "bracket", {"model.py": [('"standoffs": standoffs, "controller": controller}', '"standoffs": standoffs, "controller": controller, "bracket": box(170, -40, 3, 20, 20, 6)}'),
                                                   ('MATERIALS = {"base": "pla",', 'MATERIALS = {"bracket": "pla", "base": "pla",')],
                                      "mech.py": [(LISTED, 'links={"base": ["base", "yaw_servo"], "turret": ["turret", "shoulder_servo"], "upper": ["upper_arm", "elbow_servo"], "fore": ["forearm", "bracket"]}'),
                                                  ('m.joint("yaw", "base", "turret", axis=(0, 0, 1), at=(0, 0, YAW_Z), limit_deg=(-90, 90), actuator=servo)',
                                                   'm.joint("yaw", "base", "turret", axis=(0, 0, 1), at=(0, 0, YAW_Z), limit_deg=(-90, 90), actuator=servo, home=30)')]})
    cad, mlm = load_model(d / "model.py"), load_model(d / "mech.py")
    parts, mech = cad.build(), mlm.build()
    links = mlm.module.COUPLING["links"]
    at_rest, _ = K.attachment_check(parts, mech, links)                                   # the zero pose alone: the bracket is 2 mm off the forearm's end, not floating by much
    c, info = K.attachment_check(parts, mech, links, poses=K.keyframes(mech, C.firmware_sequence(d, mech, "mech.py")))
    b = info["bodies"]["bracket"]
    assert c.severity == "fail" and b["attached"] is False and "bracket" in c.refs
    assert at_rest.severity == "fail" or b.get("pose"), (at_rest.measured, b)


def test_a_servo_whose_shaft_is_off_the_joint_axis_fails_with_the_angle(tmp_path):
    d = variant(tmp_path, "offaxis", {"mech.py": [(LISTED, UNLISTED), ('m.joint("elbow", "upper", "fore", axis=(0, -1, 0)', 'm.joint("elbow", "upper", "fore", axis=(0, 0, 1)')]})
    checks, data = mech_checks(d)
    c = one(checks, "every actuator is at the joint it drives")
    assert c.severity == "fail" and "elbow_servo has its output face at joint elbow normal to (0, 1, 0); the joint axis (0, 0, 1) is 90° off it" in c.measured
    assert data["coupling"]["actuators"]["elbow"]["axis_deg"] == 90.0 and data["coupling"]["actuators"]["elbow"]["distance_mm"] == 0.0
    # a small tilt is within the tolerance
    cad, mlm = load_model(UNO / "model.py"), load_model(UNO / "mech.py")
    parts, mech = cad.build(), mlm.build()
    elbow = next(j for j in mech.joints if j.name == "elbow")
    import numpy as np
    elbow.axis = np.array([0.0, -np.cos(np.radians(3)), np.sin(np.radians(3))])
    js = K.actuator_bindings(parts, mech, mlm.module.COUPLING)["joints"]
    assert js["elbow"]["ok"] and js["elbow"]["axis_deg"] == 3.0


def test_a_joint_without_a_body_warns_and_a_servo_that_drives_nothing_warns(tmp_path):
    d = variant(tmp_path, "nobody", {"model.py": [('"elbow_servo": elbow_servo, ', ""), (', "elbow_servo": "MG996R"}', "}")],
                                     "mech.py": [(LISTED, UNLISTED)]})
    checks, data = mech_checks(d)
    c = one(checks, "every actuator is at the joint it drives")
    assert c.severity == "warn" and "no body for the MG996R of joint elbow" in c.measured and data["coupling"]["actuators"]["elbow"]["part"] is None
    d2 = variant(tmp_path, "extra", {"model.py": [('"standoffs": standoffs, "controller": controller}', '"standoffs": standoffs, "controller": controller, "spare_servo": box(-60, 40, 8 + 21.45, L, W, H)}'),
                                                  ('HARDWARE = {"yaw_servo": "MG996R",', 'HARDWARE = {"spare_servo": "MG996R", "yaw_servo": "MG996R",')]})
    c2 = one(mech_checks(d2)[0], "every actuator is at the joint it drives")
    assert c2.severity == "warn" and "spare_servo is an actuator body that drives no joint of the mechanism" in c2.measured


# ---------------------------------------------------------------------------- bodies of every domain

def test_the_harness_connectors_and_the_board_are_bodies_of_the_attachment_check(tmp_path):
    cchecks, cdata = cad_checks(UNO)
    c = one(cchecks, "every body is attached")
    assert c.severity == "pass" and "(9 CAD parts, 1 board, 6 connectors, 2 splices)" in c.measured, c.measured
    att = cdata["placement"]["attachment"]
    assert att["UNO"] == {"link": "base", "attached": True, "distance_mm": 0.0, "to": "controller", "kind": "board"}
    assert att["SERVO_EL"]["kind"] == "connector" and att["SERVO_EL"]["to"] == "elbow_servo" and att["SERVO_EL"]["link"] == "upper"
    assert att["BUS_5V"]["kind"] == "splice" and "PSU" not in att                           # a bare wire end is not a body
    assert not any(c.name == "every harness component sits on a part" for c in cchecks)      # covered by the coupled check
    # a connector anchored in the air, and one whose anchor names no part and sits in none
    d = variant(tmp_path, "air", {"model.py": [('anchor("SERVO_SH", at=(20 - L / 2, 0, SHOULDER_Z), z=(-1, 0, 0), part="shoulder_servo")', 'anchor("SERVO_SH", at=(20 - L / 2, 0, SHOULDER_Z + 30), z=(-1, 0, 0), part="shoulder_servo")'),
                                               ('anchor("BUS_GND", at=(-25, -30, BASE_T), z=(0, 0, 1), part="base")', 'anchor("BUS_GND", at=(-25, -30, BASE_T + 40), z=(0, 0, 1))')]})
    c2 = one(cad_checks(d)[0], "every body is attached")
    assert c2.severity == "fail" and "connector SERVO_SH is 20.2 mm off its part shoulder_servo" in c2.measured and "splice BUS_GND floats: no part under its anchor" in c2.measured
    assert {"SERVO_SH", "BUS_GND"} <= set(c2.refs)


def test_without_a_mechanism_the_components_are_still_checked_on_their_parts(tmp_path):
    d = tmp_path / "nomech"
    shutil.copytree(UNO, d, ignore=shutil.ignore_patterns("__pycache__"))
    (d / "mech.py").unlink()
    cad = load_model(d / "model.py")
    parts = cad.build()
    checks, data = A.run(parts, anchors_json(parts), load_model(d / "wiring.py").build(), cad.module.INTEGRATION, "uno")
    c = one(checks, "every harness component sits on a part")
    assert c.severity == "pass" and "UNO on controller" in c.measured and "SERVO_EL on elbow_servo" in c.measured
    assert data["placed"]["BUS_5V"]["part"] == "base"
    # an anchor without part= is on the part it sits in (what the view draws); one in free space has none
    moved = [dict(a, part=None) if a["name"] == "SERVO_YAW" else a for a in anchors_json(parts)]
    checks2, data2 = A.run(parts, moved, load_model(d / "wiring.py").build(), cad.module.INTEGRATION, "uno")
    assert data2["placed"]["SERVO_YAW"]["part"] == "yaw_servo" and one(checks2, "every harness component sits on a part").severity == "pass"
    cchecks, _ = projectchecks.extras("cad", cad.module, parts, d / "model.py")
    assert one(cchecks, "every harness component sits on a part").severity == "pass" and not any("every body is attached" in c.name for c in cchecks)
