"""Measurement helpers: hole classification, wall thickness, relationships."""
from __future__ import annotations

import math

from eitri import *  # noqa: F403
from eitri.measure import (bosses, cylindrical_features, fillet_radii, free_edge_count, holes, min_distance,
                             min_wall, overlap_volume, point_inside, signed_volume)


def _plate():
    return Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))


def test_through_blind_counterbore():
    p = _plate()
    p = p - Pos(-20, 0, 6) * hole_cutter(5, depth=20)              # through
    p = p - Pos(0, 0, 6) * hole_cutter(4, depth=3)                 # blind
    p = p - Pos(20, 0, 6) * hole_cutter(3.4, depth=20, counterbore=(6.5, 3.5))
    hs = holes(p)
    by_d = {round(h.diameter, 1): h for h in hs}
    assert by_d[5.0].through is True and abs(by_d[5.0].depth - 6) < 1e-6
    assert by_d[4.0].through is False and abs(by_d[4.0].depth - 3) < 1e-6
    assert by_d[3.4].through is True
    assert by_d[6.5].through is False and abs(by_d[6.5].depth - 3.5) < 1e-6
    assert holes(p, axis="Z") == hs and holes(p, axis="X") == []
    assert len(holes(p, through=True)) == 2


def test_bosses_fillets_and_partial_cylinders():
    p = _plate() + Pos(0, 0, 6) * Cylinder(6, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    b = bosses(p)
    assert len(b) == 1 and abs(b[0].diameter - 12) < 1e-6 and abs(b[0].depth - 10) < 1e-6
    r = round_vertical_edges(_plate(), 4)
    assert fillet_radii(r) == [4.0]
    assert holes(r) == [] and bosses(r) == []          # partial cylinders are not holes/bosses
    partial = cylindrical_features(r, min_coverage=0.2)
    assert len(partial) == 4 and all(c.kind == "boss" for c in partial)


def test_side_holes_and_slots():
    p = _plate()
    side = Plane(p.faces().sort_by(Axis.X)[-1])
    p = cut_holes(p, [(0, 0)], d=3, plane=side)
    hs = holes(p, axis="X")
    assert len(hs) == 1 and hs[0].through is True and abs(hs[0].depth - 60) < 1e-6
    s = _plate() - Pos(0, 0, 6) * slot_cutter(20, 6, depth=10)
    assert holes(s) == []                               # slot halves are not full holes
    assert len([c for c in cylindrical_features(s, min_coverage=0.4) if c.kind == "hole"]) == 2


def test_min_wall_and_relationships():
    box = shell_box(40, 30, 20, wall=1.5, floor=2.0)
    w = min_wall(box, samples=500)
    assert w is not None and abs(w.thickness - 1.5) < 0.05
    thin = Box(20, 20, 0.4)
    assert abs(min_wall(thin).thickness - 0.4) < 1e-3
    a = Box(10, 10, 10)
    b = Pos(12, 0, 0) * Box(10, 10, 10)
    c = Pos(5, 0, 0) * Box(10, 10, 10)
    assert overlap_volume(a, b) == 0 and abs(min_distance(a, b) - 2) < 1e-6
    assert abs(overlap_volume(a, c) - 500) < 1e-6 and min_distance(a, c) == 0
    assert point_inside(a, (0, 0, 0)) and not point_inside(a, (20, 0, 0))
    assert signed_volume(a) > 0
    assert free_edge_count(a) == 0 and free_edge_count(a.faces()[0]) == 4
    assert free_edge_count(Cylinder(5, 10)) == 0


def test_angled_axis_hole_and_curved_exit():
    cyl = Cylinder(15, 20)
    cyl = cyl - Rot(0, 90, 0) * Cylinder(2.5, 60)          # radial through hole along X exiting on curved faces
    hs = holes(cyl, diameter=5)
    assert len(hs) == 1 and hs[0].through is True and abs(hs[0].depth - 30) < 1e-6
    tilted = Box(40, 40, 10) - Rot(0, 30, 0) * Cylinder(3, 60)
    hs = holes(tilted)
    assert len(hs) == 1 and hs[0].through is True and abs(abs(hs[0].axis.Z) - math.cos(math.radians(30))) < 1e-6


def test_sharp_edges_and_sliver_detection():
    from eitri.measure import sharp_edges
    assert sharp_edges(Box(20, 20, 10)) == []
    assert sharp_edges(Cylinder(5, 10)) == []
    assert sharp_edges(chamfer(Box(20, 20, 10).edges().filter_by(Axis.Z), 2)) == []
    wedge = extrude(Polygon((0, 0), (30, 0), (0, 8), align=None), 10)      # ~15 deg tip
    found = sharp_edges(wedge)
    assert len(found) == 1 and 14 < found[0].angle_deg < 16 and abs(found[0].length - 10) < 1e-6
    # a 0.3 mm sliver flange on a plate: random sampling can miss it, edge-inset sampling must not
    plate = Box(40, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    sliver = Pos(20.15, 0, 6) * Box(0.3, 10, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
    part = plate + sliver
    w = min_wall(part, samples=100)
    assert w is not None and abs(w.thickness - 0.3) < 0.02


def test_point_inside_matches_occt_classifier():
    """The ray-parity fast path agrees with OCCT's classifier, including points a hair off a surface."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON
    from OCP.gp import gp_Pnt
    p = _plate() - Pos(0, 0, 6) * hole_cutter(8, depth=20)
    p = p.solids()[0]
    cl = BRepClass3d_SolidClassifier(p.wrapped)
    pts = [(x, y, z) for x in (-30.01, -29.99, -4.01, -3.99, 0, 3.99, 4.01, 29.99) for y in (-20.01, -19.99, 0, 19.99)
           for z in (-0.01, 0.0, 0.01, 3, 5.99, 6.01)]
    for pt in pts:
        cl.Perform(gp_Pnt(*pt), 1e-6)
        assert point_inside(p, pt) == (cl.State() in (TopAbs_IN, TopAbs_ON)), pt


def test_big_assembly_gets_clearances_and_interference():
    """Over 40 parts the report still checks neighbours (only pairs whose boxes are near), lists the tightest
    clearance first and counts the far pairs it skipped."""
    from eitri.model import Part, Parts
    from eitri.report import analyze, summarize
    parts = Parts([Part(f"b{i}", Pos(i * 10, 0, 0) * Box(5, 5, 5), "#888888") for i in range(45)]
                  + [Part("clash", Box(2, 2, 2), "#cc3333"),                                   # inside b0
                     Part("tight", Pos(10, 4.0, 0) * Box(2, 2, 2), "#3333cc")])                # 0.5 mm from b1
    rep = analyze(parts, wall_samples=50)
    assert any("INTERFERENCE: b0 and clash" in s for s in rep.issues)
    assert rep.far_pairs > 0 and len(rep.pairs) < 47 * 46 // 2
    text = summarize(rep)
    clear = next(line for line in text.splitlines() if line.startswith("Clearances: "))
    assert clear.startswith("Clearances: b1<->tight 0.5 mm") and "other part pair(s) are over" in text


def test_free_edges_ignore_sphere_poles_and_cone_tips():
    """A sphere's poles and a cone's apex are degenerate edges, not holes; an open shell still counts."""
    assert free_edge_count(Sphere(5)) == 0 and free_edge_count(Cone(5, 0, 10)) == 0
    open_box = Shell(Box(10, 10, 10).faces()[:-1])
    assert free_edge_count(open_box) == 4
