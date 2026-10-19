"""A blank design flows through the whole pipeline, and a ground-plane sketch makes the first part."""
from eitri.edits import apply_edits
from eitri.model import Parts, normalize_result
from eitri.project import Project
from eitri.report import analyze, summarize
from eitri.viewer.server import _state


def test_blank_design_pipeline(tmp_path):
    assert len(normalize_result({}, "m")) == 0 and len(normalize_result([], "m")) == 0
    rep = analyze(Parts([]))
    assert "0 part(s)" in summarize(rep)
    proj = Project(tmp_path / "blank", name="blank")
    assert proj.ensure_model() and not proj.ensure_model()
    res = proj.build(note="blank", render=True)
    assert res.ok, res.error
    assert "view_glb" in res.outputs and "png" in res.outputs and "step" not in res.outputs
    st = _state(proj, None)
    assert st["parts"] == [] and st["ok_revision"] == 1
    # the first part: a rectangle sketched on the ground, extruded 5 mm
    proj.add_edit({"op": "sketch_extrude", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [0, 0], "w": 20, "h": 10}, "distance": 5, "mode": "add", "new_part": "base"})
    res = proj.build(note="first part", render=False)
    assert res.ok and res.edit_notes[0]["ok"], res.edit_notes
    st = _state(proj, None)
    assert [p["name"] for p in st["parts"]] == ["base"] and abs(st["parts"][0]["volume"] - 1000) < 1e-3
    # and a second sketch on the ground joins the design as another part; cutting without a part is refused
    out, notes = apply_edits(Parts([]), [
        {"op": "sketch_extrude", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": {"type": "circle", "center": [30, 0], "r": 5}, "distance": 8, "new_part": "peg"},
        {"op": "sketch_extrude", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": {"type": "circle", "center": [30, 0], "r": 2}, "distance": 8, "mode": "cut"},
    ])
    assert out.names == ["peg"] and notes[0].ok and not notes[1].ok
