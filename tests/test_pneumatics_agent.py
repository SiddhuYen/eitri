"""A pneumatic circuit through the rest of eitri: the build subprocess, the project, the agent's tools and prompt, editor
selections for the agent, and the CLI."""
from __future__ import annotations

import json
from pathlib import Path

from eitri.agent.prompt import system_prompt
from eitri.agent.tools import Tools
from eitri.kernel import build_model
from eitri.project import Project

MODEL = '''from eitri.pneumatics import *

PARAMS = dict(p_bar=6.0, bore_mm=32, load_N=250)


def build(p):
    c = Circuit("Pusher")
    air = c.supply("AIR", pressure_bar=7.0, flow_l_min=600)
    frl = c.frl("FRL", set_bar=p.p_bar, C=2.5, b=0.35)
    v = c.valve("V1", "5/2", C=1.2, b=0.3)
    cyl = c.cylinder("C1", bore_mm=p.bore_mm, rod_mm=12, stroke_mm=100, load_N=p.load_N, load_retract_N=100, speed_mm_s=200, cycles_per_min=20)
    c.tube("T1", air["OUT"], frl["IN"], id_mm=8, length_m=2.0, C=3.0)
    c.tube("T2", frl["OUT"], v["P"], id_mm=6, length_m=1.0, C=2.0)
    c.tube("T3", v["A"], cyl["CAP"], id_mm=6, length_m=1.5, C=1.8)
    c.tube("T4", v["B"], cyl["ROD"], id_mm=6, length_m=1.5, C=1.8)
    c.silencer(v["R"], C=3, b=0.3)
    c.silencer(v["S"], C=3, b=0.3)
    return c


def verify(c, p, chk):
    chk.at_least("force margin", cylinder_forces(c.get("C1"), p.p_bar)["extend_usable_N"] / p.load_N, 1.3)
'''
CAD = "from eitri import *\n\nPARAMS = dict(w=10)\n\ndef build(p):\n    return Box(p.w, 10, 10)\n"


def project(tmp_path, code=MODEL, name="pusher") -> Project:
    pr = Project(tmp_path / name, name=name)
    pr.write_model(code)
    return pr


def checks_of(res):
    return {c["name"]: c for c in res.domain_report["checks"]}


def test_kind_and_build_subprocess(tmp_path):
    pr = project(tmp_path)
    assert pr.kind == "pneumatics"
    res = build_model(pr.model_path, pr.out_dir)
    assert res.ok and res.kind == "pneumatics" and res.report is None
    assert {"svg", "png", "bom_md", "csv", "cylinders_md", "cylinders_csv"} <= set(res.outputs) and all(Path(v).is_file() for v in res.outputs.values())
    assert res.summary.startswith("Pneumatic circuit 'Pusher'") and res.domain["scene"]["nodes"]["C1"]["kind"] == "component"
    data = json.loads((pr.out_dir / "result.json").read_text())
    assert data["kind"] == "pneumatics" and data["pneumatics"]["tubes"][0]["name"] == "T1"
    c = checks_of(res)
    assert c["force C1 extend"]["severity"] == "pass" and c["force margin"]["category"] == "verify"
    assert data["report"]["cylinders"][0]["strokes"][0]["fill"]["names"] == ["T1", "FRL", "T2", "V1", "T3"]       # serialisable path info


def test_failing_checks_do_not_fail_the_build(tmp_path):
    pr = project(tmp_path)
    res = build_model(pr.model_path, pr.out_dir, params={"load_N": "600"})
    assert res.ok and checks_of(res)["force C1 extend"]["severity"] == "fail"
    bad = project(tmp_path, MODEL.replace('v["P"]', 'v["Q"]'), name="bad")
    res = build_model(bad.model_path, bad.out_dir)
    assert not res.ok and "no port 'Q'" in res.error


def test_project_edits_and_layout(tmp_path):
    pr = project(tmp_path)
    assert pr.build(note="first").ok
    pr.add_edit({"op": "set_component", "component": "C1", "bore_mm": 40})
    pr.add_edit({"op": "set_component", "component": "C1", "load_N": 500})              # the same component: one edit
    assert len(pr.edits()) == 1
    pr.set_layout({"V1": [300, 200]})
    res = pr.build(note="edits")
    assert res.ok and all(n["ok"] for n in res.edit_notes)
    cyl = next(x for x in res.domain["components"] if x["name"] == "C1")
    assert cyl["bore_mm"] == 40 and cyl["load_N"] == 500 and res.domain["layout"]["V1"] == [300.0, 200.0] and res.domain["scene"]["nodes"]["V1"]["x"] == 300.0
    assert any(r["name"] == "C1" and r["status"] == "changed" for r in pr.compare()["parts"])


def test_tools(tmp_path):
    pr = Project(tmp_path / "t", name="t")
    tools = Tools(pr)
    res = tools.build_model(MODEL, "first")
    assert not res.is_error and "Pneumatic circuit 'Pusher'" in res.text and res.images and "schematic" in res.text and "ATTENTION" not in res.text
    out = tools.inspect("cylinders()\npaths()\nprint(round(flow(1.0, 0.3, 7.0, 1.0), 3))\nprint(round(tube_C(6, 1.0)[0], 2))")
    assert not out.is_error, out.text
    assert "C1: Ø32/12 x 100 mm, double-acting, 6 bar at the cylinder" in out.text and "C1.CAP supply: T1 -> FRL -> T2 -> V1 -> T3" in out.text and "7.0" in out.text and "2.19" in out.text
    bad = tools.set_params({"load_N": 600})
    assert "ATTENTION" in bad.text and "bore, pressure, valve and tube sizes" in bad.text
    assert not tools.view().is_error and "cylinders_csv" in tools.export([]).text
    for r in (tools.drawing(), tools.cam()):
        assert r.is_error and "pneumatic circuit" in r.text
    assert tools.pneumatics_reference().text.startswith("# Pneumatics reference")


def test_prompt_and_selection(tmp_path):
    pr = project(tmp_path)
    prompt = system_prompt(pr)
    assert "expert pneumatic systems designer" in prompt and "# Pneumatics reference" in prompt and "Feature library" not in prompt
    pr.build(note="first", render=False)
    from eitri.agent.hub import AgentHub
    text = AgentHub(pr, lambda h: None).compose("bigger", ["component:C1", "tube:T3", "component:V1", "tube:nope"])
    assert "- C1: cylinder Ø32/12 x 100 mm, double-acting, load 250 N, speed 200 mm/s, 20 cycles/min; 6 bar at the cylinder; extend 434 N usable vs 250 N" in text
    assert "- tube T3: V1.A -> C1.CAP, 6 mm bore, 1.5 m, 2 fittings, C 1.8" in text and "- V1: 5/2 valve, solenoid / spring return, C 1.2 b 0.3" in text and "- tube:nope (no longer exists)" in text


def test_cli(tmp_path, capsys):
    from eitri.cli import main
    pr = project(tmp_path)
    assert main(["build", str(pr.model_path)]) == 0
    assert capsys.readouterr().out.startswith("Pneumatic circuit 'Pusher'")
    assert main(["bom", str(pr.folder)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Bill of materials: Pusher") and "pneumatic tube, 6 mm bore (ID)" in out and "sized by the tube's OD" in out
    assert main(["bom", str(pr.folder), "--csv"]) == 0
    assert capsys.readouterr().out.startswith("item,category,description")
