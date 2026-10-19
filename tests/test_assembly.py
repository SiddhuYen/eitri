"""Assemblies: joints (revolute on a pin, slider on faces, limits, chains that follow, motion links), groups
moved together, components inserted from a STEP file, the bill of materials and drawing balloons."""
from __future__ import annotations

import math

import build123d as bd
import pytest

from eitri import assembly as A
from eitri.drawing import drawing_svg, parts_list
from eitri.edits import apply_edits, describe, selector_for_face
from eitri.model import Part, Parts
from eitri.report import analyze, summarize
from eitri.viewglb import topology


def ok(notes):
    assert all(n.ok for n in notes), [n.detail for n in notes]


def lever_assembly():
    """A base plate with a vertical pin at the origin, and a lever (bar with a bore) lying beside it."""
    base = bd.Box(60, 40, 6, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)) + bd.Pos(0, 0, 6) * bd.Cylinder(4, 20, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    lever = (bd.Pos(100, 0, 0) * bd.Box(50, 10, 5, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))) - bd.Pos(100, 0, 0) * bd.Cylinder(4, 20)
    return Parts([Part("base", base, "#999", "aluminum"), Part("lever", lever, "#c33", "steel")])


def test_revolute_joint_on_a_pin_drive_limits_and_chain():
    parts = lever_assembly()
    t = topology(parts)
    bore = next(f for f in t.faces if f.part == "lever" and f.type == "cylinder" and f.concave)
    pin = next(f for f in t.faces if f.part == "base" and f.type == "cylinder" and not f.concave)
    edits = [
        {"op": "joint", "type": "revolute", "name": "pivot", "part": "lever", "to_part": "base", "face": selector_for_face(bore.__dict__), "to_face": selector_for_face(pin.__dict__), "center": False, "limits": {"min": 0, "max": 90}},
    ]
    out, notes = apply_edits(parts, edits)
    ok(notes)
    assert len(out.joints) == 1 and out.joints[0]["type"] == "revolute" and out.joints[0]["dofs"] == ["angle"]
    j = out.joints[0]
    assert [round(x, 6) for x in j["axis"]] == [0, 0, 1] and abs(j["origin"][0]) < 1e-6 and abs(j["origin"][1]) < 1e-6 and j["limits"] == {"angle": [0, 90]}
    lb = out["lever"].bounding_box()
    assert abs(lb.min.X) < 1e-6 and abs(lb.max.X - 50) < 1e-6                         # the bore axis now sits on the pin at the origin
    # drive to 45 degrees: the bar's far end swings around the pin
    out2, notes = apply_edits(parts, edits + [{"op": "drive_joint", "joint": "pivot", "value": 45}])
    ok(notes)
    lb = out2["lever"].bounding_box()
    c45, s45 = math.cos(math.radians(45)), math.sin(math.radians(45))
    assert abs(lb.max.X - (50 * c45 + 5 * s45)) < 1e-6 and abs(lb.max.Y - (50 * s45 + 5 * c45)) < 1e-6 and out2.joints[0]["value"]["angle"] == 45
    assert "angle 45°" in notes[-1].detail
    # beyond the limit: clamped to 90, and said so
    out3, notes = apply_edits(parts, edits + [{"op": "drive_joint", "joint": "pivot", "value": 135}])
    ok(notes)
    assert out3.joints[0]["value"]["angle"] == 90 and "limited to 90" in notes[-1].detail
    lb = out3["lever"].bounding_box()
    assert abs(lb.max.Y - 50) < 1e-6 and abs(lb.min.X + 5) < 1e-6
    # a second drive sets an absolute value (not cumulative): 90 then 30 ends at 30
    out4, notes = apply_edits(parts, edits + [{"op": "drive_joint", "joint": "pivot", "value": 90}, {"op": "drive_joint", "joint": "pivot", "value": 30}])
    ok(notes)
    assert out4.joints[0]["value"]["angle"] == 30 and abs(out4["lever"].bounding_box().max.X - 50 * math.cos(math.radians(30)) - 5 * math.sin(math.radians(30))) < 0.5
    # a knob jointed rigidly onto the lever's end follows the swing, and its own joint frame moves too
    knob = bd.Pos(146, 0, 5) * bd.Cylinder(3, 8, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    parts3 = Parts(list(parts) + [Part("knob", knob, "#3a3", "pla")])
    t = topology(parts3)
    lever_top = next(f for f in t.faces if f.part == "lever" and f.type == "plane" and abs(f.center[2] - 5) < 1e-6 and f.normal[2] > 0.9)
    knob_bottom = next(f for f in t.faces if f.part == "knob" and f.type == "plane" and f.normal[2] < -0.9)
    edits3 = [
        {"op": "joint", "type": "rigid", "part": "knob", "to_part": "lever", "face": selector_for_face(knob_bottom.__dict__), "to_face": selector_for_face(lever_top.__dict__), "as_built": True},
        edits[0],
        {"op": "drive_joint", "joint": "pivot", "value": 90},
    ]
    out5, notes = apply_edits(parts3, edits3)
    ok(notes)
    kc = out5["knob"].bounding_box().center()
    assert abs(kc.X) < 1e-6 and abs(kc.Y - 46) < 1e-6 and abs(kc.Z - 9) < 1e-6              # swung with the lever
    kj = next(j for j in out5.joints if j["part"] == "knob")
    assert abs(kj["origin"][0]) < 1e-6 and abs(kj["origin"][1] - 25) < 1e-6              # the knob's joint origin (the lever top's centre) rode along
    assert "revolute joint pivot: lever to base" in describe(edits[0]) and "drive joint pivot to 45" == describe({"op": "drive_joint", "joint": "pivot", "value": 45})


def test_slider_cylindrical_planar_and_motion_link():
    rail = bd.Box(100, 10, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))
    block = bd.Pos(0, 0, 10) * bd.Box(10, 10, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))
    parts = Parts([Part("rail", rail, "#999"), Part("block", block, "#c33")])
    t = topology(parts)
    rail_top = next(f for f in t.faces if f.part == "rail" and f.type == "plane" and f.normal[2] > 0.9)
    block_bottom = next(f for f in t.faces if f.part == "block" and f.type == "plane" and f.normal[2] < -0.9)
    j = {"op": "joint", "type": "planar", "name": "slide", "part": "block", "to_part": "rail", "face": selector_for_face(block_bottom.__dict__), "to_face": selector_for_face(rail_top.__dict__), "as_built": True, "limits": {"u": [0, 90]}}
    out, notes = apply_edits(parts, [j, {"op": "drive_joint", "joint": "slide", "value": {"u": 30, "v": 2, "angle": 0}}])
    ok(notes)
    bb = out["block"].bounding_box()
    assert abs(bb.min.X - 30) < 1e-6 and abs(bb.center().Y - 2) < 1e-6                      # u runs along world X on a +Z face, v along Y
    out, notes = apply_edits(parts, [j, {"op": "drive_joint", "joint": "slide", "value": {"u": 200}}])
    ok(notes)
    assert abs(out["block"].bounding_box().min.X - 90) < 1e-6 and "limited to 90" in notes[-1].detail
    # a slider between round faces slides along the axis; a motion link drives a second joint by a ratio
    shaft = bd.Cylinder(5, 100, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    collar = bd.Cylinder(8, 10, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)) - bd.Cylinder(5, 10, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    collar2 = bd.Pos(0, 0, 50) * collar
    parts = Parts([Part("shaft", shaft, "#999"), Part("collar", collar, "#c33"), Part("collar2", collar2, "#3a3")])
    t = topology(parts)
    shaft_face = next(f for f in t.faces if f.part == "shaft" and f.type == "cylinder")
    bore1 = next(f for f in t.faces if f.part == "collar" and f.type == "cylinder" and f.concave)
    bore2 = next(f for f in t.faces if f.part == "collar2" and f.type == "cylinder" and f.concave)
    edits = [
        {"op": "joint", "type": "slider", "name": "s1", "part": "collar", "to_part": "shaft", "face": selector_for_face(bore1.__dict__), "to_face": selector_for_face(shaft_face.__dict__), "as_built": True},
        {"op": "joint", "type": "cylindrical", "name": "s2", "part": "collar2", "to_part": "shaft", "face": selector_for_face(bore2.__dict__), "to_face": selector_for_face(shaft_face.__dict__), "as_built": True},
        {"op": "motion_link", "joints": ["s1", "s2"], "ratio": 0.5},
        {"op": "drive_joint", "joint": "s1", "value": 20},
    ]
    out, notes = apply_edits(parts, edits)
    ok(notes)
    assert abs(out["collar"].bounding_box().min.Z - 20) < 1e-6 and abs(out["collar2"].bounding_box().min.Z - 60) < 1e-6
    assert next(jj for jj in out.joints if jj["name"] == "s2")["value"] == {"angle": 0.0, "slide": 10.0}
    out, notes = apply_edits(parts, edits + [{"op": "drive_joint", "joint": "s2", "value": {"angle": 90, "slide": 10}}])
    ok(notes)
    assert abs(out["collar2"].bounding_box().min.Z - 60) < 1e-6 and "angle 90°" in notes[-1].detail


def test_groups_insert_step_bom_and_balloons(tmp_path):
    bolt = bd.Cylinder(2, 12, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    plate = bd.Box(60, 40, 5, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    parts = Parts([Part("plate", plate, "#999", "aluminum"), Part("bolt_1", bd.Pos(-20, 0, 5) * bolt, "#555", "steel"), Part("bolt_2", bd.Pos(20, 0, 5) * bolt, "#555", "steel")])
    rows = A.bom(parts)
    assert [(r["item"], r["name"], r["qty"]) for r in rows] == [(1, "plate", 1), (2, "bolt", 2)] and rows[1]["parts"] == ["bolt_1", "bolt_2"]
    assert rows[1]["mass_total_g"] == pytest.approx(2 * rows[1]["mass_g"])
    rep = analyze(parts)
    assert len(rep.bom) == 2 and "Bill of materials" in summarize(rep) and "bolt x2" in summarize(rep)
    # group the bolts and move the group as one
    out, notes = apply_edits(parts, [{"op": "group", "name": "fasteners", "parts": ["bolt_1", "bolt_2"]}, {"op": "move", "group": "fasteners", "delta": [0, 0, 3]}])
    ok(notes)
    assert out["bolt_1"].bounding_box().min.Z == pytest.approx(8) and out["bolt_2"].bounding_box().min.Z == pytest.approx(8) and out["plate"].bounding_box().min.Z == pytest.approx(0)
    assert next(p for p in out if p.name == "bolt_1").group == "fasteners" and "2 parts" in notes[1].detail
    assert analyze(out).parts[1].group == "fasteners"
    # insert a component from a STEP file at a position, as a group when it has several bodies
    from eitri.kernel import export_parts
    comp = Parts([Part("hub", bd.Cylinder(10, 5), "#888"), Part("spoke", bd.Pos(15, 0, 0) * bd.Box(10, 2, 2), "#888")])
    paths = export_parts(comp, tmp_path, "wheel", ("step",))
    out, notes = apply_edits(parts, [{"op": "insert", "kind": "step", "path": paths["step"], "at": [0, 60, 0], "name": "wheel"}])
    ok(notes)
    names = out.names
    assert names[:3] == ["plate", "bolt_1", "bolt_2"] and len(names) == 5 and all(n.startswith("wheel") for n in names[3:])
    assert all(next(p for p in out if p.name == n).group == "wheel" for n in names[3:])
    assert abs(sum(abs(out[n].volume) for n in names[3:]) - (math.pi * 100 * 5 + 40)) < 1e-3
    assert abs(out[names[3]].bounding_box().center().Y - 60) < 1e-6
    # the drawing: ITEM/QTY columns and numbered balloons on the isometric view
    plist = parts_list(parts)
    assert [(r["item"], r["qty"]) for r in plist] == [(1, 1), (2, 2)]
    svg = drawing_svg(parts, "plate_assembly")
    assert svg.count('class="balloon"') == 3 and ">ITEM<" in svg and ">QTY<" in svg and ">bolt<" in svg
    (tmp_path / "assembly.svg").write_text(svg, encoding="utf-8")
