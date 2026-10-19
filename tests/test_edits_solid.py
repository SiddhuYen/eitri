"""The solid-completeness ops: extrude extents (symmetric, taper, thin, to next, to face, flip), revolve
about model axes / an edge, scaled loft sections, ribs, variable-radius fillets, unequal and angled
chamfers, patterns along a path, bosses, lips/grooves, part copies, repair, wedge and coil primitives."""
from __future__ import annotations

import math

import build123d as bd
import pytest

from eitri.edits import apply_edits, describe, selector_for_edge, selector_for_face
from eitri.model import Part, Parts
from eitri.viewglb import topology
from eitri import solidops as solid

TOP = {"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [0, 0, 10], "area": 800}


def block(w=40.0, d=20.0, h=10.0, name="block"):
    return Parts([Part(name, bd.Box(w, d, h, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)), "#999")])


def tunnel():
    """A 40 x 20 x 30 block with a 30 x 20 x 18 tunnel through Y (floor top at z=6, ceiling at z=24)."""
    body = bd.Box(40, 20, 30, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)) - bd.Pos(0, 0, 15) * bd.Box(30, 40, 18)
    return Parts([Part("tunnel", body, "#999")])


def vol(parts, name="block"):
    return abs(parts[name].volume)


def ok(notes):
    assert all(n.ok for n in notes), [n.detail for n in notes]


def test_extrude_symmetric_taper_thin_and_flip():
    out, notes = apply_edits(Parts([]), [
        {"op": "sketch_extrude", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [0, 0], "w": 10, "h": 10}, "distance": 5, "mode": "add", "new_part": "sym", "symmetric": True},
        {"op": "sketch_extrude", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [30, 0], "w": 10, "h": 10}, "distance": 5, "mode": "add", "new_part": "tap", "taper": 10},
        {"op": "sketch_extrude", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [60, 0], "w": 10, "h": 10}, "distance": 5, "mode": "add", "new_part": "down", "flip": True},
    ])
    ok(notes)
    assert abs(vol(out, "sym") - 1000) < 1e-6 and out["sym"].bounding_box().min.Z == pytest.approx(-5)
    assert abs(vol(out, "tap") - bd.extrude(bd.Rectangle(10, 10), 5, taper=10).volume) < 1e-6 and vol(out, "tap") < 500
    assert out["down"].bounding_box().max.Z == pytest.approx(0) and out["down"].bounding_box().min.Z == pytest.approx(-5)
    # thin walls on a block: a closed profile gives a wall just inside it, an open chain a band centred on it
    parts = block()
    v0 = vol(parts)
    chain = {"type": "sketch", "entities": [{"id": "l1", "type": "line", "a": [-15, 0], "b": [-5, 0]}], "constraints": [], "dims": []}
    out, notes = apply_edits(parts, [
        {"op": "sketch_extrude", "part": "block", "face": TOP, "origin": [0, 0, 10], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [8, 0], "w": 20, "h": 10}, "distance": 5, "mode": "add", "thin": 2},
        {"op": "sketch_extrude", "part": "block", "face": TOP, "origin": [0, 0, 10], "normal": [0, 0, 1], "profile": chain, "distance": 5, "mode": "add", "thin": 2},
    ])
    ok(notes)
    band_area = bd.make_face(bd.Wire([bd.Edge.make_line((-15, 0, 0), (-5, 0, 0))]).offset_2d(1, kind=bd.Kind.INTERSECTION, side=bd.Side.BOTH)).area
    assert abs(vol(out) - (v0 + (200 - 16 * 6) * 5 + band_area * 5)) < 1e-3
    assert "thin 2 mm" in describe({"op": "sketch_extrude", "part": "block", "profile": {"type": "rect", "w": 1, "h": 1}, "distance": 5, "thin": 2})


def test_extrude_to_next_and_to_face():
    parts = tunnel()
    v0 = vol(parts, "tunnel")
    floor = {"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [0, 0, 6], "area": 600}
    top = {"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [0, 0, 30], "area": 800}
    bottom = {"kind": "face", "type": "plane", "normal": [0, 0, -1], "center": [0, 0, 0], "area": 800}
    out, notes = apply_edits(parts, [
        {"op": "sketch_extrude", "part": "tunnel", "face": floor, "origin": [0, 0, 6], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [0, 0], "w": 10, "h": 6}, "mode": "add", "extent": "to_next"},
    ])
    ok(notes)
    assert abs(vol(out, "tunnel") - (v0 + 10 * 6 * 18)) < 1e-3 and "to the next face" in notes[0].detail        # a pillar from the floor to the ceiling
    out, notes = apply_edits(parts, [
        {"op": "sketch_extrude", "part": "tunnel", "face": top, "origin": [0, 0, 30], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [0, 0], "w": 4, "h": 4}, "mode": "cut", "extent": "to_next"},
    ])
    ok(notes)
    assert abs(vol(out, "tunnel") - (v0 - 4 * 4 * 6)) < 1e-3                                                      # through the lid only
    out, notes = apply_edits(parts, [
        {"op": "sketch_extrude", "part": "tunnel", "face": top, "origin": [0, 0, 30], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [0, 0], "w": 4, "h": 4}, "mode": "cut", "extent": "to_face", "to_face": bottom},
    ])
    ok(notes)
    assert abs(vol(out, "tunnel") - (v0 - 4 * 4 * 12)) < 1e-3                                                     # lid and floor, down to the bottom face
    # nothing above the lid: refused rather than extruded forever
    out, notes = apply_edits(parts, [
        {"op": "sketch_extrude", "part": "tunnel", "face": top, "origin": [0, 0, 30], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [0, 0], "w": 4, "h": 4}, "mode": "add", "extent": "to_next"},
    ])
    assert not notes[0].ok and "nothing to extrude to" in notes[0].detail and abs(vol(out, "tunnel") - v0) < 1e-6


def test_revolve_about_model_axis_and_edge_and_scaled_loft():
    # a disc profile sketched on the XZ plane (normal -Y) revolved about the model Z axis: a ring
    out, notes = apply_edits(Parts([]), [
        {"op": "revolve", "part": None, "origin": [0, 0, 0], "normal": [0, -1, 0], "profile": {"type": "circle", "center": [10, 0], "r": 2}, "axis": "z", "angle": 360, "mode": "add", "new_part": "ring"},
    ])
    ok(notes)
    assert abs(vol(out, "ring") - 2 * math.pi * 10 * math.pi * 4) < 1e-2
    # an axis that does not lie in the sketch plane is refused
    out, notes = apply_edits(Parts([]), [
        {"op": "revolve", "part": None, "origin": [0, 0, 0], "normal": [0, -1, 0], "profile": {"type": "circle", "center": [10, 0], "r": 2}, "axis": "y", "mode": "add", "new_part": "bad"},
    ])
    assert not notes[0].ok and "lie in the sketch plane" in notes[0].detail
    # about a straight edge of a part: the block's bottom-front edge (y = -10, z = 0) with a sketch on the front face
    parts = block()
    t = topology(parts)
    edge = next(e for e in t.edges if e.type == "line" and e.mid == [0, -10, 0])
    front = {"kind": "face", "type": "plane", "normal": [0, -1, 0], "center": [0, -10, 5], "area": 400}
    out, notes = apply_edits(parts, [
        {"op": "revolve", "part": "block", "face": front, "origin": [0, -10, 0], "normal": [0, -1, 0], "profile": {"type": "rect", "center": [0, -3], "w": 10, "h": 4}, "axis": {"edge": selector_for_edge(edge.__dict__)}, "angle": 90, "mode": "add"},
    ])
    ok(notes)
    assert vol(out) > 8000 + 1
    # loft: a base profile and a scaled, rotated copy 10 mm up
    out, notes = apply_edits(Parts([]), [
        {"op": "loft", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [0, 0], "w": 20, "h": 20}, "sections": [{"offset": 0}, {"offset": 10, "scale": 0.5, "rotate": 45}], "mode": "add", "new_part": "frustum"},
    ])
    ok(notes)
    assert 100 * 10 < vol(out, "frustum") < 400 * 10 and out["frustum"].bounding_box().max.Z == pytest.approx(10)


def test_rib_to_next_and_by_distance():
    # a U channel open at the top: floor 4 thick, walls 5 thick at x = +-15..20
    body = bd.Box(40, 20, 20, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)) - bd.Pos(0, 0, 12) * bd.Box(30, 40, 16)
    parts = Parts([Part("channel", body, "#999")])
    v0 = vol(parts, "channel")
    # sketch plane y = 0 looking along -y: u = world x, v = -world z; the rib's top edge slopes from z=20 at x=-15 to z=12 at x=15
    out, notes = apply_edits(parts, [
        {"op": "rib", "part": "channel", "origin": [0, 0, 0], "normal": [0, 1, 0], "profile": {"type": "line", "points": [[-15, -20], [15, -12]]}, "thickness": 3, "direction": "v"},
    ])
    ok(notes)
    assert abs(vol(out, "channel") - (v0 + (16 + 8) / 2 * 30 * 3)) < 1e-3                 # the web stops at the floor (z = 4)
    out, notes = apply_edits(parts, [
        {"op": "rib", "part": "channel", "origin": [0, 0, 0], "normal": [0, 1, 0], "profile": {"type": "line", "points": [[-15, -20], [15, -12]]}, "thickness": 3, "direction": "v", "distance": 4},
    ])
    ok(notes)
    assert abs(vol(out, "channel") - (v0 + 30 * 4 * 3)) < 1e-3                             # a fixed 4 mm deep strip under the edge
    assert "rib 3 mm" in describe({"op": "rib", "part": "channel", "profile": {"type": "line", "points": [[0, 0], [1, 0]]}, "thickness": 3})


def test_variable_fillet_and_unequal_or_angled_chamfer():
    parts = block(20, 20, 10)
    t = topology(parts)
    vertical = [e for e in t.edges if e.type == "line" and abs(e.start[0] - e.end[0]) < 1e-9 and abs(e.start[1] - e.end[1]) < 1e-9]
    assert len(vertical) == 4
    out, notes = apply_edits(parts, [{"op": "fillet", "part": "block", "edges": [selector_for_edge(vertical[0].__dict__)], "radius": 1, "radius_end": 3}])
    ok(notes)
    v_var = vol(out)
    const1 = apply_edits(parts, [{"op": "fillet", "part": "block", "edges": [selector_for_edge(vertical[0].__dict__)], "radius": 1}])[0]
    const3 = apply_edits(parts, [{"op": "fillet", "part": "block", "edges": [selector_for_edge(vertical[0].__dict__)], "radius": 3}])[0]
    assert abs(const3["block"].volume) < v_var < abs(const1["block"].volume) and "r=1->3" in notes[0].detail
    top_edges = [e for e in t.edges if e.type == "line" and abs(e.mid[2] - 10) < 1e-9]
    top = {"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [0, 0, 10], "area": 400}
    out, notes = apply_edits(parts, [{"op": "chamfer", "part": "block", "edges": [selector_for_edge(e.__dict__) for e in top_edges], "size": 2, "size2": 1, "reference": top}])
    ok(notes)
    ref = bd.chamfer(parts["block"].faces().sort_by(bd.Axis.Z)[-1].edges(), 2, length2=1, reference=parts["block"].faces().sort_by(bd.Axis.Z)[-1])
    assert abs(vol(out) - ref.volume) < 1e-6 and "2 x 1 mm" in notes[0].detail
    out, notes = apply_edits(parts, [{"op": "chamfer", "part": "block", "edges": [selector_for_edge(e.__dict__) for e in top_edges], "size": 2, "angle": 30}])
    ok(notes)
    ref = bd.chamfer(parts["block"].faces().sort_by(bd.Axis.Z)[-1].edges(), 2, angle=30, reference=parts["block"].faces().sort_by(bd.Axis.Z)[-1])
    assert abs(vol(out) - ref.volume) < 1e-6 and "at 30 deg" in notes[0].detail


def test_pattern_along_a_path():
    rail = bd.Box(60, 10, 5, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))
    peg = bd.Pos(0, 0, 5) * bd.Cylinder(2, 6, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    parts = Parts([Part("rail", rail, "#999"), Part("peg", peg, "#c33")])
    t = topology(parts)
    edge = next(e for e in t.edges if e.part == "rail" and e.type == "line" and e.mid == [30, -5, 5])     # the top-front edge, x = 0..60
    out, notes = apply_edits(parts, [{"op": "pattern", "part": "peg", "type": "path", "path_part": "rail", "edges": [selector_for_edge(edge.__dict__)], "n": 4}])
    ok(notes)
    assert out.names == ["rail", "peg", "peg_2", "peg_3", "peg_4"]
    xs = sorted(round(out[n].bounding_box().center().X, 6) for n in ("peg", "peg_2", "peg_3", "peg_4"))
    assert xs == [0, 20, 40, 60] or xs == [-60, -40, -20, 0], xs                                  # spread over the whole edge
    out, notes = apply_edits(parts, [{"op": "pattern", "part": "peg", "type": "path", "path_part": "rail", "edges": [selector_for_edge(edge.__dict__)], "n": 3, "spacing": 7.5}])
    ok(notes)
    xs = sorted(abs(round(out[n].bounding_box().center().X, 6)) for n in ("peg", "peg_2", "peg_3"))
    assert xs == [0, 7.5, 15]
    # along a circle, aligned: a cube patterned around a cylinder's top rim ends up rotated with the tangent
    hub = bd.Cylinder(20, 5, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    cube = bd.Pos(20, 0, 5) * bd.Box(4, 2, 2, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    parts = Parts([Part("hub", hub, "#999"), Part("cube", cube, "#c33")])
    t = topology(parts)
    rim = next(e for e in t.edges if e.part == "hub" and e.type == "circle" and abs(e.mid[2] - 5) < 1e-9)
    out, notes = apply_edits(parts, [{"op": "pattern", "part": "cube", "type": "path", "path_part": "hub", "edges": [selector_for_edge(rim.__dict__)], "n": 4, "align": True}])
    ok(notes)
    for n in ("cube_2", "cube_3", "cube_4"):
        c = out[n].bounding_box().center()
        assert abs(math.hypot(c.X, c.Y) - 20) < 1e-6 and abs(out[n].volume - 16) < 1e-6
    assert "along 1 edge(s)" in describe({"op": "pattern", "part": "cube", "type": "path", "edges": [{}], "n": 4})


def test_boss_lip_groove_copy_and_repair():
    parts = block()
    v0 = vol(parts)
    out, notes = apply_edits(parts, [
        {"op": "boss", "part": "block", "face": TOP, "at": [5, 5], "d": 8, "height": 6, "hole_d": 3},
        {"op": "boss", "part": "block", "face": TOP, "at": [-10, 0], "kind": "insert", "size": "M3", "height": 6},
    ])
    ok(notes)
    assert abs(vol(out) - (v0 + math.pi * (16 - 2.25) * 6)) > 1 and vol(out) > v0 + math.pi * (16 - 2.25) * 6 - 1e-3   # the plain boss, plus an insert boss
    plain = apply_edits(parts, [{"op": "boss", "part": "block", "face": TOP, "at": [5, 5], "d": 8, "height": 6, "hole_d": 3}])[0]
    assert abs(abs(plain["block"].volume) - (v0 + math.pi * (16 - 2.25) * 6)) < 1e-3
    # a shelled box open at the top: a lip on the inner edge of the rim, then a groove instead
    box = bd.Box(40, 30, 20, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)) - bd.Pos(0, 0, 11) * bd.Box(36, 26, 18)
    parts = Parts([Part("case", box, "#999")])
    v0 = vol(parts, "case")
    rim = {"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [0, 0, 20], "area": 40 * 30 - 36 * 26}
    out, notes = apply_edits(parts, [{"op": "lip", "part": "case", "face": rim, "width": 1, "height": 1.5, "mode": "lip"}])
    ok(notes)
    assert abs(vol(out, "case") - (v0 + (38 * 28 - 36 * 26) * 1.5)) < 1e-3
    out, notes = apply_edits(parts, [{"op": "lip", "part": "case", "face": rim, "width": 1, "height": 1.5, "mode": "groove"}])
    ok(notes)
    assert abs(vol(out, "case") - (v0 - (38 * 28 - 36 * 26) * 1.5)) < 1e-3
    out, notes = apply_edits(parts, [{"op": "lip", "part": "case", "face": rim, "width": 1, "height": 1.5, "mode": "lip", "side": "outer"}])
    ok(notes)
    assert abs(vol(out, "case") - (v0 + (40 * 30 - 38 * 28) * 1.5)) < 1e-3
    # copy and repair
    out, notes = apply_edits(block(), [{"op": "copy_part", "part": "block", "new_part": "block_b", "delta": [50, 0, 0]}, {"op": "repair", "part": "block_b"}])
    ok(notes)
    assert out.names == ["block", "block_b"] and abs(vol(out, "block_b") - 8000) < 1e-6 and out["block_b"].bounding_box().center().X == pytest.approx(50)
    assert "valid yes -> yes" in notes[1].detail


def test_wedge_and_coil_primitives():
    out, notes = apply_edits(Parts([]), [
        {"op": "primitive", "kind": "wedge", "size": {"w": 20, "d": 10, "h": 8}, "mode": "new", "new_part": "ramp"},
        {"op": "primitive", "kind": "coil", "size": {"R": 6, "r": 1, "pitch": 4, "h": 20}, "mode": "new", "new_part": "spring", "at": [40, 0, 0]},
    ])
    ok(notes)
    assert abs(vol(out, "ramp") - 20 * 10 * 8 / 2) < 1e-6 and out["ramp"].bounding_box().min.Z == pytest.approx(0)
    bb = out["spring"].bounding_box()
    assert abs(bb.min.Z) < 0.05 and abs(bb.max.Z - 22) < 0.5 and abs(bb.min.X - 33) < 0.1 and out["spring"].is_valid
    assert abs(vol(out, "spring") - math.pi * 1 * 1 * bd.Helix(pitch=4, height=20, radius=6).length) < 5
