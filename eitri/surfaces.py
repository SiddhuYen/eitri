"""Surface (sheet-body) modelling: planar / extruded / revolved / swept / lofted surfaces from sketches,
offset, extend, trim, split, stitch, patch, thicken, to-solid, and a continuity report. A surface body
is a Part whose shape is a Face or Shell (no solids). Used by eitri.edits and by model code."""
from __future__ import annotations

import math
from typing import Sequence

import build123d as bd
from build123d import Vector
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.BRepLib import BRepLib
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_C0, GeomAbs_G1, GeomAbs_Shape
from OCP.ShapeAnalysis import ShapeAnalysis_Surface
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopoDS import TopoDS


def _xyz(v) -> tuple[float, float, float]:
    return (float(v.X), float(v.Y), float(v.Z))


def is_sheet(shape: bd.Shape) -> bool:
    """A surface body: faces but no solids."""
    try:
        return len(shape.solids()) == 0 and len(shape.faces()) > 0
    except Exception:
        return False


def wrap(ocp_shape) -> bd.Shape:
    kind = ocp_shape.ShapeType()
    if kind == TopAbs_ShapeEnum.TopAbs_SOLID:
        return bd.Solid(TopoDS.Solid(ocp_shape))
    if kind == TopAbs_ShapeEnum.TopAbs_SHELL:
        return bd.Shell(TopoDS.Shell(ocp_shape))
    if kind == TopAbs_ShapeEnum.TopAbs_FACE:
        return bd.Face(TopoDS.Face(ocp_shape))
    comp = bd.Compound(ocp_shape)
    sol = comp.solids()
    if len(sol) == 1:
        return sol[0]
    sh = comp.shells()
    if len(sh) == 1 and not sol:
        return sh[0]
    fs = comp.faces()
    if len(fs) == 1 and not sh and not sol:
        return fs[0]
    return comp


def sew(faces: Sequence[bd.Face], tolerance: float = 1e-3, solid_if_closed: bool = True) -> bd.Shape:
    """Stitch faces into a shell (one shell when they connect); a closed shell becomes a solid."""
    faces = list(faces)
    if not faces:
        raise ValueError("nothing to stitch")
    s = BRepBuilderAPI_Sewing(float(tolerance))
    for f in faces:
        s.Add(f.wrapped)
    s.Perform()
    out = wrap(s.SewedShape())
    if solid_if_closed:
        shells = out.shells() if not isinstance(out, bd.Shell) else [out]
        if len(shells) == 1 and BRep_Tool.IsClosed_s(shells[0].wrapped):
            sol = bd.Solid(BRepBuilderAPI_MakeSolid(TopoDS.Shell(shells[0].wrapped)).Solid())
            if sol.volume < 0:
                sol = bd.Solid(TopoDS.Solid(sol.wrapped.Reversed()))
            return sol
    return out


def free_edges(shape: bd.Shape) -> list[bd.Edge]:
    """Boundary edges (owned by exactly one face)."""
    faces = shape.faces()
    out = []
    for e in shape.edges():
        owners = sum(1 for f in faces if any(x.is_same(e) for x in f.edges()))
        if owners == 1:
            out.append(e)
    return out


# ---------------------------------------------------------------------------- surfaces from sketches

def sketch_geometry(profile: dict) -> tuple[list[bd.Face], list[bd.Wire]]:
    """The closed faces and open chains of a profile (sketch-plane 2D coordinates)."""
    from . import sketch as S
    sk = profile if profile.get("type") == "sketch" else S.from_simple(profile)
    if sk.get("constraints") or sk.get("dims"):
        st = S.solve(sk)
        if not st["ok"]:
            raise ValueError("the sketch's constraints conflict: " + ", ".join(st["conflicts"][:4]))
    return S.profile_faces(sk)


def sketch_wires(profile: dict, closed_outlines: bool = True) -> list[bd.Wire]:
    """Open chains, plus the outlines of closed profiles (outer and inner wires) when asked."""
    faces, chains = sketch_geometry(profile)
    wires = list(chains)
    if closed_outlines:
        for f in faces:
            wires.append(f.outer_wire())
            wires += list(f.inner_wires())
    if not wires:
        raise ValueError("the sketch has no curves")
    return wires


def planar_surface(frame: bd.Plane, profile: dict) -> bd.Shape:
    faces, chains = sketch_geometry(profile)
    if not faces:
        raise ValueError("a planar surface needs a closed profile")
    located = [frame * f for f in faces]
    return located[0] if len(located) == 1 else sew(located, solid_if_closed=False)


def extrude_surface(frame: bd.Plane, profile: dict, distance: float, symmetric: bool = False, flip: bool = False) -> bd.Shape:
    d = abs(float(distance))
    if d <= 0:
        raise ValueError("the extrude distance must be positive")
    n = frame.z_dir * (-1 if flip else 1)
    faces = []
    for w in sketch_wires(profile):
        w3 = frame * w
        if symmetric:
            w3 = w3.moved(bd.Location(_xyz(n * -d)))
        faces += list(bd.Shell.extrude(w3, n * (2 * d if symmetric else d)).faces())
    return sew(faces, solid_if_closed=False)


def revolve_surface(frame: bd.Plane, profile: dict, axis_a: Vector, axis_b: Vector, angle: float) -> bd.Shape:
    axis = bd.Axis(axis_a, axis_b - axis_a)
    faces = []
    for w in sketch_wires(profile):
        for e in (frame * w).edges():
            faces.append(bd.Face.revolve(e, float(angle), axis))
    return sew(faces, solid_if_closed=False)


def sweep_surface(frame: bd.Plane, profile: dict, path: bd.Wire) -> bd.Shape:
    faces = []
    for w in sketch_wires(profile):
        faces += list(bd.Shell.sweep(frame * w, path).faces())
    return sew(faces, solid_if_closed=False)


def loft_surface(frame: bd.Plane, sections: Sequence[dict], base_profile: dict | None, ruled: bool = False) -> bd.Shape:
    wires = []
    for sec in sections:
        pr = sec.get("profile") or base_profile
        if not pr:
            raise ValueError("a loft section needs a profile")
        ws = sketch_wires(pr)
        if len(ws) != 1:
            raise ValueError("each loft section must be a single chain or outline")
        w = ws[0]
        if sec.get("scale") not in (None, "", 1, 1.0):
            w = w.scale(float(sec["scale"]))
        if sec.get("rotate"):
            w = bd.Rot(0, 0, float(sec["rotate"])) * w
        wires.append(frame.offset(float(sec.get("offset", 0))) * w)
    if len(wires) < 2:
        raise ValueError("a loft needs at least two sections")
    return bd.Shell.make_loft(wires, ruled=ruled)


# ---------------------------------------------------------------------------- editing surfaces

def offset_surface(shape: bd.Shape, distance: float) -> bd.Shape:
    from OCP.BRepOffset import BRepOffset_Skin
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
    from OCP.GeomAbs import GeomAbs_Arc
    mk = BRepOffsetAPI_MakeOffsetShape()
    mk.PerformByJoin(shape.wrapped, float(distance), 1e-4, BRepOffset_Skin, False, False, GeomAbs_Arc)
    out = wrap(mk.Shape())
    if not out.faces():
        raise ValueError("the surface could not be offset by that distance")
    return out


def _uv_of(face: bd.Face, p: Vector) -> tuple[float, float]:
    sas = ShapeAnalysis_Surface(BRep_Tool.Surface_s(face.wrapped))
    uv = sas.ValueOfUV(bd.Vector(p).to_pnt(), 1e-6)
    return uv.X(), uv.Y()


def _uv_bounds(face: bd.Face) -> tuple[float, float, float, float]:
    return BRepTools.UVBounds_s(face.wrapped)


def extend_surface(shape: bd.Shape, distance: float, edges: Sequence[bd.Edge] | None = None, mode: str = "natural") -> bd.Shape:
    """Extend a surface beyond its boundary edges (all free edges when none are given). 'natural'
    continues each face's own surface; 'tangent' adds ruled strips along the boundary."""
    d = float(distance)
    if d <= 0:
        raise ValueError("the extension must be positive")
    faces = list(shape.faces())
    targets = list(edges) if edges else free_edges(shape)
    if not targets:
        raise ValueError("the surface has no boundary edge to extend")
    owner = {}
    for e in targets:
        fs = [f for f in faces if any(x.is_same(e) for x in f.edges())]
        if not fs:
            fs = [f for f in faces if e.distance_to(f) < 1e-6]
        if not fs:
            raise ValueError("an edge to extend is not on the surface")
        owner.setdefault(id(fs[0]), (fs[0], []))[1].append(e)
    new_faces = [f for f in faces if id(f) not in owner]
    for fid, (f, es) in owner.items():
        if mode == "natural":
            umin, umax, vmin, vmax = _uv_bounds(f)
            flags = [False, False, False, False]
            for e in es:
                u, v = _uv_of(f, e.position_at(0.5))
                du, dv = umax - umin, vmax - vmin
                cands = [(abs(u - umin) / (du or 1), 0), (abs(u - umax) / (du or 1), 1), (abs(v - vmin) / (dv or 1), 2), (abs(v - vmax) / (dv or 1), 3)]
                cands.sort()
                flags[cands[0][1]] = True
            copy = TopoDS.Face(BRepBuilderAPI_Copy(f.wrapped).Shape())
            out = TopoDS.Face(BRepBuilderAPI_Copy(f.wrapped).Shape())
            BRepLib.ExtendFace_s(copy, d, flags[0], flags[1], flags[2], flags[3], out)
            nf = bd.Face(out)
            if nf.area <= f.area + 1e-9:
                raise ValueError("the kernel could not extend that face naturally; try the tangent mode")
            new_faces.append(nf)
        else:
            new_faces.append(f)
            for e in es:
                mid = e.position_at(0.5)
                n = f.normal_at(mid)
                t = e.tangent_at(0.5)
                out_dir = n.cross(t).normalized()
                probe = mid + out_dir * (d * 0.05)
                if f.distance_to(probe) < 1e-6:            # that way is into the face: flip
                    out_dir = -out_dir
                far = e.moved(bd.Location(_xyz(out_dir * d)))
                new_faces.append(bd.Face.make_surface_from_curves(e, far))
    return sew(new_faces, solid_if_closed=False)


def trim_surface(shape: bd.Shape, tool, keep: str) -> bd.Shape:
    """Trim by a plane or face (keep top|bottom of it) or by a solid (keep inside|outside)."""
    if isinstance(tool, (bd.Plane, bd.Face, bd.Shell)):
        k = bd.Keep.TOP if keep in ("top", "inside", "positive") else bd.Keep.BOTTOM
        fs = []                                          # face by face: split() mishandles shells
        for f in shape.faces():
            fs += [x for x in bd.split(f, bisect_by=tool, keep=k).faces() if x.area > 1e-9]
    else:
        out = (shape & tool) if keep in ("inside", "top") else (shape - tool)
        fs = [f for f in out.faces() if f.area > 1e-9]
    if not fs:
        raise ValueError("nothing of the surface is left on that side")
    return sew(fs, solid_if_closed=False) if len(fs) > 1 else fs[0]


def split_surface(shape: bd.Shape, tool) -> tuple[bd.Shape, bd.Shape]:
    a = trim_surface(shape, tool, "top")
    b = trim_surface(shape, tool, "bottom")
    return a, b


def patch(edges: Sequence[bd.Edge], continuity: str = "G0", adjacent: bd.Shape | None = None) -> bd.Face:
    """A surface patch filling a closed loop of edges; 'G1' keeps tangency to the faces of
    `adjacent` that own the edges."""
    from OCP.BRepFill import BRepFill_Filling
    edges = list(edges)
    if len(edges) < 1:
        raise ValueError("pick the edges of the opening")
    wires = bd.Wire.combine(edges, tol=1e-3)
    if len(wires) != 1 or not wires[0].is_closed:
        raise ValueError("the edges must form one closed loop")
    want_g1 = continuity.upper() == "G1" and adjacent is not None
    fill = BRepFill_Filling(3, 15, 2, False, 1e-5, 1e-4, 1e-2, 0.1, 8, 9)
    adj_faces = list(adjacent.faces()) if adjacent is not None else []
    for e in edges:
        owner = next((f for f in adj_faces if any(x.is_same(e) for x in f.edges())), None) if want_g1 else None
        if owner is not None:
            fill.Add(e.wrapped, owner.wrapped, GeomAbs_G1, True)
        else:
            fill.Add(e.wrapped, GeomAbs_C0, True)
    fill.Build()
    if not fill.IsDone():
        raise ValueError("the kernel could not fill that loop")
    face = bd.Face(fill.Face())
    if not face.is_valid or face.area <= 0:
        raise ValueError("the patch is not a valid face")
    return face


def thicken_surface(shape: bd.Shape, thickness: float, both: bool = False) -> bd.Shape:
    t = float(thickness)
    if t == 0:
        raise ValueError("the thickness must not be zero")
    faces = shape.faces()
    if len(faces) == 1:
        out = bd.thicken(faces[0], t, both=both)
    else:
        shell = shape if isinstance(shape, bd.Shell) else sew(faces, solid_if_closed=False)
        if not isinstance(shell, bd.Shell):
            shells = shell.shells()
            if len(shells) != 1:
                raise ValueError("the faces do not form one connected surface; stitch them first")
            shell = shells[0]
        out = bd.Solid.thicken(shell, t) if not both else bd.Solid.thicken(shell.moved(bd.Location((0, 0, 0))), t)
        if both:
            # a symmetric thickening: offset the shell by -t/2 and thicken by t
            back = offset_surface(shell, -t / 2)
            back_shell = back if isinstance(back, bd.Shell) else back.shells()[0]
            out = bd.Solid.thicken(back_shell, t)
    sol = out.solids()
    if not sol or not out.is_valid:
        raise ValueError("thickening did not produce a valid solid (self-intersecting offset?)")
    return sol[0] if len(sol) == 1 else out


def to_solid(shape: bd.Shape, tolerance: float = 1e-3) -> bd.Shape:
    out = sew(shape.faces(), tolerance, solid_if_closed=True)
    if not out.solids():
        n = len(free_edges(out))
        raise ValueError(f"the surface is not closed ({n} free edge(s)); patch the openings first")
    return out.solids()[0]


# ---------------------------------------------------------------------------- continuity

def continuity(shape: bd.Shape) -> dict:
    """Internal edges by continuity between their two faces (G0 = a crease, G1 = tangent,
    G2 = curvature continuous), plus the free (boundary) edge count."""
    faces = shape.faces()
    counts = {"G0": 0, "G1": 0, "G2": 0}
    creases = []
    free = 0
    for e in shape.edges():
        owners = [f for f in faces if any(x.is_same(e) for x in f.edges())]
        if len(owners) < 2:
            free += 1
            continue
        c = BRep_Tool.Continuity_s(e.wrapped, owners[0].wrapped, owners[1].wrapped)
        v = int(c)
        if v == int(GeomAbs_C0):
            counts["G0"] += 1
            if len(creases) < 20:
                creases.append([round(x, 3) for x in _xyz(e.position_at(0.5))])
        elif v in (int(GeomAbs_G1), int(GeomAbs_Shape.GeomAbs_C1)):
            counts["G1"] += 1
        else:
            counts["G2"] += 1
    return {"faces": len(faces), "free_edges": free, **counts, "creases": creases}
