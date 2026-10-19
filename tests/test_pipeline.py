"""Build pipeline, project revisions, parameter edits and tools - without an LLM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eitri.agent.tools import Tools
from eitri.kernel import build_model
from eitri.project import Project

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

PLATE = '''from eitri import *

PARAMS = dict(width=50.0, depth=30.0, thickness=4.0, hole_d=3.4)

def build(p):
    plate = Box(p.width, p.depth, p.thickness, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return cut_holes(plate, [(-15, 0), (15, 0)], d=p.hole_d)

def verify(parts, p, c):
    c.equal("width", parts.size.X, p.width)
    c.count("holes", holes(parts[0], diameter=p.hole_d), 2)
'''


def test_build_examples(tmp_path):
    for name in ("bracket", "enclosure"):
        res = build_model(EXAMPLES / f"{name}.py", tmp_path / name, render=True)
        assert res.ok, res.error
        assert res.report is not None and res.report.ok
        assert Path(res.outputs["step"]).stat().st_size > 1000
        assert Path(res.outputs["glb"]).stat().st_size > 500
        assert Path(res.outputs["png"]).stat().st_size > 5000
        assert all(c.ok for c in res.report.checks)
    enc = build_model(EXAMPLES / "enclosure.py", tmp_path / "enc2", render=False)
    assert [p.name for p in enc.report.parts] == ["base", "lid"]
    import json as _json, struct as _struct
    glb = Path(enc.outputs["glb"]).read_bytes()
    js = _json.loads(glb[20:20 + _struct.unpack_from("<I", glb, 12)[0]])
    assert {n.get("name") for n in js["nodes"]} >= {"base", "lid"}      # part names survive into the GLB
    assert enc.report.pairs and enc.report.pairs[0].overlap_volume == 0


def test_build_errors_are_readable(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from eitri import *\nPARAMS = {}\ndef build(p):\n    return Box(10, 10, 10) - Cylinder(x, 5)\n")
    res = build_model(bad, tmp_path / "out", render=False)
    assert not res.ok
    assert "NameError" in res.error and "bad.py" in res.error
    none = tmp_path / "none.py"
    none.write_text("from eitri import *\nPARAMS = {}\ndef build(p):\n    return None\n")
    res = build_model(none, tmp_path / "out2", render=False)
    assert not res.ok and "no geometry" in res.error


def test_param_override_and_timeout(tmp_path):
    m = tmp_path / "plate.py"
    m.write_text(PLATE)
    res = build_model(m, tmp_path / "out", params={"width": "80"}, render=False)
    assert res.ok and res.report.size[0] == 80
    with pytest.raises(KeyError):
        from eitri.model import load_model
        load_model(m, {"nope": "1"})
    slow = tmp_path / "slow.py"
    slow.write_text("from eitri import *\nimport time\nPARAMS = {}\ndef build(p):\n    time.sleep(5)\n    return Box(1,1,1)\n")
    res = build_model(slow, tmp_path / "out3", render=False, timeout=1.5)
    assert not res.ok and "timed out" in res.error


def test_project_revisions_params_undo(tmp_path):
    project = Project(tmp_path / "proj", name="plate")
    project.write_model(PLATE)
    r1 = project.build(note="first")
    assert r1.ok and project.revision == 1
    assert project.current_params()["width"] == 50.0
    project.set_params({"width": "64", "hole_d": 4.5})
    assert "width=64.0" in project.read_model() and "hole_d=4.5" in project.read_model()
    r2 = project.build(note="wider")
    assert r2.ok and r2.report.size[0] == 64 and project.revision == 2
    # undo: restore revision 1's file
    project.restore(1)
    assert project.current_params()["width"] == 50.0
    assert [h.note for h in project.history] == ["first", "wider"]
    # a second Project instance sees the same state (cross-process reload)
    other = Project(tmp_path / "proj")
    assert other.revision == 2 and other.model_path.name == "plate.py"
    # scratch python
    ok, out = project.run_python("print(len(holes(parts[0])), round(parts.size.X, 1))")
    assert ok and out.strip() == "2 50.0"
    ok, out = project.run_python("raise ValueError('boom')")
    assert not ok and "boom" in out


def test_tools_build_edit_inspect_view(tmp_path):
    project = Project(tmp_path / "proj", name="plate")
    tools = Tools(project)
    res = tools.build_model(PLATE, note="init")
    assert not res.is_error and res.images and "[PASS] width" in res.text
    res = tools.edit_model("hole_d=3.4", "hole_d=5.5", note="bigger holes")
    assert not res.is_error and "Ø5.5" in res.text
    res = tools.edit_model("not in file", "x")
    assert res.is_error
    res = tools.set_params({"width": 70})
    assert not res.is_error and "70" in res.text and project.current_params()["width"] == 70
    res = tools.inspect("print(parts.size.X)")
    assert not res.is_error and res.text.strip() == "70.0"
    res = tools.view(views=["bottom"], section="XZ:0")
    assert not res.is_error and res.images and Path(res.images[0]).exists()
    res = tools.view(exploded=True, views=["iso"])
    assert not res.is_error
    res = tools.library("cut_holes")
    assert "cut_holes(" in res.text
    res = tools.export(["stl", "3mf"])
    assert not res.is_error and "stl" in res.text and "3mf" in res.text
    res = tools.read_model()
    assert "def build" in res.text
    bad = tools.build_model("from eitri import *\nPARAMS={}\ndef build(p):\n    return Box(1,1,1) - Cylinder(q, 1)\n")
    assert bad.is_error and "NameError" in bad.text


def test_existing_step_file_as_model(tmp_path):
    """Any STEP file can be built/reported/rendered like a model (no parameters)."""
    res = build_model(EXAMPLES / "enclosure.py", tmp_path / "src", render=False)
    assert res.ok
    step = Path(res.outputs["step"])
    res2 = build_model(step, tmp_path / "from_step", render=True)
    assert res2.ok, res2.error
    assert [p.name for p in res2.report.parts] == ["base", "lid"]
    assert Path(res2.outputs["png"]).exists()
    tools = Tools(Project(tmp_path / "p", name="x"))
    from eitri.model import load_model
    m = load_model(step)
    assert m.params == {} and len(m.build()) == 2
