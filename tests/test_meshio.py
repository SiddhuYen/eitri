"""Meshes and formats: STL -> faceted solid (repaired), OBJ and IGES round trips, mesh and IGES files as
models and as inserted components, DXF files as sketches, and the download formats."""
from __future__ import annotations

import math

import build123d as bd
import pytest

from eitri import meshio
from eitri.edits import apply_edits
from eitri.kernel import export_parts
from eitri.model import Part, Parts, load_model
from eitri import sketch as S


def test_mesh_to_solid_round_trip_and_repair(tmp_path):
    shape = bd.Box(20, 10, 5) + bd.Pos(0, 0, 5) * bd.Cylinder(3, 6)
    parts = Parts([Part("part", shape, "#999")])
    out = export_parts(parts, tmp_path, "part", ("stl", "obj", "iges"))
    assert set(out) >= {"stl", "obj", "iges"}
    solid, rep = meshio.import_mesh(out["stl"])
    assert abs(solid.volume - shape.volume) < 0.5 and solid.is_valid and rep["after"]["watertight"]
    assert len(solid.faces()) < 400                                                     # coplanar triangles merged (the box faces are 6 of them)
    # a damaged mesh: faces removed, then repaired back to watertight
    m = meshio.load_mesh(out["stl"])
    import trimesh
    broken = trimesh.Trimesh(vertices=m.vertices, faces=m.faces[:-3])
    assert not broken.is_watertight
    rep2 = meshio.repair_mesh(broken)
    assert rep2["before"]["watertight"] is False and rep2["after"]["watertight"] is True
    # OBJ and IGES come back
    m2 = meshio.load_mesh(out["obj"])
    assert m2.is_watertight and abs(abs(m2.volume) - shape.volume) < 1.0
    shapes = meshio.import_iges(out["iges"])
    assert len(shapes) == 1 and abs(abs(shapes[0].volume) - shape.volume) < 1e-3
    # files as models
    for path in (out["stl"], out["iges"], out["obj"]):
        built = load_model(path).build()
        assert len(built) == 1 and abs(abs(built[0].volume) - shape.volume) < 1.0, path


def test_insert_mesh_and_iges_components(tmp_path):
    comp = Parts([Part("hub", bd.Cylinder(10, 5), "#888")])
    out = export_parts(comp, tmp_path, "hub", ("stl", "iges"))
    base = Parts([Part("plate", bd.Box(60, 40, 5, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)), "#999")])
    res, notes = apply_edits(base, [
        {"op": "insert", "kind": "mesh", "path": out["stl"], "at": [0, 0, 7.5], "name": "hub_mesh"},
        {"op": "insert", "kind": "iges", "path": out["iges"], "at": [30, 0, 7.5], "name": "hub_iges"},
    ])
    assert all(n.ok for n in notes), [n.detail for n in notes]
    assert res.names == ["plate", "hub_mesh", "hub_iges"]
    assert abs(abs(res["hub_mesh"].volume) - math.pi * 100 * 5) < 2.0 and abs(abs(res["hub_iges"].volume) - math.pi * 100 * 5) < 1e-3
    assert abs(res["hub_iges"].bounding_box().center().X - 30) < 1e-6


def test_dxf_to_sketch(tmp_path):
    import ezdxf
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 10), (0, 10)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_circle((10, 5), 2, dxfattribs={"layer": "HOLES"})
    msp.add_arc((30, 5), 3, 0, 180)
    msp.add_line((40, 0), (50, 10))
    msp.add_lwpolyline([(60, 0, 0, 0, 1.0), (70, 0, 0, 0, 0)], close=False)             # a bulge of 1 = a half circle
    path = tmp_path / "sk.dxf"
    doc.saveas(str(path))
    sk = meshio.dxf_to_sketch(path)
    kinds = sorted(e["type"] for e in sk["entities"])
    assert kinds == ["arc", "arc", "circle", "line", "line", "line", "line", "line"], kinds
    faces, chains = S.profile_faces(sk)
    assert len(faces) == 1 and abs(faces[0].area - (200 - math.pi * 4)) < 1e-3 and len(chains) == 3
    only = meshio.dxf_to_sketch(path, layer="HOLES")
    assert [e["type"] for e in only["entities"]] == ["circle"]
    half = next(e for e in sk["entities"] if e["type"] == "arc" and abs(e["r"] - 5) < 1e-6)
    assert abs(half["c"][0] - 65) < 1e-6 and abs(half["c"][1]) < 1e-6 and abs(half["end"] - half["start"] - 180) < 1e-6
    scaled = meshio.dxf_to_sketch(path, scale=0.5)
    assert abs(S.profile_faces(scaled)[0][0].area - (200 - math.pi * 4) / 4) < 1e-3


def test_decimation(tmp_path):
    """A dense mesh can be decimated on import (explicitly, or automatically above the size limit) and
    still becomes a valid solid; the insert op and the model loader take the triangle budget."""
    import trimesh
    from eitri import meshio
    from eitri.model import load_model
    dense = trimesh.creation.icosphere(subdivisions=4, radius=10)          # 5120 triangles
    path = tmp_path / "ball.stl"
    dense.export(path)
    solid, rep = meshio.import_mesh(path, decimate_to=600)
    assert rep["decimated"]["from"] == 5120 and rep["decimated"]["to"] <= 600 and solid.is_valid and len(solid.faces()) <= 600
    assert abs(solid.volume - 4 / 3 * 3.141592653589793 * 1000) < 0.15 * 4 / 3 * 3.141592653589793 * 1000      # still a ball
    full, rep2 = meshio.import_mesh(path)
    assert "decimated" not in rep2 and len(full.faces()) == 5120
    parts = load_model(path, {"decimate": "400"}).build()
    assert len(parts["ball"].faces()) <= 400
    old = meshio.AUTO_DECIMATE_ABOVE, meshio.AUTO_DECIMATE_TO
    meshio.AUTO_DECIMATE_ABOVE, meshio.AUTO_DECIMATE_TO = 1000, 800
    try:
        auto, rep3 = meshio.import_mesh(path)
        assert rep3["decimated"]["requested"] == 800 and len(auto.faces()) <= 800
    finally:
        meshio.AUTO_DECIMATE_ABOVE, meshio.AUTO_DECIMATE_TO = old
