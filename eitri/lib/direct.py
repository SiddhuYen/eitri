"""Direct edits in code: move corners, edges and faces of a solid anywhere; delete faces.

These are the code form of the viewer's Edit tool, so a model can keep a "grab that
corner and pull it up 6 mm" change parametrically: the points are plain coordinates
that can come from PARAMS.
"""
from __future__ import annotations

from typing import Sequence

import build123d as bd
from build123d import Vector

from .. import direct as _d

__all__ = ["move_vertices", "move_edge", "move_face", "delete_faces"]


def _pt(p) -> Vector:
    return p if isinstance(p, Vector) else Vector(*[float(c) for c in p])


def move_vertices(shape: bd.Shape, near: Sequence, delta, tol: float = 0.5) -> bd.Shape:
    """Move the corner(s) nearest to each point in `near` by `delta` = (dx, dy, dz).

    The flat faces around them follow; a face whose corners stop being coplanar becomes
    a smooth patch (a box corner pulled up turns the box into a wedge-ish solid).
    `move_vertices(box, [(10, 10, 10)], (0, 0, 6))`
    """
    pts = []
    for p in near:
        v = _d.nearest_vertex(shape, _pt(p), tol)
        if v is None:
            raise ValueError(f"no corner within {tol} mm of {tuple(_pt(p))}")
        pts.append(v)
    return _d.move_vertices(shape, pts, _pt(delta))


def move_edge(shape: bd.Shape, near, delta, tol: float = 1.0) -> bd.Shape:
    """Move the straight edge whose midpoint is nearest to `near` by `delta` (both of its
    corners move; the two faces beside it tilt)."""
    p = _pt(near)
    best, best_d = None, tol
    for e in shape.edges():
        if e.geom_type != bd.GeomType.LINE:
            continue
        d = (e.position_at(0.5) - p).length
        if d <= best_d:
            best, best_d = e, d
    if best is None:
        raise ValueError(f"no straight edge within {tol} mm of {tuple(p)}")
    return _d.move_vertices(shape, [best.position_at(0), best.position_at(1)], _pt(delta))


def _nearest_face(shape: bd.Shape, near, kinds=None) -> bd.Face:
    p = bd.Vertex(*_pt(near))
    best, best_d = None, None
    for f in shape.faces():
        if kinds and f.geom_type not in kinds:
            continue
        try:
            d = f.distance_to(p)
        except Exception:
            continue
        if best_d is None or d < best_d:
            best, best_d = f, d
    if best is None:
        raise ValueError("no face found")
    return best


def move_face(shape: bd.Shape, near, delta=(0, 0, 0), tilt: float = 0.0, tilt_axis=(0, 1, 0), about=None) -> bd.Shape:
    """Move the face nearest to the point `near` by `delta`, and/or tilt it by `tilt`
    degrees about `tilt_axis` (through `about`, default the moved face centre).
    Flat faces: the faces beside it extend or trim to meet the new plane. Round faces
    (holes, bosses, spherical bumps) translate.
    `move_face(plate, (0, 0, 6), delta=(0, 0, 2), tilt=10, tilt_axis=(0, 1, 0))`
    """
    face = _nearest_face(shape, near)
    rot = {"axis": list(_pt(tilt_axis)), "angle": float(tilt), "about": list(_pt(about)) if about is not None else None} if tilt else None
    return _d.move_face(shape, face, _pt(delta), rot)


def delete_faces(shape: bd.Shape, near: Sequence, tol: float = 2.0) -> bd.Shape:
    """Remove the faces nearest to the given points and heal the body: delete a fillet,
    a chamfer, a hole, a boss or a pocket. Give one point per face of the feature
    (a hole: a point on its wall; a fillet: a point on the round)."""
    faces = []
    for p in near:
        f = _nearest_face(shape, p)
        if all(not f.is_same(g) for g in faces):
            faces.append(f)
    return _d.delete_faces(shape, faces)
