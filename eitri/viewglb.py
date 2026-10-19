"""A pickable GLB for the viewer, plus the topology the viewer needs to describe a click.

build123d's own glTF export merges each part into one mesh, so nothing in it can
be picked below the part level. This writer puts every B-rep face and edge in its
own node (names like `base:f7`, `base:e12`) and writes `topology.json` beside it
with what each one is: planes with normals, cylinders with axis and radius, hole
membership, edge types and lengths. The viewer uses the names for picking and the
JSON for the selection panel; the kernel's edit selectors are built from the same
numbers (see `eitri.edits`).
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import build123d as bd
from build123d import Vector

from .model import Parts


# ---------------------------------------------------------------------------- topology

@dataclass
class FaceInfo:
    id: str                      # "part:f7"
    part: str
    index: int
    type: str                    # plane, cylinder, cone, sphere, torus, bspline, extrusion, revolution, offset, other
    area: float
    center: list[float]          # a point on the face (centre of mass projected is fine for description)
    bbox: list[float]            # [minx, miny, minz, maxx, maxy, maxz]
    normal: list[float] | None = None     # planes: outward normal
    axis: list[float] | None = None       # cylinders/cones: axis direction
    axis_point: list[float] | None = None
    radius: float | None = None
    concave: bool | None = None  # cylinders: True for holes/bores
    full: bool | None = None     # cylinders: True for a full round (hole/boss), False for a fillet/partial round
    edges: list[str] = field(default_factory=list)
    hole: str | None = None      # id of the hole this face belongs to (from the report's hole list)


@dataclass
class EdgeInfo:
    id: str                      # "part:e12"
    part: str
    index: int
    type: str                    # line, circle, bspline, ...
    length: float
    start: list[float]
    end: list[float]
    mid: list[float]
    radius: float | None = None
    faces: list[str] = field(default_factory=list)
    vertices: list[str] = field(default_factory=list)   # its two end vertices ("part:v3")
    tangent: list[str] = field(default_factory=list)    # edges continuing smoothly through its ends (a tangent chain)
    convex: bool | None = None   # True for an outside corner (fillet/chamfer candidate)
    angle: float | None = None   # dihedral angle in degrees between the two faces (180 = flat)


@dataclass
class VertexInfo:
    id: str                      # "part:v3"
    part: str
    index: int
    pos: list[float]
    edges: list[str] = field(default_factory=list)


@dataclass
class HoleInfo:
    id: str                      # "part:h1"
    part: str
    diameter: float
    depth: float
    through: bool | None
    axis: list[float]
    center: list[float]
    faces: list[str] = field(default_factory=list)


@dataclass
class PartInfo:
    name: str
    color: str
    volume: float
    bbox: list[float]
    faces: int
    edges: int
    opacity: float = 1.0


@dataclass
class Topology:
    parts: list[PartInfo]
    faces: list[FaceInfo]
    edges: list[EdgeInfo]
    holes: list[HoleInfo]
    bbox: list[float]
    vertices: list[VertexInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _v(v: Vector) -> list[float]:
    return [round(v.X, 4), round(v.Y, 4), round(v.Z, 4)]


def _bbox(shape: bd.Shape | bd.BoundBox) -> list[float]:
    from .measure import bounding_box
    bb = shape if isinstance(shape, bd.BoundBox) else bounding_box(shape)
    return [round(bb.min.X, 3), round(bb.min.Y, 3), round(bb.min.Z, 3), round(bb.max.X, 3), round(bb.max.Y, 3), round(bb.max.Z, 3)]


_GEOM_NAMES = {
    "PLANE": "plane", "CYLINDER": "cylinder", "CONE": "cone", "SPHERE": "sphere", "TORUS": "torus",
    "BSPLINE": "bspline", "BEZIER": "bezier", "EXTRUSION": "extrusion", "REVOLUTION": "revolution",
    "OFFSET": "offset", "LINE": "line", "CIRCLE": "circle", "ELLIPSE": "ellipse", "HYPERBOLA": "hyperbola",
    "PARABOLA": "parabola", "OTHER": "other",
}


def _geom(shape) -> str:
    try:
        return _GEOM_NAMES.get(shape.geom_type.name, shape.geom_type.name.lower())
    except Exception:
        return "other"


def part_topology(name: str, shape: bd.Shape, holes_from_report: list | None = None,
                  face_boxes: list | None = None) -> tuple[list[FaceInfo], list[EdgeInfo], list[HoleInfo], list[VertexInfo]]:
    """Describe every face, edge and vertex of one part; numbered in shape order. `face_boxes` (from the
    triangulation, see write_view_glb) replaces the per-face optimal bounding boxes, which are slow."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer

    faces = list(shape.faces())
    edges = list(shape.edges())
    edge_index = {hash(e): i for i, e in enumerate(edges)}
    face_infos: list[FaceInfo] = []
    edge_faces: dict[int, list[str]] = {}
    face_normals_at_edges: dict[int, list[tuple[bd.Face, str]]] = {}
    face_kind: dict[int, str] = {}                 # geom_type builds a surface adaptor per call: ask once per face

    for i, f in enumerate(faces):
        fid = f"{name}:f{i}"
        kind = _geom(f)
        face_kind[hash(f)] = kind
        fb = face_boxes[i] if face_boxes is not None and i < len(face_boxes) else None
        info = FaceInfo(id=fid, part=name, index=i, type=kind, area=round(f.area, 3), center=_v(_point_on_face(f)),
                        bbox=fb if fb is not None else _bbox(f))
        try:
            if kind == "plane":
                info.normal = _v(f.normal_at())
            elif kind in ("cylinder", "cone"):
                surf = BRepAdaptor_Surface(f.wrapped)
                ax = surf.Cylinder().Axis() if kind == "cylinder" else surf.Cone().Axis()
                d = Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z())
                loc = Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
                info.axis = _v(d)
                info.axis_point = _v(loc)
                if kind == "cylinder":
                    info.radius = round(surf.Cylinder().Radius(), 4)
                    umin, umax = BRepTools.UVBounds_s(f.wrapped)[:2]
                    info.full = bool((umax - umin) > 2 * math.pi * 0.95)
                    pt = Vector(*info.center)
                    n = f.normal_at(pt)
                    radial = pt - loc
                    radial = radial - d * radial.dot(d)
                    info.concave = bool(n.dot(radial) < 0) if radial.length > 1e-9 else None
            elif kind == "sphere":
                surf = BRepAdaptor_Surface(f.wrapped)
                info.radius = round(surf.Sphere().Radius(), 4)
                c = surf.Sphere().Location()
                info.axis_point = [round(c.X(), 4), round(c.Y(), 4), round(c.Z(), 4)]
        except Exception:
            pass
        # edges of this face (with repetition for seams; dedupe)
        ex = TopExp_Explorer(f.wrapped, TopAbs_EDGE)
        seen = set()
        while ex.More():
            e = bd.Edge(ex.Current())
            ex.Next()
            idx = edge_index.get(hash(e))
            if idx is None or idx in seen:
                continue
            seen.add(idx)
            info.edges.append(f"{name}:e{idx}")
            edge_faces.setdefault(idx, []).append(fid)
            face_normals_at_edges.setdefault(idx, []).append((f, fid))
        face_infos.append(info)

    centers: dict[int, Vector] = {}                # per face, once (a surface integral each)
    flat_normals: dict[int, Vector] = {}

    def center_of(f: bd.Face) -> Vector:
        k = hash(f)
        if k not in centers:
            centers[k] = f.center()
        return centers[k]

    def normal_of(f: bd.Face, p: Vector) -> Vector:
        if face_kind.get(hash(f)) == "plane":       # constant on a flat face: no projection
            k = hash(f)
            if k not in flat_normals:
                flat_normals[k] = f.normal_at()
            return flat_normals[k]
        return f.normal_at(p)

    edge_infos: list[EdgeInfo] = []
    for i, e in enumerate(edges):
        kind = _geom(e)
        try:
            start, end, mid = e.position_at(0), e.position_at(1), e.position_at(0.5)
        except Exception:
            continue
        info = EdgeInfo(id=f"{name}:e{i}", part=name, index=i, type=kind, length=round(e.length, 3),
                        start=_v(start), end=_v(end), mid=_v(mid), faces=edge_faces.get(i, []))
        if kind in ("circle",):
            try:
                info.radius = round(e.radius, 4)
            except Exception:
                pass
        adj = face_normals_at_edges.get(i, [])
        if len(adj) == 2:
            try:
                f1, f2 = adj[0][0], adj[1][0]
                n1, n2 = normal_of(f1, mid), normal_of(f2, mid)
                ang = n1.get_angle(n2)
                c1, c2 = center_of(f1) - mid, center_of(f2) - mid
                info.angle = round(180.0 - ang, 1)
                info.convex = bool(c2.dot(n1) < 0 and c1.dot(n2) < 0) if ang > 1.0 else None
            except Exception:
                pass
        edge_infos.append(info)

    # vertices: distinct positions, with the edges that end there
    vertex_infos: list[VertexInfo] = []
    vkey: dict[tuple, VertexInfo] = {}

    def key_of(p: list[float]) -> tuple:
        return (round(p[0], 3), round(p[1], 3), round(p[2], 3))

    for v in shape.vertices():
        pos = _v(Vector(v.X, v.Y, v.Z))
        k = key_of(pos)
        if k not in vkey:
            vi = VertexInfo(id=f"{name}:v{len(vertex_infos)}", part=name, index=len(vertex_infos), pos=pos)
            vkey[k] = vi
            vertex_infos.append(vi)
    for ei in edge_infos:
        for p in (ei.start, ei.end):
            vi = vkey.get(key_of(p))
            if vi is not None:
                if vi.id not in ei.vertices:
                    ei.vertices.append(vi.id)
                if ei.id not in vi.edges:
                    vi.edges.append(ei.id)

    # tangent chains: edges that continue through a shared vertex without a kink (within 5 degrees)
    at_vertex: dict[str, list[tuple[EdgeInfo, Vector]]] = {}
    for ei in edge_infos:
        e = edges[ei.index]
        try:
            t0, t1 = e.tangent_at(0), e.tangent_at(1)
        except Exception:
            continue
        for p, leaving in ((ei.start, t0), (ei.end, t1 * -1)):     # the direction leaving the vertex along the edge
            vi = vkey.get(key_of(p))
            if vi is not None:
                at_vertex.setdefault(vi.id, []).append((ei, leaving))
    for items in at_vertex.values():
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                ea, ta = items[a]
                eb, tb = items[b]
                if ea.id != eb.id and ta.dot(tb) < -0.9962:          # straight through: opposite leaving directions
                    if eb.id not in ea.tangent:
                        ea.tangent.append(eb.id)
                    if ea.id not in eb.tangent:
                        eb.tangent.append(ea.id)

    hole_infos: list[HoleInfo] = []
    for k, h in enumerate(holes_from_report or []):
        hid = f"{name}:h{k + 1}"
        c = Vector(*h.center) if not isinstance(h.center, Vector) else h.center
        ax = Vector(*h.axis) if not isinstance(h.axis, Vector) else h.axis
        members = []
        for fi in face_infos:
            if fi.type == "cylinder" and fi.concave and fi.radius is not None and abs(2 * fi.radius - h.diameter) < 1e-3 and fi.axis:
                a = Vector(*fi.axis)
                if abs(abs(a.dot(ax)) - 1) < 1e-3:
                    p = Vector(*fi.axis_point)
                    off = (p - c)
                    off = off - ax * off.dot(ax)
                    if off.length < 1e-3:
                        members.append(fi.id)
                        fi.hole = hid
        hole_infos.append(HoleInfo(id=hid, part=name, diameter=round(h.diameter, 4), depth=round(h.depth, 3),
                                   through=h.through, axis=_v(ax), center=_v(c), faces=members))
    return face_infos, edge_infos, hole_infos, vertex_infos


def _point_on_face(f: bd.Face) -> Vector:
    """The face's parametric middle when it lies on the face (tested in the face's own (u, v): build123d's
    3D is_inside builds a solid classifier per call), else its centre."""
    try:
        from OCP.gp import gp_Pnt2d
        from OCP.IntTools import IntTools_FClass2d
        from OCP.TopAbs import TopAbs_IN, TopAbs_ON
        u0, u1, v0, v1 = f._uv_bounds()
        if IntTools_FClass2d(f.wrapped, 1e-6).Perform(gp_Pnt2d((u0 + u1) / 2, (v0 + v1) / 2)) in (TopAbs_IN, TopAbs_ON):
            return f.position_at(0.5, 0.5)
    except Exception:
        pass
    try:
        return f.center()
    except Exception:
        return Vector(0, 0, 0)


def topology(parts: Parts, holes_by_part: dict[str, list] | None = None, face_boxes_by_part: dict[str, list] | None = None) -> Topology:
    from . import measure
    all_faces: list[FaceInfo] = []
    all_edges: list[EdgeInfo] = []
    all_holes: list[HoleInfo] = []
    all_vertices: list[VertexInfo] = []
    part_infos: list[PartInfo] = []
    for p in parts:
        hs = (holes_by_part or {}).get(p.name)
        if hs is None:
            try:
                hs = [h for h in measure.cylindrical_features(p.shape) if h.kind == "hole"]
            except Exception:
                hs = []
        f, e, h, v = part_topology(p.name, p.shape, hs, (face_boxes_by_part or {}).get(p.name))
        all_faces += f
        all_edges += e
        all_holes += h
        all_vertices += v
        part_infos.append(PartInfo(name=p.name, color=p.color, volume=round(abs(p.shape.volume), 3), bbox=_bbox(p.shape),
                                   faces=len(f), edges=len(e), opacity=float(getattr(p, "opacity", 1.0) or 1.0)))
    return Topology(parts=part_infos, faces=all_faces, edges=all_edges, holes=all_holes, bbox=_bbox(parts.bbox) if len(parts) else [0] * 6,
                    vertices=all_vertices)


# ---------------------------------------------------------------------------- GLB writer

def _hex_rgb(h: str) -> list[float]:
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


class _Buffer:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.views: list[dict] = []
        self.accessors: list[dict] = []
        self.offset = 0

    def add(self, arr: np.ndarray, target: int, component: int, type_: str, minmax: bool = False) -> int:
        data = arr.tobytes()
        pad = (-len(data)) % 4
        self.chunks.append(data + b"\0" * pad)
        self.views.append({"buffer": 0, "byteOffset": self.offset, "byteLength": len(data), "target": target})
        self.offset += len(data) + pad
        acc = {"bufferView": len(self.views) - 1, "componentType": component, "count": int(arr.shape[0]), "type": type_}
        if minmax:
            acc["min"] = [float(x) for x in arr.min(axis=0)]
            acc["max"] = [float(x) for x in arr.max(axis=0)]
        self.accessors.append(acc)
        return len(self.accessors) - 1


def face_mesh(face: bd.Face) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Triangulation of one face from the shape-level mesh: (positions, normals, indices)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepLib import BRepLib_ToolTriangulatedShape
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopLoc import TopLoc_Location

    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face.wrapped, loc)
    if tri is None or tri.NbTriangles() == 0:
        return None
    trsf = loc.Transformation()
    n = tri.NbNodes()
    pos = np.empty((n, 3), dtype=np.float32)
    for i in range(1, n + 1):
        p = tri.Node(i).Transformed(trsf)
        pos[i - 1] = (p.X(), p.Y(), p.Z())
    try:
        if not tri.HasNormals():
            BRepLib_ToolTriangulatedShape.ComputeNormals_s(face.wrapped, tri)
        nrm = np.empty((n, 3), dtype=np.float32)
        for i in range(1, n + 1):
            d = tri.Normal(i)
            nrm[i - 1] = (d.X(), d.Y(), d.Z())
        if loc.IsIdentity() is False:
            # rotate normals with the location (no translation)
            q = trsf.GetRotation()
            for i in range(n):
                from OCP.gp import gp_Vec
                v = gp_Vec(float(nrm[i, 0]), float(nrm[i, 1]), float(nrm[i, 2])).Transformed(trsf)
                nrm[i] = (v.X(), v.Y(), v.Z())
    except Exception:
        nrm = None
    reversed_ = face.wrapped.Orientation() == TopAbs_REVERSED
    m = tri.NbTriangles()
    idx = np.empty((m, 3), dtype=np.uint32)
    for i in range(1, m + 1):
        a, b, c = tri.Triangle(i).Get()
        idx[i - 1] = (a - 1, c - 1, b - 1) if reversed_ else (a - 1, b - 1, c - 1)
    if nrm is None:
        # flat normals from geometry
        v0, v1, v2 = pos[idx[:, 0]], pos[idx[:, 1]], pos[idx[:, 2]]
        fn = np.cross(v1 - v0, v2 - v0)
        fn /= (np.linalg.norm(fn, axis=1)[:, None] + 1e-12)
        nrm = np.zeros_like(pos)
        for k in range(3):
            np.add.at(nrm, idx[:, k], fn)
        nrm /= (np.linalg.norm(nrm, axis=1)[:, None] + 1e-12)
    elif reversed_:
        nrm = -nrm
    return pos, nrm.astype(np.float32), idx


def edge_polyline(edge: bd.Edge, ang: float = 0.3, lin: float = 0.05) -> np.ndarray | None:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_TangentialDeflection
    try:
        if edge.geom_type == bd.GeomType.LINE:
            a, b = edge.position_at(0), edge.position_at(1)
            return np.array([[a.X, a.Y, a.Z], [b.X, b.Y, b.Z]], dtype=np.float32)
        td = GCPnts_TangentialDeflection(BRepAdaptor_Curve(edge.wrapped), ang, lin, 2)
        n = td.NbPoints()
        if n < 2:
            return None
        pts = np.empty((n, 3), dtype=np.float32)
        for i in range(1, n + 1):
            p = td.Value(i)
            pts[i - 1] = (p.X(), p.Y(), p.Z())
        return pts
    except Exception:
        return None


def write_view_glb(parts: Parts, glb_path: str | Path, topo_path: str | Path | None = None,
                   quality: float = 1.0, holes_by_part: dict[str, list] | None = None) -> Topology:
    """Write the pickable GLB (one node per face and edge, mm, Z-up) and topology.json."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh

    diag = max(parts.bbox.diagonal, 1e-6) if len(parts) else 1.0
    lin = max(diag * 0.0012 / quality, 1e-3)
    ang = 0.25 / quality
    buf = _Buffer()
    nodes: list[dict] = []
    meshes: list[dict] = []
    materials: list[dict] = []
    root_children: list[int] = []
    # mesh every part first: its triangles (one C++ call per part) give the face meshes AND tight face boxes
    chunks_by_part: dict[str, list | None] = {}
    boxes_by_part: dict[str, list] = {}
    for p in parts:
        BRepMesh_IncrementalMesh(p.shape.wrapped, lin, False, ang, True)
        try:
            from .fastmesh import face_triangles
            chunks = face_triangles(p.shape)
        except Exception:
            chunks = None
        chunks_by_part[p.name] = chunks
        if chunks is not None:
            boxes_by_part[p.name] = [None if c is None or not len(c) else
                                     [round(float(v), 3) for v in (*c.reshape(-1, 3).min(axis=0), *c.reshape(-1, 3).max(axis=0))]
                                     for c in chunks]
    topo = topology(parts, holes_by_part, boxes_by_part)

    for pi, p in enumerate(parts):
        shape = p.shape
        rgb = _hex_rgb(p.color)
        op = float(getattr(p, "opacity", 1.0) or 1.0)
        materials.append({"name": p.name, "pbrMetallicRoughness": {"baseColorFactor": rgb + [op], "metallicFactor": 0.05, "roughnessFactor": 0.65},
                          **({"alphaMode": "BLEND"} if op < 0.999 else {}),
                          "doubleSided": True})
        mat = len(materials) - 1
        face_mats: dict[str, int] = {}                        # per-face appearances: one material per colour
        fcolors = getattr(p, "face_colors", None) or {}
        part_node = {"name": p.name, "children": [], "extras": {"kind": "part", "name": p.name, "id": p.name}}
        nodes.append(part_node)
        part_idx = len(nodes) - 1
        root_children.append(part_idx)
        faces = shape.faces()
        chunks = chunks_by_part.get(p.name)
        from .fastmesh import indexed
        for fi, face in enumerate(faces):
            if chunks is not None:
                fm = indexed(chunks[fi]) if chunks[fi] is not None and len(chunks[fi]) else None
            else:
                fm = face_mesh(face)
            if fm is None:
                continue
            pos, nrm, idx = fm
            a_pos = buf.add(pos, 34962, 5126, "VEC3", minmax=True)
            a_nrm = buf.add(nrm, 34962, 5126, "VEC3")
            a_idx = buf.add(idx.reshape(-1), 34963, 5125, "SCALAR")
            fmat = mat
            fc = fcolors.get(fi, fcolors.get(str(fi)))
            if fc:
                if fc not in face_mats:
                    materials.append({"name": f"{p.name}:{fc}", "pbrMetallicRoughness": {"baseColorFactor": _hex_rgb(fc) + [op], "metallicFactor": 0.05, "roughnessFactor": 0.65},
                                      **({"alphaMode": "BLEND"} if op < 0.999 else {}), "doubleSided": True})
                    face_mats[fc] = len(materials) - 1
                fmat = face_mats[fc]
            meshes.append({"name": f"{p.name}:f{fi}", "primitives": [{"attributes": {"POSITION": a_pos, "NORMAL": a_nrm}, "indices": a_idx, "material": fmat, "mode": 4}]})
            nodes.append({"name": f"{p.name}:f{fi}", "mesh": len(meshes) - 1, "extras": {"kind": "face", "part": p.name, "index": fi, "id": f"{p.name}:f{fi}"}})
            part_node["children"].append(len(nodes) - 1)
        for ei, edge in enumerate(shape.edges()):
            pl = edge_polyline(edge, ang, max(diag * 0.002 / quality, 1e-3))
            if pl is None or len(pl) < 2:
                continue
            a_pos = buf.add(pl, 34962, 5126, "VEC3", minmax=True)
            seg = np.array([[i, i + 1] for i in range(len(pl) - 1)], dtype=np.uint32).reshape(-1)
            a_idx = buf.add(seg, 34963, 5125, "SCALAR")
            meshes.append({"name": f"{p.name}:e{ei}", "primitives": [{"attributes": {"POSITION": a_pos}, "indices": a_idx, "material": len(materials), "mode": 1}]})
            nodes.append({"name": f"{p.name}:e{ei}", "mesh": len(meshes) - 1, "extras": {"kind": "edge", "part": p.name, "index": ei, "id": f"{p.name}:e{ei}"}})
            part_node["children"].append(len(nodes) - 1)
    materials.append({"name": "edges", "pbrMetallicRoughness": {"baseColorFactor": [0.16, 0.17, 0.2, 1.0], "metallicFactor": 0.0, "roughnessFactor": 1.0}})
    nodes.append({"name": "model", "children": root_children, "extras": {"kind": "root"}})
    root = len(nodes) - 1

    binary = b"".join(buf.chunks)
    gltf = {
        "asset": {"version": "2.0", "generator": "eitri"},
        "scene": 0, "scenes": [{"nodes": [root]}],
        "nodes": nodes, "meshes": meshes, "materials": materials,
        "buffers": [{"byteLength": len(binary)}], "bufferViews": buf.views, "accessors": buf.accessors,
        "extras": {"units": "mm", "up": "Z"},
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    out = bytearray()
    out += struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(js), 0x4E4F534A) + js
    out += struct.pack("<II", len(binary), 0x004E4942) + binary
    Path(glb_path).write_bytes(bytes(out))
    if topo_path is not None:
        Path(topo_path).write_text(json.dumps(topo.to_dict()), encoding="utf-8")
    return topo


# ---------------------------------------------------------------------------- describing a selection

def describe_selection(topo: dict | None, ids: list[str]) -> str:
    """Human/agent-readable lines for selected ids ("plate:f7", "lid:e3", "lid")."""
    lines = []
    faces = {f["id"]: f for f in (topo or {}).get("faces", [])}
    edges = {e["id"]: e for e in (topo or {}).get("edges", [])}
    holes = {h["id"]: h for h in (topo or {}).get("holes", [])}
    parts = {p["name"]: p for p in (topo or {}).get("parts", [])}
    vertices = {v["id"]: v for v in (topo or {}).get("vertices", [])}

    def fmt(v):
        return "(" + ", ".join(f"{x:g}" for x in v) + ")"

    for sid in ids:
        if sid in faces:
            f = faces[sid]
            bits = [f"face {sid}: {f['type']}"]
            if f.get("normal"):
                bits.append(f"outward normal {fmt(f['normal'])}")
            if f.get("axis"):
                bits.append(f"axis {fmt(f['axis'])} through {fmt(f['axis_point'])}")
            if f.get("radius") is not None:
                bits.append(f"radius {f['radius']:g} ({'concave: a hole/bore' if f.get('concave') else 'convex: a boss/round'})")
            bits.append(f"area {f['area']:g} mm^2, point on face {fmt(f['center'])}")
            b = f.get("bbox")
            if b:
                bits.append(f"extent X {b[0]:g}..{b[3]:g} Y {b[1]:g}..{b[4]:g} Z {b[2]:g}..{b[5]:g}")
            if f.get("hole") and f["hole"] in holes:
                h = holes[f["hole"]]
                bits.append(f"part of hole {h['id']}: Ø{h['diameter']:g} depth {h['depth']:g} {'through' if h['through'] else 'blind' if h['through'] is False else ''} centre {fmt(h['center'])}")
            lines.append("- " + "; ".join(bits))
        elif sid in edges:
            e = edges[sid]
            bits = [f"edge {sid}: {e['type']}, length {e['length']:g}, from {fmt(e['start'])} to {fmt(e['end'])}"]
            if e.get("radius") is not None:
                bits.append(f"radius {e['radius']:g}")
            if e.get("angle") is not None:
                bits.append(f"{'convex' if e.get('convex') else 'concave'} corner, {e['angle']:g} deg between faces {', '.join(e.get('faces', []))}")
            lines.append("- " + "; ".join(bits))
        elif sid in vertices:
            v = vertices[sid]
            lines.append(f"- vertex {sid}: corner at {fmt(v['pos'])}, where edges {', '.join(v.get('edges', []))} meet")
        elif sid in holes:
            h = holes[sid]
            lines.append(f"- hole {sid}: Ø{h['diameter']:g}, depth {h['depth']:g}, axis {fmt(h['axis'])}, centre {fmt(h['center'])}")
        elif sid in parts:
            p = parts[sid]
            b = p["bbox"]
            lines.append(f"- part {sid}: volume {p['volume']:g} mm^3, extent X {b[0]:g}..{b[3]:g} Y {b[1]:g}..{b[4]:g} Z {b[2]:g}..{b[5]:g}")
        else:
            lines.append(f"- {sid}")
    return "\n".join(lines)
