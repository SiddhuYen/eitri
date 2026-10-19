"""Benchmark tasks: a natural-language prompt plus a checker over the produced geometry.

A checker receives `parts` (eitri Parts loaded from the produced STEP, one entry
per top-level body/occurrence) and returns a list of (name, ok, detail). The
score of a task is the fraction of checks passed; "solved" means all passed.
Checkers only use geometry (never the source), so the same checker scores any
system's STEP output.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import build123d as bd

from eitri.measure import (bosses, fillet_radii, holes, is_valid, min_distance, min_wall, overlap_volume,
                             point_inside, signed_volume)
from eitri.model import Parts

Check = tuple[str, bool, str]


@dataclass
class Task:
    name: str
    prompt: str
    check: Callable[[Parts], list[Check]]
    tags: list[str] = field(default_factory=list)
    difficulty: int = 1   # 1 easy .. 3 hard
    followup: str | None = None                                   # a second request in the same session
    followup_check: Callable[[Parts], list[Check]] | None = None  # checker for the result after the follow-up


def _near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _size(parts: Parts) -> bd.Vector:
    return parts.size


def _sorted_size(parts: Parts) -> tuple[float, float, float]:
    s = parts.size
    return tuple(sorted((s.X, s.Y, s.Z)))  # type: ignore[return-value]


def _single_valid_solid(parts: Parts) -> list[Check]:
    all_solids = parts.all_solids
    return [
        ("one solid body", len(all_solids) == 1, f"{len(all_solids)} solids in {len(parts)} part(s)"),
        ("valid geometry", all(is_valid(p.shape) for p in parts), ""),
    ]


def _valid_parts(parts: Parts, n: int) -> list[Check]:
    return [
        (f"{n} separate parts", len(parts) == n and all(len(p.shape.solids()) == 1 for p in parts),
         f"{len(parts)} parts: " + ", ".join(f"{p.name}({len(p.shape.solids())})" for p in parts)),
        ("valid geometry", all(is_valid(p.shape) for p in parts), ""),
    ]


def _no_interference(parts: Parts) -> Check:
    worst = 0.0
    pl = list(parts)
    for i in range(len(pl)):
        for j in range(i + 1, len(pl)):
            worst = max(worst, overlap_volume(pl[i].shape, pl[j].shape))
    return ("parts do not interfere", worst < 1e-3, f"max overlap {worst:.4f} mm^3")


# ---------------------------------------------------------------------------- tasks

def t_plate(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("60 x 40 x 5 envelope", _near(s.X, 60, 0.05) and _near(s.Y, 40, 0.05) and _near(s.Z, 5, 0.05), f"{s.X:.2f} x {s.Y:.2f} x {s.Z:.2f}"))
    hs = holes(parts.compound, diameter=4.5, tol=0.2, through=True)
    out.append(("four M4 clearance through holes (4.3-4.7)", len(hs) == 4, f"{len(hs)} found: " + ", ".join(f"{h.diameter:.2f}" for h in hs)))
    if len(hs) == 4:
        bb = parts.bbox
        ok = all(_near(abs(h.center.X - bb.center().X), 22, 0.3) and _near(abs(h.center.Y - bb.center().Y), 12, 0.3) for h in hs)
        out.append(("holes 8 mm from edges", ok, ", ".join(f"({h.center.X:.1f},{h.center.Y:.1f})" for h in hs)))
    fr = fillet_radii(parts.compound)
    out.append(("5 mm corner radius", any(_near(r, 5, 0.1) for r in fr), f"radii {fr}"))
    return out


def t_l_bracket(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = _sorted_size(parts)
    out.append(("envelope 50 x 50 x 30 (sorted)", _near(s[0], 30, 0.2) and _near(s[1], 50, 0.2) and _near(s[2], 50, 0.2), f"{s}"))
    hs = holes(parts.compound, diameter=5.5, tol=0.2, through=True)
    out.append(("four 5.5 mm through holes", len(hs) == 4, f"{len(hs)}: " + ", ".join(f"{h.diameter:.2f}" for h in hs)))
    if hs:
        axes = {tuple(h.axis) for h in hs}
        out.append(("holes on both legs (two axes)", len({tuple(round(abs(c), 2) for c in a) for a in axes}) == 2, f"{axes}"))
    w = min_wall(parts.compound)
    out.append(("thickness 4 mm", w is not None and _near(w.thickness, 4, 0.15), f"{w.thickness if w else None}"))
    fr = fillet_radii(parts.compound, kind="concave")
    out.append(("3 mm inside fillet present", any(_near(r, 3, 0.3) for r in fr), f"concave radii {fr}"))
    return out


def t_enclosure(parts: Parts) -> list[Check]:
    out = _valid_parts(parts, 2)
    out.append(_no_interference(parts))
    s = parts.size
    out.append(("outer 100 x 60 x 40 assembled", _near(s.X, 100, 0.3) and _near(s.Y, 60, 0.3) and _near(s.Z, 40, 0.5), f"{s.X:.2f} x {s.Y:.2f} x {s.Z:.2f}"))
    if len(parts) == 2:
        base = max(parts, key=lambda p: p.shape.volume)
        lid = min(parts, key=lambda p: p.shape.volume)
        w = min_wall(base.shape)
        out.append(("nothing thinner than 1.4 mm", w is not None and w.thickness >= 1.4, f"{w.thickness if w else None}"))
        bb = base.shape.bounding_box()
        c = bb.center()
        zm = (bb.min.Z + bb.max.Z) / 2
        wall_ok = point_inside(base.shape, (bb.min.X + 1.0, c.Y, zm)) and not point_inside(base.shape, (bb.min.X + 3.0, c.Y, zm)) \
            and point_inside(base.shape, (c.X, bb.min.Y + 1.0, zm)) and not point_inside(base.shape, (c.X, bb.min.Y + 3.0, zm))
        out.append(("side walls about 2 mm (material 1 mm in, empty 3 mm in)", wall_ok, ""))
        # cavity: a point in the middle of the base must be empty
        out.append(("base is hollow", not point_inside(base.shape, (c.X, c.Y, bb.min.Z + 8)), ""))
        ins = holes(base.shape, diameter=4.0, tol=0.25)
        out.append(("four M3 heat-set insert holes (3.8-4.2) in base", len(ins) == 4, f"{len(ins)}"))
        cl = holes(lid.shape, diameter=3.4, tol=0.25, through=True)
        out.append(("four M3 clearance holes in lid", len(cl) == 4, f"{len(cl)}"))
        d = min_distance(base.shape, lid.shape)
        out.append(("lid seated on base (<=0.3 mm)", d <= 0.3, f"{d:.3f}"))
    return out


def t_flange(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("OD 80 mm", _near(s.X, 80, 0.2) and _near(s.Y, 80, 0.2), f"{s.X:.2f} x {s.Y:.2f}"))
    out.append(("height 25 mm", _near(s.Z, 25, 0.2), f"{s.Z:.2f}"))
    bore = holes(parts.compound, diameter=20, tol=0.2, through=True)
    out.append(("20 mm through bore", len(bore) == 1, f"{len(bore)}"))
    bolts = holes(parts.compound, diameter=6.6, tol=0.3, through=True)
    out.append(("6 bolt holes 6.4-6.9 mm", len(bolts) == 6, f"{len(bolts)}"))
    if len(bolts) == 6:
        r = [math.hypot(h.center.X - parts.bbox.center().X, h.center.Y - parts.bbox.center().Y) for h in bolts]
        out.append(("bolt circle 60 mm", all(_near(x, 30, 0.3) for x in r), f"radii {[round(x, 2) for x in r]}"))
    hub = bosses(parts.compound, diameter=40, tol=0.3)
    out.append(("40 mm hub", len(hub) >= 1, f"{len(hub)}"))
    return out


def t_gear(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("outer diameter 44 mm (m2, 20t)", _near(s.X, 44, 0.3) and _near(s.Y, 44, 0.3), f"{s.X:.2f} x {s.Y:.2f}"))
    out.append(("thickness 8 mm", _near(s.Z, 8, 0.1), f"{s.Z:.2f}"))
    bore = holes(parts.compound, diameter=8, tol=0.15, through=True)
    out.append(("8 mm bore", len(bore) == 1, f"{len(bore)}"))
    # tooth count: count cylindrical-ish features is hard; use volume band for a 20t m2 gear 8 thick w/ bore
    v = parts.compound.volume
    expected = math.pi * (20 ** 2) * 8 - math.pi * 16 * 8   # pitch-circle area approx minus bore
    out.append(("volume consistent with 20 teeth (±12%)", abs(v - expected) / expected < 0.12, f"{v:.0f} vs ~{expected:.0f}"))
    faces = len(parts.compound.faces())
    out.append(("has tooth geometry (>= 60 faces)", faces >= 60, f"{faces} faces"))
    return out


def t_shaft(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = _sorted_size(parts)
    out.append(("length 80 mm", _near(s[2], 80, 0.2), f"{s}"))
    out.append(("max diameter 20 mm", _near(s[1], 20, 0.2), f"{s}"))
    b = bosses(parts.compound)
    ds = sorted({round(x.diameter, 1) for x in b})
    out.append(("steps of 12, 16 and 20 mm", all(any(_near(d, t, 0.2) for d in ds) for t in (12, 16, 20)), f"{ds}"))
    v = parts.compound.volume
    full = math.pi * (10 ** 2) * 30 + math.pi * 64 * 30 + math.pi * 36 * 20
    out.append(("keyway removes material", v < full - 10, f"{v:.0f} vs {full:.0f}"))
    out.append(("chamfered ends (chamfer faces)", any(f.geom_type == bd.GeomType.CONE for f in parts.compound.faces()), ""))
    return out


def t_phone_stand(parts: Parts) -> list[Check]:
    out = [("valid geometry", all(is_valid(p.shape) for p in parts), "")]
    out.append(("at most 2 bodies", len(parts.all_solids) <= 2, f"{len(parts.all_solids)}"))
    s = parts.size
    out.append(("fits 100 x 100 x 100 footprint", s.X <= 120 and s.Y <= 120 and s.Z <= 120, f"{s.X:.0f} x {s.Y:.0f} x {s.Z:.0f}"))
    out.append(("at least 60 mm tall", s.Z >= 60, f"{s.Z:.1f}"))
    out.append(("at least 60 mm wide", max(s.X, s.Y) >= 60, f"{s.X:.1f} / {s.Y:.1f}"))
    w = min_wall(parts.compound)
    out.append(("no wall under 2 mm", w is None or w.thickness >= 1.9, f"{w.thickness if w else None}"))
    bb = parts.bbox
    out.append(("sits on Z=0", _near(bb.min.Z, 0, 0.01), f"{bb.min.Z:.3f}"))
    # a slot for the phone: some horizontal section near the base must have a gap >= 9 mm wide
    return out


def t_cable_clip(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("fits 30 mm", max(s.X, s.Y, s.Z) <= 32, f"{s.X:.1f} x {s.Y:.1f} x {s.Z:.1f}"))
    w = min_wall(parts.compound)
    out.append(("walls >= 1.5 mm", w is not None and w.thickness >= 1.45, f"{w.thickness if w else None}"))
    hs = holes(parts.compound, diameter=3.4, tol=0.25, through=True)
    out.append(("one M3 clearance hole", len(hs) == 1, f"{len(hs)}"))
    # cable channel: the clip must enclose a ~6 mm cable: look for a concave cylinder near 6 mm
    from eitri.measure import cylindrical_features
    cf = [c for c in cylindrical_features(parts.compound, min_coverage=0.4) if c.kind == "hole" and 5.8 <= c.diameter <= 7.0]
    out.append(("6 mm cable channel (concave, 5.8-7.0)", len(cf) >= 1, f"{[round(c.diameter, 2) for c in cf]}"))
    return out


def t_hinge(parts: Parts) -> list[Check]:
    out = _valid_parts(parts, 2)
    out.append(_no_interference(parts))
    if len(parts) == 2:
        a, b = parts[0], parts[1]
        d = min_distance(a, b)
        out.append(("print-in-place clearance 0.25-0.6 mm", 0.2 <= d <= 0.7, f"{d:.3f}"))
        s = parts.size
        bb = parts.bbox
        from eitri.measure import cylindrical_features
        cf = cylindrical_features(parts.compound, min_coverage=0.5)
        pins = [c for c in cf if c.kind == "boss" and 3 <= c.diameter <= 6]
        bores = [c for c in cf if c.kind == "hole" and 3 <= c.diameter <= 7]
        # length along the pin axis (fall back to the longer horizontal extent)
        if pins:
            ax = pins[0].axis
            along = abs(s.X * ax.X) + abs(s.Y * ax.Y) + abs(s.Z * ax.Z) if max(abs(ax.X), abs(ax.Y), abs(ax.Z)) > 0.99 else max(s.X, s.Y)
        else:
            along = s.X if _near(s.X, 40, 0.5) else s.Y
        out.append(("40 mm long along the pin", _near(along, 40, 0.5), f"{along:.1f} (bbox {s.X:.1f} x {s.Y:.1f} x {s.Z:.1f})"))
        out.append(("lies flat on Z=0", _near(bb.min.Z, 0, 0.01), f"{bb.min.Z:.3f}"))
        out.append(("pin and bore present", len(pins) >= 1 and len(bores) >= 1, f"pins {[round(p.diameter, 2) for p in pins]} bores {[round(h.diameter, 2) for h in bores]}"))
        hs = holes(parts.compound, diameter=4.5, tol=0.3, through=True)
        out.append(("four M4 mounting holes", len(hs) == 4, f"{len(hs)}"))
    return out


def t_knob(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("30 mm diameter", _near(max(s.X, s.Y), 30, 0.4), f"{s.X:.2f} x {s.Y:.2f}"))
    out.append(("15 mm tall", _near(s.Z, 15, 0.3), f"{s.Z:.2f}"))
    from eitri.measure import cylindrical_features
    cf = cylindrical_features(parts.compound, min_coverage=0.5)
    dshaft = [c for c in cf if c.kind == "hole" and 5.8 <= c.diameter <= 6.4]
    out.append(("6 mm shaft hole (D-shaft: partial cylinder)", len(dshaft) >= 1, f"{[round(c.diameter, 2) for c in dshaft]}"))
    bb = parts.bbox
    flat = bool(bd.Vector) and True
    # D flat: a planar face inside the bore region at x = 3-0.5 = 2.5 from the axis (within tolerance)
    c = bb.center()
    planar_inside = [f for f in parts.compound.faces() if f.geom_type == bd.GeomType.PLANE and
                     abs(abs(f.normal_at().Z) - 1) > 1e-3 and math.hypot(f.center().X - c.X, f.center().Y - c.Y) < 3.2]
    out.append(("D flat face in the bore", len(planar_inside) >= 1, f"{len(planar_inside)}"))
    faces = len(parts.compound.faces())
    out.append(("grip ridges/knurl (>= 24 faces)", faces >= 24, f"{faces} faces"))
    w = min_wall(parts.compound)
    out.append(("no wall under 1.5 mm", w is None or w.thickness >= 1.45, f"{w.thickness if w else None}"))
    return out


def t_bottle_cap(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("outer diameter 32-36 mm", 32 <= max(s.X, s.Y) <= 36, f"{s.X:.2f} x {s.Y:.2f}"))
    out.append(("height 15 mm", _near(s.Z, 15, 0.5), f"{s.Z:.2f}"))
    bb = parts.bbox
    c = bb.center()
    out.append(("open on one end (hollow cup)", (not point_inside(parts.compound, (c.X, c.Y, bb.min.Z + 2))) or
                (not point_inside(parts.compound, (c.X, c.Y, bb.max.Z - 2))), ""))
    from eitri.measure import cylindrical_features
    cf = cylindrical_features(parts.compound, min_coverage=0.5)
    inner = [x for x in cf if x.kind == "hole" and 27.5 <= x.diameter <= 30.5]
    out.append(("inner diameter ~28-30 mm (M28 thread)", len(inner) >= 1, f"{[round(x.diameter, 2) for x in inner]}"))
    faces = len(parts.compound.faces())
    out.append(("has thread geometry (>= 20 faces)", faces >= 20, f"{faces}"))
    return out


def t_heatsink(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("40 x 40 footprint", _near(s.X, 40, 0.2) and _near(s.Y, 40, 0.2), f"{s.X:.2f} x {s.Y:.2f}"))
    out.append(("20 mm tall", _near(s.Z, 20, 0.2), f"{s.Z:.2f}"))
    # 10 fins: count planar faces with normal ±X (or ±Y) above the base plate
    faces = parts.compound.faces()
    bb = parts.bbox
    def side_faces(axis_idx):
        n = 0
        for f in faces:
            if f.geom_type != bd.GeomType.PLANE:
                continue
            nv = tuple(f.normal_at())
            if abs(abs(nv[axis_idx]) - 1) < 1e-3 and f.center().Z > bb.min.Z + 4.5 and f.area > 40:
                n += 1
        return n
    nx, ny = side_faces(0), side_faces(1)
    out.append(("10 fins (20 fin side faces along one axis)", max(nx, ny) == 20, f"X-normal faces {nx}, Y-normal {ny}"))
    w = min_wall(parts.compound)
    out.append(("fin thickness 2 mm (±0.2)", w is not None and _near(w.thickness, 2, 0.2), f"{w.thickness if w else None}"))
    return out


def t_pulley(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("OD 50 mm", _near(max(s.X, s.Y), 50, 0.3), f"{s.X:.2f} x {s.Y:.2f}"))
    out.append(("width 12 mm", _near(s.Z, 12, 0.2), f"{s.Z:.2f}"))
    bore = holes(parts.compound, diameter=8, tol=0.15, through=True)
    out.append(("8 mm bore", len(bore) == 1, f"{len(bore)}"))
    # groove: a concave feature with diameter < 50 at mid height
    c = parts.bbox.center()
    out.append(("V/round groove at the rim (mid-height radius < 24)",
                not point_inside(parts.compound, (c.X + 24.3, c.Y, c.Z)), ""))
    out.append(("rim intact at edges (radius 25 at the faces)",
                point_inside(parts.compound, (c.X + 24.5, c.Y, parts.bbox.min.Z + 0.5)), ""))
    set_screw = holes(parts.compound, diameter=2.5, tol=0.3)
    out.append(("M3 set-screw tap hole (2.5) radial", len(set_screw) >= 1 and abs(set_screw[0].axis.Z) < 0.01 if set_screw else False, f"{len(set_screw)}"))
    return out


def t_tray(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("120 x 80 x 25 outer", _near(s.X, 120, 0.3) and _near(s.Y, 80, 0.3) and _near(s.Z, 25, 0.3), f"{s.X:.1f} x {s.Y:.1f} x {s.Z:.1f}"))
    w = min_wall(parts.compound)
    out.append(("1.6 mm walls (±0.2)", w is not None and _near(w.thickness, 1.6, 0.2), f"{w.thickness if w else None}"))
    bb = parts.bbox
    c = bb.center()
    # 3 compartments along X: dividers at x = -20 and +20 (120/3 = 40 each)
    d1 = point_inside(parts.compound, (c.X - 20, c.Y, bb.min.Z + 12))
    d2 = point_inside(parts.compound, (c.X + 20, c.Y, bb.min.Z + 12))
    empty = all(not point_inside(parts.compound, (c.X + dx, c.Y, bb.min.Z + 12)) for dx in (-40, 0, 40))
    out.append(("two dividers making 3 equal compartments", d1 and d2 and empty, f"dividers {d1},{d2}; compartments empty {empty}"))
    fr = fillet_radii(parts.compound)
    out.append(("rounded vertical corners", any(r >= 2 for r in fr), f"{fr}"))
    return out



def _pairwise_match(points: list[tuple[float, float]], ref: list[tuple[float, float]], tol: float) -> bool:
    """True if the set of pairwise distances of `points` matches that of `ref` (translation/rotation invariant)."""
    def dists(pts):
        return sorted(math.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(pts) for b in pts[i + 1:])
    if len(points) != len(ref):
        return False
    return all(abs(a - b) <= tol for a, b in zip(dists(points), dists(ref)))


def t_rpi_case(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("outer 92 x 63 (board + 1.5 clearance + 2 walls, ±0.6)", _near(s.X, 92, 0.6) and _near(s.Y, 63, 0.6), f"{s.X:.2f} x {s.Y:.2f}"))
    out.append(("total height 26.5-30 (25 inside + floor)", 26.4 <= s.Z <= 30.5, f"{s.Z:.2f}"))
    bb = parts.bbox
    c = bb.center()
    out.append(("open top, hollow inside", not point_inside(parts.compound, (c.X, c.Y, bb.max.Z - 2)), ""))
    ins = holes(parts.compound, diameter=3.6, tol=0.25)
    out.append(("four M2.5 insert holes (3.35-3.85)", len(ins) == 4, f"{len(ins)}: {[round(h.diameter, 2) for h in ins]}"))
    if len(ins) == 4:
        pts = [(h.center.X, h.center.Y) for h in ins]
        out.append(("insert holes on the Pi pattern (58 x 49)", _pairwise_match(pts, [(0, 0), (58, 0), (0, 49), (58, 49)], 0.3), f"{[(round(x, 1), round(y, 1)) for x, y in pts]}"))
        out.append(("standoffs 5 mm above the floor", all(bb.min.Z + 6 <= h.end.Z <= bb.min.Z + 9 for h in ins), f"tops at {[round(h.end.Z - bb.min.Z, 2) for h in ins]}"))
    w = min_wall(parts.compound)
    out.append(("nothing thinner than 1.4 mm", w is not None and w.thickness >= 1.4, f"{w.thickness if w else None}"))
    zm = (bb.min.Z + bb.max.Z) / 2
    wall_ok = point_inside(parts.compound, (bb.min.X + 1.0, c.Y, zm)) and not point_inside(parts.compound, (bb.min.X + 3.0, c.Y, zm)) \
        and point_inside(parts.compound, (c.X, bb.min.Y + 1.0, zm)) and not point_inside(parts.compound, (c.X, bb.min.Y + 3.0, zm))
    out.append(("side walls about 2 mm (material at 1 mm in, empty at 3 mm in)", wall_ok, ""))
    return out


def t_nema17_mount(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    m3 = holes(parts.compound, diameter=3.4, tol=0.25, through=True)
    out.append(("four M3 clearance holes", len(m3) == 4, f"{len(m3)}"))
    if len(m3) == 4:
        ax = m3[0].axis
        pts = []
        for h in m3:
            # project centres onto the plane perpendicular to the hole axis
            c = h.center
            u = ax.cross(bd.Vector(0, 0, 1)) if abs(ax.Z) < 0.9 else ax.cross(bd.Vector(1, 0, 0))
            u = u.normalized(); v = ax.cross(u).normalized()
            pts.append((c.dot(u), c.dot(v)))
        out.append(("holes on a 31 x 31 square", _pairwise_match(pts, [(0, 0), (31, 0), (0, 31), (31, 31)], 0.3), f"{[(round(x, 1), round(y, 1)) for x, y in pts]}"))
    pilot = holes(parts.compound, diameter=22.5, tol=0.6, through=True)
    out.append(("22.5 mm pilot hole", len(pilot) == 1, f"{len(pilot)}: {[round(h.diameter, 2) for h in pilot]}"))
    if len(m3) == 4 and len(pilot) == 1:
        cx = sum(h.center.X for h in m3) / 4; cy = sum(h.center.Y for h in m3) / 4; cz = sum(h.center.Z for h in m3) / 4
        pc = pilot[0].center
        d = math.sqrt(((pc.X - cx) ** 2 + (pc.Y - cy) ** 2 + (pc.Z - cz) ** 2) - (bd.Vector(pc.X - cx, pc.Y - cy, pc.Z - cz).dot(m3[0].axis)) ** 2)
        out.append(("pilot hole centred in the pattern", d < 0.3, f"offset {d:.2f}"))
    m4 = holes(parts.compound, diameter=4.5, tol=0.3, through=True)
    out.append(("two M4 base holes", len(m4) == 2, f"{len(m4)}"))
    if len(m3) == 4 and len(m4) == 2:
        out.append(("base holes perpendicular to motor holes", abs(m4[0].axis.dot(m3[0].axis)) < 0.05, ""))
    w = min_wall(parts.compound)
    out.append(("4 mm thick", w is not None and _near(w.thickness, 4, 0.2), f"{w.thickness if w else None}"))
    return out


def t_arduino_plate(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("plate at least 78 x 63, 4 thick", s.X >= 78 and s.Y >= 63 and _near(s.Z, 4, 0.1), f"{s.X:.1f} x {s.Y:.1f} x {s.Z:.2f}"))
    hs = holes(parts.compound, diameter=3.4, tol=0.25, through=True)
    out.append(("four M3 clearance holes", len(hs) == 4, f"{len(hs)}"))
    if len(hs) == 4:
        pts = [(h.center.X, h.center.Y) for h in hs]
        ref = [(14.0, 2.5), (15.3, 50.7), (66.1, 7.6), (66.1, 35.5)]
        out.append(("holes on the Uno pattern (pairwise distances ±0.3)", _pairwise_match(pts, ref, 0.3), f"{[(round(x, 1), round(y, 1)) for x, y in pts]}"))
    fr = fillet_radii(parts.compound)
    out.append(("rounded corners", len(fr) >= 1, f"{fr}"))
    return out


def t_name_tag(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("70 x 25 plate, 3 + 1 mm tall", _near(s.X, 70, 0.3) and _near(s.Y, 25, 0.3) and _near(s.Z, 4, 0.15), f"{s.X:.2f} x {s.Y:.2f} x {s.Z:.2f}"))
    hs = holes(parts.compound, diameter=4, tol=0.2, through=True)
    out.append(("4 mm keyring hole", len(hs) == 1, f"{len(hs)}"))
    if hs:
        out.append(("hole near the left end", hs[0].center.X < parts.bbox.min.X + 12, f"x={hs[0].center.X:.1f}"))
    faces = len(parts.compound.faces())
    out.append(("raised text present (>= 50 faces)", faces >= 50, f"{faces}"))
    plate_v = 70 * 25 * 3
    v = parts.compound.volume
    out.append(("text adds material on top (volume > plate - hole)", v > plate_v - math.pi * 4 * 3 + 20, f"{v:.0f}"))
    fr = fillet_radii(parts.compound)
    out.append(("rounded corners", len(fr) >= 1, f"{fr}"))
    return out


def t_hex_bolt(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = _sorted_size(parts)
    out.append(("length 35.3 (30 shank + 5.3 head, ±0.6)", _near(s[2], 35.3, 0.6), f"{s}"))
    out.append(("13 mm across flats (bbox 13.0 x 15.0 ±0.4)", _near(s[0], 13, 0.4) and _near(s[1], 15.01, 0.5), f"{s}"))
    faces = len(parts.compound.faces())
    out.append(("thread geometry present (>= 12 faces)", faces >= 12, f"{faces}"))
    v = parts.compound.volume
    plain = math.pi * 16 * 30 + (3 * math.sqrt(3) / 2) * (13 / math.sqrt(3)) ** 2 * 5.3
    out.append(("volume below a plain cylinder+head (thread cut)", 0.72 * plain < v < 0.985 * plain, f"{v:.0f} vs plain {plain:.0f}"))
    return out


def t_slotted_bracket(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("100 x 30 x 5", _near(s.X, 100, 0.2) and _near(s.Y, 30, 0.2) and _near(s.Z, 5, 0.1), f"{s.X:.1f} x {s.Y:.1f} x {s.Z:.2f}"))
    mid = holes(parts.compound, diameter=10, tol=0.2, through=True)
    out.append(("10 mm centre hole", len(mid) == 1 and abs(mid[0].center.X - parts.bbox.center().X) < 0.3, f"{len(mid)}"))
    from eitri.measure import cylindrical_features
    halves = [c for c in cylindrical_features(parts.compound, min_coverage=0.4) if c.kind == "hole" and _near(c.diameter, 6.5, 0.3) and c.coverage < 0.8]
    out.append(("two 6.5 mm slots (4 half-round ends)", len(halves) == 4, f"{len(halves)} ends: {[round(c.diameter, 2) for c in halves]}"))
    if len(halves) == 4:
        xs = sorted(c.center.X for c in halves)
        out.append(("slots 20 mm long (ends 13.5 apart)", _near(xs[1] - xs[0], 13.5, 0.3) and _near(xs[3] - xs[2], 13.5, 0.3), f"{[round(x, 1) for x in xs]}"))
        cx = parts.bbox.center().X
        out.append(("slots centred 30 mm from each end", _near((xs[0] + xs[1]) / 2, cx - 20, 0.4) and _near((xs[2] + xs[3]) / 2, cx + 20, 0.4), ""))
    return out


def t_bearing_housing(parts: Parts) -> list[Check]:
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("40 wide x 30 tall x 12 thick", _near(s.X, 40, 0.3) and _near(s.Z, 30, 0.3) and _near(s.Y, 12, 0.3), f"{s.X:.1f} x {s.Y:.1f} x {s.Z:.1f}"))
    pocket = holes(parts.compound, diameter=22.1, tol=0.2)
    out.append(("22.1 mm pocket, 7 deep, blind", len(pocket) == 1 and pocket[0].through is False and _near(pocket[0].depth, 7, 0.2), f"{[(round(h.diameter, 2), round(h.depth, 2), h.through) for h in pocket]}"))
    bore = holes(parts.compound, diameter=9, tol=0.2, through=True)
    out.append(("9 mm through hole", len(bore) == 1, f"{len(bore)}"))
    if pocket and bore:
        out.append(("pocket and bore coaxial", (pocket[0].center - bore[0].center).cross(bore[0].axis).length < 0.2, ""))
        out.append(("bearing centre 18 mm above the base", _near(bore[0].center.Z - parts.bbox.min.Z, 18, 0.3), f"{bore[0].center.Z - parts.bbox.min.Z:.2f}"))
    m4 = holes(parts.compound, diameter=4.5, tol=0.3, through=True)
    out.append(("two M4 mounting holes 30 apart", len(m4) == 2 and _near(abs(m4[0].center.X - m4[1].center.X), 30, 0.3), f"{len(m4)}"))
    return out


def t_gear_pair(parts: Parts) -> list[Check]:
    out = _valid_parts(parts, 2)
    pl = list(parts)
    if len(pl) == 2:
        small = min(pl, key=lambda p: p.shape.bounding_box().size.X)
        big = max(pl, key=lambda p: p.shape.bounding_box().size.X)
        ss, bs = small.shape.bounding_box().size, big.shape.bounding_box().size
        out.append(("ODs 25.5 and 48 (m1.5: 15t, 30t)", _near(ss.X, 25.5, 0.3) and _near(bs.X, 48, 0.3), f"{ss.X:.2f}, {bs.X:.2f}"))
        out.append(("6 mm thick, both on Z=0", _near(ss.Z, 6, 0.1) and _near(bs.Z, 6, 0.1) and _near(parts.bbox.min.Z, 0, 0.01), f"{ss.Z:.2f}, {bs.Z:.2f}, z0 {parts.bbox.min.Z:.2f}"))
        b1 = holes(small.shape, diameter=5, tol=0.15, through=True)
        b2 = holes(big.shape, diameter=5, tol=0.15, through=True)
        out.append(("5 mm bores in both", len(b1) == 1 and len(b2) == 1, f"{len(b1)}, {len(b2)}"))
        if b1 and b2:
            cd = math.hypot(b1[0].center.X - b2[0].center.X, b1[0].center.Y - b2[0].center.Y)
            out.append(("centre distance 33.75 (±0.15)", _near(cd, 33.75, 0.15), f"{cd:.3f}"))
        ov = overlap_volume(small.shape, big.shape)
        out.append(("teeth mesh without interference (< 0.5 mm^3)", ov < 0.5, f"{ov:.3f}"))
        out.append(("teeth engage (min distance < 0.3 mm)", min_distance(small.shape, big.shape) < 0.3, ""))
        f1, f2 = len(small.shape.faces()), len(big.shape.faces())
        out.append(("involute tooth geometry (>= 60 faces each)", f1 >= 60 and f2 >= 60, f"{f1}, {f2}"))
    return out



# ---------------------------------------------------------------------------- follow-ups (multi-turn)

def t_plate_followup(parts: Parts) -> list[Check]:
    """plate -> 'make the holes M5 counterbored for socket head screws and add a 20 mm centre hole'."""
    out = _single_valid_solid(parts)
    s = parts.size
    out.append(("still 60 x 40 x 5", _near(s.X, 60, 0.05) and _near(s.Y, 40, 0.05) and _near(s.Z, 5, 0.05), f"{s.X:.2f} x {s.Y:.2f} x {s.Z:.2f}"))
    m5 = holes(parts.compound, diameter=5.5, tol=0.25, through=True)
    out.append(("four M5 clearance holes (5.3-5.8)", len(m5) == 4, f"{len(m5)}: {[round(h.diameter, 2) for h in m5]}"))
    cb = holes(parts.compound, diameter=10, tol=0.6, through=False)
    out.append(("four counterbores ~10 mm, blind", len(cb) == 4, f"{len(cb)}: {[(round(h.diameter, 2), round(h.depth, 2)) for h in cb]}"))
    centre = holes(parts.compound, diameter=20, tol=0.2, through=True)
    out.append(("20 mm centre hole", len(centre) == 1 and abs(centre[0].center.X - parts.bbox.center().X) < 0.2, f"{len(centre)}"))
    return out


def t_enclosure_followup(parts: Parts) -> list[Check]:
    """enclosure -> 'add a 12 mm round hole for a cable on one short end wall, 10 mm above the floor, and emboss MODEL A on the lid'."""
    out = _valid_parts(parts, 2)
    out.append(_no_interference(parts))
    if len(parts) == 2:
        base = max(parts, key=lambda p: p.shape.volume)
        lid = min(parts, key=lambda p: p.shape.volume)
        cable = [h for h in holes(base.shape, diameter=12, tol=0.3, through=True) if abs(h.axis.Z) < 0.1]
        out.append(("12 mm cable hole through a wall (horizontal axis)", len(cable) == 1, f"{len(cable)}"))
        if cable:
            bb = base.shape.bounding_box()
            out.append(("cable hole 10 mm above the floor (±1.5)", _near(cable[0].center.Z - bb.min.Z, 10 + 2, 1.5) or _near(cable[0].center.Z - bb.min.Z, 10, 1.5), f"{cable[0].center.Z - bb.min.Z:.2f}"))
            out.append(("cable hole on a short (60 mm) end wall: axis along X", abs(cable[0].axis.X) > 0.99, f"axis {tuple(round(c, 2) for c in cable[0].axis)}"))
        lf = len(lid.shape.faces())
        out.append(("lid has embossed text (>= 60 faces)", lf >= 60, f"{lf}"))
        out.append(("insert holes still there", len(holes(base.shape, diameter=4.0, tol=0.25)) == 4, ""))
    return out


TASKS: list[Task] = [
    Task("plate", "Make a 60 x 40 x 5 mm mounting plate with four M4 clearance holes 8 mm in from each edge and 5 mm rounded corners.", t_plate, ["basic"], 1,
         followup="Change the four holes to M5 clearance holes counterbored for socket head cap screws, and add a 20 mm hole in the centre of the plate.", followup_check=t_plate_followup),
    Task("l_bracket", "Design an L-bracket: two 50 x 50 mm legs at 90 degrees, 4 mm thick, 30 mm wide, with two 5.5 mm through holes in each leg and a 3 mm inside fillet for strength. Mount with M5 screws.", t_l_bracket, ["basic", "fillet"], 1),
    Task("enclosure", "Design a two-part electronics enclosure, 100 x 60 x 40 mm outside when assembled, 2 mm walls, with a removable lid that locates on the base and is held by four M3 screws into heat-set inserts in corner bosses. Rounded corners. Give me base and lid as separate parts in their assembled position.", t_enclosure, ["assembly", "enclosure"], 2,
         followup="Add a 12 mm round cable hole through one of the short end walls of the base, centred 10 mm above the inside floor, and emboss the text MODEL A 0.6 mm high on top of the lid.", followup_check=t_enclosure_followup),
    Task("flange", "Make a pipe flange: 80 mm outer diameter, 25 mm total height, with a 40 mm diameter hub, a 20 mm through bore, and six 6.6 mm bolt holes on a 60 mm bolt circle.", t_flange, ["revolve", "pattern"], 1),
    Task("gear", "Make a spur gear: module 2, 20 teeth, 20 degree pressure angle, 8 mm thick, with an 8 mm bore.", t_gear, ["gear"], 2),
    Task("shaft", "Make a stepped shaft 80 mm long: 20 mm diameter for the middle 30 mm, stepping down to 16 mm for 30 mm on one end and 12 mm for 20 mm on the other. Add a 5 mm wide x 3 mm deep keyway on the 16 mm section and 1 mm chamfers on both ends.", t_shaft, ["shaft"], 2),
    Task("phone_stand", "Design a desk phone stand for a 75 mm wide, 9 mm thick phone, holding it at about 65 degrees, printable in PLA without supports, at least 2 mm walls, with a cable slot at the bottom.", t_phone_stand, ["freeform"], 2),
    Task("cable_clip", "Make a small screw-mount cable clip for a 6 mm round cable: a C-shaped clip with 1.6 mm walls, a base with one M3 clearance hole, and a snap opening so the cable pushes in. Fit in 30 mm.", t_cable_clip, ["freeform"], 2),
    Task("hinge", "Make a print-in-place hinge, 40 mm long along the pin, two leaves each with two M4 mounting holes, with 0.4 mm clearance between the pin and knuckles so it prints as one piece and moves after printing. Model it flat on the bed, as two parts.", t_hinge, ["assembly", "print-in-place"], 3),
    Task("knob", "Make a control knob 30 mm diameter, 15 mm tall, for a 6 mm D-shaft (flat 0.5 mm deep), with grip ridges around the outside and a pointer mark on top.", t_knob, ["freeform"], 2),
    Task("bottle_cap", "Make a screw-on bottle cap for a standard 28 mm PCO 1881 soda bottle neck (internal thread ~ 27.4 mm major diameter, pitch 2.7 mm), 15 mm tall, about 34 mm outside, with grip ridges.", t_bottle_cap, ["thread"], 3),
    Task("heatsink", "Make a heatsink: 40 x 40 mm base, 4 mm thick, with 10 straight fins 2 mm thick evenly spaced across the base, total height 20 mm.", t_heatsink, ["pattern"], 1),
    Task("pulley", "Make a V-belt pulley: 50 mm outer diameter, 12 mm wide, an 8 mm bore, a V groove for a 6 mm belt, and a radial M3 set-screw tap hole (2.5 mm) into the bore.", t_pulley, ["revolve"], 2),
    Task("tray", "Make an open storage tray 120 x 80 x 25 mm outside with 1.6 mm walls and floor, rounded vertical corners, divided into 3 equal compartments along the long axis by two dividers.", t_tray, ["enclosure"], 1),
    # --- harder: real interfaces, standard parts, text, threads ---
    Task("rpi_case", "Design a bottom case for a Raspberry Pi 4 Model B. The board is 85 x 56 mm with four 2.7 mm mounting holes at (3.5, 3.5), (61.5, 3.5), (3.5, 52.5) and (61.5, 52.5) mm from the board's corner. The case needs 2 mm walls and floor, 1.5 mm clearance around the board, four standoffs 5 mm tall with M2.5 heat-set insert holes under the mounting holes, an open top and 25 mm inside height. One printable part.", t_rpi_case, ["enclosure", "interface"], 2),
    Task("nema17_mount", "Make an L-shaped NEMA 17 stepper motor mounting bracket, 4 mm thick: a vertical motor plate 44 x 44 mm with the NEMA 17 pattern (four M3 clearance holes on a 31 x 31 mm square and a 22.5 mm centre hole for the pilot boss), and a horizontal base leg 44 x 40 mm with two M4 clearance holes for mounting it down. One part.", t_nema17_mount, ["interface"], 2),
    Task("arduino_plate", "Make a 4 mm thick mounting plate for an Arduino Uno with rounded corners, at least 10 mm larger than the 68.6 x 53.3 mm board in each direction, with M3 clearance holes at the Uno's mounting positions (14.0, 2.5), (15.3, 50.7), (66.1, 7.6) and (66.1, 35.5) mm measured from the board's lower-left corner, board centred on the plate.", t_arduino_plate, ["interface"], 2),
    Task("name_tag", "Make a name tag: a 70 x 25 x 3 mm plate with rounded corners, the text CADCODE raised 1 mm on top in about 8 mm tall letters, and a 4 mm keyring hole near the left end.", t_name_tag, ["text"], 1),
    Task("hex_bolt", "Model an M8 x 30 hex bolt: ISO 4017 hex head 13 mm across flats and 5.3 mm tall, with a real M8 thread (1.25 mm pitch) modelled along the full 30 mm shank.", t_hex_bolt, ["thread", "standard-part"], 2),
    Task("slotted_bracket", "Make a flat mounting bracket 100 x 30 x 5 mm with two adjustment slots 20 mm long and 6.5 mm wide (for M6), each centred 30 mm from its end along the long axis, and a 10 mm hole in the middle.", t_slotted_bracket, ["slots"], 1),
    Task("bearing_housing", "Make a bearing housing block for a 608 bearing (8 x 22 x 7 mm): a 40 mm wide, 30 mm tall, 12 mm thick block with a 22.1 mm diameter pocket 7 mm deep for the bearing from one face, a 9 mm through hole for the shaft, the bearing centre 18 mm above the bottom, and two M4 clearance holes through the thickness, 30 mm apart, near the bottom corners for mounting.", t_bearing_housing, ["interface", "bearing"], 2),
    Task("gear_pair", "Make a pair of meshing spur gears as two separate parts placed in their correct meshed position: module 1.5, 15 teeth and 30 teeth, both 6 mm thick with 5 mm bores, lying flat on Z=0 side by side.", t_gear_pair, ["gear", "assembly"], 3),
]


def get(name: str) -> Task:
    for t in TASKS:
        if t.name == name:
            return t
    raise KeyError(name)
