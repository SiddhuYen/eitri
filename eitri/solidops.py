"""Solid-feature helpers behind the direct-edit ops of the solid-completeness tier: extrude extents
(to next / to face), thin walls, ribs, variable-radius fillets, patterns along a path, lips and
grooves, repair, and the wedge / coil primitives. Everything here works on build123d shapes and
is called from eitri.edits (the viewer) and from model code alike."""
from __future__ import annotations

import math
from typing import Sequence

import build123d as bd
from build123d import Vector


def _xyz(v) -> tuple[float, float, float]:
    return (float(v.X), float(v.Y), float(v.Z))
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopoDS import TopoDS


def wrap(ocp_shape) -> bd.Shape:
    """A build123d shape for an OCP result: a lone solid as Solid, otherwise a Compound."""
    kind = ocp_shape.ShapeType()
    if kind == TopAbs_ShapeEnum.TopAbs_SOLID:
        return bd.Solid(TopoDS.Solid(ocp_shape))
    comp = bd.Compound(ocp_shape)
    sol = comp.solids()
    if len(sol) == 1:
        return sol[0]
    return comp


def one_solid(shape: bd.Shape, what: str = "result") -> bd.Shape:
    sol = shape.solids()
    if not sol:
        raise ValueError(f"{what} has no solid")
    if len(sol) == 1:
        return sol[0]
    out = sol[0]
    for s in sol[1:]:
        out = out + s
    return out


def touches_plane(solid: bd.Shape, plane: bd.Plane, tol: float = 1e-3) -> bool:
    """Does the solid reach the plane (one of its vertices lies on it)?"""
    n = plane.z_dir
    return any(abs((Vector(v.X, v.Y, v.Z) - plane.origin).dot(n)) < tol for v in solid.vertices())


def reach_along(solid: bd.Shape, origin: Vector, direction: Vector) -> float:
    return max((Vector(v.X, v.Y, v.Z) - origin).dot(direction) for v in solid.vertices())


def sketch_of(faces: Sequence[bd.Face]):
    """One extrudable 2D object from several faces."""
    faces = list(faces)
    if not faces:
        raise ValueError("the profile has no closed region")
    out = faces[0]
    for f in faces[1:]:
        out = out + f
    return out


# ---------------------------------------------------------------------------- extrude extents

def to_next_piece(part: bd.Shape, frame: bd.Plane, region, mode: str, length: float, flip: bool = False,
                  must_touch: bd.Face | None = None) -> bd.Shape:
    """Fusion's "to next" extent: the slab of material between the sketch plane and the first
    face of the part in the extrusion direction (for `add`: the empty space the extrusion fills;
    for `cut`: the first wall it goes through). Raises when nothing is there to stop it."""
    sgn = (-1.0 if flip else 1.0) * (1.0 if mode == "add" else -1.0)      # adds go outward, cuts go into the part
    tool = bd.extrude(frame * region, sgn * length)
    pieces = (tool - part) if mode == "add" else (tool & part)
    sol = [s for s in pieces.solids() if touches_plane(s, frame)]
    if not sol:
        raise ValueError("the extrusion does not start on the part" if mode == "cut" else "nothing lies in that direction")
    piece = sol[0]
    for s in sol[1:]:
        piece = piece + s
    if mode == "add" and reach_along(piece, frame.origin, frame.z_dir * sgn) > length - 1e-3:
        raise ValueError("nothing to extrude to in that direction (use a distance)")
    if must_touch is not None and piece.distance_to(must_touch) > 1e-3:
        raise ValueError("the extrusion does not reach that face")
    return piece


def thin_region(faces: Sequence[bd.Face], chains: Sequence[bd.Wire], thickness: float) -> list[bd.Face]:
    """The 2D regions of a thin extrude: a wall of `thickness` just inside each closed profile,
    and a band of `thickness` centred on each open chain."""
    out: list[bd.Face] = []
    t = float(thickness)
    for f in faces:
        inner = bd.offset(f, -t)
        ring = f - inner
        out += [x for x in ring.faces() if x.area > 1e-9]
    for w in chains:
        band = w.offset_2d(t / 2, kind=bd.Kind.INTERSECTION, side=bd.Side.BOTH)
        out.append(bd.make_face(band))
    if not out:
        raise ValueError("the sketch has nothing to make a wall from")
    return out


# ---------------------------------------------------------------------------- ribs

def rib_region(chain: bd.Wire, direction: Sequence[float], length: float) -> bd.Face:
    """The planar region swept by an open sketch chain moved `length` along an in-plane direction."""
    d = Vector(float(direction[0]), float(direction[1]), 0).normalized()
    shifted = chain.moved(bd.Location(_xyz(d * length)))
    e1 = bd.Edge.make_line(chain.position_at(1), shifted.position_at(1))
    e2 = bd.Edge.make_line(shifted.position_at(0), chain.position_at(0))
    wires = bd.Wire.combine(list(chain.edges()) + [e1] + list(shifted.edges()) + [e2], tol=1e-3)
    if len(wires) != 1 or not wires[0].is_closed:
        raise ValueError("the rib profile must be a single open chain")
    return bd.make_face(wires[0])


def rib_piece(part: bd.Shape, frame: bd.Plane, chain2d: bd.Wire, direction: Sequence[float], thickness: float,
              distance: float | None, length: float) -> bd.Shape:
    """The rib solid: the chain extended in-plane along `direction` until it meets the part
    (or by `distance`), `thickness` thick across the sketch plane."""
    region = rib_region(chain2d, direction, float(distance) if distance else length)
    slab = bd.extrude(frame * region, float(thickness) / 2, both=True)
    if distance:
        return slab
    chain3d = frame * chain2d
    pieces = slab - part
    sol = [s for s in pieces.solids() if s.distance_to(chain3d) < 1e-3]
    if not sol:
        raise ValueError("the rib profile lies inside the part")
    piece = sol[0]
    for s in sol[1:]:
        piece = piece + s
    d = Vector(float(direction[0]), float(direction[1]), 0).normalized()
    d3 = frame.x_dir * d.X + frame.y_dir * d.Y
    if reach_along(piece, chain3d.position_at(0.5), d3) > length - 1e-3:
        raise ValueError("the rib did not meet the part in that direction")
    return piece


# ---------------------------------------------------------------------------- fillets and chamfers

def variable_fillet(shape: bd.Shape, edges: Sequence[bd.Edge], r_start: float, r_end: float) -> bd.Shape:
    """A fillet whose radius runs linearly from r_start to r_end along each edge."""
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    mk = BRepFilletAPI_MakeFillet(shape.wrapped)
    for e in edges:
        mk.Add(float(r_start), float(r_end), e.wrapped)
    mk.Build()
    if not mk.IsDone():
        raise ValueError("the kernel could not build that variable fillet (radii too large?)")
    return one_solid(wrap(mk.Shape()), "the fillet")


def reference_face(shape: bd.Shape, edges: Sequence[bd.Edge]) -> bd.Face:
    """A face of the shape that borders every one of the edges (planar preferred)."""
    cands = []
    for f in shape.faces():
        fe = f.edges()
        if all(any(e.distance_to(x) < 1e-6 and abs(e.length - x.length) < 1e-6 for x in fe) for e in edges):
            cands.append(f)
    if not cands:
        raise ValueError("the chamfer's reference face must border every selected edge")
    cands.sort(key=lambda f: (f.geom_type != bd.GeomType.PLANE, -f.area))
    return cands[0]


# ---------------------------------------------------------------------------- patterns along a path

def path_locations(wire: bd.Wire, n: int, spacing: float | None, align: bool) -> list[tuple]:
    """Where copies 2..n go along a path: (delta, axis, angle) per copy; the first copy stays at the
    path start. `spacing` None spreads the copies over the whole path."""
    n = max(1, int(n))
    L = wire.length
    if n < 2:
        return []
    if spacing:
        ts = [min(1.0, k * float(spacing) / L) for k in range(n)]
    else:
        ts = [k / (n - 1) for k in range(n)] if not wire.is_closed else [k / n for k in range(n)]
    p0, t0 = wire.position_at(0), wire.tangent_at(0)
    out = []
    for t in ts[1:]:
        p = wire.position_at(t)
        delta = p - p0
        if align:
            from .edits import rotation_between
            axis, ang = rotation_between(t0, wire.tangent_at(t))
            out.append(("path", delta, bd.Axis(p, axis), ang))
        else:
            out.append(("path", delta, None, 0.0))
    return out


# ---------------------------------------------------------------------------- lips and grooves

def rim_band(face: bd.Face, width: float, side: str = "inner") -> list[bd.Face]:
    """The band of `width` along the inner (or outer) outline of a rim face (a wall's top)."""
    pl = bd.Plane(face)
    local = pl.to_local_coords(face)
    inners = local.inner_wires()
    if side == "inner":
        if not inners:
            raise ValueError("that face has no inner outline: pick the rim of a wall")
        opening = bd.make_face(max(inners, key=lambda w: w.length))
        band = bd.offset(opening, float(width), kind=bd.Kind.INTERSECTION) - opening      # sharp corners, like the wall
    else:
        outer = bd.make_face(local.outer_wire())
        band = outer - bd.offset(outer, -float(width), kind=bd.Kind.INTERSECTION)
    region = band & local
    faces = [f for f in region.faces() if f.area > 1e-9]
    if not faces:
        raise ValueError("the band does not fit on that face")
    return [pl.from_local_coords(f) for f in faces]


# ---------------------------------------------------------------------------- repair

def repair(shape: bd.Shape) -> tuple[bd.Shape, str]:
    from OCP.ShapeFix import ShapeFix_Shape
    before_ok = shape.is_valid
    fx = ShapeFix_Shape(shape.wrapped)
    fx.Perform()
    fixed = wrap(fx.Shape())
    try:
        fixed = fixed.clean()
    except Exception:
        pass
    fixed = one_solid(fixed, "the repaired shape")
    after_ok = fixed.is_valid
    v0, v1 = abs(shape.volume), abs(fixed.volume)
    report = f"valid {'yes' if before_ok else 'NO'} -> {'yes' if after_ok else 'NO'}, {len(shape.faces())} -> {len(fixed.faces())} faces, volume {v0:.2f} -> {v1:.2f} mm³"
    return fixed, report


# ---------------------------------------------------------------------------- primitives

def wedge_solid(w: float, d: float, h: float, top_w: float | None = None, top_h: float = 0.0) -> bd.Shape:
    """A ramp/wedge standing on z=0, `w` across X, `d` along Y, `h` high at y=-d/2 falling to
    `top_h` at y=+d/2 (a triangular prism by default); `top_w` narrows the far end."""
    xmin = (w - (top_w if top_w is not None else w)) / 2
    xmax = w - xmin
    return bd.Wedge(w, d, h, xmin, 0, xmax, max(0.0, float(top_h)), align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))


def coil_solid(R: float, r: float, pitch: float, height: float, lefthand: bool = False) -> bd.Shape:
    """A helical coil (spring) of wire radius r on a helix of radius R, standing on z=0."""
    helix = bd.Helix(pitch=float(pitch), height=float(height), radius=float(R), lefthand=lefthand)
    pl = bd.Plane(origin=helix.position_at(0), z_dir=helix.tangent_at(0))
    coil = bd.sweep(pl * bd.Circle(float(r)), path=helix, is_frenet=True)
    if not coil.is_valid or not coil.solids():
        raise ValueError("the coil did not sweep to a valid solid (wire radius must be smaller than the pitch/2)")
    return bd.Pos(0, 0, float(r)) * coil
