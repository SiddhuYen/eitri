"""Two-part enclosures (base + lid) with lips, corner bosses and screws."""
from __future__ import annotations

from typing import Sequence

import build123d as bd
from build123d import Align, Axis, Box, Cylinder, Pos, RectangleRounded, extrude, fillet

from .fasteners import heat_set_insert_dims, screw_clearance, counterbore_dims
from .features import hole_cutter

__all__ = ["enclosure"]


def enclosure(length: float, width: float, height: float, wall: float = 2.0, lid_thickness: float = 2.0,
              corner_radius: float = 4.0, lip_height: float = 2.0, gap: float = 0.25,
              screw_size: str | None = "M3", boss_od: float | None = None, floor: float | None = None,
              assembled: bool = True, lid_counterbore: bool = True) -> dict[str, bd.Shape]:
    """A box with a lid: returns {"base": ..., "lid": ...}.

    Outer size length x width x height (height = base + lid). The lid has a lip that
    drops inside the base walls with `gap` clearance per side. With screw_size, four
    corner bosses (for heat-set inserts) stand inside the base and the lid gets
    matching clearance holes (counterbored for socket head screws when
    lid_counterbore). assembled=True places the lid on the base; False lays the lid
    beside the base upside down, ready to print.
    """
    floor = wall if floor is None else floor
    base_h = height - lid_thickness
    outer = extrude(RectangleRounded(length, width, corner_radius), base_h)
    ir = max(corner_radius - wall, 0.3)
    cavity = Pos(0, 0, floor) * extrude(RectangleRounded(length - 2 * wall, width - 2 * wall, ir), base_h)
    base = outer - cavity

    lid = extrude(RectangleRounded(length, width, corner_radius), lid_thickness)
    lip_or = max(corner_radius - wall - gap, 0.3)
    lip_outer = extrude(RectangleRounded(length - 2 * wall - 2 * gap, width - 2 * wall - 2 * gap, lip_or), lip_height)
    lip_ir = max(lip_or - wall, 0.3)
    lip_inner = extrude(RectangleRounded(length - 4 * wall - 2 * gap, width - 4 * wall - 2 * gap, lip_ir), lip_height + 2)
    lip = (lip_outer - Pos(0, 0, -1) * lip_inner).moved(bd.Location((0, 0, -lip_height)))
    lid = lid + lip

    if screw_size:
        hd, hdepth = heat_set_insert_dims(screw_size)
        od = boss_od or hd + 3.2
        inset = wall + od / 2 + 0.2
        pts = [(sx * (length / 2 - inset), sy * (width / 2 - inset)) for sx in (-1, 1) for sy in (-1, 1)]
        boss_h = base_h - floor
        bosses = []
        for x, y in pts:
            b = Pos(x, y, floor) * Cylinder(od / 2, boss_h, align=(Align.CENTER, Align.CENTER, Align.MIN))
            b = b - Pos(x, y, base_h) * hole_cutter(hd, hdepth)
            bosses.append(b)
        base = base + bosses
        # lid holes: clearance (+ counterbore), and relieve the lip where bosses sit
        cl = screw_clearance(screw_size)
        cb_dims = counterbore_dims(screw_size)
        # counterbore only when at least 1 mm of lid remains under the head
        cb = cb_dims if lid_counterbore and lid_thickness - cb_dims[1] >= 1.0 else None
        lid = lid - [Pos(x, y, lid_thickness) * hole_cutter(cl, lid_thickness + lip_height + 2, counterbore=cb)
                     for x, y in pts]
        lid = lid - [Pos(x, y, -lip_height - 1) * Cylinder(od / 2 + gap, lip_height + 1, align=(Align.CENTER, Align.CENTER, Align.MIN))
                     for x, y in pts]

    base.label, lid.label = "base", "lid"
    if assembled:
        lid = lid.moved(bd.Location((0, 0, base_h)))
    else:
        lid = lid.rotate(Axis.X, 180).moved(bd.Location((0, width + 10, lid_thickness)))
    return {"base": base, "lid": lid}
