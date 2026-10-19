"""Every library helper must produce valid geometry with the dimensions it promises."""
from __future__ import annotations

import math

import pytest

from eitri import *  # noqa: F403
from eitri import bd
from eitri.measure import holes, bosses, min_wall, overlap_volume, min_distance

TOL = 1e-6


def _valid_solid(shape, n=1):
    assert shape.is_valid, "invalid geometry"
    assert len(shape.solids()) == n, f"expected {n} solid(s), got {len(shape.solids())}"
    assert shape.volume > 0


def test_fastener_tables():
    assert screw_clearance("M3") == 3.4 and screw_clearance("M3", "close") == 3.2
    assert tap_drill("M4") == 3.3
    assert counterbore_dims("M3") == (6.5, 3.5)
    assert nut_dims("M5") == (8.0, 4.7)
    assert heat_set_insert_dims("M3") == (4.0, 5.7)
    with pytest.raises(KeyError):
        screw_clearance("M7")


def test_hole_cutter_and_cut_holes():
    plate = Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    out = cut_holes(plate, [(-20, 0), (20, 0)], size="M3")
    _valid_solid(out)
    hs = holes(out, diameter=3.4)
    assert len(hs) == 2 and all(h.through for h in hs)
    out2 = cut_holes(plate, [(0, 0)], size="M3", head="counterbore")
    cb = holes(out2, diameter=6.5)
    assert len(cb) == 1 and cb[0].through is False and abs(cb[0].depth - 3.5) < 1e-3
    out3 = cut_holes(plate, [(0, 0)], size="M3", head="countersink")
    _valid_solid(out3)
    assert out3.volume < plate.volume - math.pi * 1.7 ** 2 * 6
    blind = cut_holes(plate, [(0, 10)], d=4, depth=3)
    bl = holes(blind, diameter=4)
    assert len(bl) == 1 and bl[0].through is False and abs(bl[0].depth - 3) < 1e-6
    # holes into a side face via a plane
    side = Plane(plate.faces().sort_by(Axis.X)[-1])
    out4 = cut_holes(plate, [(0, 0)], d=3, plane=side)
    assert len(holes(out4, diameter=3, axis="X")) == 1


def test_boss_standoff_insert():
    b = boss(10, 12, hole_d=3, hole_depth=8)
    _valid_solid(b)
    assert abs(b.bounding_box().size.Z - 12) < TOL and b.bounding_box().min.Z == pytest.approx(0)
    h = holes(b, diameter=3)
    assert len(h) == 1 and h[0].through is False
    s = standoff(10, "M3", kind="insert")
    assert len(holes(s, diameter=4.0)) == 1
    s2 = standoff(10, "M3", kind="clearance")
    assert holes(s2, diameter=3.4)[0].through


def test_nut_pocket_and_slot():
    block = Box(30, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    out = block - Pos(0, 0, 10) * nut_pocket_cutter("M3", depth=3)
    _valid_solid(out)
    assert out.volume < block.volume
    out2 = block - Pos(0, 0, 10) * slot_cutter(20, 6, depth=4)
    _valid_solid(out2)
    assert abs((block.volume - out2.volume) - ((20 - 6) * 6 + math.pi * 9) * 4) < 1e-3


def test_gusset_rib_patterns():
    g = gusset(10, 10, 2)
    _valid_solid(g)
    assert abs(g.volume - 0.5 * 10 * 10 * 2) < TOL
    r = rib(20, 5, 2)
    assert abs(r.volume - 200) < TOL and r.bounding_box().min.Z == pytest.approx(0)
    pins = pattern_polar(Cylinder(1, 5), 6, radius=10)
    assert len(pins) == 6
    assert abs(pins[1].center().X - 10 * math.cos(math.radians(60))) < 1e-6
    grid = pattern_grid(Cylinder(1, 5), 3, 2, 10, 8)
    assert len(grid) == 6
    xs = sorted({round(s.center().X, 6) for s in grid})
    assert xs == [-10, 0, 10]


def test_fillets_chamfers_text():
    box = Box(30, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    r = round_vertical_edges(box, 3)
    _valid_solid(r)
    assert r.volume < box.volume
    c = chamfer_top_edges(box, 1)
    _valid_solid(c)
    out, applied = try_fillet(box, box.edges().filter_by(Axis.Z), 50, fallbacks=(3,))
    assert applied == 3
    out2, applied2 = try_fillet(box, box.edges().filter_by(Axis.Z), 50)
    assert applied2 is None and out2.volume == pytest.approx(box.volume)
    e = emboss_text(box, "A", size=8, height=1)
    _valid_solid(e)
    assert e.volume > box.volume and abs(e.bounding_box().max.Z - 11) < TOL
    d = deboss_text(box, "A", size=8, depth=0.5)
    assert d.volume < box.volume and abs(d.bounding_box().max.Z - 10) < TOL


def test_prisms_boxes_tubes_shafts():
    h = hex_prism(10, 5)
    _valid_solid(h)
    assert abs(h.bounding_box().size.Y - 10) < 1e-6   # flats parallel to Y? across-flats measured on Y
    rb = rounded_box(40, 30, 10, 5, top_radius=2)
    _valid_solid(rb)
    sb = shell_box(40, 30, 20, wall=2, corner_radius=4)
    _valid_solid(sb)
    w = min_wall(sb)
    assert w is not None and abs(w.thickness - 2) < 0.05
    t = tube(10, 6, 20)
    assert abs(t.volume - math.pi * (25 - 9) * 20) < 1e-3
    ds = d_shaft(6, 20, 0.5)
    _valid_solid(ds)
    assert abs(ds.bounding_box().max.X - 2.5) < TOL
    ks = keyed_shaft(8, 30, 2, 1.5, key_length=10)
    _valid_solid(ks)


def test_enclosure():
    e = enclosure(80, 50, 30, wall=2, screw_size="M3")
    base, lid = e["base"], e["lid"]
    _valid_solid(base)
    _valid_solid(lid)
    assert abs(base.bounding_box().size.X - 80) < TOL and abs(lid.bounding_box().max.Z - 30) < TOL
    assert overlap_volume(base, lid) < 1e-6
    assert len(holes(base, diameter=4.0)) == 4      # insert holes in the bosses
    assert len(holes(lid, diameter=3.4)) == 4       # clearance holes
    e2 = enclosure(60, 40, 20, screw_size=None, assembled=False)
    assert overlap_volume(e2["base"], e2["lid"]) < 1e-6
    assert e2["lid"].bounding_box().min.Z == pytest.approx(0, abs=1e-6)


def test_bearings():
    assert bearing_dims("608") == (8, 22, 7) and bearing_dims("608zz") == (8, 22, 7)
    block = Box(40, 40, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    out = block - Pos(0, 0, 10) * bearing_pocket_cutter("608", fit=0.1)
    _valid_solid(out)
    pocket = holes(out, diameter=22.1)
    assert len(pocket) == 1 and abs(pocket[0].depth - 7) < 1e-6
    ring = bearing_placeholder("625")
    assert abs(ring.bounding_box().size.X - 16) < TOL


def test_parts_gear_thread_screws():
    g = spur_gear(1.5, 20, 6, bore=5)
    assert g.is_valid and len(g.solids()) == 1
    assert abs(g.bounding_box().size.X - 33) < 0.05
    assert g.bounding_box().min.Z == pytest.approx(0, abs=1e-6)
    assert len(holes(g, diameter=5)) == 1
    rod = threaded_rod("M8", 12)
    assert rod.is_valid and len(rod.solids()) == 1
    assert abs(rod.bounding_box().size.X - 8) < 0.15
    s = screw("M3", 10)
    assert s.bounding_box().max.Z == pytest.approx(0, abs=1e-6) and abs(s.bounding_box().min.Z + 13) < 0.5
    n = hex_nut("M3")
    assert n.bounding_box().min.Z == pytest.approx(0, abs=1e-6)
    assert abs(n.bounding_box().size.Z - 2.4) < 0.2
    b = bearing("608")
    assert abs(b.bounding_box().size.X - 22) < 0.1
    block = Box(20, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    tapped = tapped_hole(block, "M6", (0, 0, 10), depth=8)
    assert tapped.is_valid and tapped.volume < block.volume
