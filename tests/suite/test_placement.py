"""Where the bodies are in the assembly (eitri_suite.kinematics) and what the view draws once (eitri_suite.assembly.stand_ins):
every body attached to the base or a link (a floating one named, with its distance), no interference at rest (a board sunk into its
floor fails, one lying flat on it warns), the payload attached to the end-effector frame, carried through the plan and swept
against every body but the fingers, and a board drawn once where its CAD stand-in is. The desk arm (eitri_suite/demos/desk-gripper)
is the bottle-cap project: a folded home, a wrist, a gripper, an Uno on standoffs in a clear enclosure on a clear plate."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from eitri.anchors import to_json as anchors_json
from eitri.model import load_model
from eitri_suite import assembly as A
from eitri_suite import kinematics as K
from eitri_suite import coupling as C
from eitri_suite import projectchecks

DEMOS = Path(__file__).resolve().parents[2] / "eitri_suite" / "demos"
DESK = DEMOS / "desk-gripper"


def one(checks, needle):
    hits = [c for c in checks if needle in c.name]
    assert len(hits) == 1, [c.name for c in checks]
    return hits[0]


def variant(tmp_path, name, edits: dict[str, list[tuple[str, str]]]) -> Path:
    d = tmp_path / name
    shutil.copytree(DESK, d, ignore=shutil.ignore_patterns("__pycache__"))
    for f, subs in edits.items():
        text = (d / f).read_text(encoding="utf-8")
        for a, b in subs:
            assert a in text, (f, a)
            text = text.replace(a, b)
        (d / f).write_text(text, encoding="utf-8")
    return d


def built(folder: Path):
    cad = load_model(folder / "model.py")
    mlm = load_model(folder / "mech.py")
    return cad.build(), mlm.build(), mlm.module.COUPLING


def mech_checks(folder: Path):
    lm = load_model(folder / "mech.py")
    return projectchecks.extras("mechanism", lm.module, lm.build(), folder / "mech.py")


@pytest.fixture(scope="module")
def desk():
    return built(DESK)


# ---------------------------------------------------------------------------- the kinematics are the kernel's, about zero-pose pivots

def test_the_home_pose_is_not_the_zero_pose_and_the_bodies_follow_the_kernel(desk):
    parts, mech, coupling = desk
    assert {j.name: j.home for j in mech.joints}["shoulder"] == 45 and mech.home_pose()["wrist"] == -90
    from eitri.mechanism.report import mechanism_data
    d = mechanism_data(mech)
    wrist = next(j for j in d["joints"] if j["name"] == "wrist")
    assert np.linalg.norm(np.array(wrist["world_at"]) - np.array(wrist["at"])) > 50     # world_at is the HOME point: never a pivot
    # the gripper at home, through the kernel's transforms about the zero-pose pivots: on the end of the forearm, not on the ground
    T = mech.transforms(mech.home_pose())
    grip = K.moved(next(p for p in parts if p.name == "gripper_base").shape, T["grip"])
    fore = K.moved(next(p for p in parts if p.name == "wrist_servo").shape, T["fore"])
    from eitri import measure
    assert measure.min_distance(grip, fore) < 0.5 and measure.bounding_box(grip).min.Z > 20


# ---------------------------------------------------------------------------- every body is attached

def test_every_body_of_the_desk_arm_is_attached(desk):
    parts, mech, coupling = desk
    c, info = K.attachment_check(parts, mech, coupling["links"])
    assert c.severity == "pass" and "15 bodies attached at the zero pose" in c.measured
    assert info["bodies"]["finger_jaw"] == {"link": "jaw", "attached": True, "distance_mm": 0.0, "to": info["bodies"]["finger_jaw"]["to"], "kind": "cad"}
    assert info["bodies"]["controller"]["link"] == "base" and info["implicit_base"] == []


def test_a_floating_body_fails_naming_it_and_its_distance(tmp_path):
    d = variant(tmp_path, "float", {"model.py": [("wrist_servo = box(x_wr, ", "wrist_servo = box(x_wr - 150, ")]})
    checks, data = mech_checks(d)
    c = one(checks, "every body is attached")
    assert c.severity == "fail" and c.refs == ["wrist_servo"]
    assert "wrist_servo (link fore) floats" in c.measured and "mm from the nearest attachment point (forearm)" in c.measured
    b = data["coupling"]["attachment"]["wrist_servo"]
    assert b["attached"] is False and b["distance_mm"] == pytest.approx(58.6, abs=0.5)
    # the CAD build of the same project says the same (whichever side changed)
    cad = load_model(d / "model.py")
    cchecks, _ = projectchecks.extras("cad", cad.module, cad.build(), d / "model.py")
    assert one(cchecks, "every body is attached").severity == "fail"


def test_a_body_on_a_link_the_mechanism_lacks_never_moves_and_fails(tmp_path):
    d = variant(tmp_path, "unknown", {"mech.py": [('"grip": ["gripper_base"', '"gripper": ["gripper_base"')]})
    c = one(mech_checks(d)[0], "every body is attached")
    assert c.severity == "fail" and "gripper_base is listed under link 'gripper', which the mechanism does not have" in c.measured
    assert set(c.refs) >= {"gripper_base", "gripper_servo", "finger_fixed"}


def test_a_body_listed_twice_and_an_unlisted_one(desk):
    parts, mech, coupling = desk
    links = {k: list(v) for k, v in coupling["links"].items()}
    links["upper"].append("forearm")                        # forearm also on fore
    links["base"].remove("standoffs")                       # not listed: taken as fixed to the base (it is: it stands on the floor)
    c, info = K.attachment_check(parts, mech, links)
    assert c.severity == "fail" and "forearm is listed under 2 links (upper, fore)" in c.measured and "standoffs" in info["implicit_base"]


# ---------------------------------------------------------------------------- interference at rest, and a board on its mounts

def test_a_board_sunk_into_its_floor_is_interference(tmp_path):
    d = variant(tmp_path, "sunk", {"model.py": [("board_z = floor_top + p.standoff_h ", "board_z = floor_top - 0.4 ")]})
    c = one(mech_checks(d)[0], "bodies do not interfere at rest")
    assert c.severity == "fail" and c.refs == ["enclosure", "controller"] and "enclosure and controller overlap by 1463.22 mm^3" in c.measured


def test_bodies_across_a_joint_may_overlap_at_rest_but_not_otherwise(desk):
    parts, mech, coupling = desk
    import build123d as bd
    from eitri.model import Part, Parts
    horn = Part("horn", bd.Pos(20, 19.7, 63.75) * bd.Box(10, 3.5, 10), None)     # inside the shoulder servo's face (turret), on the upper arm's face
    with_horn = Parts([*parts, horn])
    on_upper = {**coupling["links"], "upper": [*coupling["links"]["upper"], "horn"]}
    c, found = K.interference_check(with_horn, mech, on_upper)
    assert c.severity == "pass" and found == [], c.measured           # turret / upper: the two links of the shoulder joint
    on_fore = {**coupling["links"], "fore": [*coupling["links"]["fore"], "horn"]}
    c2, found2 = K.interference_check(with_horn, mech, on_fore)
    assert c2.severity == "fail" and c2.refs == ["shoulder_servo", "horn"] and found2[0]["links"] == ["turret", "fore"]


def test_a_board_lying_flat_on_its_floor_warns_and_on_standoffs_passes(tmp_path):
    def rest(folder):
        cad = load_model(folder / "model.py")
        parts = cad.build()
        har = load_model(folder / "wiring.py").build()
        return one(A.run(parts, anchors_json(parts), har, cad.module.INTEGRATION, "uno")[0], "rests on its mounts")
    ok = rest(DESK)
    assert ok.severity == "pass" and "touches standoffs over 79 mm^2 (2% of its footprint)" in ok.measured
    d = variant(tmp_path, "flush", {"model.py": [("board_z = floor_top + p.standoff_h ", "board_z = floor_top "),
                                                  ("standoffs = s if standoffs is None else standoffs + s", "standoffs = s if standoffs is None else standoffs + s\n    standoffs = Pos(-30, 0, 0) * standoffs")]})
    flat = rest(d)
    assert flat.severity == "warn" and "controller lies flat on enclosure" in flat.measured and "(100% of its footprint)" in flat.measured


# ---------------------------------------------------------------------------- one board, drawn once

def test_the_board_is_drawn_once_where_its_cad_stand_in_is(desk):
    parts = desk[0]
    har = load_model(DESK / "wiring.py").build()
    checks, data = A.run(parts, anchors_json(parts), har, None, "uno")
    assert data["replaces"] == {"UNO": "controller"} and data["placed"]["UNO"]["kind"] == "board"
    # the reference Uno arm too (its stand-in PCB at the anchor), and never a part of another size
    uno = load_model(DEMOS / "uno-arm" / "model.py")
    up = uno.build()
    _c, udata = A.run(up, anchors_json(up), load_model(DEMOS / "uno-arm" / "wiring.py").build(), None, "uno")
    assert udata["replaces"] == {"UNO": "controller"}
    moved = [dict(a, part="base") if a["name"] == "UNO" else a for a in anchors_json(up)]
    _c, mdata = A.run(up, moved, load_model(DEMOS / "uno-arm" / "wiring.py").build(), None, "uno")
    assert mdata["replaces"] == {}                                     # the plate is not a stand-in for the board


# ---------------------------------------------------------------------------- the payload, attached to the end effector and swept

def test_the_cap_is_grasped_carried_in_the_gripper_frame_and_released_at_place(desk):
    parts, mech, coupling = desk
    seq = C.firmware_sequence(DESK, mech, "mech.py")
    from eitri.mechanism.checks import run_checks
    pl = K.plan(mech, seq, run_checks(mech)[2])
    assert pl["source"] == "firmware SEQUENCE" and [s["name"] for s in pl["steps"] if s["grab"]] == ["grab"] and [s["name"] for s in pl["steps"] if s["release"]] == ["release"]
    spec = K.payload_spec(coupling, mech, coupling["links"])
    assert spec["shape"] == "cylinder" and spec["size_mm"] == [30.0, 12.0] and spec["fingers"] == ["finger_fixed", "finger_jaw"] and spec["tip_link"] == "grip"
    checks, data = K.payload_sweep(parts, mech, coupling["links"], spec, pl)
    assert one(checks, "carried payload").severity == "pass" and data["samples"] > 30 and data["hits"] == []
    grab, release = data["events"]
    assert grab["event"] == "grab" and grab["tip_to_object_mm"] < 1.0
    assert release["event"] == "release" and np.linalg.norm(np.array(release["at"]) - np.array(pl["place"]["at"])) < 1.0     # set down on place
    assert one(checks, "closes on the payload").severity == "pass"
    # attached: between grab and release the cap keeps one pose in the end-effector frame, so it moves (and turns) with it
    qg = next(s["q"] for s in pl["steps"] if s["grab"])
    G = np.linalg.inv(K.tip_frame(mech, qg)) @ np.vstack([np.hstack([np.eye(3), np.array(pl["pick"]["at"])[:, None]]), [0, 0, 0, 1]])
    lift = next(s["q"] for s in pl["steps"] if s["name"] == "above place")
    P = K.tip_frame(mech, lift) @ G
    assert P[2, 3] > 30 and np.linalg.norm(P[:3, 3] - np.array(pl["pick"]["at"])) > 50                   # it went up and across with the gripper


def test_a_payload_that_hits_the_arm_fails_with_the_joint_configuration(tmp_path):
    d = variant(tmp_path, "bigcap", {"mech.py": [("size_mm=[30, 12]", "size_mm=[30, 60]")]})
    c = one(mech_checks(d)[0], "carried payload")
    assert c.severity == "fail" and c.measured.startswith("bottle cap and gripper_servo overlap by")
    assert "at {yaw: -35.0, shoulder:" in c.measured and "(moving grab -> lift," in c.measured and c.refs == ["gripper_servo"]


def test_the_fingers_are_the_only_bodies_left_out(tmp_path):
    squeezed = ("size_mm=[30, 12]", "size_mm=[31, 12]")                                   # the jaw closes 0.5 mm into the cap on each side
    d = variant(tmp_path, "fingers", {"mech.py": [squeezed]})
    assert one(mech_checks(d)[0], "carried payload").severity == "pass"
    d2 = variant(tmp_path, "nofingers", {"mech.py": [squeezed, ('fingers=["finger_fixed", "finger_jaw"]', "fingers=[]")]})
    c = one(mech_checks(d2)[0], "carried payload")
    assert c.severity == "fail" and c.refs[0] in ("finger_fixed", "finger_jaw")          # the fingers' contacts, and nothing else, are left out


def test_no_payload_body_unless_the_object_is_declared():
    """A prompt that asks to lift something light gets a mass in the torque checks (m.payload), not a visible object: no cube appears
    near the arm, nothing is swept, and the view (asmrun.js payloadSpec) has nothing to draw. COUPLING payload= is for an object
    the person asked to model."""
    lm = load_model(DEMOS / "uno-arm" / "mech.py")
    mech = lm.build()
    spec = K.payload_spec(lm.module.COUPLING, mech, lm.module.COUPLING["links"])
    assert spec["declared"] is False and spec["shape"] is None and spec["size_mm"] is None and spec["mass_g"] == 50.0
    checks, data = projectchecks.extras("mechanism", lm.module, mech, DEMOS / "uno-arm" / "mech.py")
    c = one(checks, "carried payload")
    assert c.severity == "pass" and "no payload body: the 50 g payload rides in the torque checks as a mass" in c.measured
    pl = data["coupling"]["payload"]
    assert pl["declared"] is False and pl["body"] is False and pl["carried"] is False and pl["shape"] is None
    # the torque checks still carry the 50 g at the tip
    from eitri.mechanism.checks import mass_rollup
    assert mass_rollup(mech)["payload_g"] == 50.0 and any(c.name.startswith("torque") and "masses from CAD" in c.name for c in checks)
    # declared, the object is a body: swept and drawn
    declared = K.payload_spec({"payload": {"shape": "box", "size_mm": [20]}}, mech, {})
    assert declared["declared"] is True and declared["size_mm"] == [20.0, 20.0, 20.0]


# ---------------------------------------------------------------------------- wire segments against the distance between their ends

def test_wire_segments_agree_with_their_ends_and_a_stretched_one_is_flagged(desk):
    parts = desk[0]
    har = load_model(DESK / "wiring.py").build()
    checks, data = A.run(parts, anchors_json(parts), har, None, "uno")
    assert one(checks, "wire paths agree").severity == "pass"
    # a path that does not reach its pin (the view drew one end where the body was not) fails, naming the wire
    paths = {k: dict(v) for k, v in data["paths"].items()}
    pts = [list(p) for p in paths["WR_SIG"]["points"]]
    pts[-1][2] -= 150                                                                  # its last point down on the ground, away from the pin
    paths["WR_SIG"] = dict(paths["WR_SIG"], points=pts)
    c = A.segment_check(har, paths, data["pins"])
    assert c.severity == "fail" and c.refs == ["WR_SIG"] and "WR_SIG: its path does not reach its pins" in c.measured
    # a routed wire whose end moved away from its bundle spans the gap in free air: flagged at the pose where it does
    routes = [{"name": "arm", "wires": ["WR_SIG"], "waypoints": [[-60, 0, 30], [0, 30, 70], [100, 60, 70]]}]
    rpaths = A.wire_paths(har, data["pins"], list(data["boxes"].values()), routes)
    far = {"label": "step 'far'", "T": {"wrist_servo": np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, -150.0], [0, 0, 0, 1]])}}
    pp = {k: (data["placed"].get(k.split(".")[0]) or {}).get("part") for k in data["pins"]}
    c2 = A.segment_check(har, rpaths, data["pins"], poses=[far], boxes=data["boxes"], routes=routes, part_of_pin=pp)
    assert c2.severity == "warn" and "WR_SIG: leaves its bundle and spans" in c2.measured and "(step 'far')" in c2.measured


def test_the_desk_arm_passes_every_cross_domain_check():
    for f, kind in (("model.py", "cad"), ("wiring.py", "harness"), ("mech.py", "mechanism"), ("fw.py", "firmware")):
        lm = load_model(DESK / f)
        checks, _ = projectchecks.extras(kind, lm.module, lm.build(), DESK / f)
        bad = [(c.name, c.severity, c.measured[:160]) for c in checks if c.severity != "pass"]
        assert bad == [], (f, bad)
