"""Surface bodies: sheets from sketches (planar / extrude / revolve / sweep / loft), offset, extend, trim,
split, stitch, patch (G0/G1), thicken, to-solid, and the continuity report."""
from __future__ import annotations

import math

import build123d as bd
import pytest

from eitri.edits import apply_edits, describe, selector_for_edge, selector_for_face
from eitri.model import Part, Parts
from eitri.report import analyze
from eitri.viewglb import topology
from eitri import surfaces as SF


def ok(notes):
    assert all(n.ok for n in notes), [n.detail for n in notes]


def area(parts, name):
    return parts[name].area


LINE = {"type": "sketch", "entities": [{"id": "l1", "type": "line", "a": [-10, 0], "b": [10, 0]}], "constraints": [], "dims": []}
ARC = {"type": "sketch", "entities": [{"id": "a1", "type": "arc", "c": [0, 0], "r": 10, "start": 0, "end": 180}], "constraints": [], "dims": []}


def test_surfaces_from_sketches():
    out, notes = apply_edits(Parts([]), [
        {"op": "surface", "kind": "planar", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [0, 0], "w": 20, "h": 10}, "new_part": "flat"},
        {"op": "surface", "kind": "extrude", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": LINE, "distance": 8, "new_part": "wall"},
        {"op": "surface", "kind": "extrude", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": {"type": "circle", "center": [40, 0], "r": 5}, "distance": 8, "new_part": "tube"},
        {"op": "surface", "kind": "revolve", "part": None, "origin": [0, 0, 0], "normal": [0, -1, 0], "profile": {"type": "sketch", "entities": [{"id": "l1", "type": "line", "a": [5, 0], "b": [5, 10]}]}, "axis": "v", "angle": 180, "new_part": "half"},
        {"op": "surface", "kind": "loft", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": LINE, "sections": [{"offset": 0}, {"offset": 10, "scale": 0.5}], "new_part": "fan"},
    ])
    ok(notes)
    assert abs(area(out, "flat") - 200) < 1e-6 and SF.is_sheet(out["flat"])
    assert abs(area(out, "wall") - 160) < 1e-6 and len(out["wall"].faces()) == 1
    assert abs(area(out, "tube") - 2 * math.pi * 5 * 8) < 1e-6 and SF.is_sheet(out["tube"])
    assert abs(area(out, "half") - math.pi * 5 * 10) < 1e-6
    assert abs(area(out, "fan") - (20 + 10) / 2 * 10) < 1e-6
    assert "surface wall: extrude" in describe({"op": "surface", "kind": "extrude", "profile": LINE, "distance": 8, "new_part": "wall"})
    # the report describes a surface body instead of flagging a missing solid
    rep = analyze(out)
    flat = next(p for p in rep.parts if p.name == "flat")
    assert any("surface body" in i and "4 free edge" in i for i in flat.issues), flat.issues
    # sweep: a vertical line swept along an arc edge of another part
    hub = Parts([Part("hub", bd.Cylinder(10, 2, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)), "#999")])
    t = topology(hub)
    rim = next(e for e in t.edges if e.type == "circle" and abs(e.mid[2] - 2) < 1e-9)
    out, notes = apply_edits(hub, [
        {"op": "surface", "kind": "sweep", "part": "hub", "origin": [10, 0, 2], "normal": [0, 1, 0], "profile": {"type": "sketch", "entities": [{"id": "l1", "type": "line", "a": [0, 0], "b": [0, -6]}]}, "path": [selector_for_edge(rim.__dict__)], "path_part": "hub", "new_part": "skirt"},
    ])
    ok(notes)
    assert abs(area(out, "skirt") - 2 * math.pi * 10 * 6) < 1e-3


def test_offset_extend_trim_split():
    cyl = bd.Cylinder(8, 30).faces().filter_by(bd.GeomType.CYLINDER)[0]
    parts = Parts([Part("skin", cyl, "#999"), Part("sheet", (bd.Plane.XY * bd.Rectangle(20, 10)).face(), "#999")])
    out, notes = apply_edits(parts, [
        {"op": "surface_offset", "part": "skin", "distance": 2},
        {"op": "surface_extend", "part": "sheet", "distance": 5},
        {"op": "surface_trim", "part": "sheet", "by": "YZ", "keep": "top"},
    ])
    ok(notes)
    assert abs(area(out, "skin") - 2 * math.pi * 10 * 30) < 1e-3
    assert abs(area(out, "sheet") - 30 * 20 / 2) < 1e-6                                   # extended 5 all round (30 x 20), then half kept
    # tangent extension of one edge, then a split by a face of another part
    parts = Parts([Part("sheet", (bd.Plane.XY * bd.Rectangle(20, 10)).face(), "#999"), Part("blade", bd.Box(2, 40, 40), "#c33")])
    t = topology(parts)
    right = next(e for e in t.edges if e.part == "sheet" and abs(e.mid[0] - 10) < 1e-9)
    side = next(f for f in t.faces if f.part == "blade" and f.type == "plane" and abs(f.center[0] - 1) < 1e-9)
    out, notes = apply_edits(parts, [
        {"op": "surface_extend", "part": "sheet", "distance": 4, "edges": [selector_for_edge(right.__dict__)], "mode": "tangent"},
        {"op": "surface_split", "part": "sheet", "by": {"face": selector_for_face(side.__dict__), "part": "blade"}, "new_part": "sheet_left"},
    ])
    ok(notes)
    assert out.names == ["sheet", "blade", "sheet_left"]
    assert abs(area(out, "sheet") + area(out, "sheet_left") - 240) < 1e-6 and abs(area(out, "sheet") - 13 * 10) < 1e-6   # the part keeps the face-normal side
    # trim by a sketch profile: keep what is inside a circle
    parts = Parts([Part("sheet", (bd.Plane.XY * bd.Rectangle(20, 20)).face(), "#999")])
    out, notes = apply_edits(parts, [{"op": "surface_trim", "part": "sheet", "by": {"profile": {"type": "circle", "center": [0, 0], "r": 5}, "origin": [0, 0, 0], "normal": [0, 0, 1]}, "keep": "inside"}])
    ok(notes)
    assert abs(area(out, "sheet") - math.pi * 25) < 1e-3
    out, notes = apply_edits(parts, [{"op": "surface_trim", "part": "sheet", "by": {"profile": {"type": "circle", "center": [0, 0], "r": 5}, "origin": [0, 0, 0], "normal": [0, 0, 1]}, "keep": "outside"}])
    ok(notes)
    assert abs(area(out, "sheet") - (400 - math.pi * 25)) < 1e-3


def test_stitch_patch_thicken_to_solid_and_continuity():
    box = bd.Box(10, 10, 10)
    faces = box.faces()
    parts = Parts([Part(f"f{i}", f, "#999") for i, f in enumerate(faces)])
    # five faces stitched: an open shell with 4 free edges; the patch closes it (G0) and it becomes a solid
    out, notes = apply_edits(parts, [{"op": "stitch", "parts": [f"f{i}" for i in range(5)]}])
    ok(notes)
    assert out.names == ["f0", "f5"] and SF.is_sheet(out["f0"]) and len(SF.free_edges(out["f0"])) == 4 and "4 free edge" in notes[0].detail
    t = topology(out)
    loop = [e for e in t.edges if e.part == "f0" and e.id in {x.id for x in t.edges} and sum(1 for f in t.faces if f.part == "f0" and e.id in f.edges) == 1]
    assert len(loop) == 4
    out2, notes = apply_edits(out, [{"op": "patch", "part": "f0", "edges": [selector_for_edge(e.__dict__) for e in loop]}])
    ok(notes)
    assert not SF.is_sheet(out2["f0"]) and abs(out2["f0"].volume - 1000) < 1e-6 and "closed solid" in notes[0].detail
    # all six stitched at once -> a solid directly; to_solid on an open shell is refused with the free-edge count
    out3, notes = apply_edits(parts, [{"op": "stitch", "parts": [f"f{i}" for i in range(6)]}])
    ok(notes)
    assert abs(out3["f0"].volume - 1000) < 1e-6
    out4, notes = apply_edits(out, [{"op": "to_solid", "part": "f0"}])
    assert not notes[0].ok and "4 free edge" in notes[0].detail
    # thicken a half cylinder sheet into a solid; a G1 patch over its open end stays tangent (no crease)
    half = bd.split(bd.Cylinder(8, 20).faces().filter_by(bd.GeomType.CYLINDER)[0], bisect_by=bd.Plane.XZ, keep=bd.Keep.TOP).faces()[0]
    parts = Parts([Part("half", half, "#999")])
    out, notes = apply_edits(parts, [{"op": "thicken", "part": "half", "thickness": 2}])
    ok(notes)
    assert abs(abs(out["half"].volume) - math.pi * (10 ** 2 - 8 ** 2) / 2 * 20) < 1e-2 or abs(abs(out["half"].volume) - math.pi * (8 ** 2 - 6 ** 2) / 2 * 20) < 1e-2
    c = SF.continuity(bd.extrude(bd.RectangleRounded(20, 10, 3), 5))
    assert c["G0"] == 8 + 8 and c["G1"] == 8 and c["free_edges"] == 0            # top/bottom creases are G0, the rounded sides meet tangentially
    assert SF.continuity(half)["free_edges"] == 4
    assert "thicken surface half by 2 mm" == describe({"op": "thicken", "part": "half", "thickness": 2})
