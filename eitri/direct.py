"""Direct geometry edits of a B-rep: grab vertices, edges or faces and move them anywhere;
delete faces and heal. The solid equivalents of Fusion's Edit Form / Move Face / Delete.

- move_vertices: the faces around the moved vertices are rebuilt. Flat faces with straight
  edges stay flat when they can; a face whose corners no longer lie in one plane becomes a
  smooth patch through its (moved) edges, so a box corner can be pulled up and the box
  simply stops being a box.
- move_face: translate and/or tilt a flat face freely; the neighbouring faces extend or
  trim to meet it (what Fusion's Move Face does). Round faces translate (holes, bosses,
  spherical bumps/dimples).
- delete_faces: remove faces and heal the body (OCCT defeaturing): fillets, holes,
  bosses, pockets, chamfers.
"""
from __future__ import annotations

import math
from typing import Sequence

import build123d as bd
from build123d import Vector

TOL = 1e-4


def _vec(x) -> Vector:
    return x if isinstance(x, Vector) else Vector(*[float(c) for c in x])


def rotate_vector(v: Vector, axis: Vector, angle_deg: float) -> Vector:
    """Rodrigues rotation of v about a unit axis."""
    k = axis.normalized()
    a = math.radians(angle_deg)
    return v * math.cos(a) + k.cross(v) * math.sin(a) + k * (k.dot(v) * (1 - math.cos(a)))


# ---------------------------------------------------------------------------- vertices

def unique_vertices(shape: bd.Shape) -> list[Vector]:
    """Distinct vertex positions of a shape (seams and shared corners counted once)."""
    seen: dict[tuple, Vector] = {}
    for v in shape.vertices():
        p = Vector(v.X, v.Y, v.Z)
        seen.setdefault((round(p.X, 5), round(p.Y, 5), round(p.Z, 5)), p)
    return list(seen.values())


def nearest_vertex(shape: bd.Shape, point, tol: float = 0.5) -> Vector | None:
    p = _vec(point)
    best, best_d = None, tol
    for v in unique_vertices(shape):
        d = (v - p).length
        if d <= best_d:
            best, best_d = v, d
    return best


def _wire_points(wire: bd.Wire, face: bd.Face) -> list[Vector]:
    """The vertices of a wire in traversal order (oriented by the face)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools_WireExplorer
    exp = BRepTools_WireExplorer(wire.wrapped, face.wrapped)
    pts: list[Vector] = []
    while exp.More():
        p = BRep_Tool.Pnt_s(exp.CurrentVertex())
        pts.append(Vector(p.X(), p.Y(), p.Z()))
        exp.Next()
    return pts


def _poly_normal(pts: Sequence[Vector]) -> Vector:
    """Newell's method: a robust normal for a (possibly non-convex) polygon."""
    n = Vector(0, 0, 0)
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)]
        n = n + Vector((p.Y - q.Y) * (p.Z + q.Z), (p.Z - q.Z) * (p.X + q.X), (p.X - q.X) * (p.Y + q.Y))
    return n.normalized() if n.length > 1e-12 else Vector(0, 0, 1)


def _polygon_wire(pts: Sequence[Vector]):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt
    poly = BRepBuilderAPI_MakePolygon()
    for p in pts:
        poly.Add(gp_Pnt(p.X, p.Y, p.Z))
    poly.Close()
    if not poly.IsDone():
        raise ValueError("could not rebuild a face outline from the moved points")
    return poly.Wire()


def _ordered_edges(wire: bd.Wire, face: bd.Face):
    """(edge, start, end) along a wire in traversal order (oriented by the face)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools_WireExplorer
    exp = BRepTools_WireExplorer(wire.wrapped, face.wrapped)
    edges, starts = [], []
    while exp.More():
        p = BRep_Tool.Pnt_s(exp.CurrentVertex())
        edges.append(exp.Current())
        starts.append(Vector(p.X(), p.Y(), p.Z()))
        exp.Next()
    return [(e, starts[i], starts[(i + 1) % len(starts)]) for i, e in enumerate(edges)]


def _rebuild_face(face: bd.Face, hit, d: Vector):
    """The face with its moved corners: edges whose ends did not move are kept exactly (arcs
    included); straight edges with a moved end become new lines. Flat when the corners stay
    coplanar (inner wires such as holes are kept), else a filled patch over a straight outline."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.ShapeFix import ShapeFix_Face
    from OCP.gp import gp_Pnt
    outer = _ordered_edges(face.outer_wire(), face)
    inner_ws = list(face.inner_wires())
    for w in inner_ws:
        if any(hit(p) for p in _wire_points(w, face)):
            raise ValueError("that point belongs to a hole or pocket outline; move the hole instead")
    moved = lambda p: (p + d) if hit(p) else p
    new_pts = [moved(s) for _, s, _ in outer]
    if len(new_pts) < 3:
        raise ValueError("a face would collapse")
    n = _poly_normal(new_pts)
    inner_pts = [p for w in inner_ws for p in _wire_points(w, face)]
    planar = all(abs((p - new_pts[0]).dot(n)) < 1e-6 for p in new_pts + inner_pts)
    if planar:
        mw = BRepBuilderAPI_MakeWire()
        for e, s, t in outer:
            if not hit(s) and not hit(t):
                mw.Add(e)
                continue
            if bd.Edge(e).geom_type != bd.GeomType.LINE:
                raise ValueError("a curved edge ends at that corner; move the face instead")
            a, b = moved(s), moved(t)
            mw.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(a.X, a.Y, a.Z), gp_Pnt(b.X, b.Y, b.Z)).Edge())
        if not mw.IsDone():
            raise ValueError("could not rebuild the face outline from the moved points")
        mk = BRepBuilderAPI_MakeFace(mw.Wire(), True)
        for w in inner_ws:
            mk.Add(w.wrapped)
        fix = ShapeFix_Face(mk.Face())
        fix.Perform()
        return fix.Face()
    if inner_ws:
        raise ValueError("cannot bend a face that has a hole or a pocket in it; move the whole face instead")
    if any(bd.Edge(e).geom_type != bd.GeomType.LINE for e, _, _ in outer):
        raise ValueError("cannot bend a face with a curved outline; move the face instead")
    from OCP.BRepFill import BRepFill_Filling
    from OCP.GeomAbs import GeomAbs_C0
    fill = BRepFill_Filling()
    for e in bd.Wire(_polygon_wire(new_pts)).edges():
        fill.Add(e.wrapped, GeomAbs_C0)
    fill.Build()
    if not fill.IsDone():
        raise ValueError("could not fit a surface through the moved edges")
    return fill.Face()


def solid_from_faces(faces) -> bd.Solid:
    """Sew faces into one closed, outward-oriented solid."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
    from OCP.BRepLib import BRepLib
    from OCP.ShapeFix import ShapeFix_Shell
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    sew = BRepBuilderAPI_Sewing(TOL)
    for f in faces:
        sew.Add(f)
    sew.Perform()
    sewn = sew.SewedShape()
    if sewn.ShapeType() == TopAbs_SHELL:
        shell = TopoDS.Shell(sewn)
    else:
        shells = []
        exp = TopExp_Explorer(sewn, TopAbs_SHELL)
        while exp.More():
            shells.append(TopoDS.Shell(exp.Current()))
            exp.Next()
        if len(shells) != 1:
            raise ValueError("the moved faces no longer close into one body")
        shell = shells[0]
    fix = ShapeFix_Shell()
    fix.Init(shell)
    fix.Perform()
    shell = fix.Shell()
    solid = BRepBuilderAPI_MakeSolid(shell).Solid()
    BRepLib.OrientClosedSolid_s(solid)
    out = bd.Solid(solid)
    if not out.is_valid or out.volume <= 1e-9:
        raise ValueError("the result is not a closed solid")
    return out


def move_vertices(shape: bd.Shape, points: Sequence, delta, tol: float = 1e-3) -> bd.Shape:
    """Move the vertices at `points` by `delta`, rebuilding the faces around them.

    Only flat faces with straight edges can be touched (the prism/box/wedge family):
    a touched face stays flat if its corners remain coplanar, otherwise it becomes a
    smooth patch through its edges. Untouched faces are kept exactly.
    """
    d = _vec(delta)
    targets = [_vec(p) for p in points]
    if not targets:
        raise ValueError("no points to move")
    if d.length < 1e-9:
        return shape

    def hit(p: Vector) -> bool:
        return any((p - t).length <= tol for t in targets)

    solids_out: list[bd.Shape] = []
    touched_total = 0
    for solid in shape.solids():
        faces_out = []
        touched = 0
        for f in solid.faces():
            if not any(hit(Vector(v.X, v.Y, v.Z)) for v in f.vertices()):
                faces_out.append(f.wrapped)
                continue
            touched += 1
            if f.geom_type != bd.GeomType.PLANE:
                raise ValueError("a face next to the selected point is curved; move the face instead, or use push/pull")
            faces_out.append(_rebuild_face(f, hit, d))
        if touched:
            solids_out.append(solid_from_faces(faces_out))
        else:
            solids_out.append(solid)
        touched_total += touched
    if not touched_total:
        raise ValueError("none of the selected points are corners of this part")
    if len(solids_out) == 1:
        return solids_out[0]
    return bd.Part(bd.Compound(children=solids_out).wrapped)


# ---------------------------------------------------------------------------- faces

def face_centre(face: bd.Face) -> Vector:
    """The centre of a flat face's outline (the centroid drifts away from holes)."""
    if face.geom_type == bd.GeomType.PLANE:
        n = face.normal_at()
        c = face.bounding_box().center()
        return c - n * (c - face.center()).dot(n)
    return face.center()


def _split_keep(solid, plane: bd.Plane, keep: str):
    try:
        r = bd.split(solid, bisect_by=plane, keep=bd.Keep.TOP if keep == "top" else bd.Keep.BOTTOM)
    except Exception:
        return None
    return r if (r is not None and r.solids()) else None


def move_face(shape: bd.Shape, face: bd.Face, delta=(0, 0, 0), rotate: dict | None = None) -> bd.Shape:
    """Move a face of `shape` by `delta` and optionally tilt it: rotate = {"axis": [..],
    "angle": degrees, "about": [x, y, z]} (about defaults to the moved face centre).
    Flat faces: the neighbouring faces extend/trim to meet the new plane. Round faces
    translate (a hole moves, a boss moves, a spherical bump moves)."""
    d = _vec(delta)
    kind = face.geom_type
    if kind == bd.GeomType.PLANE:
        n = face.normal_at()
        c = face_centre(face)
        n_new, c_new = n, c + d
        if rotate and abs(float(rotate.get("angle", 0))) > 1e-9:
            axis = _vec(rotate["axis"])
            ang = float(rotate["angle"])
            about = _vec(rotate["about"]) if rotate.get("about") else c_new
            n_new = rotate_vector(n, axis, ang).normalized()
            c_new = about + rotate_vector(c_new - about, axis, ang)
        if (n_new - n).length < 1e-9 and d.length < 1e-9:
            return shape
        p_old = bd.Plane(origin=c, z_dir=n)
        p_new = bd.Plane(origin=c_new, z_dir=n_new)
        length = max(shape.bounding_box().diagonal * 2, 10.0)
        column = bd.extrude(face, amount=length) + bd.extrude(face, amount=length, dir=(-n.X, -n.Y, -n.Z))
        add = _split_keep(column, p_old, "top")
        add = _split_keep(add, p_new, "bottom") if add is not None else None
        rem = _split_keep(column, p_old, "bottom")
        rem = _split_keep(rem, p_new, "top") if rem is not None else None
        new = shape
        if add is not None:
            new = new + add
        if rem is not None:
            new = new - rem
        if not new.is_valid or not new.solids():
            raise ValueError("moving the face there does not leave a valid solid")
        return new
    if kind == bd.GeomType.CYLINDER:
        from .edits import _cylinder_extent, _is_concave
        r, ax, start, length, full = _cylinder_extent(face)
        if not full:
            raise ValueError("only full-round holes and bosses can be moved; use push/pull on partial rounds")
        dv = d - ax * d.dot(ax)                      # across the axis only
        if dv.length < 1e-9:
            return shape
        centre = start + ax * (length / 2)
        frame = bd.Plane(origin=(centre.X, centre.Y, centre.Z), z_dir=(ax.X, ax.Y, ax.Z))
        if _is_concave(face):                        # a hole: fill it, cut it again elsewhere
            filler = frame * bd.Cylinder(r + 0.001, length)
            cutter = bd.Pos(dv.X, dv.Y, dv.Z) * (frame * bd.Cylinder(r, length + 0.2))
            return (shape + filler) - cutter
        boss = frame * bd.Cylinder(r, length)         # a boss: take it off, put it back elsewhere
        return (shape - boss) + (bd.Pos(dv.X, dv.Y, dv.Z) * boss)
    if kind == bd.GeomType.SPHERE:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        sph = BRepAdaptor_Surface(face.wrapped).Sphere()
        r = sph.Radius()
        c = sph.Location()
        centre = Vector(c.X(), c.Y(), c.Z())
        pt = face.position_at(0.5, 0.5)
        convex = face.normal_at(pt).dot(pt - centre) > 0
        ball = bd.Pos(centre.X, centre.Y, centre.Z) * bd.Sphere(r)
        moved = bd.Pos(centre.X + d.X, centre.Y + d.Y, centre.Z + d.Z) * bd.Sphere(r)
        if convex:
            return (shape - ball) + moved
        return (shape + bd.Pos(centre.X, centre.Y, centre.Z) * bd.Sphere(r + 0.001)) - moved
    raise ValueError(f"a {kind.name.lower()} face cannot be moved directly; describe the change in the chat")


def delete_faces(shape: bd.Shape, faces: Sequence[bd.Face]) -> bd.Shape:
    """Remove faces and heal the body (fillets, holes, bosses, pockets, chamfers)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
    if not faces:
        raise ValueError("no faces to remove")
    def run(fs):
        op = BRepAlgoAPI_Defeaturing()
        op.SetShape(shape.wrapped)
        for f in fs:
            op.AddFaceToRemove(f.wrapped)
        op.SetRunParallel(False)
        op.Build()
        if not op.IsDone():
            return None
        res = op.Shape()
        out = bd.Solid(res) if res.ShapeType() == 2 else bd.Part(res)
        if not out.is_valid or not out.solids():
            return None
        if len(out.faces()) == len(shape.faces()) and abs(abs(out.volume) - abs(shape.volume)) < 1e-6:
            return None
        return out

    faces = _whole_cylinders(shape, list(faces))
    out = run(list(faces))
    if out is None:
        # take the captive faces along: a blind hole's floor, a counterbore's step, a boss's top —
        # faces whose every edge belongs to the selected faces
        sel_edges = {hash(e) for f in faces for e in f.edges()}
        extra = [g for g in shape.faces() if all(not g.is_same(f) for f in faces) and g.edges() and all(hash(e) in sel_edges for e in g.edges())]
        if extra:
            out = run(list(faces) + extra)
    if out is None:
        raise ValueError("these faces do not form a feature that can be removed (select every face of the feature: a hole's wall, a boss's side and top)")
    if _shell_count(out) > _shell_count(shape):
        raise ValueError("removing these faces would leave a sealed cavity inside the part; select every face of the feature")
    return out


def _shell_count(shape: bd.Shape) -> int:
    return sum(len(s.shells()) for s in shape.solids())


def _whole_cylinders(shape: bd.Shape, faces: list) -> list:
    """Add the other pieces of the same cylinder (a hole wall is often two faces, or gets split
    by a later cut) so a feature is removed whole."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    def cyl(f):
        if f.geom_type != bd.GeomType.CYLINDER:
            return None
        c = BRepAdaptor_Surface(f.wrapped).Cylinder()
        ax = c.Axis()
        return (Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z()), Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()).normalized(), c.Radius())
    wanted = [cyl(f) for f in faces]
    wanted = [w for w in wanted if w]
    out = list(faces)
    for g in shape.faces():
        if any(g.is_same(f) for f in out):
            continue
        c = cyl(g)
        if not c:
            continue
        for loc, d, r in wanted:
            if abs(c[2] - r) < 1e-6 and abs(abs(c[1].dot(d)) - 1) < 1e-6:
                off = c[0] - loc
                off = off - d * off.dot(d)
                if off.length < 1e-6:
                    out.append(g)
                    break
    return out


# ---------------------------------------------------------------------------- fillets, face splits

def fillet_radius(shape: bd.Shape, face: bd.Face, new_r: float) -> bd.Shape:
    """Change the radius of an existing fillet: remove its faces (defeaturing recovers the
    sharp edge underneath) and fillet that edge again."""
    if face.geom_type != bd.GeomType.CYLINDER:
        raise ValueError("pick the round face of a fillet")
    if new_r <= 0.05:
        raise ValueError("the fillet radius would vanish")
    old_keys = {_edge_key(e) for e in shape.edges()}
    centre = face.center()
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    r = BRepAdaptor_Surface(face.wrapped).Cylinder().Radius()
    sharp = delete_faces(shape, [face])
    reach = 2.5 * r + 1.0
    edges = [e for e in sharp.edges() if _edge_key(e) not in old_keys and (e.position_at(0.5) - centre).length <= reach]
    if not edges:
        raise ValueError("could not find the edge under this fillet")
    out = bd.fillet(edges, new_r)
    if not out.is_valid or not out.solids():
        raise ValueError(f"a {new_r:g} mm fillet does not fit here")
    return out


def _edge_key(e: bd.Edge) -> tuple:
    m = e.position_at(0.5)
    return (round(m.X, 3), round(m.Y, 3), round(m.Z, 3), round(e.length, 3))


def split_face(shape: bd.Shape, face: bd.Face, plane: bd.Plane) -> bd.Shape:
    """Imprint the line where `plane` crosses `face`, splitting the face in two (the solid is
    unchanged) so each half can be pushed, moved or tilted on its own."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepFeat import BRepFeat_SplitShape
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    big = plane * bd.Rectangle(4 * shape.bounding_box().diagonal + 10, 4 * shape.bounding_box().diagonal + 10)
    sec = BRepAlgoAPI_Section(face.wrapped, big.faces()[0].wrapped)
    sec.Build()
    if not sec.IsDone():
        raise ValueError("the plane does not cross this face")
    splitter = BRepFeat_SplitShape(shape.wrapped)
    n = 0
    exp = TopExp_Explorer(sec.Shape(), TopAbs_EDGE)
    while exp.More():
        splitter.Add(TopoDS.Edge(exp.Current()), face.wrapped)
        n += 1
        exp.Next()
    if n == 0:
        raise ValueError("the plane does not cross this face")
    splitter.Build()
    res = splitter.Shape()
    out = bd.Solid(res) if res.ShapeType() == 2 else bd.Part(res)
    if not out.is_valid or len(out.faces()) <= len(shape.faces()):
        raise ValueError("could not split the face there")
    return out


def _adjacent_face(shape: bd.Shape, face: bd.Face, edge: bd.Edge) -> bd.Face | None:
    for g in shape.faces():
        if g.is_same(face):
            continue
        if any(e.is_same(edge) for e in g.edges()):
            return g
    return None


def _corner_region(frame: bd.Plane, length: float, reach: float, f1: bd.Face, f2: bd.Face, plane: bd.Plane):
    """The material in the corner between faces f1 and f2 that lies beyond `plane`."""
    big = frame * bd.Box(length, reach, reach)
    r = _split_keep(big, bd.Plane(origin=f1.center(), z_dir=f1.normal_at()), "bottom")
    r = _split_keep(r, bd.Plane(origin=f2.center(), z_dir=f2.normal_at()), "bottom") if r is not None else None
    r = _split_keep(r, plane, "top") if r is not None else None
    return r


def chamfer_size(shape: bd.Shape, face: bd.Face, new_size: float) -> bd.Shape:
    """Change the size of an existing chamfer (equal legs): put the sharp corner back, then
    cut the corner at the new size. The two faces beside the chamfer grow or shrink with it."""
    if face.geom_type != bd.GeomType.PLANE:
        raise ValueError("pick the flat face of a chamfer")
    if new_size <= 0.05:
        raise ValueError("the chamfer size would vanish")
    edges = sorted(face.edges(), key=lambda e: -e.length)
    if len(edges) < 2:
        raise ValueError("this does not look like a chamfer face")
    e1, e2 = edges[0], edges[1]
    f1, f2 = _adjacent_face(shape, face, e1), _adjacent_face(shape, face, e2)
    if f1 is None or f2 is None or f1.geom_type != bd.GeomType.PLANE or f2.geom_type != bd.GeomType.PLANE:
        raise ValueError("a chamfer between two flat faces is needed")
    n, n1, n2 = face.normal_at(), f1.normal_at(), f2.normal_at()
    width = min(e.length for e in face.edges())
    old_size = width / math.sqrt(2)
    d = n1.cross(n2)
    if d.length < 1e-9:
        raise ValueError("the faces beside this chamfer are parallel")
    d = d.normalized()
    m = face_centre(face)
    # the corner line: where the planes of f1 and f2 meet, at the chamfer's mid-length
    import numpy as np
    A = np.array([[n1.X, n1.Y, n1.Z], [n2.X, n2.Y, n2.Z], [d.X, d.Y, d.Z]])
    b = np.array([n1.dot(f1.center()), n2.dot(f2.center()), d.dot(m)])
    c0 = Vector(*np.linalg.solve(A, b))
    t_old = (c0 - m).dot(n)                              # distance from the corner to the old plane
    if t_old <= 1e-6:
        raise ValueError("this face is not a chamfer of a convex corner")
    t_new = t_old * new_size / old_size
    frame = bd.Plane(origin=c0, x_dir=d, z_dir=n)
    length = e1.length + 2.0
    reach = 4 * max(old_size, new_size) + 2.0
    fill = _corner_region(frame, length, reach, f1, f2, bd.Plane(origin=m, z_dir=n))
    cut = _corner_region(frame, length, reach, f1, f2, bd.Plane(origin=c0 - n * t_new, z_dir=n))
    out = shape
    if fill is not None:
        out = out + fill
    if cut is not None:
        out = out - cut
    if not out.is_valid or not out.solids():
        raise ValueError(f"a {new_size:g} mm chamfer does not fit here")
    return out


def sweep_profile(sketch, path_edges: Sequence[bd.Edge]) -> bd.Shape:
    """Sweep a (located) 2D profile along connected edges (Fusion's Sweep). The profile
    should sit at the start of the path, perpendicular to it, as drawn in the viewer."""
    if not path_edges:
        raise ValueError("pick the edges of the path")
    wires = bd.Wire.combine(list(path_edges))
    if len(wires) != 1:
        raise ValueError("the path edges must connect end to end")
    wire = wires[0]
    centre = sketch.center()
    if (centre - wire.position_at(1)).length < (centre - wire.position_at(0)).length and not wire.is_closed:
        from OCP.TopoDS import TopoDS
        wire = bd.Wire(TopoDS.Wire(wire.wrapped.Reversed()))   # the path starts where the profile was drawn
    tangent = wire.tangent_at(0)
    normal = sketch.faces()[0].normal_at()
    if abs(normal.dot(tangent)) < 0.3:
        raise ValueError("the profile lies along the path; sketch it on a face the path leaves at a right angle")
    out = bd.sweep(sketch, path=wire, transition=bd.Transition.ROUND)
    if not out.is_valid or not out.solids():
        raise ValueError("the profile did not sweep to a valid solid (keep it small compared to the path's bends)")
    return out
