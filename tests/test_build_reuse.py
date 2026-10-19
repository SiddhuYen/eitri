"""Reuse between builds must never change a result: the analysis cache, the per-part view cache, the reuse of
build(p) across direct edits, and the reuse of an unchanged prefix of the edit list all give exactly what a
fresh build gives."""
from __future__ import annotations

import build123d as bd

from eitri import model as M
from eitri import report as R
from eitri import viewglb
from eitri.project import Project

MODEL = '''from eitri import *
from pathlib import Path

PARAMS = dict(width=40.0)

def build(p):
    out = Path(__file__).with_name("out"); out.mkdir(exist_ok=True)
    with open(out / "calls.txt", "a") as f:                       # counts how often build(p) really runs (out/ is
        f.write("x")                                              # outputs, not something the model depends on)
    base = Box(p.width, 30, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    post = Pos(0, 0, 6) * Cylinder(4, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    lid = Pos(30, 0, 0) * Box(12, 12, 3, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return {"base": base, "post": post, "lid": lid}

EDITS = []
'''


def _project(tmp_path) -> Project:
    project = Project(tmp_path / "reuse", name="reuse")
    project.write_model(MODEL)
    return project


def _geometry(shape):
    """What a part is, geometrically (a reused result is a copy: same geometry, its own B-rep objects)."""
    from eitri import measure
    bb = measure.bounding_box(shape)
    return (round(measure.signed_volume(shape), 6), round(shape.area, 6), tuple(round(v, 6) for v in (*bb.min, *bb.max)),
            len(shape.faces()), len(shape.edges()), len(shape.vertices()))


def _shapes(project):
    m = M.load_model(project.model_path)
    parts = m.build()
    return {p.name: _geometry(p.shape) for p in parts}, [n.line() for n in m.edit_notes], parts


def _calls(project) -> int:
    f = project.folder / "out" / "calls.txt"
    return len(f.read_text()) if f.exists() else 0


def _fresh(project, monkeypatch):
    """Built from scratch: nothing kept from earlier builds (build(p) runs, every edit is applied)."""
    M._BASE_BUILDS.clear()
    M._EDITED.clear()
    return _shapes(project)


def test_build_is_reused_across_direct_edits_only(tmp_path, monkeypatch):
    project = _project(tmp_path)
    M._BASE_BUILDS.clear(); M._EDITED.clear()
    _shapes(project)
    assert _calls(project) == 1
    project.set_edits([{"op": "move", "part": "lid", "delta": [0, 5, 0]}])
    reused, notes, _ = _shapes(project)
    assert _calls(project) == 1                                   # only EDITS changed: build(p) did not run
    fresh, fresh_notes, _ = _fresh(project, monkeypatch)
    assert reused == fresh and notes == fresh_notes
    project.set_params({"width": 44.0})
    _shapes(project)
    before = _calls(project)
    project.set_params({"width": 46.0})
    _shapes(project)
    assert _calls(project) == before + 1                          # a parameter changed: build(p) ran again
    (project.folder / "helper.py").write_text("X = 1\n")
    _shapes(project)
    assert _calls(project) == before + 2                          # a file next to the model changed: ran again


def test_edit_prefix_is_reused_exactly(tmp_path, monkeypatch):
    project = _project(tmp_path)
    M._BASE_BUILDS.clear(); M._EDITED.clear()
    first = [{"op": "move", "part": "lid", "delta": [0, 5, 0]},
             {"op": "push_pull", "part": "base", "face": {"kind": "face", "type": "plane", "center": [0, 0, 6], "normal": [0, 0, 1], "area": 1200}, "distance": 2}]
    project.set_edits(first)
    _shapes(project)
    project.set_edits(first + [{"op": "fillet", "part": "lid", "edges": [{"kind": "edge", "type": "line", "mid": [30, 5, 3], "length": 12}], "radius": 0.5}])
    reused, notes, _ = _shapes(project)
    fresh, fresh_notes, _ = _fresh(project, monkeypatch)
    assert reused == fresh and notes == fresh_notes


def test_analysis_cache_matches_a_fresh_analysis(tmp_path):
    project = _project(tmp_path)
    _, _, parts = _shapes(project)
    fps = {p.name: R.fingerprint(p.shape) for p in parts}
    a = R.analyze(parts, fingerprints=fps).to_dict()
    hits = R.cache_stats["part_hits"]
    b = R.analyze(parts, fingerprints=fps).to_dict()
    assert R.cache_stats["part_hits"] >= hits + len(parts)
    for d in (a, b):
        d.pop("analysis_seconds")
    assert a == b
    for p in parts:                                               # one part changes: cached == fresh
        if p.name == "lid":
            p.shape = p.shape.moved(bd.Location((0, 0, 4)))
    fps = {p.name: R.fingerprint(p.shape) for p in parts}
    cached = R.analyze(parts, fingerprints=fps).to_dict()
    fresh = R.analyze(parts).to_dict()
    for d in (cached, fresh):
        d.pop("analysis_seconds")
    assert cached == fresh


def test_view_cache_gives_the_same_files(tmp_path):
    project = _project(tmp_path)
    _, _, parts = _shapes(project)
    fps = {p.name: R.fingerprint(p.shape) for p in parts}
    viewglb.write_view_glb(parts, tmp_path / "a.glb", tmp_path / "a.json", fingerprints=fps)
    seen = []
    viewglb.write_view_glb(parts, tmp_path / "b.glb", tmp_path / "b.json", fingerprints=fps, progress=seen.append)
    assert (tmp_path / "a.glb").read_bytes() == (tmp_path / "b.glb").read_bytes()
    assert (tmp_path / "a.json").read_text() == (tmp_path / "b.json").read_text()
    assert seen and seen[-1].startswith("meshing lid")
    partial = []
    viewglb.write_view_glb(parts, tmp_path / "c.glb", None, on_partial=lambda glb, topo, k, n: partial.append((k, n, len(topo.parts))), partial_every=0)
    assert partial and all(k == len_parts and n == 3 for k, n, len_parts in partial)


def test_an_edit_that_fails_only_on_a_reused_copy_is_rebuilt_from_scratch(tmp_path):
    """A variable fillet, then an unequal chamfer: the chamfer fails on a copy of the filleted shape (a kernel
    quirk), so the build must notice the failure, run from scratch, and apply it."""
    project = Project(tmp_path / "tunnel", name="tunnel")
    project.write_model("from eitri import *\nPARAMS = dict(w=40.0)\n\ndef build(p):\n"
                        "    return Box(p.w, 20, 30, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Pos(0, 0, 15) * Box(30, 40, 18)\n\nEDITS = []\n")
    first = [{"op": "sketch_extrude", "part": "tunnel", "face": {"kind": "face", "type": "plane", "center": [0, 0, 6], "area": 600, "normal": [0, 0, 1]},
              "origin": [0, 0, 6], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [0, 0], "w": 10, "h": 6}, "distance": 5, "mode": "add", "extent": "to_next"},
             {"op": "fillet", "part": "tunnel", "edges": [{"kind": "edge", "type": "line", "mid": [20, -10, 15], "length": 30, "start": [20, -10, 30], "end": [20, -10, 0]}],
              "radius": 1, "radius_end": 3}]
    M._BASE_BUILDS.clear(); M._EDITED.clear()
    project.set_edits(first)
    _shapes(project)                                              # keeps the result of the two edits
    chamfer = {"op": "chamfer", "part": "tunnel", "edges": [{"kind": "edge", "type": "line", "mid": [-20, 0, 30], "length": 20, "start": [-20, -10, 30], "end": [-20, 10, 30]}],
               "size": 2, "size2": 1}
    project.set_edits(first + [chamfer])
    _, notes, _ = _shapes(project)
    assert notes[-1].startswith("[applied] chamfer"), notes
