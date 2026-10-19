"""Meshes and exchange formats: STL / OBJ / 3MF / PLY meshes in (as faceted solids, after repair), OBJ and
IGES out, IGES in (surfaces sewn into solids), DXF files as sketches, and mesh repair reports."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

import build123d as bd
from build123d import Vector
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.gp import gp_Pnt
from OCP.ShapeFix import ShapeFix_Solid
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopoDS import TopoDS

MESH_SUFFIXES = (".stl", ".obj", ".3mf", ".ply", ".off", ".glb", ".gltf")
IGES_SUFFIXES = (".igs", ".iges")


# ---------------------------------------------------------------------------- meshes in

def load_mesh(path: str | Path):
    """A trimesh.Trimesh from an STL/OBJ/3MF/PLY file (scenes are flattened into one mesh)."""
    import trimesh
    m = trimesh.load(str(path), force="mesh")
    if isinstance(m, trimesh.Scene):
        m = trimesh.util.concatenate([g for g in m.geometry.values()])
    if not hasattr(m, "faces") or len(m.faces) == 0:
        raise ValueError(f"{Path(path).name} holds no triangles")
    return m


def repair_mesh(mesh) -> dict:
    """Fix what trimesh can: duplicate/degenerate faces, winding and normals, small holes. Returns a report."""
    import trimesh
    before = {"faces": int(len(mesh.faces)), "watertight": bool(mesh.is_watertight), "winding": bool(mesh.is_winding_consistent)}
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    trimesh.repair.fix_normals(mesh)
    if not mesh.is_watertight:
        try:
            trimesh.repair.fill_holes(mesh)                 # single triangle / quad holes
        except Exception:
            pass
    if not mesh.is_watertight:
        _fill_boundary_loops(mesh)                          # larger holes: a fan from each loop's centroid
    trimesh.repair.fix_winding(mesh)
    after = {"faces": int(len(mesh.faces)), "watertight": bool(mesh.is_watertight), "winding": bool(mesh.is_winding_consistent)}
    return {"before": before, "after": after, "volume": float(abs(mesh.volume)) if mesh.is_watertight else None}


def _fill_boundary_loops(mesh, max_loop: int = 400) -> int:
    """Close every boundary loop (open edges forming a cycle) with a triangle fan about its centroid.
    Returns the number of loops closed. Meant for small holes left by damaged files."""
    import numpy as np
    import networkx as nx
    from trimesh.grouping import group_rows
    edges = mesh.edges_sorted
    open_idx = group_rows(edges, require_count=1)
    if len(open_idx) == 0:
        return 0
    g = nx.from_edgelist(edges[open_idx].tolist())
    loops = [c for c in nx.cycle_basis(g) if 3 <= len(c) <= max_loop]
    if not loops:
        return 0
    V = mesh.vertices
    new_v, new_f = [], []
    base = len(V)
    for loop in loops:
        # order the loop by walking the boundary graph
        sub = g.subgraph(loop)
        try:
            cyc = [u for u, _ in nx.find_cycle(sub)]
        except nx.NetworkXNoCycle:
            continue
        centroid = V[cyc].mean(axis=0)
        ci = base + len(new_v)
        new_v.append(centroid)
        for a, b in zip(cyc, cyc[1:] + cyc[:1]):
            new_f.append([a, b, ci])
    if not new_f:
        return 0
    mesh.vertices = np.vstack([V, np.array(new_v)])
    mesh.faces = np.vstack([mesh.faces, np.array(new_f)])
    mesh.merge_vertices()
    import trimesh
    trimesh.repair.fix_normals(mesh)
    return len(loops)


def mesh_to_solid(mesh, tolerance: float = 1e-4, unify: bool = True, max_faces: int = 200_000) -> bd.Solid:
    """A B-rep solid from a watertight triangle mesh: every triangle becomes a planar face, sewn into a
    shell; coplanar triangles merge into one face when `unify` is set (flat STL parts come back as
    clean boxes and cylinders become many narrow facets)."""
    if len(mesh.faces) > max_faces:
        raise ValueError(f"the mesh has {len(mesh.faces)} triangles; simplify it below {max_faces} first")
    V = mesh.vertices
    sew = BRepBuilderAPI_Sewing(float(tolerance))
    for tri in mesh.faces:
        poly = BRepBuilderAPI_MakePolygon(gp_Pnt(*map(float, V[tri[0]])), gp_Pnt(*map(float, V[tri[1]])), gp_Pnt(*map(float, V[tri[2]])), True)
        if not poly.IsDone():
            continue
        sew.Add(BRepBuilderAPI_MakeFace(poly.Wire(), True).Face())
    sew.Perform()
    sh = sew.SewedShape()
    if sh.ShapeType() != TopAbs_ShapeEnum.TopAbs_SHELL:
        shells = bd.Compound(sh).shells()
        if len(shells) != 1:
            raise ValueError(f"the mesh did not sew into one closed shell ({len(shells)} shells); repair it first")
        sh = shells[0].wrapped
    solid = BRepBuilderAPI_MakeSolid(TopoDS.Shell(sh)).Solid()
    fx = ShapeFix_Solid(solid)
    fx.Perform()
    out = bd.Solid(TopoDS.Solid(fx.Solid()))
    if out.volume < 0:
        out = bd.Solid(TopoDS.Solid(out.wrapped.Reversed()))
    if unify:
        u = ShapeUpgrade_UnifySameDomain(out.wrapped, True, True, True)
        u.Build()
        merged = u.Shape()
        if merged.ShapeType() == TopAbs_ShapeEnum.TopAbs_SOLID:
            cand = bd.Solid(TopoDS.Solid(merged))
            if cand.is_valid and abs(abs(cand.volume) - abs(out.volume)) < 1e-6 * max(1.0, abs(out.volume)) + 1e-3:
                out = cand
    if not out.is_valid or abs(out.volume) < 1e-9:
        raise ValueError("the mesh did not make a valid solid")
    return out


AUTO_DECIMATE_ABOVE = 200_000       # meshes with more triangles are decimated to AUTO_DECIMATE_TO before conversion
AUTO_DECIMATE_TO = 100_000


def decimate(mesh, faces: int):
    """Quadric-error decimation to about `faces` triangles (fast_simplification through trimesh), then a
    repair pass so the result stays watertight."""
    faces = int(faces)
    if faces <= 0 or len(mesh.faces) <= faces:
        return mesh
    out = mesh.simplify_quadric_decimation(face_count=faces)
    repair_mesh(out)
    return out


def import_mesh(path: str | Path, tolerance: float = 1e-4, unify: bool = True, decimate_to: int | None = None) -> tuple[bd.Solid, dict]:
    """A solid from a mesh file, after repair (and decimation to `decimate_to` triangles when asked, or
    automatically above AUTO_DECIMATE_ABOVE); returns (solid, repair report)."""
    mesh = load_mesh(path)
    rep = repair_mesh(mesh)
    target = int(decimate_to) if decimate_to else (AUTO_DECIMATE_TO if len(mesh.faces) > AUTO_DECIMATE_ABOVE else 0)
    if target and len(mesh.faces) > target:
        before = len(mesh.faces)
        mesh = decimate(mesh, target)
        rep["decimated"] = {"from": before, "to": len(mesh.faces), "requested": target}
    if not mesh.is_watertight:
        raise ValueError(f"{Path(path).name} is not watertight even after repair ({len(mesh.faces)} faces); it cannot become a solid")
    return mesh_to_solid(mesh, tolerance, unify), rep


# ---------------------------------------------------------------------------- IGES

def import_iges(path: str | Path) -> list[bd.Shape]:
    """Shapes from an IGES file: closed surface sets become solids, open ones stay shells/faces."""
    from OCP.IGESControl import IGESControl_Reader
    r = IGESControl_Reader()
    if r.ReadFile(str(path)) != 1:
        raise ValueError(f"could not read {Path(path).name} as IGES")
    r.TransferRoots()
    comp = bd.Compound(r.OneShape())
    solids = list(comp.solids())
    if solids:
        return solids
    faces = list(comp.faces())
    if not faces:
        return [comp]
    sew = BRepBuilderAPI_Sewing(1e-3)
    for f in faces:
        sew.Add(f.wrapped)
    sew.Perform()
    sh = sew.SewedShape()
    out: list[bd.Shape] = []
    shells = [TopoDS.Shell(sh)] if sh.ShapeType() == TopAbs_ShapeEnum.TopAbs_SHELL else [s.wrapped for s in bd.Compound(sh).shells()]
    from OCP.BRep import BRep_Tool
    for shell in shells:
        if BRep_Tool.IsClosed_s(shell):
            sol = bd.Solid(BRepBuilderAPI_MakeSolid(shell).Solid())
            if sol.volume < 0:
                sol = bd.Solid(TopoDS.Solid(sol.wrapped.Reversed()))
            out.append(sol)
        else:
            out.append(bd.Shell(shell))
    return out or [comp]


def export_iges(parts, path: str | Path) -> str:
    from OCP.IGESControl import IGESControl_Writer
    w = IGESControl_Writer()
    for p in parts:
        w.AddShape(p.shape.wrapped)
    if not w.Write(str(path)):
        raise ValueError("IGES write failed")
    return str(path)


# ---------------------------------------------------------------------------- meshes out

def to_trimesh(shape: bd.Shape, tolerance: float = 0.05, angular: float = 0.2):
    import numpy as np
    import trimesh
    verts, tris = shape.tessellate(tolerance, angular)
    return trimesh.Trimesh(vertices=np.array([[v.X, v.Y, v.Z] for v in verts]), faces=np.array(tris), process=False)


def export_obj(parts, path: str | Path, tolerance: float | None = None) -> str:
    """Every part as a named object in one OBJ file."""
    import trimesh
    diag = max(parts.bbox.diagonal, 1.0) if len(parts) else 1.0
    tol = tolerance if tolerance is not None else min(0.05, diag * 0.0005)
    scene = trimesh.Scene()
    for p in parts:
        scene.add_geometry(to_trimesh(p.shape, tol), node_name=p.name, geom_name=p.name)
    scene.export(str(path), file_type="obj")
    return str(path)


# ---------------------------------------------------------------------------- DXF -> sketch

def dxf_to_sketch(path: str | Path, layer: str | None = None, scale: float = 1.0) -> dict:
    """A eitri sketch (lines, arcs, circles, polylines as lines/arcs, splines as fit splines) from
    the model space of a DXF file, optionally one layer only."""
    import ezdxf
    doc = ezdxf.readfile(str(path))
    from . import sketch as S
    sk: dict = {"type": "sketch", "entities": [], "constraints": [], "dims": []}
    s = float(scale)
    def P(x, y):
        return [round(float(x) * s, 6), round(float(y) * s, 6)]
    for e in doc.modelspace():
        if layer and e.dxf.layer != layer:
            continue
        t = e.dxftype()
        if t == "LINE":
            S.add(sk, {"type": "line", "a": P(e.dxf.start.x, e.dxf.start.y), "b": P(e.dxf.end.x, e.dxf.end.y)})
        elif t == "CIRCLE":
            S.add(sk, {"type": "circle", "c": P(e.dxf.center.x, e.dxf.center.y), "r": round(float(e.dxf.radius) * s, 6)})
        elif t == "ARC":
            a0, a1 = float(e.dxf.start_angle), float(e.dxf.end_angle)
            while a1 <= a0:
                a1 += 360
            S.add(sk, {"type": "arc", "c": P(e.dxf.center.x, e.dxf.center.y), "r": round(float(e.dxf.radius) * s, 6), "start": round(a0, 6), "end": round(a1, 6)})
        elif t in ("LWPOLYLINE", "POLYLINE"):
            if t == "LWPOLYLINE":
                pts = [(float(v[0]), float(v[1]), float(v[4]) if len(v) > 4 and v[4] is not None else 0.0) for v in e.get_points("xyseb")]
            else:
                pts = [(float(v.dxf.location.x), float(v.dxf.location.y), float(getattr(v.dxf, "bulge", 0.0) or 0.0)) for v in e.vertices]
            closed = bool(e.closed) if t == "LWPOLYLINE" else bool(e.is_closed)
            n = len(pts)
            for i in range(n if closed else n - 1):
                (x0, y0, bulge), (x1, y1, _b) = pts[i], pts[(i + 1) % n]
                if abs(bulge) < 1e-12:
                    S.add(sk, {"type": "line", "a": P(x0, y0), "b": P(x1, y1)})
                else:                                       # a bulge arc: bulge = tan(theta / 4)
                    theta = 4 * math.atan(bulge)
                    chord = math.hypot(x1 - x0, y1 - y0)
                    r = chord / (2 * math.sin(abs(theta) / 2))
                    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                    d = math.sqrt(max(r * r - (chord / 2) ** 2, 0.0))
                    nx, ny = -(y1 - y0) / chord, (x1 - x0) / chord
                    sgn = 1 if theta > 0 else -1
                    cx, cy = mx - sgn * nx * d, my - sgn * ny * d
                    sa, ea = math.degrees(math.atan2(y0 - cy, x0 - cx)), math.degrees(math.atan2(y1 - cy, x1 - cx))
                    if theta < 0:
                        sa, ea = ea, sa
                    while ea <= sa:
                        ea += 360
                    S.add(sk, {"type": "arc", "c": P(cx, cy), "r": round(r * s, 6), "start": round(sa, 6), "end": round(ea, 6)})
        elif t == "SPLINE":
            try:
                pts = [P(p[0], p[1]) for p in e.fit_points] or [P(p[0], p[1]) for p in e.control_points]
            except Exception:
                pts = []
            if len(pts) >= 2:
                S.add(sk, {"type": "spline", "kind": "fit", "closed": bool(e.closed), "points": pts})
        elif t == "ELLIPSE":
            c = e.dxf.center; major = e.dxf.major_axis
            rx = math.hypot(major.x, major.y); ry = rx * float(e.dxf.ratio)
            S.add(sk, {"type": "ellipse", "c": P(c.x, c.y), "rx": round(rx * s, 6), "ry": round(ry * s, 6), "angle": round(math.degrees(math.atan2(major.y, major.x)), 6)})
    if not sk["entities"]:
        raise ValueError(f"no usable entities in {Path(path).name}" + (f" on layer {layer!r}" if layer else ""))
    return sk
