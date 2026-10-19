"""Parameters as expressions, named configurations, mass properties (inertia), revision compare and the
turntable render."""
from __future__ import annotations

import math

import build123d as bd
import pytest

from eitri import measure
from eitri.model import Part, Parts, load_model, resolve_expressions, Params
from eitri.project import Project
from eitri.render import turntable
from eitri.report import analyze, summarize

MODEL = '''from eitri import *

PARAMS = dict(width=40.0, depth="width / 2", height="max(depth / 4, 6)", hole_d=5.0)
CONFIGS = {"small": {"width": 20.0}, "large": {"width": 80.0, "hole_d": 8.0}}
MATERIALS = {"plate": "aluminum"}

def build(p):
    plate = Box(p.width, p.depth, p.height, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return {"plate": cut_holes(plate, [(0, 0)], d=p.hole_d)}
'''


def test_expressions_and_configs(tmp_path):
    f = tmp_path / "plate.py"
    f.write_text(MODEL, encoding="utf-8")
    m = load_model(f)
    assert m.params.width == 40 and m.params.depth == 20 and m.params.height == 6 and m.param_exprs == {"depth": "width / 2", "height": "max(depth / 4, 6)"}
    assert list(m.configs) == ["small", "large"]
    parts = m.build()
    assert abs(parts["plate"].bounding_box().size.Y - 20) < 1e-6
    big = load_model(f, config="large")
    assert big.params.width == 80 and big.params.depth == 40 and big.params.height == 10 and big.params.hole_d == 8 and big.config == "large"
    over = load_model(f, {"width": "60"}, config="small")                                     # explicit overrides win over the configuration
    assert over.params.width == 60 and over.params.depth == 30
    with pytest.raises(KeyError):
        load_model(f, config="huge")
    with pytest.raises(ValueError):
        resolve_expressions(Params({"a": "b + 1", "b": "a * 2"}))
    with pytest.raises(ValueError):
        resolve_expressions(Params({"a": "__import__('os')"}))
    # a project applies a configuration by rewriting PARAMS, keeping expressions as expressions
    project = Project(tmp_path / "proj", name="plate")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    assert project.configs() == {"small": {"width": 20.0}, "large": {"width": 80.0, "hole_d": 8.0}}
    new = project.apply_config("large")
    assert new["width"] == 80.0 and new["depth"] == "width / 2"
    res = project.build(note="large", render=False)
    assert res.ok and abs(res.report.parts[0].size[1] - 40) < 1e-6
    project.set_params({"width": 50, "depth": "width * 0.75", "hole_d": "width / 10"})          # an expression on a numeric parameter too
    res = project.build(note="expr", render=False)
    assert res.ok and abs(res.report.parts[0].size[1] - 37.5) < 1e-6
    # compare: the last two successful builds
    cmp = project.compare()
    assert cmp["a"] < cmp["b"]
    plate = next(r for r in cmp["parts"] if r["name"] == "plate")
    assert plate["status"] == "changed" and abs(plate["before"]["bbox"][4] - 20) < 1e-6 and abs(plate["after"]["bbox"][4] - 18.75) < 1e-6
    assert "plate changed" in cmp["summary"] and "-    width=80.0" in cmp["source_diff"] and "+    width=50" in cmp["source_diff"]
    cmp2 = project.compare(1, 2)
    assert cmp2["a"] == 1 and cmp2["b"] == 2 and "width=80.0" in cmp2["source_diff"]


def test_mass_properties_and_report():
    box = bd.Box(20, 10, 5)
    mp = measure.mass_properties(box, "aluminum")
    assert abs(mp["volume"] - 1000) < 1e-6 and abs(mp["mass_g"] - 2.7) < 1e-6 and mp["center_of_mass"] == [0, 0, 0]
    rho = 2.7 / 1000
    assert abs(mp["inertia"][0][0] - 1000 * (10 ** 2 + 5 ** 2) / 12 * rho) < 1e-6 and abs(mp["inertia"][2][2] - 1000 * (20 ** 2 + 10 ** 2) / 12 * rho) < 1e-6
    assert mp["principal"][0] >= mp["principal"][1] >= mp["principal"][2] and mp["units"] == "g*mm^2"
    rep = analyze(Parts([Part("block", box, "#999", "aluminum"), Part("plain", bd.Box(5, 5, 5), "#999")]))
    assert rep.parts[0].inertia and rep.parts[0].inertia["principal"][0] > 0 and rep.parts[1].inertia is None
    assert "Mass properties" in summarize(rep) and "block: 2.7 g, principal moments" in summarize(rep)


def test_turntable_gif(tmp_path):
    parts = Parts([Part("block", bd.Box(20, 10, 5), "#cc3333")])
    path = turntable(parts, str(tmp_path / "turn.gif"), frames=6, size=(120, 90))
    from PIL import Image
    im = Image.open(path)
    assert im.format == "GIF" and getattr(im, "n_frames", 1) == 6 and im.size == (120, 90)


def test_text_params_stay_text():
    """Plain text values (a screw size, a label) are values, not expressions; real expressions and typos in them
    still resolve or fail as before."""
    out, exprs = resolve_expressions(Params({"width": 40.0, "screw": "M3", "insert": "M2.5", "label": "MODEL A",
                                             "material": "PLA", "half": "width / 2", "name": "'quoted'"}))
    assert out.screw == "M3" and out.insert == "M2.5" and out.label == "MODEL A" and out.material == "PLA"
    assert out.half == 20 and exprs == {"half": "width / 2"}
    with pytest.raises(ValueError):
        resolve_expressions(Params({"width": 40.0, "half": "widht / 2"}))
