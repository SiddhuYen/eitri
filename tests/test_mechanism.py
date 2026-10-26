"""Mechanism kernel: the typed API, forward kinematics, static loads against hand-computed values, every check firing on a crafted
bad mechanism and passing on the fixed one, targets, direct edits, the diagram, the tables, the reference examples and the demo."""
from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from eitri.mechanism import (G, KGCM_PER_NM, Actuator, Mechanism, MechanismError, joint_loads, mass_rollup, sample_poses, solve_ik)
from eitri.mechanism import diagram, edits as E, outputs
from eitri.mechanism.report import analyze, mechanism_data, summarize
from eitri.model import Checker

LINK_G = 12.4          # a 100 x 10 x 10 mm PLA link: 10 cm^3 x 1.24 g/cm^3


def one(rep, needle):
    hits = [c for c in rep.checks if needle in c.name]
    assert len(hits) == 1, f"{needle!r} matched {[c.name for c in hits]} in {[c.name for c in rep.checks]}"
    return hits[0]


def cantilever(payload_g=100.0, limit=(-90.0, 90.0), actuator=Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=400, travel_deg=180), axis=(0, -1, 0), home=0.0):
    """A 100 mm, 12.4 g link on a joint at the origin, a point payload at its end."""
    m = Mechanism("cantilever")
    m.link("base", fixed=True)
    m.link("arm", length_mm=100, mass_g=LINK_G)
    m.joint("j1", "base", "arm", axis=axis, at=(0, 0, 0), limit_deg=limit, actuator=actuator, home=home)
    if payload_g:
        m.payload(mass_g=payload_g)
    m.poses = 0
    return m


# ---------------------------------------------------------------------------- the typed API and FK


def test_api_validation():
    m = Mechanism()
    m.link("base", fixed=True)
    with pytest.raises(MechanismError, match="already defined"):
        m.link("base")
    with pytest.raises(MechanismError, match="no link 'arm'"):
        m.joint("j", "base", "arm", limit_deg=(0, 1))
    m.link("arm", 50, 5)
    with pytest.raises(MechanismError, match="low end must be below"):
        m.joint("j", "base", "arm", limit_deg=(10, -10))
    with pytest.raises(MechanismError, match="limit_deg"):
        m.joint("j", "base", "arm")
    with pytest.raises(MechanismError, match="takes limit_deg"):
        m.joint("j", "base", "arm", limit_mm=(0, 10))
    with pytest.raises(MechanismError, match="zero vector"):
        m.joint("j", "base", "arm", axis=(0, 0, 0), limit_deg=(0, 1))
    with pytest.raises(MechanismError, match="outside the limits"):
        m.joint("j", "base", "arm", limit_deg=(10, 20), home=0)
    m.joint("j", "base", "arm", limit_deg=(0, 90))
    with pytest.raises(MechanismError, match="already moved"):
        m.joint("j2", "base", "arm", limit_deg=(0, 1))
    with pytest.raises(MechanismError, match="fixed link"):
        m.joint("j3", "arm", "base", limit_deg=(0, 1))
    with pytest.raises(MechanismError):
        Actuator("x", stall_kgcm=-1)
    with pytest.raises(MechanismError):
        Actuator("x", gear_ratio=0)
    with pytest.raises(MechanismError, match="no joint"):
        m.speed("nope", deg_s=10)
    with pytest.raises(MechanismError, match="deg_s"):
        m.speed("j", mm_s=10)


def test_forward_kinematics_matches_hand_geometry():
    m = cantilever(payload_g=0)
    assert m.tip_position({}) == pytest.approx([100, 0, 0])
    # about -Y a positive angle lifts the tip: at 90 degrees it points straight up
    assert m.tip_position({"j1": 90}) == pytest.approx([0, 0, 100], abs=1e-9)
    assert m.tip_position({"j1": 30}) == pytest.approx([100 * np.cos(np.radians(30)), 0, 50], abs=1e-9)
    # a second link, a joint at the first link's tip: the elbow folds back
    m.link("fore", length_mm=50, mass_g=0)
    m.joint("j2", "arm", "fore", axis=(0, -1, 0), limit_deg=(-180, 180))
    assert m.tip_position({}) == pytest.approx([150, 0, 0])
    assert m.tip_position({"j2": 180}) == pytest.approx([50, 0, 0], abs=1e-9)
    assert m.tip_position({"j1": 90, "j2": -90}) == pytest.approx([50, 0, 100], abs=1e-9)
    # the joint's world position moves with the links before it
    pts = m.points({"j1": 90})
    assert pts["joints"]["j2"] == pytest.approx([0, 0, 100], abs=1e-9) and pts["axes"]["j2"] == pytest.approx([0, -1, 0], abs=1e-9)


def test_link_direction_and_prismatic_joints():
    m = Mechanism()
    m.link("base", fixed=True)
    m.link("mast", length_mm=200, direction=(0, 0, 1))                      # a vertical mast
    m.link("carriage", length_mm=80)                                        # horizontal at the top of the mast at zero slide
    m.joint("fix", "base", "mast", type="fixed")
    m.joint("lift", "mast", "carriage", type="prismatic", axis=(0, 0, -1), at=(0, 0, 200), limit_mm=(0, 150))
    assert m.tip_position({}) == pytest.approx([80, 0, 200])
    assert m.tip_position({"lift": 50}) == pytest.approx([80, 0, 150])
    assert [j.name for j in m.moving_joints] == ["lift"]


def test_tree_errors_are_failed_checks():
    m = Mechanism()
    m.link("a", fixed=True)
    m.link("b", 10, 1)
    m.link("c", 10, 1)                                                      # never attached
    m.joint("j", "a", "b", limit_deg=(0, 1))
    c = one(analyze(m), "tree from the ground link")
    assert c.severity == "fail" and "'c'" in c.measured
    m2 = Mechanism()
    m2.link("a", fixed=True)
    m2.link("b", fixed=True)
    c = one(analyze(m2), "tree from the ground link")
    assert c.severity == "fail" and "more than one fixed" in c.measured
    m3 = Mechanism()
    m3.link("a", 10, 1)
    m3.link("b", 10, 1)
    m3.joint("j", "a", "b", limit_deg=(0, 1))
    assert m3.base == "a"                                                   # no fixed link, but one root: fine


# ---------------------------------------------------------------------------- statics, hand computed


def test_static_torque_matches_a_hand_computation():
    m = cantilever()
    # link 12.4 g with its centre 50 mm out, payload 100 g at 100 mm, horizontal
    expected_nm = (0.0124 * 0.050 + 0.100 * 0.100) * G
    expected = expected_nm * KGCM_PER_NM
    assert expected == pytest.approx(1.0620, abs=1e-3)
    assert joint_loads(m, {})["j1"] == pytest.approx(expected, rel=1e-6)
    assert joint_loads(m, {"j1": 60})["j1"] == pytest.approx(expected * 0.5, rel=1e-6)       # cos 60
    assert joint_loads(m, {"j1": 90})["j1"] == pytest.approx(0.0, abs=1e-9)
    rep = analyze(m)
    j = rep.joint_info[0]
    assert j.required == pytest.approx(expected, abs=1e-3) and j.worst_pose == {"j1": 0.0} and j.moving_mass_g == pytest.approx(112.4)
    c = one(rep, "torque joint j1")
    assert c.severity == "pass" and "1.1 kg·cm required at the worst pose (j1 0°) vs 9.4 kg·cm stall, margin 8.85x stall, 4.43x rated continuous (4.7 kg·cm, an estimate" in c.measured
    assert j.stall == 9.4 and j.rated == pytest.approx(4.7) and j.rated_is_estimate


def test_limits_change_the_worst_pose():
    m = cantilever(limit=(60, 90), home=60)                                 # never horizontal: the lever arm is cos 60 at most
    rep = analyze(m)
    assert rep.joint_info[0].required == pytest.approx(1.0620 * 0.5, abs=2e-3) and rep.joint_info[0].worst_pose == {"j1": 60.0}


def test_a_vertical_axis_has_no_gravity_load():
    m = cantilever(axis=(0, 0, 1))
    c = one(analyze(m), "torque joint j1")
    assert c.severity == "pass" and "no gravity load" in c.measured


def test_levels_pass_warn_fail():
    stall = 9.4
    for payload, sev in ((300, "pass"), (500, "warn"), (1000, "fail")):     # 3.0, 4.96, 9.9 kgf.cm against 4.7 rated / 9.4 stall
        c = one(analyze(cantilever(payload_g=payload)), "torque joint j1")
        assert c.severity == sev, (payload, c.measured)
        assert c.ok == (sev != "fail")
    c = one(analyze(cantilever(payload_g=1000)), "torque joint j1")
    assert "margin 0.93x stall" in c.measured and c.refs == ["j1"]


def test_rated_torque_from_the_datasheet_is_not_an_estimate():
    m = cantilever(payload_g=500, actuator=Actuator("x", stall_kgcm=9.4, rated_kgcm=6.0))
    rep = analyze(m)
    assert not rep.joint_info[0].rated_is_estimate and rep.joint_info[0].rated == 6.0
    assert one(rep, "torque joint j1").severity == "pass"                   # 4.96 < 6


def test_gearing_multiplies_torque_and_divides_speed():
    a = Actuator("geared", stall_kgcm=2.0, speed_deg_s=600, gear_ratio=4, efficiency=0.8, travel_deg=None)
    assert a.stall_out("revolute") == pytest.approx(6.4) and a.speed_out("revolute") == pytest.approx(150)
    m = cantilever(payload_g=200, actuator=a)
    m.speed("j1", deg_s=120)
    rep = analyze(m)
    assert one(rep, "torque joint j1").severity == "pass" and "x4 @ 80%" in one(rep, "torque joint j1").name
    s = one(rep, "speed joint j1")
    assert s.severity == "warn" and "120 °/s required vs 150 °/s available" in s.measured and "through the gearing" in s.measured


def test_speed_levels_and_ungraded():
    m = cantilever()
    m.speed("j1", deg_s=100)
    assert one(analyze(m), "speed joint j1").severity == "pass"
    m.speed("j1", deg_s=500)
    assert one(analyze(m), "speed joint j1").severity == "fail"
    m2 = cantilever(actuator=Actuator("slow", stall_kgcm=9.4))             # no speed figure
    m2.speed("j1", deg_s=10)
    c = one(analyze(m2), "speed joint j1")
    assert c.severity == "warn" and "declares no speed" in c.measured
    m3 = cantilever()                                                       # a speed but no requirement: informative
    assert "0.45 s across its 180 deg range" in one(analyze(m3), "speed joint j1").measured


def test_missing_or_incomplete_actuator():
    c = one(analyze(cantilever(actuator=None)), "torque joint j1")
    assert c.severity == "warn" and "no actuator declared" in c.measured
    c = one(analyze(cantilever(actuator=Actuator("x", speed_deg_s=100))), "torque joint j1")
    assert c.severity == "fail" and "declares no stall_kgcm" in c.measured


def test_prismatic_force_is_the_gravity_component_along_the_axis():
    m = Mechanism()
    m.link("base", fixed=True)
    m.link("slide", length_mm=0, mass_g=0)
    m.joint("lift", "base", "slide", type="prismatic", axis=(0, 0, 1), at=(0, 0, 0), limit_mm=(0, 100),
            actuator=Actuator("linear", stall_N=20, rated_N=12, speed_mm_s=50, travel_mm=100))
    m.payload(mass_g=1000, link="slide", at=(0, 0, 0))
    rep = analyze(m)
    j = rep.joint_info[0]
    assert j.load_unit == "N" and j.required == pytest.approx(G, abs=1e-4)             # 1 kg straight up: 9.81 N
    c = one(rep, "force joint lift")
    assert c.severity == "pass" and "9.8 N required" in c.measured
    m.masses[0].mass_g = 1500                                                            # 14.7 N: above rated, below stall
    assert one(analyze(m), "force joint lift").severity == "warn"
    m.joints[0].axis = np.array([1.0, 0, 0])                                             # a horizontal slide carries no gravity load
    assert one(analyze(m), "force joint lift").measured.startswith("0.0 N")


def test_joint_limits_against_the_actuator_travel():
    m = cantilever(limit=(-100, 100))                                                    # 200 degrees on a 180 degree servo
    c = one(analyze(m), "joint limits j1")
    assert c.severity == "fail" and "exceeds the actuator's travel of 180" in c.measured
    assert one(analyze(cantilever(limit=(-90, 90))), "joint limits j1").severity == "pass"


def test_actuator_mass_sits_on_the_parent_link():
    m = cantilever(payload_g=0, actuator=Actuator("servo", stall_kgcm=9.4, mass_g=55))
    m.link("fore", 50, 0)
    m.joint("j2", "arm", "fore", axis=(0, -1, 0), limit_deg=(0, 1), actuator=Actuator("small", stall_kgcm=2, mass_g=14))
    roll = mass_rollup(m)
    assert roll["links_g"] == {"base": 55.0, "arm": pytest.approx(LINK_G + 14), "fore": 0.0} and roll["total_g"] == pytest.approx(LINK_G + 69)
    assert roll["moving_g"]["j1"] == pytest.approx(LINK_G + 14) and roll["moving_g"]["j2"] == 0.0
    # the elbow servo's 14 g at 100 mm loads the shoulder: + 0.014 * 0.1 * g
    assert joint_loads(m, {})["j1"] == pytest.approx((0.0124 * 0.05 + 0.014 * 0.1) * G * KGCM_PER_NM, rel=1e-6)
    assert "arm 26.4 g" in one(analyze(m), "mass rollup").measured


# ---------------------------------------------------------------------------- targets and reach


def arm():
    m = Mechanism("arm")
    m.link("base", fixed=True)
    m.link("turret", 40, 70, direction=(0, 0, 1))
    m.link("upper", 120, 60)
    m.link("fore", 100, 30)
    big = Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=400, travel_deg=180, mass_g=55)
    m.joint("yaw", "base", "turret", axis=(0, 0, 1), at=(0, 0, 0), limit_deg=(-90, 90), actuator=big)
    m.joint("shoulder", "turret", "upper", axis=(0, -1, 0), limit_deg=(-20, 100), actuator=big)
    m.joint("elbow", "upper", "fore", axis=(0, -1, 0), limit_deg=(-10, 120), actuator=Actuator("MG90S", stall_kgcm=2.2, speed_deg_s=600, travel_deg=180, mass_g=14))
    m.payload(mass_g=50)
    m.poses = 8
    return m


def test_targets_reachable_unreachable_and_diagnosed():
    m = arm()
    m.target("ahead", (220, 0, 40), tol_mm=1)                                            # the home pose itself
    m.target("low", (150, 60, 20))                                                       # needs shoulder -45: outside the -20 limit
    m.target("far", (300, 0, 40))                                                        # beyond 220 mm of links
    rep = analyze(m)
    ok, low, far = (one(rep, f"target {n} reachable") for n in ("ahead", "low", "far"))
    assert ok.severity == "pass" and "reached within 0.0" in ok.measured
    assert low.severity == "fail" and "closest approach 46.9 mm" in low.measured and "would be reached within 0.0 mm with the limits ignored (at yaw 22°, shoulder -45°, elbow 85°): widen the limits" in low.measured
    assert far.severity == "fail" and "out of reach even with the limits ignored" in far.measured
    ti = {t.name: t for t in rep.target_info}
    assert ti["ahead"].reachable and ti["low"].distance_mm == pytest.approx(46.9, abs=0.2) and ti["low"].unlimited_mm == pytest.approx(0.0, abs=0.01)
    assert ok.refs == ["ahead"]                                                          # a target check is about the target only
    # widen the shoulder limit: the low target is reached, by the hand-computed pose
    m.get_joint("shoulder").limit = (-50.0, 100.0)
    rep = analyze(m)
    assert one(rep, "target low reachable").severity == "pass"
    t = next(t for t in rep.target_info if t.name == "low")
    assert t.pose["yaw"] == pytest.approx(21.8, abs=0.2) and t.pose["shoulder"] == pytest.approx(-44.8, abs=0.3) and t.pose["elbow"] == pytest.approx(85.0, abs=0.3)


def test_reach_envelope_and_requirement():
    m = arm()
    rep = analyze(m)
    assert rep.reach["max_radius_mm"] == pytest.approx(260, abs=1) and rep.reach["z_max_mm"] > 250 and rep.reach["from"] == [0, 0, 0]
    assert one(rep, "reach envelope").severity == "pass"
    m.require_reach(300)
    c = one(analyze(m), "reach envelope")
    assert c.severity == "fail" and ">= 300" in c.expected


def test_sample_poses_are_deterministic_and_within_limits():
    m = arm()
    a, b = sample_poses(m), sample_poses(m)
    assert a == b and a[0] == m.home_pose() and len(a) > 30
    for p in a:
        for j in m.moving_joints:
            assert j.limit[0] <= p[j.name] <= j.limit[1]


def test_solve_ik_without_targets_and_verify_helpers():
    m = arm()
    d, pose = solve_ik(m, np.array([220.0, 0, 40]), sample_poses(m))
    assert d < 0.01 and abs(pose["shoulder"]) < 0.5
    c = Checker()
    c.at_most("shoulder at home", joint_loads(m, {})["shoulder"], 4.0)
    rep = analyze(m, c)
    assert one(rep, "shoulder at home").severity == "pass" and one(rep, "shoulder at home").category == "verify"


# ---------------------------------------------------------------------------- summary, data, outputs, diagram


def test_summary_and_data():
    m = arm()
    m.target("t", (220, 0, 40))
    rep = analyze(m)
    text = summarize(rep)
    assert text.startswith("Mechanism 'arm': 4 links, 3 moving joint(s), 1 target(s)") and "Joints (worst-pose load vs actuator):" in text
    assert "shoulder (revolute): -20..100 deg, needs 2.14 kg·cm" in text and "Targets: t reached" in text
    data = mechanism_data(m, rep)
    assert [j["name"] for j in data["joints"]] == ["yaw", "shoulder", "elbow"] and data["joints"][1]["required"] == pytest.approx(2.138, abs=2e-3)
    assert data["joints"][1]["world_at"] == pytest.approx([0, 0, 40]) and data["tip"]["link"] == "fore" and data["targets"][0]["reachable"] is True
    assert data["links"][2]["parent_joint"] == "shoulder" and data["base"] == "base"


def test_tables():
    m = arm()
    rep = analyze(m)
    rows = outputs.joint_rows(rep)
    assert [r["joint"] for r in rows] == ["yaw", "shoulder", "elbow"] and rows[1]["margin_stall"] == pytest.approx(4.40, abs=0.01)
    parsed = list(csv.DictReader(io.StringIO(outputs.joints_csv(rows))))
    assert parsed[1]["actuator"] == "MG996R" and float(parsed[1]["required"]) == pytest.approx(2.138, abs=2e-3)
    md = outputs.joints_markdown(m, rep)
    assert "| shoulder |" in md and "4.40x / 2.20x" in md
    mass = outputs.mass_rows(m)
    assert {r["item"] for r in mass} >= {"turret", "payload", "MG996R (yaw)"} and "Total 334 g" in outputs.mass_markdown(m, rep)


def test_diagram_svg_and_png(tmp_path):
    m = arm()
    m.target("t", (300, 0, 40))
    rep = analyze(m)
    sc = diagram.build_scene(m, rep, diagram.status_from(rep))
    svg = diagram.scene_to_svg(sc)
    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    ids = {(g.get("data-kind"), g.get("data-id")) for g in root.iter(ns + "g") if g.get("data-id")}
    assert {("joint", "shoulder"), ("link", "fore"), ("target", "t"), ("mass", "payload")} <= ids
    texts = " ".join("".join(t.itertext()) for t in root.iter(ns + "text"))
    assert "shoulder  2.1/9.4 kg·cm" in texts and "t (missed)" in texts and "side view" in texts
    assert sc.nodes["elbow"]["kind"] == "joint"
    diagram.scene_to_png(diagram.build_scene(m, rep, diagram.status_from(rep), footer=diagram.footer_from(rep)), tmp_path / "m.png")
    from PIL import Image
    img = Image.open(tmp_path / "m.png")
    assert img.size[0] > 800
    # a broken structure still draws a (short) diagram
    bad = Mechanism("bad")
    bad.link("a", fixed=True)
    bad.link("b", fixed=True)
    ET.fromstring(diagram.scene_to_svg(diagram.build_scene(bad, analyze(bad))))


# ---------------------------------------------------------------------------- direct edits


def test_edits_apply_describe_merge():
    m = arm()
    notes = E.apply_edits(m, [
        {"op": "set_joint", "joint": "shoulder", "limit": [-50, 100]},
        {"op": "set_actuator", "joint": "elbow", "stall_kgcm": 4.0, "name": "bigger"},
        {"op": "set_link", "link": "fore", "length_mm": 120, "mass_g": 35},
        {"op": "set_payload", "mass_g": 80},
        {"op": "add_target", "name": "x", "at": [100, 0, 100]},
        {"op": "set_target", "target": "x", "tol_mm": 5},
        {"op": "set_speed", "joint": "yaw", "value": 90},
        {"op": "set_joint", "joint": "ghost", "limit": [0, 1]},
        {"op": "remove_target", "target": "x"},
    ])
    assert [n.ok for n in notes] == [True] * 7 + [False, True] and "ghost" in notes[7].detail
    assert m.get_joint("shoulder").limit == (-50.0, 100.0) and m.get_joint("elbow").actuator.name == "bigger" and m.get_joint("elbow").actuator.stall_kgcm == 4.0
    assert m.links["fore"].length_mm == 120 and m.links["fore"].cg[0] == 60 and m.masses[0].mass_g == 80 and m.speeds["yaw"] == 90 and not m.targets
    assert E.describe({"op": "set_joint", "joint": "shoulder", "limit": [-50, 100]}) == "joint shoulder: limit = [-50, 100]"
    assert E.describe({"op": "set_actuator", "joint": "elbow", "remove": True}) == "joint elbow: no actuator"
    merged = E.merge_edit({"op": "set_link", "link": "fore", "length_mm": 120}, {"op": "set_link", "link": "fore", "mass_g": 35, "by": "x"})
    assert merged == {"op": "set_link", "link": "fore", "length_mm": 120, "mass_g": 35}
    assert E.merge_edit({"op": "set_link", "link": "fore", "length_mm": 120}, {"op": "set_link", "link": "upper", "mass_g": 35}) is None
    assert E.describe_state(m)["joint elbow"]["actuator"] == "bigger"
    from eitri.edits import describe, merge_edit
    assert describe({"op": "set_speed", "joint": "yaw", "value": 90}) == "required speed of yaw = 90"       # dispatched through eitri.edits
    assert merge_edit({"op": "set_payload", "mass_g": 1}, {"op": "set_payload", "mass_g": 2}) == {"op": "set_payload", "mass_g": 2}


# ---------------------------------------------------------------------------- the reference examples and the demo


def test_reference_examples_build_green(tmp_path):
    from eitri.agent.prompt import domain_reference_text
    from eitri.model import load_model
    text = domain_reference_text("mechanism")
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    assert len(blocks) == 2
    for i, code in enumerate(blocks):
        f = tmp_path / f"ex{i}" / "model.py"
        f.parent.mkdir()
        f.write_text(code, encoding="utf-8")
        lm = load_model(f)
        m = lm.build()
        rep = analyze(m, lm.verify(m))
        assert rep.ok and not rep.warnings, f"example {i + 1}:\n{summarize(rep)}"
    # the pan-tilt's tilt load at level: 90 g x 35 mm + the cradle's 10 g at 17.5 mm
    lm = load_model(tmp_path / "ex1" / "model.py")
    m = lm.build()
    assert joint_loads(m, {})["tilt"] == pytest.approx((0.09 * 0.035 + 0.010 * 0.0175) * G * KGCM_PER_NM, rel=1e-6)


def test_pan_tilt_demo_and_its_configs():
    from eitri.model import load_model
    demo = Path(__file__).resolve().parents[1] / "eitri" / "demos" / "pan-tilt" / "pan_tilt.py"
    lm = load_model(demo)
    m = lm.build()
    rep = analyze(m, lm.verify(m))
    assert rep.ok and not rep.warnings, summarize(rep)
    heavy = load_model(demo, config="heavy")
    mh = heavy.build()
    reph = analyze(mh, heavy.verify(mh))
    assert one(reph, "torque joint tilt").severity == "fail" and one(reph, "tilt load at level").severity == "fail"


def test_mass_rollup_says_where_the_payload_is_counted():
    m = cantilever(payload_g=50)
    roll = mass_rollup(m)
    text = one(analyze(m), "mass rollup").measured
    # the payload is part of the tip link's figure and of the total, once
    assert roll["total_g"] == pytest.approx(sum(roll["links_g"].values())) and "the 50 g payload counted on arm" in text


def test_a_misused_payload_call_says_how_to_call_it():
    m = cantilever(payload_g=0)
    with pytest.raises(MechanismError, match=r"mass_g must be a number of grams, not 'payload' .*payload\(mass_g"):
        m.payload("payload", 50)
