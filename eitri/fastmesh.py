"""Fast triangulations of B-rep shapes.

OCCT's mesher fills each face's triangulation in C++; reading it back node by node and triangle by
triangle from Python costs seconds on a detailed part (a knurled knob: ~4 s). Instead, OCCT's own
STL writer dumps every triangle of the shape in one C++ call and numpy reads that buffer back at once
(the same knob: ~0.06 s). The triangles come out face by face in explorer order, so they are split
per face using each face's triangle count, and mapped onto the shape's faces() order.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np

_STL = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])


def mesh_shape(shape, lin: float, ang: float) -> None:
    """Mesh (or re-mesh to these tolerances) the whole shape in C++."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    BRepMesh_IncrementalMesh(shape.wrapped, float(lin), False, float(ang), True)


def all_triangles(shape) -> np.ndarray:
    """Every triangle of the (already meshed) shape as an (N, 3, 3) float32 array, outward winding."""
    from OCP.StlAPI import StlAPI_Writer
    fd, name = tempfile.mkstemp(suffix=".stl", prefix="eitri-mesh-")
    os.close(fd)
    try:
        w = StlAPI_Writer()
        w.ASCIIMode = False
        if not w.Write(shape.wrapped, name):
            return np.zeros((0, 3, 3), dtype=np.float32)
        data = Path(name).read_bytes()
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass
    if len(data) < 84:
        return np.zeros((0, 3, 3), dtype=np.float32)
    n = int.from_bytes(data[80:84], "little")
    if 84 + n * _STL.itemsize > len(data):              # an ASCII file: this OCCT build ignored the flag
        raise RuntimeError("the STL writer did not produce a binary file")
    return np.frombuffer(data, dtype=_STL, count=n, offset=84)["v"].astype(np.float32)


def face_triangles(shape, faces=None) -> list[np.ndarray | None]:
    """The (already meshed) shape's triangles split per face, in the order of `faces` (default:
    shape.faces()); None for a face without a triangulation."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    faces = list(shape.faces()) if faces is None else list(faces)
    index: dict[int, list[int]] = {}
    for i, f in enumerate(faces):
        index.setdefault(hash(f.wrapped), []).append(i)
    tris = all_triangles(shape)
    out: list[np.ndarray | None] = [None] * len(faces)
    exp = TopExp_Explorer(shape.wrapped, TopAbs_FACE)
    start = 0
    while exp.More():
        cur = exp.Current()
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(TopoDS.Face(cur), loc)
        n = tri.NbTriangles() if tri is not None else 0
        if n:
            chunk = tris[start:start + n]
            start += n
            for i in index.get(hash(cur), []):
                if faces[i].wrapped.IsSame(cur) and out[i] is None:
                    out[i] = chunk
                    break
        exp.Next()
    if start != len(tris):                               # the writer and the explorer disagree: be safe
        raise RuntimeError(f"triangle split mismatch ({start} of {len(tris)})")
    return out


def indexed(tri: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(positions, normals, indices) of one face's triangles: coincident corners merged, normals the
    area-weighted average of the facets around each vertex (flat faces stay flat)."""
    n = len(tri)
    corners = tri.reshape(-1, 3).astype(np.float64)
    scale = max(float(np.abs(corners).max()), 1.0)
    key = np.round(corners / (scale * 1e-9)).astype(np.int64)
    uniq, inverse = np.unique(key, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    pos = np.zeros((len(uniq), 3))
    np.add.at(pos, inverse, corners)
    counts = np.bincount(inverse, minlength=len(uniq))[:, None]
    pos /= np.maximum(counts, 1)
    idx = inverse.reshape(n, 3).astype(np.uint32)
    v0, v1, v2 = corners[0::3], corners[1::3], corners[2::3]
    fn = np.cross(v1 - v0, v2 - v0)                     # area-weighted facet normals
    nrm = np.zeros_like(pos)
    for k in range(3):
        np.add.at(nrm, idx[:, k], fn)
    nrm /= (np.linalg.norm(nrm, axis=1)[:, None] + 1e-20)
    return pos.astype(np.float32), nrm.astype(np.float32), idx
