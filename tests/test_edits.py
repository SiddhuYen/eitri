"""Pickable GLB + topology, geometric selectors, and the direct-edit operations."""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

from eitri import *  # noqa: F403
from eitri import bd
from eitri.edits import apply_edits, resolve_edges, resolve_face, selector_for_edge, selector_for_face
from eitri.measure import holes
from eitri.model import normalize_result
from eitri.viewglb import topology, write_view_glb


def _plate():
    plate = Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return cut_holes(plate, [(-20, 0), (20, 0)], d=5)


def _glb_json(path: Path) -> dict:
    b = path.read_bytes()
    assert b[:4] == b"glTF"
    length = struct.unpack_from("<I", b, 12)[0]
    return json.loads(b[20:20 + length])


def test_view_glb_and_topology(tmp_path):
    parts = normalize_result({"plate": _plate(), "lid": Pos(0, 0, 6) * Box(60, 40, 2, align=(Align.CENTER, Align.CENTER, Align.MIN))}, "m")
    topo = write_view_glb(parts, tmp_path / "v.glb", tmp_path / "t.json")
    js = _glb_json(tmp_path / "v.glb")
    names = [n["name"] for n in js["nodes"]]
    assert "model" in names and "plate" in names and "lid" in names
    assert sum(1 for n in names if n.startswith("plate:f")) == len(parts["plate"].faces())
    assert sum(1 for n in names if n.startswith("plate:e")) == len(parts["plate"].edges())
    assert js["extras"] == {"units": "mm", "up": "Z"}
    acc = js["accessors"][0]
    assert acc["max"][0] <= 30.01 and acc["min"][0] >= -30.01       # mm, not metres
    t = json.loads((tmp_path / "t.json").read_text())
    assert [p["name"] for p in t["parts"]] == ["plate", "lid"]
    top = [f for f in t["faces"] if f["part"] == "plate" and f["type"] == "plane" and f["normal"] == [0, 0, 1]]
    assert len(top) == 1 and abs(top[0]["center"][2] - 6) < 1e-6
    hole_faces = [f for f in t["faces"] if f["type"] == "cylinder" and f["concave"]]
    assert len(hole_faces) == 2 and all(abs(f["radius"] - 2.5) < 1e-6 for f in hole_faces)
    assert len(t["holes"]) == 2 and all(h["faces"] for h in t["holes"])
    convex = [e for e in t["edges"] if e["part"] == "plate" and e["convex"]]
    assert len(convex) >= 8 and all(abs(e["angle"] - 90) < 1 for e in convex if e["type"] == "line")


def test_selectors_survive_parameter_changes():
    plate = _plate()
    t = topology(normalize_result(plate, "plate"))
    top = next(f for f in t.faces if f.type == "plane" and f.normal == [0, 0, 1])
    sel = selector_for_face(top.__dict__)
    # a wider, thicker plate: the "top face" selector still finds the top
    plate2 = cut_holes(Box(80, 40, 8, align=(Align.CENTER, Align.CENTER, Align.MIN)), [(-20, 0), (20, 0)], d=5)
    f = resolve_face(plate2, sel)
    assert f is not None and abs(f.center().Z - 8) < 1e-6 and f.normal_at().Z > 0.99
    hole = next(f for f in t.faces if f.type == "cylinder" and f.concave and f.axis_point[0] > 0)
    hsel = selector_for_face(hole.__dict__)
    hf = resolve_face(plate2, hsel)
    assert hf is not None and hf.geom_type == bd.GeomType.CYLINDER and hf.center().X > 10
    edge = next(e for e in t.edges if e.type == "line" and abs(e.mid[2] - 6) < 1e-6 and abs(e.mid[1] - 20) < 1e-6)
    es = resolve_edges(plate2, [selector_for_edge(edge.__dict__)])
    assert len(es) == 1 and abs(es[0].position_at(0.5).Z - 8) < 1e-6


def test_apply_edits_push_fillet_hole_move():
    parts = normalize_result({"plate": _plate(), "lid": Pos(0, 0, 10) * Box(20, 20, 2)}, "m")
    t = topology(parts)
    top = next(f for f in t.faces if f.part == "plate" and f.type == "plane" and f.normal == [0, 0, 1])
    hole = next(f for f in t.faces if f.part == "plate" and f.type == "cylinder" and f.concave)
    vert = [e for e in t.edges if e.part == "plate" and e.type == "line" and abs(e.start[2] - e.end[2]) > 1][:4]
    edits = [
        {"op": "push_pull", "part": "plate", "face": selector_for_face(top.__dict__), "distance": 4.0},
        {"op": "hole_diameter", "part": "plate", "face": selector_for_face(hole.__dict__), "diameter": 8.0},
        {"op": "fillet", "part": "plate", "edges": [selector_for_edge(e.__dict__) for e in vert], "radius": 3.0},
        {"op": "move", "part": "lid", "delta": [0, 0, 5]},
        {"op": "fillet", "part": "plate", "edges": [{"kind": "edge", "type": "line", "mid": [999, 999, 999], "length": 1}], "radius": 1.0},
        {"op": "color", "part": "lid", "color": "#FF0000"},
    ]
    out, notes = apply_edits(parts, edits)
    plate = out["plate"]
    assert abs(plate.bounding_box().max.Z - 10) < 1e-6                       # 6 + 4 push
    assert len(holes(plate, diameter=8)) == 1 and len(holes(plate, diameter=5)) == 1
    assert len(plate.solids()) == 1 and plate.is_valid
    assert abs(out["lid"].bounding_box().min.Z - 14) < 1e-6
    assert [n.ok for n in notes] == [True, True, True, True, False, True]
    assert "no longer exist" in notes[4].detail or "none of" in notes[4].detail
    assert next(p for p in out if p.name == "lid").color == "#FF0000"
    # push/pull inward (cut) and delete
    out2, notes2 = apply_edits(parts, [{"op": "push_pull", "part": "plate", "face": selector_for_face(top.__dict__), "distance": -2.0},
                                       {"op": "delete_part", "part": "lid"}])
    assert abs(out2["plate"].bounding_box().max.Z - 4) < 1e-6 and out2.names == ["plate"]


def test_project_edits_round_trip_and_build(tmp_path):
    from eitri.project import Project
    from eitri.kernel import build_model
    proj = Project(tmp_path / "p", name="plate")
    proj.write_model("from eitri import *\nPARAMS = dict(width=60.0)\n\ndef build(p):\n    plate = Box(p.width, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))\n    return cut_holes(plate, [(-20, 0), (20, 0)], d=5)\n")
    res = proj.build(note="first", render=False)
    assert res.ok and Path(res.outputs["view_glb"]).exists() and Path(res.outputs["topology"]).exists()
    topo = json.loads(Path(res.outputs["topology"]).read_text())
    top = next(f for f in topo["faces"] if f["type"] == "plane" and f["normal"] == [0, 0, 1])
    proj.add_edit({"op": "push_pull", "part": "plate", "face": selector_for_face(top), "distance": 4.0})
    assert "EDITS = [" in proj.read_model() and len(proj.edits()) == 1
    res2 = proj.build(note="edited", render=False)
    assert res2.ok, res2.error
    assert abs(res2.report.size[2] - 10) < 1e-6
    assert "Direct edits" in res2.summary and "[applied] push_pull" in res2.summary
    # parameters still work with edits on top (selector follows the top face)
    proj.set_params({"width": 80})
    res3 = proj.build(note="wider", render=False)
    assert res3.ok and abs(res3.report.size[0] - 80) < 1e-6 and abs(res3.report.size[2] - 10) < 1e-6
    # a failing edit is reported as an issue, not a crash
    proj.add_edit({"op": "fillet", "part": "plate", "edges": [{"kind": "edge", "type": "line", "mid": [999, 999, 999], "length": 1}], "radius": 2})
    res4 = proj.build(note="bad", render=False)
    assert res4.ok and any("direct edit failed" in i for i in res4.report.issues)
    proj.clear_edits()
    assert proj.edits() == [] and "EDITS = []" in proj.read_model()


def test_sketch_extrude_hole_and_curved_push_pull():
    from eitri.edits import face_frame
    plate = cut_holes(Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN)), [(20, 0)], d=5)
    boss = Pos(-15, 0, 6) * Cylinder(6, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    parts = normalize_result({"plate": plate + boss}, "m")
    t = topology(parts)
    top = next(f for f in t.faces if f.type == "plane" and f.normal == [0, 0, 1] and abs(f.center[2] - 6) < 1e-6)
    hole = next(f for f in t.faces if f.type == "cylinder" and f.concave)
    bossf = next(f for f in t.faces if f.type == "cylinder" and not f.concave)
    frame = face_frame(top.normal, top.center)
    assert abs(frame.x_dir.X - 1) < 1e-9 and abs(frame.z_dir.Z - 1) < 1e-9
    edits = [
        {"op": "sketch_extrude", "part": "plate", "face": selector_for_face(top.__dict__), "profile": {"type": "rect", "center": [0, 12], "w": 10, "h": 6}, "distance": 5, "mode": "add"},
        {"op": "sketch_extrude", "part": "plate", "face": selector_for_face(top.__dict__), "profile": {"type": "circle", "center": [0, -12], "r": 2}, "distance": 0, "mode": "cut", "through": True},
        {"op": "hole", "part": "plate", "face": selector_for_face(top.__dict__), "at": [10, -12], "size": "M3", "head": "counterbore"},
        {"op": "push_pull", "part": "plate", "face": selector_for_face(hole.__dict__), "distance": -1.0},     # hole wall pulled outward: Ø5 -> Ø7
        {"op": "push_pull", "part": "plate", "face": selector_for_face(bossf.__dict__), "distance": 2.0},     # boss grows: r6 -> r8
        {"op": "move_hole", "part": "plate", "face": {**selector_for_face(hole.__dict__), "radius": 3.5}, "delta": [0, 5, 0]},
    ]
    out, notes = apply_edits(parts, edits)
    s = out["plate"]
    assert all(n.ok for n in notes), [n.line() for n in notes]
    assert abs(s.bounding_box().max.Z - 16) < 1e-6                    # boss 6+10 still the top; rect pad 6+5=11
    assert len(holes(s, diameter=4)) == 1                               # the circle cut
    assert len(holes(s, diameter=3.4)) == 1 and len(holes(s, diameter=6.5)) == 1   # M3 counterbored
    h7 = holes(s, diameter=7)
    assert len(h7) == 1 and abs(h7[0].center.Y - 5) < 1e-6              # enlarged, then moved +5 in Y
    from eitri.measure import bosses
    assert any(abs(b.diameter - 16) < 1e-6 for b in bosses(s))
    assert s.is_valid and len(s.solids()) == 1
    # a new part from a sketch
    out2, notes2 = apply_edits(parts, [{"op": "sketch_extrude", "part": "plate", "face": selector_for_face(top.__dict__), "profile": {"type": "polygon", "points": [[-25, 15], [-10, 15], [-10, 19]]}, "distance": 3, "new_part": "tab"}])
    assert notes2[0].ok and out2.names == ["plate", "tab"] and abs(out2["tab"].volume - 0.5 * 15 * 4 * 3) < 1e-6


def test_merge_edits(tmp_path):
    from eitri.project import Project
    proj = Project(tmp_path / "p", name="plate")
    proj.write_model("from eitri import *\nPARAMS = {}\ndef build(p):\n    return Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))\n")
    sel = {"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [0, 0, 6], "area": 2400}
    proj.add_edit({"op": "push_pull", "part": "plate", "face": sel, "distance": 2})
    proj.add_edit({"op": "push_pull", "part": "plate", "face": sel, "distance": 3})
    assert len(proj.edits()) == 1 and proj.edits()[0]["distance"] == 5
    proj.add_edit({"op": "move", "part": "plate", "delta": [1, 0, 0]})
    proj.add_edit({"op": "move", "part": "plate", "delta": [2, 0, 0]})
    assert len(proj.edits()) == 2 and proj.edits()[1]["delta"] == [3, 0, 0]
    res = proj.build(note="t", render=False)
    assert res.ok and abs(res.report.size[2] - 11) < 1e-6 and abs(res.report.bbox_min[0] + 27) < 1e-6


def test_sketch_frame_anchored_to_stored_origin():
    plate = Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    parts = normalize_result({"plate": plate}, "m")
    t = topology(parts)
    top = next(f for f in t.faces if f.type == "plane" and f.normal == [0, 0, 1])
    sel = selector_for_face(top.__dict__)
    # the viewer stores the frame it used; a later push of the same face must not shift the sketch
    edits = [
        {"op": "sketch_extrude", "part": "plate", "face": sel, "origin": top.center, "normal": top.normal,
         "profile": {"type": "circle", "center": [20, 10], "r": 2}, "distance": 0, "mode": "cut", "through": True},
        {"op": "push_pull", "part": "plate", "face": sel, "distance": 4},
        {"op": "hole", "part": "plate", "face": sel, "origin": top.center, "normal": top.normal, "at": [-20, -10], "d": 3},
    ]
    out, notes = apply_edits(parts, edits)
    assert all(n.ok for n in notes), [n.line() for n in notes]
    hs = holes(out["plate"])
    assert any(abs(h.center.X - 20) < 1e-6 and abs(h.center.Y - 10) < 1e-6 for h in hs)
    assert any(abs(h.center.X + 20) < 1e-6 and abs(h.center.Y + 10) < 1e-6 and abs(h.diameter - 3) < 1e-6 for h in hs)


def test_code_built_edits_list_is_read_and_appended(tmp_path):
    """An EDITS list written as code (helper calls, variables) - as the agent does for long sculpting
    timelines - is read from the loaded module, and a direct edit appends to it instead of replacing it."""
    from eitri.project import Project
    proj = Project(tmp_path / "p", name="plate")
    proj.write_model("from eitri import *\nPARAMS = dict(width=60.0)\n\ndef build(p):\n    return {'plate': Box(p.width, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))}\n\n"
                     "def mv(d):\n    return {'op': 'move', 'part': 'plate', 'delta': d}\n\nSTEPS = [[1, 0, 0], [0, 2, 0]]\nEDITS = [mv(d) for d in STEPS]\n")
    assert proj.build(note="first", render=False).ok
    assert [e["delta"] for e in proj.edits()] == [[1, 0, 0], [0, 2, 0]]
    proj.add_edit({"op": "move", "part": "plate", "delta": [0, 0, 3]}, merge=False)
    assert [e["delta"] for e in proj.edits()] == [[1, 0, 0], [0, 2, 0], [0, 0, 3]]
    code = proj.read_model()
    assert "EDITS = [{" in code and "def mv" in code                      # expanded to a literal; the helpers stay
    res = proj.build(note="second", render=False)
    assert res.ok and len(res.edit_notes) == 3 and all(n["ok"] for n in res.edit_notes)
    proj.remove_edit(0)
    assert [e["delta"] for e in proj.edits()] == [[0, 2, 0], [0, 0, 3]]
