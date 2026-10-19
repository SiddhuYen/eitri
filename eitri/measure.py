"""Measurement helpers for build123d geometry.

These are the functions the automatic report uses and the ones a model's
`verify(parts, p, c)` calls. Everything is plain build123d/OCCT; units are mm.
"""
from __future__ import annotations

import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
import build123d as bd
from build123d import Vector

__all__ = [
    "bbox_size", "signed_volume", "solid_volumes", "is_valid", "free_edge_count",
    "Hole", "holes", "bosses", "cylindrical_features", "WallSample", "min_wall",
    "min_distance", "overlap_volume", "point_inside", "faces_with_normal",
    "planar_faces_at", "mass_grams", "fillet_radii", "DENSITY", "SharpEdge", "sharp_edges",
]

# g/cm^3 for common materials
DENSITY = {
    "pla": 1.24, "petg": 1.27, "abs": 1.04, "nylon": 1.14, "resin": 1.1,
    "aluminum": 2.70, "aluminium": 2.70, "steel": 7.85, "stainless": 8.0,
    "brass": 8.5, "copper": 8.96, "titanium": 4.43, "acrylic": 1.18, "wood": 0.6,
}


_BBOXES: list = []            # (key, shape, box): one build asks for each part's box many times


def bounding_box(shape: bd.Shape) -> bd.BoundBox:
    """shape.bounding_box(), remembered for the last shapes asked about (the optimal box costs ~0.1-0.2 s
    on a part with B-spline faces, and the view, the report and the render all want it)."""
    key = hash(shape.wrapped)
    for k, s, box in _BBOXES:
        if k == key and s.wrapped.IsSame(shape.wrapped):
            return box
    box = shape.bounding_box()
    _BBOXES.insert(0, (key, shape, box))
    del _BBOXES[128:]
    return box


def bbox_size(shape: bd.Shape) -> Vector:
    return bounding_box(shape).size


def signed_volume(shape: bd.Shape) -> float:
    """Signed volume from OCCT mass properties (negative for inverted solids)."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props)
    return props.Mass()


def solid_volumes(shape: bd.Shape) -> list[float]:
    return [signed_volume(s) for s in shape.solids()]


def is_valid(shape: bd.Shape) -> bool:
    try:
        return bool(shape.is_valid)
    except Exception:
        return False


def free_edge_count(shape: bd.Shape) -> int:
    """Edges used by fewer than two faces (boundary edges of open shells). Degenerate edges (the poles of a
    sphere, the tip of a cone: no length, one face) are not boundaries and are not counted."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    count: dict[int, int] = defaultdict(int)
    for f in shape.faces():
        # explore with repetition: a seam edge is used twice by its own face
        ex = TopExp_Explorer(f.wrapped, TopAbs_EDGE)
        while ex.More():
            if not BRep_Tool.Degenerated_s(TopoDS.Edge(ex.Current())):
                count[hash(bd.Edge(ex.Current()))] += 1
            ex.Next()
    return sum(1 for n in count.values() if n < 2)


# --------------------------------------------------------------------------
# Cylindrical features: holes and bosses
# --------------------------------------------------------------------------

@dataclass
class Hole:
    """A full-round cylindrical feature. kind is "hole" (concave) or "boss" (convex)."""
    kind: str
    diameter: float
    axis: Vector          # unit direction
    start: Vector         # axis point at one end
    end: Vector           # axis point at the other end
    depth: float
    through: bool | None  # None when not determined (bosses)
    coverage: float       # fraction of 2*pi covered by the faces (1.0 = full round)

    @property
    def center(self) -> Vector:
        return (self.start + self.end) * 0.5

    @property
    def radius(self) -> float:
        return self.diameter / 2

    def describe(self) -> str:
        c = self.center
        ax = _axis_name(self.axis)
        kind = self.kind
        if self.kind == "hole":
            kind = "through hole" if self.through else ("blind hole" if self.through is False else "hole")
        return f"Ø{self.diameter:.2f} {kind}, depth {self.depth:.2f}, axis {ax}, centre ({c.X:.2f}, {c.Y:.2f}, {c.Z:.2f})"


def _axis_name(v: Vector) -> str:
    comps = [("X", v.X), ("Y", v.Y), ("Z", v.Z)]
    for name, c in comps:
        if abs(abs(c) - 1) < 1e-3:
            return name
    return f"({v.X:.2f}, {v.Y:.2f}, {v.Z:.2f})"


def _canon_axis(d: Vector) -> Vector:
    """Flip an axis direction into a canonical half-space so parallel axes compare equal."""
    for c in (d.Z, d.Y, d.X):
        if abs(c) > 1e-9:
            return d if c > 0 else d * -1
    return d


_FEATURES: list = []          # (key, shape, holes): the report and the viewer topology ask for the same shape


def cylindrical_features(shape: bd.Shape, min_coverage: float = 0.9, tol: float = 1e-3) -> list[Hole]:
    """Group cylindrical faces by axis+radius into full-round holes and bosses (remembered for the last
    few shapes, so one build computes them once)."""
    key = (hash(shape.wrapped), min_coverage, tol)
    for k, s, holes in _FEATURES:
        if k == key and s.wrapped.IsSame(shape.wrapped):
            return list(holes)
    holes = _cylindrical_features(shape, min_coverage, tol)
    _FEATURES.insert(0, (key, shape, holes))
    del _FEATURES[8:]
    return list(holes)


def _cylindrical_features(shape: bd.Shape, min_coverage: float = 0.9, tol: float = 1e-3) -> list[Hole]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools

    groups: dict[tuple, dict] = {}
    for face in shape.faces():
        if face.geom_type != bd.GeomType.CYLINDER:
            continue
        try:
            surf = BRepAdaptor_Surface(face.wrapped)
            cyl = surf.Cylinder()
            r = cyl.Radius()
            ax = cyl.Axis()
            loc = Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
            d = Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()).normalized()
            umin, umax, vmin, vmax = BRepTools.UVBounds_s(face.wrapped)
        except Exception:
            continue
        if r < 1e-6:
            continue
        # a point on the face and its outward normal decide concave/convex (one surface evaluation)
        try:
            from OCP.BRepGProp import BRepGProp_Face
            from OCP.gp import gp_Pnt, gp_Vec
            P_, N_ = gp_Pnt(), gp_Vec()
            BRepGProp_Face(face.wrapped).Normal(umin + 0.5 * (umax - umin), vmin + 0.5 * (vmax - vmin), P_, N_)
            pt = Vector(P_.X(), P_.Y(), P_.Z())
            n = Vector(N_.X(), N_.Y(), N_.Z()).normalized()
        except Exception:
            continue
        radial = pt - loc
        radial = radial - d * radial.dot(d)
        if radial.length < 1e-9:
            continue
        concave = n.dot(radial) < 0
        # canonical axis: direction sign-normalised, point = foot of perpendicular from origin
        dc = _canon_axis(d)
        foot = loc - dc * loc.dot(dc)
        lo, hi = sorted((loc.dot(dc) + vmin * (1 if dc.dot(d) > 0 else -1),
                         loc.dot(dc) + vmax * (1 if dc.dot(d) > 0 else -1)))
        key = (
            "hole" if concave else "boss",
            round(dc.X, 3), round(dc.Y, 3), round(dc.Z, 3),
            round(foot.X, 2), round(foot.Y, 2), round(foot.Z, 2),
            round(r, 3),
        )
        g = groups.setdefault(key, {"dc": dc, "foot": foot, "r": r, "lo": lo, "hi": hi, "u": 0.0, "kind": key[0]})
        g["lo"] = min(g["lo"], lo)
        g["hi"] = max(g["hi"], hi)
        g["u"] += (umax - umin)

    out: list[Hole] = []
    solids = list(shape.solids())
    for g in groups.values():
        coverage = g["u"] / (2 * math.pi)
        if coverage < min_coverage:
            continue
        dc, foot = g["dc"], g["foot"]
        start = foot + dc * g["lo"]
        end = foot + dc * g["hi"]
        depth = g["hi"] - g["lo"]
        through: bool | None = None
        if g["kind"] == "hole" and solids:
            eps = max(depth * 0.02, 0.05)
            # probe just beyond each end, on a ring at 0.8 r: a coaxial smaller hole
            # (counterbore step) leaves material on that ring, so it reads as blind
            through = _end_open(solids, start - dc * eps, dc, g["r"] * 0.8) and \
                _end_open(solids, end + dc * eps, dc, g["r"] * 0.8)
        out.append(Hole(g["kind"], 2 * g["r"], dc, start, end, depth, through, min(coverage, 1.0)))
    out.sort(key=lambda h: (h.kind, h.diameter, h.center.X, h.center.Y, h.center.Z))
    return out


def _end_open(solids: list[bd.Solid], centre: Vector, axis: Vector, ring_r: float) -> bool:
    """True if the ring of points around `centre` (radius ring_r, perpendicular to axis) is outside every solid."""
    u = axis.cross(Vector(0, 0, 1))
    if u.length < 1e-6:
        u = axis.cross(Vector(0, 1, 0))
    u = u.normalized()
    v = axis.cross(u).normalized()
    probes = [centre + (u * math.cos(a) + v * math.sin(a)) * ring_r for a in (0, math.pi / 2, math.pi, 3 * math.pi / 2)]
    probes.append(centre)
    return not any(point_inside(s, pt) for s in solids for pt in probes)


def holes(shape: bd.Shape, diameter: float | None = None, tol: float = 0.05,
          axis: str | Sequence[float] | None = None, through: bool | None = None) -> list[Hole]:
    """Full-round holes in a shape, optionally filtered by diameter, axis and through-ness."""
    out = [h for h in cylindrical_features(shape) if h.kind == "hole"]
    if diameter is not None:
        out = [h for h in out if abs(h.diameter - diameter) <= tol]
    if axis is not None:
        ad = {"X": Vector(1, 0, 0), "Y": Vector(0, 1, 0), "Z": Vector(0, 0, 1)}.get(str(axis).upper()) \
            if isinstance(axis, str) else Vector(*axis).normalized()
        out = [h for h in out if abs(abs(h.axis.dot(ad)) - 1) < 1e-3]
    if through is not None:
        out = [h for h in out if h.through == through]
    return out


def bosses(shape: bd.Shape, diameter: float | None = None, tol: float = 0.05) -> list[Hole]:
    out = [h for h in cylindrical_features(shape) if h.kind == "boss"]
    if diameter is not None:
        out = [h for h in out if abs(h.diameter - diameter) <= tol]
    return out


def fillet_radii(shape: bd.Shape, kind: str = "convex") -> list[float]:
    """Distinct radii of partial cylinders: kind "convex" (rounded-over edges), "concave"
    (inside fillets that add material in a corner) or "any"."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    radii = set()
    for face in shape.faces():
        if face.geom_type != bd.GeomType.CYLINDER:
            continue
        try:
            cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
            umin, umax, _, _ = BRepTools.UVBounds_s(face.wrapped)
            if umax - umin > math.pi * 1.5:
                continue
            pt = face.position_at(0.5, 0.5)
            n = face.normal_at(pt)
            loc = Vector(cyl.Axis().Location().X(), cyl.Axis().Location().Y(), cyl.Axis().Location().Z())
            d = Vector(cyl.Axis().Direction().X(), cyl.Axis().Direction().Y(), cyl.Axis().Direction().Z())
            radial = pt - loc
            radial = radial - d * radial.dot(d)
            convex = n.dot(radial) > 0
            if kind == "any" or (kind == "convex" and convex) or (kind == "concave" and not convex):
                radii.add(round(cyl.Radius(), 3))
        except Exception:
            continue
    return sorted(radii)


# --------------------------------------------------------------------------
# Wall thickness
# --------------------------------------------------------------------------

@dataclass
class WallSample:
    thickness: float
    point: Vector
    normal: Vector

    def describe(self) -> str:
        p = self.point
        return f"{self.thickness:.2f} mm at ({p.X:.1f}, {p.Y:.1f}, {p.Z:.1f})"


def min_wall(shape: bd.Shape, samples: int = 600, seed: int = 0, edge_inset: float = 0.4,
             edge_samples: int = 5) -> WallSample | None:
    """Estimate the thinnest wall by casting inward rays from points on the surface.

    Two families of sample points: area-weighted random points on every face, and
    points just inside each planar face along each of its edges (`edge_inset` mm
    in, `edge_samples` per edge). The second family is what catches slivers and
    feather edges, which have almost no area but sit right next to an edge.
    Returns the smallest material thickness found (None for non-solids) and where.
    """
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

    solids = list(shape.solids())
    if not solids:
        return None
    rng = np.random.default_rng(seed)
    best: WallSample | None = None
    diag = max(bounding_box(shape).diagonal, 1e-6)

    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.GCPnts import GCPnts_AbscissaPoint
    from OCP.IntTools import IntTools_FClass2d
    from OCP.TopAbs import TopAbs_FORWARD, TopAbs_IN, TopAbs_ON
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt2d, gp_Vec

    for solid in solids:
        faces = list(solid.faces())
        areas = np.array([max(f.area, 0.0) for f in faces])
        if areas.sum() <= 0:
            continue
        isec = IntCurvesFace_ShapeIntersector()
        isec.Load(solid.wrapped, 1e-6)

        def cast(P: gp_Pnt, inward: gp_Dir) -> None:
            nonlocal best
            try:
                isec.Perform(gp_Lin(P, inward), -1e-3, diag * 2)
            except Exception:
                return
            best_w = None
            for i in range(1, isec.NbPnt() + 1):
                w = isec.WParameter(i)
                if w > 1e-5 and (best_w is None or w < best_w):
                    best_w = w
            if best_w is not None and (best is None or best_w < best.thickness):
                best = WallSample(best_w, Vector(P.X(), P.Y(), P.Z()), Vector(inward.X(), inward.Y(), inward.Z()))

        # 1. area-weighted random points: the point and its outward normal from one surface evaluation at
        #    (u, v), kept when (u, v) lies inside the face's trimmed domain (a 2D test per face, set up once)
        alloc = np.maximum(np.round(areas / areas.sum() * samples), 1).astype(int)
        for face, k in zip(faces, alloc):
            uv = rng.random((int(k), 2)) * 0.9 + 0.05
            try:
                u0, u1, v0, v1 = face._uv_bounds()
                prop = BRepGProp_Face(face.wrapped)
                inside = IntTools_FClass2d(face.wrapped, 1e-6)
            except Exception:
                continue
            for u, v in uv:
                uu, vv = u0 + float(u) * (u1 - u0), v0 + float(v) * (v1 - v0)
                try:
                    if inside.Perform(gp_Pnt2d(uu, vv)) not in (TopAbs_IN, TopAbs_ON):
                        continue                               # trimmed away (e.g. over a hole)
                    P, N = gp_Pnt(), gp_Vec()
                    prop.Normal(uu, vv, P, N)                  # outward for a solid's face
                    if N.Magnitude() < 1e-12:
                        continue
                    cast(P, gp_Dir(-N.X(), -N.Y(), -N.Z()))
                except Exception:
                    continue

        # 2. points just inside planar faces along their edges (budgeted: big parts get fewer per edge)
        if edge_inset > 0 and edge_samples > 0:
            n_edges = sum(len(f.edges()) for f in faces if f.geom_type == bd.GeomType.PLANE) or 1
            edge_samples = max(1, min(edge_samples, int(3000 / (2 * n_edges))))
            for face in faces:
                if face.geom_type != bd.GeomType.PLANE:
                    continue
                try:
                    n = face.normal_at()
                    c = face.center()
                    # inside-tests in the plane's own (u, v): one 2D classifier per face, reused for every point
                    pos = BRepAdaptor_Surface(face.wrapped).Plane().Position()
                    O, X, Y = pos.Location(), pos.XDirection(), pos.YDirection()
                    in2d = IntTools_FClass2d(face.wrapped, 1e-4)
                except Exception:
                    continue
                inward = gp_Dir(-n.X, -n.Y, -n.Z)
                nw, cw = n.wrapped, c.wrapped
                ex = TopExp_Explorer(face.wrapped, TopAbs_EDGE)
                while ex.More():
                    # what Edge.position_at/tangent_at compute (same OCCT calls, so the same points), with the
                    # arc length found once per edge instead of twice per sample, and gp_Vec math directly
                    ew = TopoDS.Edge(ex.Current())
                    ex.Next()
                    try:
                        curve = BRepAdaptor_Curve(ew)
                        length = GCPnts_AbscissaPoint.Length_s(curve)
                    except Exception:
                        continue
                    if length < 1e-6:
                        continue
                    forward = ew.Orientation() == TopAbs_FORWARD
                    first = curve.FirstParameter()
                    for i in range(edge_samples):
                        t_ = (i + 0.5) / edge_samples
                        try:
                            param = GCPnts_AbscissaPoint(curve, length * (t_ if forward else 1 - t_), first).Parameter()
                            P = gp_Vec(curve.Value(param).XYZ())
                            d1 = curve.DN(param, 1)
                            if d1.Magnitude() == 0:
                                continue
                            tangent = (d1 if forward else d1.Multiplied(-1)).Normalized()
                        except Exception:
                            continue
                        side = nw.Crossed(tangent)
                        if side.Magnitude() < 1e-9:
                            continue
                        side = side.Normalized()
                        if side.Dot(cw.Subtracted(P)) < 0:     # point into the face, not out of it
                            side = side.Multiplied(-1)
                        for inset in (edge_inset, edge_inset * 0.25):   # a finer ring catches sliver faces
                            Q = P.Added(side.Multiplied(inset))
                            qx, qy, qz = Q.X(), Q.Y(), Q.Z()
                            try:
                                dx, dy, dz = qx - O.X(), qy - O.Y(), qz - O.Z()
                                uq = dx * X.X() + dy * X.Y() + dz * X.Z()
                                vq = dx * Y.X() + dy * Y.Y() + dz * Y.Z()
                                if in2d.Perform(gp_Pnt2d(uq, vq)) not in (TopAbs_IN, TopAbs_ON):
                                    continue
                            except Exception:
                                continue
                            cast(gp_Pnt(qx, qy, qz), inward)
    return best


@dataclass
class SharpEdge:
    """A convex edge whose faces meet at a small interior angle (a knife/feather edge)."""
    angle_deg: float
    point: Vector
    length: float

    def describe(self) -> str:
        p = self.point
        return f"{self.angle_deg:.0f} deg over {self.length:.1f} mm at ({p.X:.1f}, {p.Y:.1f}, {p.Z:.1f})"


def sharp_edges(shape: bd.Shape, max_interior_deg: float = 30.0, min_length: float = 0.5) -> list[SharpEdge]:
    """Convex edges where the material wedge is thinner than `max_interior_deg`.

    These feather edges are what a sampled wall-thickness scan misses: a cut that
    meets a slanted face at a shallow angle leaves a sliver that prints badly or
    breaks off. Deterministic (every edge is checked), cheap, and independent of
    sampling.
    """
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer

    owners: dict[int, list[bd.Face]] = defaultdict(list)
    for f in shape.faces():
        ex = TopExp_Explorer(f.wrapped, TopAbs_EDGE)
        while ex.More():
            owners[hash(bd.Edge(ex.Current()))].append(f)
            ex.Next()
    centers: dict[int, Vector] = {}               # per face, computed once (a surface integral each)
    flat_normals: dict[int, Vector] = {}

    def center(f: bd.Face) -> Vector:
        k = hash(f)
        if k not in centers:
            centers[k] = f.center()
        return centers[k]

    flat = {hash(f) for f in shape.faces() if f.geom_type == bd.GeomType.PLANE}   # geom_type is slow: once per face

    def normal(f: bd.Face, p: Vector) -> Vector:
        if hash(f) in flat:                        # the same everywhere on a flat face: no projection needed
            k = hash(f)
            if k not in flat_normals:
                flat_normals[k] = f.normal_at()
            return flat_normals[k]
        return f.normal_at(p)

    out: list[SharpEdge] = []
    for e in shape.edges():
        faces = owners.get(hash(e), [])
        if len(faces) != 2 or e.length < min_length:
            continue
        f1, f2 = faces
        try:
            mid = e.position_at(0.5)
            n1 = normal(f1, mid)
            n2 = normal(f2, mid)
            # a point just inside each face, away from the edge, tells convex from concave
            c1 = center(f1) - mid
            c2 = center(f2) - mid
        except Exception:
            continue
        ang = n1.get_angle(n2)                      # 0 = flat, 180 = razor
        interior = 180.0 - ang
        if interior > max_interior_deg:
            continue
        # convex if the faces fold away from each other: the other face lies behind this face's plane
        convex = c2.dot(n1) < 0 and c1.dot(n2) < 0
        if convex:
            out.append(SharpEdge(interior, mid, e.length))
    out.sort(key=lambda s: s.angle_deg)
    return out


# --------------------------------------------------------------------------
# Relationships between shapes
# --------------------------------------------------------------------------

_DISTANCES: list = []         # (key, a, b, distance): the overlap test and the report ask for the same pairs


def min_distance(a: bd.Shape, b: bd.Shape) -> float:
    """The smallest distance between two shapes (multi-threaded; remembered for the last few pairs)."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    key = (hash(a.wrapped), hash(b.wrapped))
    for k, sa, sb, value in _DISTANCES:
        if k == key and sa.wrapped.IsSame(a.wrapped) and sb.wrapped.IsSame(b.wrapped):
            return value
    d = BRepExtrema_DistShapeShape()
    d.SetMultiThread(True)
    d.LoadS1(a.wrapped)
    d.LoadS2(b.wrapped)
    d.Perform()
    if not d.IsDone():
        raise RuntimeError("distance computation failed")
    value = d.Value()
    _DISTANCES.insert(0, (key, a, b, value))
    del _DISTANCES[16:]
    return value


def overlap_volume(a: bd.Shape, b: bd.Shape) -> float:
    """Volume of the intersection of two shapes (0 for touching or clear)."""
    ba, bb = bounding_box(a), bounding_box(b)
    if (ba.max.X < bb.min.X or bb.max.X < ba.min.X or ba.max.Y < bb.min.Y or bb.max.Y < ba.min.Y
            or ba.max.Z < bb.min.Z or bb.max.Z < ba.min.Z):
        return 0.0
    try:
        # surfaces apart means clear, unless one part sits wholly inside the other (a buried pin)
        if min_distance(a, b) > 1e-6 and not _inside_other(a, b) and not _inside_other(b, a):
            return 0.0
        common = a.intersect(b)
    except Exception:
        return 0.0
    if common is None:
        return 0.0
    try:
        return abs(sum(signed_volume(s) for s in common.solids()))
    except Exception:
        return 0.0


def _inside_other(outer: bd.Shape, inner: bd.Shape) -> bool:
    """For two shapes whose surfaces do not meet: whether `inner` lies inside a solid of `outer` (then any
    one of its vertices does)."""
    solids = list(outer.solids())
    for s in inner.solids() or [inner]:
        vs = s.vertices()
        if vs and any(point_inside(o, (vs[0].X, vs[0].Y, vs[0].Z)) for o in solids):
            return True
    return False


_LOCAL = threading.local()


def _cached(name: str, shape: bd.Shape, make):
    """A loaded per-solid helper (classifier, ray intersector), reused for later points (loading one per
    point costs ~4x more on a detailed solid). Per thread, a few solids at a time; the shape is kept alive."""
    cache = getattr(_LOCAL, name, None)
    if cache is None:
        cache = []
        setattr(_LOCAL, name, cache)
    key = hash(shape.wrapped)
    for i, (k, s, obj) in enumerate(cache):
        if k == key and s.wrapped.IsSame(shape.wrapped):
            if i:
                cache.insert(0, cache.pop(i))
            return obj
    obj = make()
    cache.insert(0, (key, shape, obj))
    del cache[8:]
    return obj


def _classifier(shape: bd.Shape):
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    return _cached("classifiers", shape, lambda: BRepClass3d_SolidClassifier(shape.wrapped))


def _intersector(shape: bd.Shape):
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

    def make():
        si = IntCurvesFace_ShapeIntersector()
        si.Load(shape.wrapped, 1e-7)
        return si
    return _cached("intersectors", shape, make)


# three fixed, unrelated directions: a ray through a vertex/edge or along a face is a measure-zero event,
# and all three would have to be fooled the same way for a wrong answer
_RAYS = ((0.31234, 0.72411, 0.61493), (-0.66851, 0.21709, -0.71132), (0.54627, -0.79311, 0.26941))


def _ray_parity(shape: bd.Shape, p) -> bool | None:
    """Inside test by counting surface crossings along three rays; None when any ray is in doubt
    (grazes an edge, touches tangentially, or the point is on/near the surface) or the rays disagree."""
    from OCP.IntCurveSurface import IntCurveSurface_Tangent
    from OCP.TopAbs import TopAbs_ON
    from OCP.gp import gp_Dir, gp_Lin
    si = _intersector(shape)
    answer = None
    for d in _RAYS:
        si.Perform(gp_Lin(p, gp_Dir(*d)), -1e-3, 1e100)
        if not si.IsDone():
            return None
        crossings = 0
        for i in range(1, si.NbPnt() + 1):
            w = si.WParameter(i)
            if abs(w) < 1e-4 or si.State(i) == TopAbs_ON or si.Transition(i) == IntCurveSurface_Tangent:
                return None
            crossings += w > 0
        inside = crossings % 2 == 1
        if answer is None:
            answer = inside
        elif inside != answer:
            return None
    return answer


def point_inside(shape: bd.Shape, point: Vector | Sequence[float], tol: float = 1e-6) -> bool:
    """True if the point is inside (or on) the shape. A solid is tested by ray parity first (fast and
    steady); OCCT's classifier, which can spend 0.1-0.3 s re-aiming its ray on some points, decides
    compounds and every point the rays are unsure of."""
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON
    from OCP.gp import gp_Pnt
    p = point if isinstance(point, Vector) else Vector(*point)
    q = gp_Pnt(p.X, p.Y, p.Z)
    if isinstance(shape, bd.Solid) and tol <= 1e-4:
        try:
            fast = _ray_parity(shape, q)
        except Exception:
            fast = None
        if fast is not None:
            return fast
    cl = _classifier(shape)
    cl.Perform(q, tol)
    return cl.State() in (TopAbs_IN, TopAbs_ON)


def faces_with_normal(shape: bd.Shape, direction: Sequence[float] | Vector, tol_deg: float = 1.0) -> list[bd.Face]:
    """Planar faces whose outward normal points along `direction`."""
    d = (direction if isinstance(direction, Vector) else Vector(*direction)).normalized()
    out = []
    for f in shape.faces():
        if f.geom_type != bd.GeomType.PLANE:
            continue
        try:
            n = f.normal_at()
        except Exception:
            continue
        if n.get_angle(d) <= tol_deg:
            out.append(f)
    return out


def planar_faces_at(shape: bd.Shape, axis: str, value: float, tol: float = 1e-3) -> list[bd.Face]:
    """Planar faces perpendicular to `axis` ("X"/"Y"/"Z") lying at coordinate `value`."""
    i = "XYZ".index(axis.upper())
    out = []
    for f in shape.faces():
        if f.geom_type != bd.GeomType.PLANE:
            continue
        try:
            n = f.normal_at()
            c = f.center()
        except Exception:
            continue
        if abs(abs(tuple(n)[i]) - 1) < 1e-6 and abs(tuple(c)[i] - value) <= tol:
            out.append(f)
    return out


def mass_grams(shape: bd.Shape, material_or_density: str | float = "pla") -> float:
    """Mass in grams from mm^3 volume and a density in g/cm^3 (or a material name)."""
    rho = DENSITY[material_or_density.lower()] if isinstance(material_or_density, str) else float(material_or_density)
    return abs(signed_volume(shape)) / 1000.0 * rho


def mass_properties(shape: bd.Shape, material_or_density: str | float | None = None) -> dict:
    """Mass properties: volume (mm^3), centre of mass, the inertia tensor about the centre of mass and the
    principal moments (g*mm^2 when a density/material is given, else per unit density in mm^5)."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    rho = 1.0
    if material_or_density is not None:
        rho = DENSITY[material_or_density.lower()] / 1000.0 if isinstance(material_or_density, str) else float(material_or_density) / 1000.0   # g/mm^3
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props)
    vol = props.Mass()
    com = props.CentreOfMass()
    m = props.MatrixOfInertia()
    tensor = [[m.Value(i, j) * rho for j in (1, 2, 3)] for i in (1, 2, 3)]
    try:
        pm = props.PrincipalProperties().Moments()
        principal = sorted([pm[0] * rho, pm[1] * rho, pm[2] * rho], reverse=True)
    except Exception:
        principal = [tensor[0][0], tensor[1][1], tensor[2][2]]
    return {"volume": round(abs(vol), 3), "mass_g": round(abs(vol) * rho, 3) if material_or_density is not None else None,
            "center_of_mass": [round(com.X(), 3), round(com.Y(), 3), round(com.Z(), 3)],
            "inertia": [[round(x, 3) for x in row] for row in tensor], "principal": [round(x, 3) for x in principal],
            "units": "g*mm^2" if material_or_density is not None else "mm^5 (unit density)"}
