"""A mechanism through the rest of eitri: the build subprocess, the project (kind, revisions, direct edits), the agent's tools and
system prompt, editor selections for the agent, and the CLI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eitri.agent.prompt import system_prompt
from eitri.agent.tools import Tools
from eitri.kernel import build_model
from eitri.project import Project

MODEL = '''from eitri.mechanism import *

PARAMS = dict(arm_mm=100.0, payload_g=100.0)


def build(p):
    m = Mechanism("Lever")
    m.mount("edge")                                       # a lever at a bench edge: it swings below its pivot
    m.link("base", fixed=True)
    m.link("arm", length_mm=p.arm_mm, mass_g=12.4)
    m.joint("j1", "base", "arm", axis=(0, -1, 0), at=(0, 0, 0), limit_deg=(-90, 90), actuator=Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=400, travel_deg=180))
    m.payload(mass_g=p.payload_g)
    m.target("up", (0, 0, p.arm_mm))
    m.poses = 4
    return m


def verify(m, p, c):
    c.at_most("load at level (kg·cm)", joint_loads(m, {})["j1"], 5.0)
'''
CAD = "from eitri import *\n\nPARAMS = dict(w=10)\n\ndef build(p):\n    return Box(p.w, 10, 10)\n"


def project(tmp_path, code=MODEL, name="lever") -> Project:
    pr = Project(tmp_path / name, name=name)
    pr.write_model(code)
    return pr


def checks_of(res):
    return {c["name"]: c for c in res.domain_report["checks"]}


def test_kind_and_build_subprocess(tmp_path):
    pr = project(tmp_path)
    assert pr.kind == "mechanism" and project(tmp_path, CAD, name="plate").kind == "cad"
    res = build_model(pr.model_path, pr.out_dir)
    assert res.ok and res.kind == "mechanism" and res.report is None and res.harness_report is None
    assert {"svg", "png", "joints_md", "joints_csv", "mass_md", "mass_csv"} <= set(res.outputs) and all(Path(v).is_file() for v in res.outputs.values())
    assert res.domain_report["name"] == "Lever" and res.domain["joints"][0]["name"] == "j1" and res.domain["scene"]["nodes"]["j1"]["kind"] == "joint"
    assert res.summary.startswith("Mechanism 'Lever'")
    data = json.loads((pr.out_dir / "result.json").read_text())
    assert data["kind"] == "mechanism" and data["mechanism"]["targets"][0]["reachable"] is True
    assert checks_of(res)["torque joint j1 (MG996R)"]["severity"] == "pass" and checks_of(res)["load at level (kg·cm)"]["category"] == "verify"


def test_failing_checks_do_not_fail_the_build_and_errors_are_readable(tmp_path):
    pr = project(tmp_path)
    res = build_model(pr.model_path, pr.out_dir, params={"payload_g": "1500"})
    assert res.ok and checks_of(res)["torque joint j1 (MG996R)"]["severity"] == "fail" and checks_of(res)["load at level (kg·cm)"]["severity"] == "fail"
    bad = project(tmp_path, MODEL.replace('m.joint("j1", "base", "arm"', 'm.joint("j1", "base", "ghost"'), name="bad")
    res = build_model(bad.model_path, bad.out_dir)
    assert not res.ok and "no link 'ghost'" in res.error and "Traceback" not in res.error


def test_project_build_edits_and_compare(tmp_path):
    pr = project(tmp_path)
    assert pr.build(note="first").ok and pr.revision == 1 and Path(pr.history[-1].outputs["png"]).is_file()
    pr.add_edit({"op": "set_link", "link": "arm", "length_mm": 150})
    pr.add_edit({"op": "set_link", "link": "arm", "mass_g": 20})                     # the same link: one edit
    pr.add_edit({"op": "set_joint", "joint": "ghost", "limit": [0, 1]})
    assert len(pr.edits()) == 2
    res = pr.build(note="edits")
    assert res.ok and [n["ok"] for n in res.edit_notes] == [True, False] and "ghost" in res.edit_notes[1]["detail"]
    links = {l["name"]: l for l in res.domain["links"]}
    assert links["arm"]["length_mm"] == 150 and links["arm"]["mass_g"] == 20 and "Direct edits" in res.summary
    assert any("direct edit failed" in i for i in res.domain_report["issues"])
    cmp = pr.compare()
    assert any(r["name"] == "link arm" and r["status"] == "changed" for r in cmp["parts"]) and "differ" in cmp["summary"]


def test_tools_build_edit_params_inspect_view_export(tmp_path):
    pr = Project(tmp_path / "t", name="t")
    tools = Tools(pr)
    res = tools.build_model(MODEL, "first")
    assert not res.is_error and "Mechanism 'Lever'" in res.text and res.images and Path(res.images[0]).is_file() and "kinematic diagram" in res.text and "ATTENTION" not in res.text
    assert pr.kind == "mechanism"
    out = tools.inspect("joints()\nlinks()\nfk(j1=90)\ntorque(j1=0)\nreach('up')\nmasses()\nprint(len(m.joints), p.arm_mm)")
    assert not out.is_error, out.text
    assert "j1 (revolute): -90..90 deg; needs" in out.text and "tip: (0.0, 0.0, 100.0)" in out.text and "j1: 1.062 kg·cm" in out.text and "closest 0.0" in out.text and "1 100.0" in out.text
    bad = tools.set_params({"payload_g": 1500})
    assert not bad.is_error and "ATTENTION" in bad.text and "FAIL" in bad.text and "link lengths, masses, actuators" in bad.text
    fixed = tools.edit_model("stall_kgcm=9.4", "stall_kgcm=9.4, gear_ratio=3", "geared")
    assert not fixed.is_error and "ATTENTION" not in fixed.text.split("Checks:")[0]
    v = tools.view()
    assert not v.is_error and v.images and v.images[0].endswith(".png")
    e = tools.export(["step"])
    assert not e.is_error and "joints_md" in e.text and "mass_csv" in e.text
    for r in (tools.drawing(), tools.cam(), tools.print_prep(), tools.cut_file()):
        assert r.is_error and "mechanism" in r.text
    ref = tools.mechanism_reference()
    assert ref.text.startswith("# Mechanism reference") and "Worked example 2: a pan-tilt" in ref.text
    assert "Wire harness reference" in tools.harness_reference().text


def test_tools_edits_list_update_remove(tmp_path):
    pr = project(tmp_path)
    pr.add_edit({"op": "set_target", "target": "up", "tol_mm": 5})
    tools = Tools(pr)
    assert "target up: tol_mm = 5" in tools.list_edits().text
    assert not tools.update_edit(1, {"tol_mm": 8}).is_error and pr.edits()[0]["tol_mm"] == 8 and pr.edits()[0]["by"] == "claude"
    assert not tools.remove_edit(1).is_error and pr.edits() == []


def test_system_prompts(tmp_path):
    prompt = system_prompt(project(tmp_path))
    assert "expert mechanism designer" in prompt and "# Mechanism reference" in prompt and "Current model file" in prompt
    assert "from eitri.mechanism import *" in prompt and "build123d" not in prompt.split("# Mechanism reference")[0] and "Feature library" not in prompt
    cad = system_prompt(project(tmp_path, CAD, name="plate"))
    assert "mechanism_reference()" in cad and "pneumatics_reference()" in cad and "firmware_reference()" in cad and "# Mechanism reference" not in cad


def test_chat_selection_is_described_for_the_agent(tmp_path):
    from eitri.agent.hub import AgentHub
    pr = project(tmp_path)
    pr.build(note="first", render=False)
    text = AgentHub(pr, lambda handler: None).compose("make this stronger", ["joint:j1", "link:arm", "target:up", "joint:ghost"])
    assert text.startswith("Selected in the editor (revision 1):\n- joint j1: revolute base -> arm, axis [0.0, -1.0, 0.0], limits -90.0..90.0 deg, actuator MG996R stall 9.4")
    assert "- link arm: length 100 mm, mass 12.4 g" in text and "- target up: at [0.0, 0.0, 100.0]" in text and "reached" in text and "- joint:ghost (no longer exists)" in text and text.endswith("make this stronger")


def test_tool_registration():
    import asyncio
    import tempfile
    from eitri.agent.api_backend import TOOL_SCHEMAS
    from eitri.agent.mcp_server import make_server
    assert {"mechanism_reference", "pneumatics_reference", "firmware_reference", "harness_reference"} <= {t["name"] for t in TOOL_SCHEMAS}
    server = make_server(Project(Path(tempfile.mkdtemp()) / "p", name="p"))
    assert {"mechanism_reference", "pneumatics_reference", "firmware_reference"} <= {t.name for t in asyncio.run(server.list_tools())}


def test_cli_build_and_bom(tmp_path, capsys):
    from eitri.cli import main
    pr = project(tmp_path)
    assert main(["build", str(pr.model_path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Mechanism 'Lever'") and "png:" in out and "joints_csv:" in out
    assert main(["bom", str(pr.folder)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Mass rollup: Lever") and "| payload |" in out
    assert main(["bom", str(pr.folder), "--csv"]) == 0
    assert capsys.readouterr().out.startswith("item,kind,on_link,mass_g")
    assert main(["bom", str(project(tmp_path, CAD, name="plate").folder)]) == 2
    assert "CAD model" in capsys.readouterr().err


def test_harness_and_cad_unaffected(tmp_path):
    hp = Project(tmp_path / "h", name="h")
    hp.write_model('from eitri.harness import *\nPARAMS = dict()\ndef build(p):\n    h = Harness("x")\n    a = h.add(Connector("A", "JST-XH", pins=2)); b = h.add(Connector("B", "JST-XH", pins=2))\n    h.net("V", current_A=1, voltage_V=5)\n    h.connect(a[1], b[1], Wire(22, "RD", 100), net="V"); h.connect(a[2], b[2], Wire(22, "BK", 100), net="V")\n    return h\n')
    assert hp.kind == "harness"
    res = hp.build(note="h")
    assert res.ok and res.kind == "harness" and res.harness_report is not None and res.domain_report is res.harness_report and res.domain is res.harness
    cp = project(tmp_path, CAD, name="plate")
    res = cp.build(note="c", render=False)
    assert res.ok and res.kind == "cad" and res.report is not None and res.domain is None
