"""Additive-manufacturing preparation: print orientation (overhang area and support volume per candidate
orientation), a support estimate, and arranging several parts on a printer bed."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import build123d as bd
from build123d import Vector

ORIENTATIONS = {              # name -> rotation (axis, degrees) that brings that side down onto the bed
    "as is": None, "upside down": ((1, 0, 0), 180), "on front": ((1, 0, 0), -90), "on back": ((1, 0, 0), 90),
    "on left": ((0, 1, 0), 90), "on right": ((0, 1, 0), -90),
}


@dataclass
class Orientation:
    name: str
    overhang_area: float          # mm^2 of faces facing down steeper than the critical angle (needs support)
    support_volume: float         # mm^3 estimate: overhang area x height above the bed
    height: float                 # build height
    footprint: float              # mm^2 of the bed footprint (bbox)
    bed_contact: float            # mm^2 of faces lying flat on the bed (adhesion)
    score: float = 0.0
    rotation: tuple | None = None


def _rotated(shape: bd.Shape, rot) -> bd.Shape:
    if not rot:
        return shape
    axis, deg = rot
    s = shape.rotate(bd.Axis((0, 0, 0), axis), deg)
    bb = s.bounding_box()
    return s.moved(bd.Location((0, 0, -bb.min.Z)))     # sit on the bed


def _rotation_matrix(rot):
    import numpy as np
    if not rot:
        return np.eye(3)
    axis, deg = rot
    k = np.asarray(axis, dtype=float)
    k /= np.linalg.norm(k)
    a = math.radians(deg)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * (K @ K)       # Rodrigues: the same turn as Shape.rotate


def _triangles(shape: bd.Shape):
    """The shape's surface as triangles (N, 3, 3), meshed finely enough to see curved overhangs."""
    import numpy as np
    from . import fastmesh
    from .measure import bounding_box
    diag = max(bounding_box(shape).diagonal, 1e-6)
    fastmesh.mesh_shape(shape, max(diag * 0.003, 0.01), 0.3)
    return fastmesh.all_triangles(shape).astype(np.float64)


def analyze_orientation(shape: bd.Shape, name: str = "as is", rotation=None, critical_deg: float = 45.0,
                        triangles=None) -> Orientation:
    """Score one orientation from the surface triangles: every bit of surface facing down more steeply than
    `critical_deg` from vertical counts as overhang (curved surfaces included), weighted by its height for
    the support estimate; surface lying on the bed counts as contact."""
    import numpy as np
    tri = _triangles(shape) if triangles is None else triangles
    if not len(tri):
        return Orientation(name, 0.0, 0.0, 0.0, 0.0, 0.0, rotation=rotation)
    t = tri @ _rotation_matrix(rotation).T
    t[..., 2] -= t[..., 2].min()
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    two_area = np.linalg.norm(n, axis=1)
    keep = two_area > 1e-12
    t, n, area = t[keep], n[keep] / two_area[keep, None], two_area[keep] / 2
    nz = n[:, 2]
    zmax = t[..., 2].max(axis=1)
    cz = t[..., 2].mean(axis=1)
    on_bed = (nz < -0.999) & (zmax < 1e-3)
    down = (nz < -math.cos(math.radians(critical_deg))) & ~on_bed & (cz > 1e-3)
    proj = area * np.minimum(1.0, -nz)                     # projected onto the bed
    overhang = float(proj[down].sum())
    support = float((proj * cz)[down].sum())
    contact = float(area[on_bed].sum())
    lo, hi = t.reshape(-1, 3).min(axis=0), t.reshape(-1, 3).max(axis=0)
    size = hi - lo
    return Orientation(name, round(overhang, 2), round(support, 2), round(float(size[2]), 3), round(float(size[0] * size[1]), 2),
                       round(contact, 2), rotation=rotation)


def best_orientation(shape: bd.Shape, critical_deg: float = 45.0) -> tuple[Orientation, list[Orientation]]:
    """Score the six axis-aligned orientations: least support first, then lower height, then more bed
    contact; a slender tower (height over 3x the side of its footprint) is penalised, because it wobbles
    and fails on most printers. Returns (best, all)."""
    tri = _triangles(shape)
    out = []
    for name, rot in ORIENTATIONS.items():
        o = analyze_orientation(shape, name, rot, critical_deg, triangles=tri)
        slender = o.height / max(math.sqrt(max(o.footprint, 1e-9)), 1e-6)
        tower = 600.0 * max(0.0, slender - 3.0) ** 2
        o.score = round(o.support_volume + 20.0 * o.overhang_area + 5.0 * o.height - 0.5 * o.bed_contact + tower, 2)
        out.append(o)
    out.sort(key=lambda o: o.score)
    return out[0], out


def oriented(shape: bd.Shape, orientation: Orientation) -> bd.Shape:
    s = _rotated(shape, orientation.rotation)
    bb = s.bounding_box()
    return s.moved(bd.Location((-bb.center().X, -bb.center().Y, -bb.min.Z)))


# ---------------------------------------------------------------------------- bed arrangement

@dataclass
class Placement:
    name: str
    x: float
    y: float
    w: float
    d: float
    h: float
    orientation: str


def arrange(parts, bed: tuple[float, float] = (220.0, 220.0), spacing: float = 8.0, orient: bool = True,
            critical_deg: float = 45.0) -> tuple[list, list[Placement], list[str]]:
    """Lay the parts out on the bed (shelf packing, largest first), each in its best orientation when
    `orient` is set. Returns (placed shapes as (name, shape), placements, parts that did not fit)."""
    items = []
    for p in parts:
        shape = p.shape
        oname = "as is"
        if orient:
            best, _ = best_orientation(shape, critical_deg)
            shape = oriented(shape, best)
            oname = best.name
        else:
            bb = shape.bounding_box()
            shape = shape.moved(bd.Location((-bb.center().X, -bb.center().Y, -bb.min.Z)))
        bb = shape.bounding_box()
        items.append((p.name, shape, bb.size.X, bb.size.Y, bb.size.Z, oname))
    items.sort(key=lambda it: it[2] * it[3], reverse=True)
    bw, bd_ = bed
    x = y = row_h = 0.0
    placed, placements, skipped = [], [], []
    for name, shape, w, d, h, oname in items:
        if w > bw or d > bd_:
            skipped.append(name)
            continue
        if x + w > bw + 1e-9:                                # next row
            x = 0.0; y += row_h + spacing; row_h = 0.0
        if y + d > bd_ + 1e-9:
            skipped.append(name)
            continue
        cx, cy = x + w / 2 - bw / 2, y + d / 2 - bd_ / 2    # bed centred on the origin
        placed.append((name, shape.moved(bd.Location((cx, cy, 0)))))
        placements.append(Placement(name, round(cx, 3), round(cy, 3), round(w, 3), round(d, 3), round(h, 3), oname))
        x += w + spacing; row_h = max(row_h, d)
    return placed, placements, skipped


def print_report(parts, bed: tuple[float, float] = (220.0, 220.0), critical_deg: float = 45.0, layer: float = 0.2) -> str:
    lines = []
    for p in parts:
        best, allo = best_orientation(p.shape, critical_deg)
        lines.append(f"{p.name}: best orientation '{best.name}' - overhang {best.overhang_area:g} mm^2, support ~{best.support_volume / 1000:.2f} cm^3, "
                     f"height {best.height:g} mm ({math.ceil(best.height / layer)} layers at {layer} mm), bed contact {best.bed_contact:g} mm^2")
        others = [o for o in allo if o is not best][:2]
        if others:
            lines.append("   next: " + "; ".join(f"'{o.name}' support ~{o.support_volume / 1000:.2f} cm^3, height {o.height:g}" for o in others))
    placed, placements, skipped = arrange(parts, bed, orient=True, critical_deg=critical_deg)
    lines.append(f"bed {bed[0]:g} x {bed[1]:g} mm: {len(placements)} part(s) placed" + (f", did not fit: {', '.join(skipped)}" if skipped else ""))
    for pl in placements:
        lines.append(f"   {pl.name} at ({pl.x:g}, {pl.y:g}), {pl.w:g} x {pl.d:g} x {pl.h:g} mm, {pl.orientation}")
    return "\n".join(lines)
