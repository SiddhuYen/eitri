"""What each corner of a part can do when dragged (eitri.viewglb.vertex_capabilities): the editor draws its
handles from this, so a corner offered as movable must really move, and the others must say what to do instead."""
from __future__ import annotations

import build123d as bd
import numpy as np

from eitri import direct, measure
from eitri.viewglb import part_topology


def _topo(name, shape):
    holes = [h for h in measure.cylindrical_features(shape) if h.kind == "hole"]
    return part_topology(name, shape, holes)


def _corner(verts, x, y, z):
    return next(v for v in verts if all(abs(a - b) < 1e-3 for a, b in zip(v.pos, (x, y, z))))


def test_a_box_corner_moves():
    faces, edges, holes, verts = _topo("b", bd.Box(20, 10, 6, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)))
    assert len(verts) == 8 and all(v.move and v.why is None for v in verts)
    assert all(len(v.faces) == 3 for v in verts)                  # each corner also offers its three faces


def test_hole_rim_and_its_face():
    plate = bd.Box(40, 30, 5, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)) - bd.Pos(8, 0, 0) * bd.Cylinder(3, 20)
    faces, edges, holes, verts = _topo("p", plate)
    rim = [v for v in verts if abs(((v.pos[0] - 8) ** 2 + v.pos[1] ** 2) ** 0.5 - 3) < 1e-3]
    assert rim and all(not v.move and v.why == "hole" for v in rim)
    full = [f for f in faces if f.hole and f.full]
    if full:                                                      # a one-piece hole wall: dragging the rim moves the hole
        assert all(v.hole == full[0].hole for v in rim)
    else:                                                         # a wall split in halves cannot be moved as a hole
        assert all(v.hole is None for v in rim)
    # the top face has the hole in it and cannot bend: its corners slide only within it (the editor locks the drag)
    top_corner = _corner(verts, 20, 15, 5)
    assert top_corner.move and top_corner.lock == [[0.0, 0.0, 1.0]]
    bottom_corner = _corner(verts, 20, 15, 0)                    # the hole goes through: the bottom face is stiff too
    assert bottom_corner.move and [abs(c) for c in bottom_corner.lock[0]] == [0.0, 0.0, 1.0]


def test_curved_neighbours_offer_flat_faces():
    rod = bd.Cylinder(5, 12, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    faces, edges, holes, verts = _topo("c", rod)
    assert verts and all(not v.move for v in verts)
    for v in verts:
        assert v.why == "curved"
        kinds = {next(f for f in faces if f.id == fid).type for fid in v.faces}
        assert "plane" in kinds                                    # an end cap can still be pushed/pulled


def test_every_corner_offered_as_movable_moves():
    shapes = {
        "box": bd.Box(20, 10, 6),
        "wedge": bd.Wedge(20, 10, 8, 4, 0, 16, 10),
        "l": bd.extrude(bd.Polygon((0, 0), (30, 0), (30, 6), (6, 6), (6, 20), (0, 20)), 8),
        "plate": bd.Box(40, 30, 5) - bd.Pos(8, 0, 0) * bd.Cylinder(3, 20),
    }
    tried = 0
    for name, shape in shapes.items():
        faces, edges, holes, verts = _topo(name, shape)
        for v in verts:
            if not v.move:
                continue
            for d in ((0.4, 0.3, 0.2), (0, 0, 0.5), (-0.3, 0.5, 0)):
                d = np.array(d, dtype=float)
                if len(v.lock) == 1:                              # the drag stays in the stiff face's plane
                    n = np.array(v.lock[0]); d = d - n * d.dot(n)
                elif len(v.lock) == 2:                            # or on the line between two stiff faces
                    t = np.cross(v.lock[0], v.lock[1]); t /= np.linalg.norm(t); d = t * d.dot(t)
                if np.linalg.norm(d) < 1e-6:
                    continue
                direct.move_vertices(shape, [v.pos], tuple(d))    # raises if the promise was wrong
                tried += 1
    assert tried >= 20
