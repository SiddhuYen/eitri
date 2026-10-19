"""Common mechanical features as verified helpers.

Conventions: cutters are built along Z with their reference face at Z=0 and the
cut going DOWN (-Z); place them with `Pos(x, y, z_top) * cutter` or use the
`cut_holes` convenience. Bosses, standoffs, ribs and gussets stand on Z=0.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import build123d as bd
from build123d import (Align, Axis, Box, Cylinder, Cone, GeomType, Location, Plane, Pos, RegularPolygon, Rot,
                       SlotOverall, Text, extrude, fillet, chamfer, mirror)

from .fasteners import counterbore_dims, countersink_dims, heat_set_insert_dims, nut_dims, screw_clearance, tap_drill

__all__ = [
    "hole_cutter", "screw_hole_cutter", "cut_holes", "boss", "standoff", "insert_boss", "nut_pocket_cutter",
    "slot_cutter", "gusset", "rib", "try_fillet", "round_vertical_edges", "chamfer_top_edges", "emboss_text",
    "deboss_text", "pattern_polar", "pattern_grid", "pattern_linear", "hex_prism", "rounded_box", "shell_box",
    "tube", "d_shaft", "keyed_shaft", "countersink_cutter", "chamfer_edges_at", "polygon_prism",
]

_BOTTOM = (Align.CENTER, Align.CENTER, Align.MIN)
_TOP = (Align.CENTER, Align.CENTER, Align.MAX)


def hole_cutter(d: float, depth: float = 200.0, counterbore: tuple[float, float] | None = None,
                countersink: tuple[float, float] | None = None, extra: float = 1.0) -> bd.Solid:
    """A cylindrical cutter for a hole of diameter d, starting at Z=0 and going down `depth`.

    counterbore=(diameter, depth) adds a counterbore at the top; countersink=(top
    diameter, included angle) adds a conical countersink. `extra` extends the cutter
    above Z=0 so it cuts cleanly through the entry face. Subtract it:
    `body - Pos(x, y, z_top) * hole_cutter(3.4)`.
    """
    cutter = Pos(0, 0, extra) * Cylinder(d / 2, depth + extra, align=_TOP)
    if counterbore:
        cb_d, cb_h = counterbore
        cutter = cutter + Pos(0, 0, extra) * Cylinder(cb_d / 2, cb_h + extra, align=_TOP)
    if countersink:
        cs_d, angle = countersink
        cs_h = (cs_d - d) / 2 / math.tan(math.radians(angle / 2))
        cone = Cone(cs_d / 2, d / 2, cs_h, align=_TOP)
        top_ext = Cylinder(cs_d / 2, extra, align=_BOTTOM)
        cutter = cutter + cone + top_ext
    return cutter


def countersink_cutter(d: float, size: str | None = None, depth: float = 200.0) -> bd.Solid:
    """Cutter for a countersunk (flat head) screw: pass the screw `size` ("M3") or a through diameter d."""
    if size:
        d = screw_clearance(size)
        cs = countersink_dims(size)
    else:
        cs = (d * 2.0, 90.0)
    return hole_cutter(d, depth, countersink=cs)


def screw_hole_cutter(size: str, depth: float = 200.0, head: str | None = None, fit: str = "normal",
                      tapped: bool = False) -> bd.Solid:
    """Cutter for a metric screw: clearance hole (or tap-drill hole if tapped=True),
    with head="counterbore" (socket head cap) or head="countersink" (flat head)."""
    d = tap_drill(size) if tapped else screw_clearance(size, fit)
    if head == "counterbore":
        return hole_cutter(d, depth, counterbore=counterbore_dims(size))
    if head == "countersink":
        return hole_cutter(d, depth, countersink=countersink_dims(size))
    if head not in (None, "", "none"):
        raise ValueError("head must be None, 'counterbore' or 'countersink'")
    return hole_cutter(d, depth)


def cut_holes(body: bd.Shape, points: Iterable[Sequence[float]], d: float | None = None, size: str | None = None,
              head: str | None = None, depth: float | None = None, plane: Plane | None = None,
              fit: str = "normal", tapped: bool = False, min_floor: float = 1.5) -> bd.Shape:
    """Cut holes into `body` at (x, y) points on its top face (or on `plane`).

    Give either a diameter `d` or a screw `size` ("M3", with head/fit/tapped as in
    screw_hole_cutter). Holes go through by default; `depth` makes them blind.
    With `plane`, points are in that plane's coordinates and holes go into the body
    along the plane's -normal (so `Plane(face)` drills into a face). A counterbore
    is made shallower when the body is too thin for the full head depth, so at
    least `min_floor` mm of material stays under the screw head (the head then
    stands proud: consider a thicker part).
    """
    bb = body.bounding_box()
    thickness = (bb.max.Z - bb.min.Z) if plane is None else None
    if size and head == "counterbore":
        dia = tap_drill(size) if tapped else screw_clearance(size, fit)
        cb_d, cb_h = counterbore_dims(size)
        if thickness is not None and cb_h > thickness - min_floor:
            cb_h = max(thickness - min_floor, 0.5)     # keep material under the head; say so in the report
        cutter = hole_cutter(dia, depth or 500.0, counterbore=(cb_d, cb_h))
    elif size:
        cutter = screw_hole_cutter(size, depth or 500.0, head, fit, tapped)
    elif d:
        cutter = hole_cutter(d, depth or 500.0)
    else:
        raise ValueError("give d or size")
    if plane is None:
        tools = [Pos(x, y, bb.max.Z) * cutter for x, y in points]
    else:
        tools = [plane * Pos(x, y, 0) * cutter for x, y in points]
    return body - tools


def boss(d: float, height: float, hole_d: float | None = None, hole_depth: float | None = None,
         fillet_base: float = 0.0) -> bd.Shape:
    """A cylindrical boss standing on Z=0 with an optional hole from the top."""
    b = Cylinder(d / 2, height, align=_BOTTOM)
    if hole_d:
        b = b - Pos(0, 0, height) * hole_cutter(hole_d, hole_depth if hole_depth else height + 2)
    return b


def standoff(height: float, size: str = "M3", od: float | None = None, kind: str = "insert",
             hole_depth: float | None = None) -> bd.Shape:
    """A screw standoff on Z=0. kind: "insert" (heat-set insert hole), "tap" (tap-drill
    hole to thread-form the screw into plastic), "clearance" (through clearance hole)."""
    if kind == "insert":
        hd, hdepth = heat_set_insert_dims(size)
        od = od or hd + 2 * max(1.6, hd * 0.35)
    elif kind == "tap":
        hd, hdepth = tap_drill(size), min(height - 1, 4 * screw_clearance(size))
        od = od or hd + 3.2
    elif kind == "clearance":
        hd, hdepth = screw_clearance(size), height + 2
        od = od or hd + 3.2
    else:
        raise ValueError("kind must be insert, tap or clearance")
    return boss(od, height, hd, hole_depth or hdepth)


def insert_boss(size: str, height: float, od: float | None = None) -> bd.Shape:
    """A boss sized for a brass heat-set insert of the given screw size, standing on Z=0."""
    return standoff(height, size, od, kind="insert")


def nut_pocket_cutter(size: str, depth: float, clearance: float = 0.2, extra: float = 1.0) -> bd.Solid:
    """Hexagonal cutter for a captive nut, top at Z=0 going down `depth` (plus the
    screw clearance hole continuing through). across-flats grows by `clearance`."""
    af, _ = nut_dims(size)
    af += clearance
    r = af / 2 / math.cos(math.radians(30))   # circumradius from across-flats
    hexagon = Pos(0, 0, extra) * extrude(RegularPolygon(r, 6), depth + extra, dir=(0, 0, -1))
    return hexagon + hole_cutter(screw_clearance(size), 500.0, extra=extra)


def slot_cutter(length: float, width: float, depth: float = 200.0, extra: float = 1.0) -> bd.Solid:
    """A stadium-shaped slot cutter along X, `length` overall, top at Z=0 going down."""
    return Pos(0, 0, extra) * extrude(SlotOverall(length, width), depth + extra, dir=(0, 0, -1))


def gusset(length: float, height: float, thickness: float) -> bd.Solid:
    """A right-triangle gusset in the XZ plane standing on Z=0: `length` along +X at the
    base, `height` up +Z at x=0, `thickness` centred on Y. Place it against a wall at x=0."""
    tri = bd.Polygon((0, 0), (length, 0), (0, height), align=None)
    solid = extrude(Plane.XZ * tri, thickness / 2, both=True)
    return solid


def rib(length: float, height: float, thickness: float) -> bd.Solid:
    """A straight rib along X, centred on Y, standing on Z=0 (length x thickness x height)."""
    return Box(length, thickness, height, align=(Align.CENTER, Align.CENTER, Align.MIN))


def try_fillet(body: bd.Shape, edges, radius: float, fallbacks: Sequence[float] = ()) -> tuple[bd.Shape, float | None]:
    """Fillet `edges` with `radius`; on kernel failure try each radius in `fallbacks`.

    Returns (shape, radius_applied) where radius_applied is None if every attempt
    failed (the body is returned unchanged). Report a fallback to the user.
    """
    edges = list(edges)
    if not edges:
        return body, None
    for r in (radius, *fallbacks):
        try:
            out = fillet(edges, r)
            if out.is_valid and len(out.solids()) == len(body.solids()):
                return out, r
        except Exception:
            continue
    return body, None


def round_vertical_edges(body: bd.Shape, radius: float) -> bd.Shape:
    """Fillet every edge parallel to Z (the typical 'rounded corners' of a plate or box)."""
    return fillet(body.edges().filter_by(Axis.Z), radius)


def chamfer_top_edges(body: bd.Shape, size: float) -> bd.Shape:
    """Chamfer the edges of the topmost face."""
    top = body.faces().sort_by(Axis.Z)[-1]
    return chamfer(top.edges(), size)


def chamfer_edges_at(body: bd.Shape, axis: Axis, value: float, size: float, tol: float = 1e-3) -> bd.Shape:
    """Chamfer all straight/circular edges lying in the plane axis=value (e.g. Axis.Z, 0 for the bottom outline)."""
    i = {Axis.X: 0, Axis.Y: 1, Axis.Z: 2}[axis]
    sel = [e for e in body.edges() if abs(tuple(e.bounding_box().min)[i] - value) < tol
           and abs(tuple(e.bounding_box().max)[i] - value) < tol]
    return chamfer(sel, size) if sel else body


def _text_solid(text: str, size: float, depth: float, font: str, bold: bool) -> bd.Shape:
    sk = Text(text, font_size=size, font=font, font_style=bd.FontStyle.BOLD if bold else bd.FontStyle.REGULAR)
    return extrude(sk, depth)


def emboss_text(body: bd.Shape, text: str, size: float = 6.0, height: float = 0.8,
                at: Sequence[float] = (0.0, 0.0), plane: Plane | None = None, font: str = "Arial",
                bold: bool = True, rotation: float = 0.0) -> bd.Shape:
    """Raise `text` by `height` on the top face of `body` (or on `plane`, text in plane coords)."""
    solid = _text_solid(text, size, height, font, bold)
    if plane is None:
        z_top = body.bounding_box().max.Z
        placed = Pos(at[0], at[1], z_top) * Rot(0, 0, rotation) * solid
    else:
        placed = plane * Pos(at[0], at[1], 0) * Rot(0, 0, rotation) * solid
    return body + placed


def deboss_text(body: bd.Shape, text: str, size: float = 6.0, depth: float = 0.6,
                at: Sequence[float] = (0.0, 0.0), plane: Plane | None = None, font: str = "Arial",
                bold: bool = True, rotation: float = 0.0) -> bd.Shape:
    """Engrave `text` `depth` deep into the top face of `body` (or into `plane`)."""
    solid = _text_solid(text, size, depth + 0.5, font, bold)
    if plane is None:
        z_top = body.bounding_box().max.Z
        placed = Pos(at[0], at[1], z_top - depth) * Rot(0, 0, rotation) * solid
    else:
        placed = plane * Pos(at[0], at[1], -depth) * Rot(0, 0, rotation) * solid
    return body - placed


def pattern_polar(shape: bd.Shape, count: int, radius: float = 0.0, start_angle: float = 0.0,
                  rotate: bool = True, axis: Axis = Axis.Z) -> list[bd.Shape]:
    """`count` copies of `shape` around `axis` on a circle of `radius` (0 = rotate in place)."""
    out = []
    for i in range(count):
        a = start_angle + 360.0 * i / count
        s = shape.rotate(axis, a) if rotate else shape
        if radius:
            s = Pos(radius * math.cos(math.radians(a)), radius * math.sin(math.radians(a)), 0) * \
                (shape.rotate(axis, a) if rotate else shape)
        out.append(s)
    return out


def pattern_grid(shape: bd.Shape, nx: int, ny: int, dx: float, dy: float, centered: bool = True) -> list[bd.Shape]:
    """nx x ny copies of `shape` on a grid with pitches dx, dy (centred on the origin by default)."""
    out = []
    x0 = -(nx - 1) * dx / 2 if centered else 0.0
    y0 = -(ny - 1) * dy / 2 if centered else 0.0
    for i in range(nx):
        for j in range(ny):
            out.append(Pos(x0 + i * dx, y0 + j * dy, 0) * shape)
    return out


def pattern_linear(shape: bd.Shape, count: int, dx: float, dy: float = 0.0, dz: float = 0.0) -> list[bd.Shape]:
    """`count` copies of `shape` stepped by (dx, dy, dz), the first one in place."""
    return [Pos(i * dx, i * dy, i * dz) * shape for i in range(count)]


def hex_prism(across_flats: float, height: float) -> bd.Solid:
    """A hexagonal prism (nut/bolt-head shape) standing on Z=0, flats parallel to Y."""
    r = across_flats / 2 / math.cos(math.radians(30))
    return extrude(RegularPolygon(r, 6), height)


def polygon_prism(points: Sequence[Sequence[float]], height: float) -> bd.Solid:
    """Extrude a closed polygon (points in order, absolute XY) up +Z by `height`."""
    return extrude(bd.Polygon(*[tuple(p) for p in points], align=None), height)


def rounded_box(length: float, width: float, height: float, radius: float, top_radius: float = 0.0) -> bd.Shape:
    """A box on Z=0 with vertical corners rounded by `radius` (and optionally the top edges by top_radius)."""
    b = extrude(bd.RectangleRounded(length, width, radius), height)
    if top_radius:
        b = fillet(b.faces().sort_by(Axis.Z)[-1].edges(), top_radius)
    return b


def shell_box(length: float, width: float, height: float, wall: float, floor: float | None = None,
              corner_radius: float = 0.0, open_top: bool = True) -> bd.Shape:
    """An open-top box (tray) on Z=0: outer length x width x height, `wall` thick sides,
    `floor` thick bottom (defaults to wall), vertical corners rounded by corner_radius."""
    floor = wall if floor is None else floor
    outer = rounded_box(length, width, height, corner_radius) if corner_radius else \
        Box(length, width, height, align=_BOTTOM)
    ir = max(corner_radius - wall, 0.0)
    cav_h = height - floor + (1.0 if open_top else 0.0)
    inner = extrude(bd.RectangleRounded(length - 2 * wall, width - 2 * wall, ir), cav_h) if ir > 0 else \
        Box(length - 2 * wall, width - 2 * wall, cav_h, align=_BOTTOM)
    inner = Pos(0, 0, floor) * inner
    if not open_top:
        inner = Pos(0, 0, floor) * (Box(length - 2 * wall, width - 2 * wall, height - 2 * floor, align=_BOTTOM)
                                    if ir <= 0 else extrude(bd.RectangleRounded(length - 2 * wall, width - 2 * wall, ir), height - 2 * floor))
    return outer - inner


def tube(od: float, id: float, length: float) -> bd.Shape:
    """A tube along Z standing on Z=0."""
    return Cylinder(od / 2, length, align=_BOTTOM) - Cylinder(id / 2, length + 2, align=_BOTTOM).moved(Location((0, 0, -1)))


def d_shaft(d: float, length: float, flat_depth: float, flat_length: float | None = None) -> bd.Shape:
    """A D-shaft along Z (base on Z=0) with a flat of `flat_depth` on the +X side over flat_length (default full)."""
    shaft = Cylinder(d / 2, length, align=_BOTTOM)
    fl = length + 2 if flat_length is None else flat_length
    z0 = -1 if flat_length is None else length - flat_length
    cut = Pos(d / 2 - flat_depth, 0, z0) * Box(flat_depth + 5, d + 2, fl, align=(Align.MIN, Align.CENTER, Align.MIN))
    return shaft - cut


def keyed_shaft(d: float, length: float, key_width: float, key_depth: float, key_length: float | None = None) -> bd.Shape:
    """A shaft along Z (base on Z=0) with a keyway on the +X side (width, depth, optional length)."""
    shaft = Cylinder(d / 2, length, align=_BOTTOM)
    kl = length + 2 if key_length is None else key_length
    z0 = -1 if key_length is None else length - key_length
    cut = Pos(d / 2 - key_depth, 0, z0) * Box(key_depth + 5, key_width, kl, align=(Align.MIN, Align.CENTER, Align.MIN))
    return shaft - cut
