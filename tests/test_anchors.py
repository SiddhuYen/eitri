"""Anchors: named points and frames a model declares with anchor() inside build(p) land in the build result, state.json and the
summary; they survive a build that reuses the kept geometry (a direct edit); mistakes are build errors that name the anchor."""
from __future__ import annotations

import json

from eitri import anchors as A
from eitri.kernel import build_model
from eitri.project import Project
from eitri.viewer.server import _state

MODEL = '''from eitri import *

PARAMS = dict(width=40.0)

def build(p):
    base = Box(p.width, 30, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    post = Pos(0, 0, 6) * Cylinder(4, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    anchor("UNO", at=(10, 0, 6), z=(0, 0, 1), x=(1, 1, 0), part="base")        # x is made perpendicular to z (it already is here)
    anchor("tip", at=Vector(0, 0, 26), part="post")
    anchor("Frame", at=Pos(5, 5, 6) * Rot(0, 0, 90))                            # a Location gives the point, z and x
    anchor("bus.V5", (-10, 0, 6), z=(0, 0, 2))                                  # z is normalised
    return {"base": base, "post": post}

EDITS = []
'''


def test_anchors_in_the_result_state_and_summary(tmp_path):
    m = tmp_path / "m.py"
    m.write_text(MODEL)
    res = build_model(m, tmp_path / "out", render=False)
    assert res.ok, res.error
    by = {a["name"]: a for a in res.anchors}
    assert list(by) == ["UNO", "tip", "Frame", "bus.V5"]                 # in the order declared
    assert by["UNO"]["at"] == [10, 0, 6] and by["UNO"]["z"] == [0, 0, 1] and by["UNO"]["part"] == "base"
    assert [round(v, 6) for v in by["UNO"]["x"]] == [round(2 ** -0.5, 6)] * 2 + [0]
    assert by["tip"] == {"name": "tip", "at": [0, 0, 26], "z": None, "x": None, "part": "post"}
    assert by["Frame"]["at"] == [5, 5, 6] and [round(v) for v in by["Frame"]["z"]] == [0, 0, 1] and [round(v) for v in by["Frame"]["x"]] == [0, 1, 0]
    assert by["bus.V5"]["z"] == [0, 0, 1] and by["bus.V5"]["part"] is None
    assert "Anchors:" in res.summary and "UNO: (10, 0, 6) z (0, 0, 1) on base" in res.summary
    data = json.loads((tmp_path / "out" / "result.json").read_text())
    assert data["anchors"] == res.anchors


def test_anchors_survive_a_reused_build_and_show_in_state(tmp_path):
    project = Project(tmp_path / "p", name="m")
    project.write_model(MODEL)
    first = project.build(render=False)
    assert first.ok and [a["name"] for a in first.anchors] == ["UNO", "tip", "Frame", "bus.V5"]
    project.add_edit({"op": "push_pull", "part": "base", "face": {"normal": [0, 0, 1], "center": [0, 0, 6]}, "distance": 2})   # build(p) is reused
    second = project.build(render=False)
    assert second.ok, second.error
    assert second.anchors == first.anchors
    st = _state(project)
    assert [a["name"] for a in st["anchors"]] == ["UNO", "tip", "Frame", "bus.V5"]


def test_a_model_without_anchors_has_none(tmp_path):
    m = tmp_path / "plain.py"
    m.write_text("from eitri import *\nPARAMS = dict()\ndef build(p):\n    return Box(10, 10, 10)\n")
    res = build_model(m, tmp_path / "out", render=False)
    assert res.ok and res.anchors == []
    assert "Anchors" not in res.summary


def test_anchor_mistakes_are_build_errors_naming_the_anchor(tmp_path):
    for body, needle in [
        ('anchor("a", at=(1, 2, 3)); anchor("A", at=(0, 0, 0))', "declared twice"),
        ('anchor("a", at=(1, 2))', "three numbers"),
        ('anchor("a", at=(1, 2, 3), z=(0, 0, 0))', "zero vector"),
        ('anchor("a", at=(1, 2, 3), z=(0, 0, 1), x=(0, 0, 1))', "parallel to z"),
        ('anchor("", at=(1, 2, 3))', "a name is required"),
    ]:
        m = tmp_path / "bad.py"
        m.write_text(f"from eitri import *\nPARAMS = dict()\ndef build(p):\n    {body}\n    return Box(10, 10, 10)\n")
        res = build_model(m, tmp_path / "out", render=False)
        assert not res.ok and needle in res.error, (body, res.error)


def test_anchor_outside_a_build_is_just_a_value():
    a = A.anchor("probe", (1, 2, 3), z=(0, 1, 0))
    assert a.name == "probe" and a.at == (1, 2, 3) and a.z == (0, 1, 0) and a.x is None
    assert A.take() == []                                              # nothing was being collected
