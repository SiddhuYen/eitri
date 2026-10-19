"""Edit-everything: move corners/edges/faces of a solid anywhere, delete faces (defeaturing),
as library calls and as timeline ops with geometric selectors."""
import math
from pathlib import Path

from build123d import Align, Axis, Box, Cylinder, GeomType, Pos, Rot, fillet

from eitri import delete_faces, move_edge, move_face, move_vertices
from eitri import measure as M
from eitri.edits import apply_edits, describe, merge_edit, selector_for_edge, selector_for_face, selector_for_vertex
from eitri.lib.features import cut_holes
from eitri.model import normalize_result
from eitri.viewglb import describe_selection, topology


def box():
    return Box(20, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))


def plate():
    return cut_holes(Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN)), [(15, 0)], d=6)


def test_move_vertices_edges_and_faces_in_code():
    r = move_vertices(box(), [(10, 10, 10)], (0, 0, 6))
    assert r.is_valid and abs(r.volume - 4600) < 1 and abs(r.bounding_box().max.Z - 16) < 1e-3 and len(r.faces()) == 6
    r = move_vertices(r, [(-10, -10, 0)], (-5, -5, 0))                    # again on the result (a filled face stays)
    assert r.is_valid and r.volume > 4600
    r = move_edge(box(), (0, 10, 10), (0, 0, 6))                            # the back top edge: top and back faces tilt
    assert r.is_valid and abs(r.volume - 5200) < 1e-6 and len(r.faces()) == 6
    r = move_vertices(box(), [(10, 10, 10), (-10, 10, 10), (10, -10, 10), (-10, -10, 10)], (3, 0, 6))
    assert abs(r.volume - 6400) < 1e-6 and abs(r.bounding_box().max.X - 13) < 1e-6   # sheared prism
    try:
        move_vertices(plate(), [(18, 3, 6)], (0, 0, 1))
        assert False
    except ValueError as exc:
        assert "no corner" in str(exc)


def test_move_face_translate_tilt_holes_and_bosses():
    p = plate()
    r = move_face(p, (0, 0, 6), delta=(0, 0, 2))                           # straight up = push/pull
    assert abs(r.volume - (p.volume + 60 * 40 * 2 - math.pi * 9 * 2)) < 0.5
    r = move_face(p, (0, 0, 6), delta=(0, 0, 2), tilt=10, tilt_axis=(0, 1, 0))
    assert r.is_valid and 8 < r.bounding_box().max.Z < 14 and len(M.holes(r, diameter=6, through=True)) == 1   # the hole follows
    r = move_face(p, (18, 0, 3), delta=(-10, 5, 0))                         # a hole translates
    h = M.holes(r, diameter=6)
    assert len(h) == 1 and abs(h[0].center.X - 5) < 1e-6 and abs(h[0].center.Y - 5) < 1e-6
    boss = p + Pos(-15, 0, 6) * Cylinder(4, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    r = move_face(boss, (-11, 0, 10), delta=(0, 10, 0))
    b = M.bosses(r)
    assert abs(r.volume - boss.volume) < 1e-6 and len(b) == 1 and abs(b[0].center.Y - 10) < 1e-6


def test_delete_faces_heals_features():
    p = plate()
    f = fillet(p.edges().filter_by(Axis.Z), 4)
    r = delete_faces(f, [(30, 20, 3)])
    assert r.is_valid and len(r.faces()) == len(f.faces()) - 1 and r.volume > f.volume
    r = delete_faces(p, [(18, 0, 3)])
    assert abs(r.volume - 14400) < 1e-6 and not M.holes(r)
    boss = p + Pos(-15, 0, 6) * Cylinder(4, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    r = delete_faces(boss, [(-11, 0, 10), (-15, 0, 14)])
    assert abs(r.volume - p.volume) < 1e-6
    try:
        delete_faces(p, [(0, 0, 6)])
        assert False
    except ValueError:
        pass


def test_timeline_ops_with_selectors_and_merging():
    parts = normalize_result({"box": box()}, "m")
    t = topology(parts)
    assert len(t.vertices) == 8 and all(len(v.edges) == 3 for v in t.vertices)
    corner = next(v for v in t.vertices if v.pos == [10, 10, 10])
    top = next(f for f in t.faces if f.type == "plane" and f.normal == [0, 0, 1])
    back_top = next(e for e in t.edges if e.type == "line" and e.mid == [0, 10, 10])
    assert "corner at (10, 10, 10)" in describe_selection(t.to_dict(), [corner.id])
    out, notes = apply_edits(parts, [{"op": "move_vertices", "part": "box", "vertices": [selector_for_vertex(corner.__dict__)], "delta": [0, 0, 6]}])
    assert notes[0].ok and abs(out["box"].volume - 4600) < 1
    out, notes = apply_edits(parts, [{"op": "move_vertices", "part": "box", "edges": [selector_for_edge(back_top.__dict__)], "delta": [0, 0, 6]}])
    assert notes[0].ok and abs(out["box"].volume - 5200) < 1e-6
    out, notes = apply_edits(parts, [{"op": "move_vertices", "part": "box", "faces": [selector_for_face(top.__dict__)], "delta": [3, 0, 6]}])
    assert notes[0].ok and abs(out["box"].volume - 6400) < 1e-6
    out, notes = apply_edits(parts, [{"op": "move_face", "part": "box", "face": selector_for_face(top.__dict__), "delta": [0, 0, 2], "rotate": {"axis": [0, 1, 0], "angle": 15}}])
    assert notes[0].ok and "tilted 15" in notes[0].detail and out["box"].is_valid
    # the edit keeps resolving after the part changes: a corner selector 0.3 mm off still finds it
    out, notes = apply_edits(parts, [{"op": "move_vertices", "part": "box", "vertices": [{"kind": "vertex", "pos": [10.3, 10, 10]}], "delta": [0, 0, 1]}])
    assert notes[0].ok
    e1 = {"op": "move_vertices", "part": "box", "vertices": [selector_for_vertex(corner.__dict__)], "delta": [0, 0, 1]}
    assert merge_edit(e1, {**e1, "delta": [0, 0, 2]})["delta"] == [0, 0, 3]
    assert merge_edit({"op": "move_face", "part": "box", "face": {"type": "plane"}, "delta": [1, 0, 0]}, {"op": "move_face", "part": "box", "face": {"type": "plane"}, "delta": [0, 2, 0]})["delta"] == [1, 2, 0]
    assert "drag 1 corner(s)" in describe(e1) and "remove feature" in describe({"op": "delete_faces", "part": "box", "faces": [{}]})
    pl = normalize_result({"plate": plate()}, "m")
    hole_faces = [f for f in topology(pl).faces if f.type == "cylinder" and f.concave]
    out, notes = apply_edits(pl, [{"op": "delete_faces", "part": "plate", "faces": [selector_for_face(f.__dict__) for f in hole_faces]}])
    assert notes[0].ok and abs(out["plate"].volume - 14400) < 1e-6
    out, notes = apply_edits(pl, [{"op": "delete_faces", "part": "plate", "faces": [{"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [0, 0, 6]}]}])
    assert not notes[0].ok and "can be removed" in notes[0].detail


def test_curved_outline_edges_are_kept_and_split_bores_removed_whole():
    from pathlib import Path
    from eitri.model import load_model
    bracket = load_model(Path(__file__).resolve().parent.parent / "examples" / "bracket.py").build()["bracket"]
    # the wall's top corner: its side face carries the fillet arc in its outline, untouched by the move
    r = move_vertices(bracket, [(30, 40, 30)], (0, 0, 10))
    assert r.is_valid and abs(r.bounding_box().max.Z - 40) < 1e-3 and len(r.faces()) == len(bracket.faces())
    assert len(M.holes(r, diameter=5.5)) == 4                                     # the back face kept its holes
    # a cross bore whose wall is split by a tilted face: deleting one piece removes the whole bore, no cavity
    block = Box(40, 30, 12, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Pos(10, 0, 5) * Rot(90, 0, 0) * Cylinder(2.5, 90)
    tilted = move_face(block, (0, -15, 6), delta=(0, -4, 0), tilt=-15, tilt_axis=(1, 0, 0), about=(0, -19, 0))
    wall = [f for f in tilted.faces() if f.geom_type == GeomType.CYLINDER]
    assert len(wall) >= 2
    r = delete_faces(tilted, [wall[0].center()])
    assert r.is_valid and not M.holes(r) and sum(len(s.shells()) for s in r.solids()) == 1
    assert abs(r.volume - tilted.volume - math.pi * 2.5 ** 2 * 30) < 60                   # the bore (≈30 mm long) is filled


def test_fillet_radius_split_face_and_separate():
    from eitri.direct import fillet_radius, split_face
    from eitri.edits import plane_spec
    from build123d import Plane
    p = plate()
    f = fillet(p.edges().filter_by(Axis.Z), 4)
    round_face = next(x for x in f.faces() if x.geom_type == GeomType.CYLINDER and abs(x.center().X) > 20)
    r = fillet_radius(f, round_face, 2)
    assert r.is_valid and abs(r.volume - f.volume - (16 - 4) * (1 - math.pi / 4) * 6) < 0.5   # one corner: r4 -> r2 adds material
    parts = normalize_result({"plate": f}, "m")
    fil = next(x for x in topology(parts).faces if x.type == "cylinder" and not x.concave)
    out, notes = apply_edits(parts, [{"op": "push_pull", "part": "plate", "face": selector_for_face(fil.__dict__), "distance": 2}])
    assert notes[0].ok and "fillet radius 4 -> 2" in notes[0].detail
    out, notes = apply_edits(parts, [{"op": "fillet_radius", "part": "plate", "face": selector_for_face(fil.__dict__), "radius": 6}])
    assert notes[0].ok and out["plate"].volume < f.volume
    # split the top face by the plane x = 0, then push only the +x half
    top = p.faces().sort_by(Axis.Z)[-1]
    s = split_face(p, top, Plane(origin=(0, 0, 0), z_dir=(1, 0, 0)))
    assert s.is_valid and len(s.faces()) == len(p.faces()) + 1 and abs(s.volume - p.volume) < 1e-6
    sp = normalize_result({"plate": s}, "m")
    tops = [x for x in topology(sp).faces if x.type == "plane" and x.normal == [0, 0, 1]]
    assert len(tops) == 2
    right = next(x for x in tops if x.center[0] > 0)
    out, notes = apply_edits(sp, [{"op": "push_pull", "part": "plate", "face": selector_for_face(right.__dict__), "distance": 3}])
    assert notes[0].ok and abs(out["plate"].volume - s.volume - (30 * 40 * 3 - math.pi * 9 * 3)) < 0.5
    out, notes = apply_edits(normalize_result({"plate": p}, "m"), [{"op": "split_face", "part": "plate", "face": {"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [0, 0, 6]}, "plane": {"origin": [0, 0, 0], "normal": [1, 0, 0]}}])
    assert notes[0].ok and len(out["plate"].faces()) == len(p.faces()) + 1
    # separate: two lumps become two parts
    two = Box(10, 10, 10) + Pos(30, 0, 0) * Box(5, 5, 5)
    out, notes = apply_edits(normalize_result({"pair": two}, "m"), [{"op": "separate", "part": "pair"}])
    assert notes[0].ok and out.names == ["pair", "pair_2"] and abs(out["pair"].volume - 1000) < 1e-6 and abs(out["pair_2"].volume - 125) < 1e-6


def test_chamfer_size_sweep_and_insert():
    from eitri.direct import chamfer_size, sweep_profile
    from eitri.edits import selector_for_edge
    from eitri.lib.parts import washer
    from build123d import Circle, Plane, chamfer
    assert washer("M3").is_valid                                              # the library's washer works again
    box = Box(40, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    c2 = chamfer(box.edges().filter_by(Axis.X).sort_by(Axis.Z)[-2:], 2)        # two top edges chamfered 2 mm
    cf = next(f for f in c2.faces() if f.geom_type == GeomType.PLANE and abs(f.normal_at().Z - 0.7071) < 1e-3)      # a 45° chamfer face
    c4 = chamfer_size(c2, cf, 4)
    assert c4.is_valid and abs((c2.volume - c4.volume) - (0.5 * 16 - 0.5 * 4) * 40) < 1e-3     # one chamfer grew from 2 to 4
    parts = normalize_result({"box": c2}, "m")
    ch = next(f for f in topology(parts).faces if f.type == "plane" and abs(f.normal[2] - 0.7071) < 1e-3)
    out, notes = apply_edits(parts, [{"op": "chamfer_size", "part": "box", "face": selector_for_face(ch.__dict__), "size": 1}])
    assert notes[0].ok and out["box"].volume > c2.volume
    # sweep: a Ø3 circle at the start of the top outline, along the whole outline (a closed tube)
    top = box.faces().sort_by(Axis.Z)[-1].edges()
    start = top[0].position_at(0)
    tube = sweep_profile(Plane(origin=start, z_dir=top[0].tangent_at(0)) * Circle(1.5), list(top))
    assert tube.is_valid and abs(tube.volume - math.pi * 1.5 ** 2 * 120) < 30
    bp = normalize_result({"box": box}, "m")
    t = topology(bp)
    top_ids = [e for e in t.edges if e.type == "line" and abs(e.mid[2] - 10) < 1e-6]
    end = next(f for f in t.faces if f.type == "plane" and f.normal == [1, 0, 0])
    e0 = next(e for e in top_ids if abs(e.mid[1] + 10) < 1e-6)                   # the front top edge, along X
    out, notes = apply_edits(bp, [{"op": "sweep", "part": "box", "face": selector_for_face(end.__dict__), "origin": [20, -10, 10], "normal": [1, 0, 0],
                                   "profile": {"type": "rect", "center": [0, 0], "w": 4, "h": 4}, "path": [selector_for_edge(e0.__dict__)], "mode": "add"}])
    assert notes[0].ok and abs(out["box"].volume - box.volume - 4 * 4 * 40 * 0.75) < 1.0   # a square rail on the corner: 3/4 outside
    out, notes = apply_edits(bp, [{"op": "sweep", "part": "box", "face": selector_for_face(next(f for f in t.faces if f.normal == [0, -1, 0]).__dict__), "origin": [-20, -10, 10], "normal": [0, -1, 0],
                                   "profile": {"type": "rect", "center": [0, 0], "w": 4, "h": 4}, "path": [selector_for_edge(e0.__dict__)], "mode": "add"}])
    assert not notes[0].ok and "along the path" in notes[0].detail
    # insert: an M3x10 screw into a plate hole from the top, a nut on the bottom
    p = cut_holes(Box(40, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN)), [(0, 0)], d=3.4)
    out, notes = apply_edits(normalize_result({"plate": p}, "m"), [
        {"op": "insert", "kind": "screw", "size": "M3", "length": 10, "at": [0, 0, 6], "axis": [0, 0, -1], "part": "screw"},
        {"op": "insert", "kind": "nut", "size": "M3", "at": [0, 0, 0], "axis": [0, 0, 1], "part": "nut"},
        {"op": "insert", "kind": "bearing", "size": "608", "at": [20, 20, 6], "axis": [0, 0, -1]},
    ])
    assert all(n.ok for n in notes), [n.line() for n in notes]
    assert out.names == ["plate", "screw", "nut", "bearing_608"]
    sb = out["screw"].bounding_box()
    assert abs(sb.max.Z - 9) < 1e-3 and abs(sb.min.Z - (-4)) < 1e-3            # 3 mm head above the plate, shank 4 mm past the bottom
    nb = out["nut"].bounding_box()
    assert abs(nb.max.Z - 0) < 1e-6 and nb.min.Z < -2                           # the nut hangs below the plate
    bb = out["bearing_608"].bounding_box()
    assert abs(bb.max.Z - 6) < 1e-3 and abs(bb.min.Z - (-1)) < 1e-3 and next(p for p in out if p.name == "bearing_608").material == "steel"


def test_rename_part():
    parts = normalize_result({"a": Box(10, 10, 10), "b": Box(5, 5, 5)}, "m")
    out, notes = apply_edits(parts, [{"op": "rename", "part": "a", "name": "base"}, {"op": "rename", "part": "b", "name": "base"}, {"op": "move", "part": "base", "delta": [1, 0, 0]}])
    assert [n.ok for n in notes] == [True, False, True] and out.names == ["base", "b"] and "already exists" in notes[1].detail
    assert describe({"op": "rename", "part": "a", "name": "base"}) == "rename a to base"


def test_opacity_flows_to_the_view_glb():
    import json
    from build123d import Color
    from eitri.viewglb import write_view_glb
    a = Box(10, 10, 10)
    a.color = Color(0.8, 0.2, 0.2, 0.4)
    parts = normalize_result({"a": a, "b": Pos(30, 0, 0) * Box(10, 10, 10)}, "m")
    assert abs(parts._parts[0].opacity - 0.4) < 1e-6 and parts._parts[1].opacity == 1.0
    out, notes = apply_edits(parts, [{"op": "color", "part": "b", "opacity": 0.5}, {"op": "color", "part": "b", "color": "#112233"}])
    assert all(n.ok for n in notes) and abs(out._parts[1].opacity - 0.5) < 1e-6 and out._parts[1].color == "#112233"
    assert merge_edit({"op": "color", "part": "b", "opacity": 0.5}, {"op": "color", "part": "b", "color": "#111"}) == {"op": "color", "part": "b", "opacity": 0.5, "color": "#111"}
    import tempfile
    d = Path(tempfile.mkdtemp())
    write_view_glb(out, d / "v.glb", d / "t.json")
    topo = json.loads((d / "t.json").read_text(encoding="utf-8"))
    assert {p["name"]: p["opacity"] for p in topo["parts"]} == {"a": 0.4, "b": 0.5}
    glb = (d / "v.glb").read_bytes()
    assert glb.count(b'"alphaMode":"BLEND"') == 2 or glb.count(b'"alphaMode": "BLEND"') == 2
