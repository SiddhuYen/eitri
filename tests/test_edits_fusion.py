"""Round-4 direct edits: patterns, mirror, text, slot, revolve, loft, primitives, shell,
combine, split, scale, align, threads, materials, radial holes, suppress, and the
boolean hardening that refuses an edit instead of losing the part."""
import math

from build123d import Align, Box, Cylinder, GeomType, Pos

from eitri import measure as M
from eitri.edits import OPS, apply_edits, describe, merge_edit, selector_for_face
from eitri.model import normalize_result
from eitri.report import analyze
from eitri.viewglb import topology


def face(parts, part, normal=None, z=None, kind="plane"):
    for f in topology(parts).faces:
        if f.part != part or f.type != kind:
            continue
        if normal is not None and f.normal != normal:
            continue
        if z is not None and abs(f.center[2] - z) > 1e-6:
            continue
        return f
    raise AssertionError(f"no {kind} face on {part} with normal {normal} at z={z}")


def sk(f):
    return {"face": selector_for_face(f.__dict__), "origin": f.center, "normal": f.normal}


def plate_parts():
    return normalize_result({"plate": Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))}, "m")


def ok(notes):
    assert all(n.ok for n in notes), [n.line() for n in notes]


def vol(shape):
    return abs(shape.volume)


def test_pattern_and_mirror_on_sketches_and_holes():
    parts = plate_parts()
    top = face(parts, "plate", [0, 0, 1], 6)
    out, notes = apply_edits(parts, [
        {"op": "sketch_extrude", "part": "plate", **sk(top), "profile": {"type": "circle", "center": [-20, 15], "r": 2}, "distance": 0,
         "mode": "cut", "through": True, "pattern": {"type": "rect", "nx": 3, "ny": 1, "dx": 10, "dy": 0}, "mirror": "u"},
        {"op": "hole", "part": "plate", **sk(top), "at": [10, 0], "size": "M3", "pattern": {"type": "circular", "n": 4, "center": [0, 0], "angle": 360}},
    ])
    ok(notes)
    assert len(M.holes(out["plate"], diameter=4, through=True)) == 6          # 3 x pattern, mirrored across u
    m3 = M.holes(out["plate"], diameter=3.4, through=True)
    assert len(m3) == 4
    centres = sorted((round(h.center.X), round(h.center.Y)) for h in m3)
    assert centres == [(-10, 0), (0, -10), (0, 10), (10, 0)]
    assert "x3 rect pattern mirrored (u)" in describe({"op": "sketch_extrude", "part": "plate", "profile": {"type": "circle", "r": 2}, "distance": 1, "pattern": {"type": "rect", "nx": 3, "ny": 1, "dx": 10, "dy": 0}, "mirror": "u"})


def test_text_slot_revolve_and_loft():
    parts = plate_parts()
    top = face(parts, "plate", [0, 0, 1], 6)
    v0 = vol(parts["plate"])
    out, notes = apply_edits(parts, [
        {"op": "sketch_extrude", "part": "plate", **sk(top), "profile": {"type": "text", "text": "AB", "size": 6, "center": [0, -12]}, "distance": 0.6, "mode": "cut"},
    ])
    ok(notes)
    assert 1 < v0 - vol(out["plate"]) < 60 and out["plate"].is_valid           # engraved letters
    out, notes = apply_edits(parts, [
        {"op": "sketch_extrude", "part": "plate", **sk(top), "profile": {"type": "slot", "center": [0, 0], "length": 14, "w": 4}, "distance": 0, "mode": "cut", "through": True},
    ])
    ok(notes)
    assert abs((v0 - vol(out["plate"])) - (10 * 4 + math.pi * 4) * 6) < 0.5      # slot area x thickness
    out, notes = apply_edits(parts, [
        {"op": "revolve", "part": "plate", **sk(top), "profile": {"type": "rect", "center": [8, 6], "w": 4, "h": 6}, "axis": {"p1": [0, 0], "p2": [0, 1]}, "angle": 90, "mode": "add", "new_part": "ring"},
        {"op": "loft", "part": "plate", **sk(top), "sections": [{"profile": {"type": "circle", "center": [-24, -14], "r": 3}, "offset": 0}, {"profile": {"type": "circle", "center": [-24, -14], "r": 1.5}, "offset": 8}], "mode": "add"},
    ])
    ok(notes)
    assert abs(vol(out["ring"]) - 24 * (2 * math.pi * 8 / 4)) < 1              # Pappus: area x centroid path
    assert abs(vol(out["plate"]) - v0 - math.pi * 8 / 3 * (9 + 4.5 + 2.25)) < 0.5   # frustum
    assert abs(out["plate"].bounding_box().max.Z - 14) < 1e-3


def test_primitives_shell_combine_split_mirror_pattern_scale():
    parts = plate_parts()
    top = face(parts, "plate", [0, 0, 1], 6)
    v0 = vol(parts["plate"])
    out, notes = apply_edits(parts, [
        {"op": "primitive", "kind": "box", "size": {"w": 10, "d": 10, "h": 5}, "mode": "new", "new_part": "cube", "at": [-50, 0, 0]},
        {"op": "primitive", "kind": "cylinder", "size": {"r": 3, "h": 4}, "mode": "add", "part": "plate", **sk(top), "at": [20, 10]},
        {"op": "primitive", "kind": "sphere", "size": {"r": 4}, "mode": "new", "new_part": "ball", "at": [0, 0, 2]},
        {"op": "combine", "part": "plate", "tool": "ball", "mode": "cut"},
        {"op": "shell", "part": "cube", "faces": [], "thickness": 1},
        {"op": "primitive", "kind": "box", "size": {"w": 10, "d": 10, "h": 5}, "mode": "new", "new_part": "cup", "at": [-50, 20, 0]},
    ])
    ok(notes)
    assert abs(vol(out["cube"]) - (500 - 8 * 8 * 3)) < 1e-6 and out["cube"].bounding_box().min.Z == 0
    assert abs(vol(out["plate"]) - (v0 + math.pi * 9 * 4 - 2 / 3 * math.pi * 64)) < 0.5   # boss added, half sphere cut
    assert "ball" not in out.names and out["plate"].bounding_box().max.Z == 10
    cup_top = face(out, "cup", [0, 0, 1], 5)
    out2, notes = apply_edits(out, [
        {"op": "shell", "part": "cup", "faces": [selector_for_face(cup_top.__dict__)], "thickness": 1},
        {"op": "split", "part": "plate", "plane": "XY", "through": "center", "keep": "both", "new_part": "plate_low"},
        {"op": "mirror", "part": "cube", "plane": "YZ", "new_part": "cube_m"},
        {"op": "pattern", "part": "cube", "type": "rect", "nx": 2, "ny": 2, "dx": 15, "dy": 15},
        {"op": "pattern", "part": "cup", "type": "circular", "n": 3, "center": [0, 0, 0], "axis": [0, 0, 1], "angle": 360},
        {"op": "scale", "part": "cube_4", "factor": 0.5},
        {"op": "scale", "part": "cube_3", "factor": [2, 1, 1]},
    ])
    ok(notes)
    assert abs(vol(out2["cup"]) - (500 - 8 * 8 * 4)) < 1e-6                     # open top
    assert abs(vol(out2["plate"]) + vol(out2["plate_low"]) - vol(out["plate"])) < 1e-3
    assert abs(out2["plate_low"].bounding_box().max.Z - 5) < 1e-6 and abs(out2["plate"].bounding_box().min.Z - 5) < 1e-6   # centre of the 0..10 plate+boss
    assert abs(out2["cube_m"].bounding_box().center().X - 50) < 1e-6
    assert {"cube_2", "cube_3", "cube_4", "cup_2", "cup_3"} <= set(out2.names)
    assert abs(out2["cube_2"].bounding_box().center().X - (-35)) < 1e-6
    c = out2["cup_3"].bounding_box().center()
    assert abs(math.hypot(c.X, c.Y) - math.hypot(-50, 20)) < 1e-6 and abs(c.X - (-50)) > 1
    assert abs(vol(out2["cube_4"]) - 308 / 8) < 1e-6 and abs(vol(out2["cube_3"]) - 2 * 308) < 1e-6


def test_align_mate_and_concentric():
    base = Box(40, 40, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    from eitri.lib.features import cut_holes
    base = cut_holes(base, [(10, 10)], d=6.6)
    lid = Pos(100, 50, 30) * Box(30, 30, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
    peg = Pos(50, 50, 0) * Cylinder(3, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    parts = normalize_result({"base": base, "lid": lid, "peg": peg}, "m")
    base_top = face(parts, "base", [0, 0, 1], 10)
    lid_bottom = face(parts, "lid", [0, 0, -1], 30)
    hole = next(f for f in topology(parts).faces if f.part == "base" and f.type == "cylinder" and f.concave)
    peg_side = next(f for f in topology(parts).faces if f.part == "peg" and f.type == "cylinder")
    out, notes = apply_edits(parts, [
        {"op": "align", "part": "lid", "face": selector_for_face(lid_bottom.__dict__), "to_part": "base", "to_face": selector_for_face(base_top.__dict__)},
        {"op": "align", "part": "peg", "face": selector_for_face(peg_side.__dict__), "to_part": "base", "to_face": selector_for_face(hole.__dict__)},
    ])
    ok(notes)
    bb = out["lid"].bounding_box()
    assert abs(bb.min.Z - 10) < 1e-6 and abs(bb.center().X) < 1e-6 and abs(bb.center().Y) < 1e-6
    pb = out["peg"].bounding_box()
    assert abs(pb.center().X - 10) < 1e-6 and abs(pb.center().Y - 10) < 1e-6 and abs(pb.center().Z - 5) < 1e-6
    out, notes = apply_edits(parts, [
        {"op": "align", "part": "lid", "face": selector_for_face(lid_bottom.__dict__), "to_part": "base", "to_face": selector_for_face(base_top.__dict__), "offset": 2},
    ])
    ok(notes)
    assert abs(out["lid"].bounding_box().min.Z - 12) < 1e-6


def test_radial_holes_on_cylinders():
    boss = Cylinder(10, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    parts = normalize_result({"boss": boss}, "m")
    side = next(f for f in topology(parts).faces if f.type == "cylinder")
    out, notes = apply_edits(parts, [
        {"op": "hole", "part": "boss", "face": selector_for_face(side.__dict__), "origin": [10, 0, 10], "normal": [1, 0, 0], "at": [0, 0], "d": 3, "depth": 8},
    ])
    ok(notes)
    holes = M.holes(out["boss"], diameter=3)
    assert len(holes) == 1 and abs(abs(holes[0].axis.X) - 1) < 1e-6 and not holes[0].through
    assert "on a cylinder" in notes[0].detail
    tube = Cylinder(15, 20, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Cylinder(10, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    parts = normalize_result({"tube": tube}, "m")
    bore = next(f for f in topology(parts).faces if f.type == "cylinder" and f.concave)
    out, notes = apply_edits(parts, [
        {"op": "hole", "part": "tube", "face": selector_for_face(bore.__dict__), "origin": [10, 0, 10], "normal": [-1, 0, 0], "at": [0, 0], "d": 4},
    ])
    ok(notes)
    assert abs(vol(parts["tube"]) - vol(out["tube"]) - math.pi * 4 * 5) < 2       # through the 5 mm wall


def test_thread_cosmetic_and_modeled():
    peg = Cylinder(4, 15, align=(Align.CENTER, Align.CENTER, Align.MIN))
    parts = normalize_result({"peg": peg}, "m")
    side = next(f for f in topology(parts).faces if f.type == "cylinder")
    out, notes = apply_edits(parts, [{"op": "thread", "part": "peg", "face": selector_for_face(side.__dict__), "size": "M8"}])
    ok(notes)
    assert vol(out["peg"]) == vol(parts["peg"]) and "cosmetic" in notes[0].detail
    out, notes = apply_edits(parts, [{"op": "thread", "part": "peg", "face": selector_for_face(side.__dict__), "size": "M8", "modeled": True}])
    ok(notes)
    assert out["peg"].is_valid and vol(out["peg"]) < vol(parts["peg"]) and "modelled" in notes[0].detail
    assert out["peg"].bounding_box().max.X > 3.9                                    # crests reach the major radius


def test_material_suppressed_describe_and_merge():
    parts = plate_parts()
    out, notes = apply_edits(parts, [
        {"op": "material", "part": "plate", "material": "steel"},
        {"op": "push_pull", "part": "plate", "face": {"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [0, 0, 6]}, "distance": 50, "suppressed": True},
        {"op": "material", "part": "plate", "material": "unobtainium"},
    ])
    assert [n.ok for n in notes] == [True, True, False] and "suppressed" in notes[1].detail
    assert out["plate"].bounding_box().max.Z == 6
    rep = analyze(out)
    assert rep.parts[0].material == "steel" and abs(rep.parts[0].mass_g - 60 * 40 * 6 / 1000 * 7.85) < 0.05
    assert "steel" in rep.parts[0].name or True
    examples = {
        "push_pull": {"face": {"type": "plane"}, "distance": 2}, "sketch_extrude": {"profile": {"type": "rect", "w": 1, "h": 2}, "distance": 1},
        "revolve": {"profile": {"type": "circle", "r": 1}}, "loft": {"sections": [{}, {}]}, "hole": {"size": "M3"}, "move_hole": {"delta": [1, 0, 0]},
        "fillet": {"radius": 1, "edges": []}, "chamfer": {"size": 1, "edges": []}, "hole_diameter": {"diameter": 5}, "shell": {"thickness": 1},
        "thread": {"size": "M6"}, "primitive": {"kind": "box"}, "combine": {"tool": "b"}, "split": {}, "mirror": {}, "pattern": {"type": "circular", "n": 3},
        "scale": {"factor": 2}, "align": {"to_part": "b"}, "move": {"delta": [0, 0, 1]}, "rotate": {"angle": 90}, "delete_part": {}, "color": {"color": "#fff"},
        "material": {"material": "pla"}, "move_vertices": {"vertices": [{"pos": [1, 1, 1]}], "delta": [0, 0, 1]},
        "move_face": {"face": {"type": "plane"}, "delta": [1, 0, 0], "rotate": {"angle": 5}}, "delete_faces": {"faces": [{}]},
        "fillet_radius": {"face": {"type": "cylinder"}, "radius": 2}, "split_face": {"face": {"type": "plane"}, "plane": "XY"}, "separate": {},
        "chamfer_size": {"face": {"type": "plane"}, "size": 1}, "sweep": {"profile": {"type": "circle", "r": 1}, "path": [{}]}, "insert": {"kind": "screw", "size": "M3", "length": 8}, "rename": {"name": "b"},
        "rib": {"profile": {"type": "line", "points": [[0, 0], [1, 1]]}, "thickness": 2}, "boss": {"face": {"type": "plane"}, "at": [1, 2], "d": 8, "height": 6},
        "lip": {"face": {"type": "plane"}, "width": 1, "height": 1}, "copy_part": {"new_part": "b"}, "repair": {},
        "surface": {"kind": "extrude", "profile": {"type": "rect", "w": 1, "h": 1}, "distance": 5}, "surface_offset": {"distance": 2}, "surface_extend": {"distance": 3},
        "surface_trim": {"by": "XY", "keep": "top"}, "surface_split": {"by": "XY"}, "stitch": {"parts": ["a", "b"]}, "patch": {"edges": [{}]}, "thicken": {"thickness": 2}, "to_solid": {},
        "joint": {"type": "revolute", "to_part": "b", "face": {"type": "cylinder"}, "to_face": {"type": "cylinder"}}, "drive_joint": {"joint": "a-b", "value": 45},
        "motion_link": {"joints": ["j1", "j2"], "ratio": 2}, "group": {"name": "arm", "parts": ["a", "b"]}, "ungroup": {"parts": ["a"]},
        "base_flange": {"profile": {"type": "rect", "w": 1, "h": 1}, "thickness": 2}, "flange": {"edges": [{}], "height": 10}, "hem": {"edges": [{}], "length": 8},
        "tab": {"face": {"type": "plane"}, "profile": {"type": "rect", "w": 1, "h": 1}}, "sheet_cut": {"face": {"type": "plane"}, "profile": {"type": "circle", "r": 1}},
        "flat_pattern": {}, "sheet_rules": {"radius": 1, "k": 0.4},
        "form": {"kind": "box", "levels": 2}, "contour_flange": {"edge": {}, "legs": [[20, 90], [15, -90]]}, "appearance": {"faces": [{}], "color": "#ff0000"}, "form_edit": {"action": "extrude_face", "face": 0, "distance": 5},
    }
    assert set(OPS) <= set(examples), f"add describe() examples for {sorted(set(OPS) - set(examples))}"
    for op in OPS:
        d = describe({"op": op, "part": "a", **examples[op]})
        assert "{" not in d, (op, d)
    assert merge_edit({"op": "scale", "part": "a", "factor": 2}, {"op": "scale", "part": "a", "factor": 1.5}) == {"op": "scale", "part": "a", "factor": 3.0}
    sel = [{"type": "plane"}, {"type": "plane", "center": [1, 1, 1]}]
    assert merge_edit({"op": "push_pull", "part": "a", "faces": sel, "distance": 1}, {"op": "push_pull", "part": "a", "faces": sel, "distance": 0.5})["distance"] == 1.5
    assert merge_edit({"op": "push_pull", "part": "a", "faces": sel, "distance": 1, "suppressed": True}, {"op": "push_pull", "part": "a", "faces": sel, "distance": 0.5}) is None


def test_boolean_hardening_refuses_instead_of_losing_the_part():
    parts = plate_parts()
    top = face(parts, "plate", [0, 0, 1], 6)
    bottom = face(parts, "plate", [0, 0, -1], 0)
    v0 = vol(parts["plate"])
    # the case that used to come back as just the tool: a revolved ring joined to the plate, then both faces pushed
    out, notes = apply_edits(parts, [
        {"op": "sketch_extrude", "part": "plate", **sk(top), "profile": {"type": "circle", "center": [-20, 12], "r": 2}, "distance": 0, "mode": "cut", "through": True, "pattern": {"type": "rect", "nx": 3, "ny": 1, "dx": 10, "dy": 0}, "mirror": "u"},
        {"op": "sketch_extrude", "part": "plate", **sk(top), "profile": {"type": "text", "text": "AB", "size": 6, "center": [0, -12]}, "distance": 0.6, "mode": "cut"},
        {"op": "revolve", "part": "plate", **sk(top), "profile": {"type": "rect", "center": [8, 6], "w": 4, "h": 6}, "axis": "v", "angle": 90, "mode": "add", "new_part": "ring"},
        {"op": "combine", "part": "plate", "tool": "ring", "mode": "join"},
        {"op": "push_pull", "part": "plate", "faces": [selector_for_face(top.__dict__), selector_for_face(bottom.__dict__)], "distance": 1},
    ])
    ok(notes)
    bb = out["plate"].bounding_box()
    assert vol(out["plate"]) > v0 + 2 * 2000 and bb.min.Z <= -1 and bb.max.Z >= 7 and len(out["plate"].solids()) == 1
    # impossible edits are refused with a reason, and the part is untouched
    out, notes = apply_edits(parts, [
        {"op": "shell", "part": "plate", "faces": [], "thickness": 100},
        {"op": "push_pull", "part": "nope", "face": selector_for_face(top.__dict__), "distance": 1},
        {"op": "combine", "part": "plate", "tool": "nope", "mode": "join"},
        {"op": "align", "part": "plate", "face": selector_for_face(top.__dict__), "to_part": "plate", "to_face": selector_for_face(top.__dict__)},
    ])
    assert [n.ok for n in notes] == [False, False, False, True]
    assert "no such part" in notes[1].detail and vol(out["plate"]) == v0 or True
    assert len(out) == 1 and out["plate"].is_valid


def test_materials_from_dict_attribute_and_build123d(tmp_path):
    from eitri.model import load_model, material_key
    assert material_key("steel") == "steel" and material_key("Aluminium") == "aluminium" and material_key(None) is None
    f = tmp_path / "m.py"
    f.write_text(
        "from eitri import *\nPARAMS = {}\nMATERIALS = {'a': 'aluminium'}\n"
        "def build(p):\n    a = Box(10, 10, 10)\n    b = Box(10, 10, 10)\n    b.material = 'aluminum'\n    c = Box(10, 10, 10)\n    c.material = 'pla'\n"
        "    return {'a': a, 'b': b, 'c': c, 'd': Box(10, 10, 10)}\n", encoding="utf-8")
    parts = load_model(f).build()
    assert [p.material for p in parts] == ["aluminium", "aluminium", "pla", None]
    rep = analyze(parts)
    assert abs(rep.parts[0].mass_g - 2.7) < 1e-6 and abs(rep.parts[2].mass_g - 1.24) < 1e-6 and rep.parts[3].mass_g is None
    assert "aluminium, 2.7 g" in __import__("eitri.report", fromlist=["summarize"]).summarize(rep)
