"""An electronics project through the rest of eitri: the build subprocess, the project and its direct edits, the agent's tools and
prompt, editor selections for the agent, the CLI (build, bom, --config) and the registry."""
from __future__ import annotations

import json
from pathlib import Path

from eitri.agent.prompt import system_prompt
from eitri.agent.tools import Tools
from eitri.kernel import build_model
from eitri.project import Project

DEMO = Path(__file__).resolve().parents[1] / "eitri" / "demos" / "uno-arm-circuit" / "uno_arm_circuit.py"
MODEL = DEMO.read_text(encoding="utf-8")


def project(tmp_path, code=MODEL, name="arm") -> Project:
    pr = Project(tmp_path / name, name=name)
    pr.write_model(code)
    return pr


def checks_of(res):
    return {c["name"]: c for c in res.domain_report["checks"]}


def test_registry():
    from eitri import domains
    assert "electronics" in domains.available() and domains.kind_of_source("from eitri.electronics import *\n") == "electronics"
    assert domains.op_owner("set_gpio") == "electronics" and domains.label("electronics") == "circuit"
    from eitri.exports import export_formats
    fmts = [f["fmt"] for f in export_formats("electronics")]
    assert fmts == ["svg", "png", "bom", "bom_md", "netlist", "netlist_md", "sources"]


def test_kind_and_build_subprocess(tmp_path):
    pr = project(tmp_path)
    assert pr.kind == "electronics"
    res = build_model(pr.model_path, pr.out_dir)
    assert res.ok and res.kind == "electronics" and res.report is None
    assert {"svg", "png", "bom_md", "csv", "netlist_md", "netlist_csv", "sources_md"} <= set(res.outputs)
    assert all(Path(v).is_file() for v in res.outputs.values())
    assert res.summary.startswith("Circuit 'Uno arm electronics': 18 parts")
    data = json.loads((pr.out_dir / "result.json").read_text())
    assert data["kind"] == "electronics" and data["electronics"]["scene"]["nodes"]["UNO"]
    c = checks_of(res)
    assert c["status LED current (mA)"]["category"] == "verify" and c["flyback diode K1 (relay coil)"]["severity"] == "pass"


def test_failing_checks_do_not_fail_the_build(tmp_path):
    pr = project(tmp_path, MODEL.replace('servo_supply="psu", flyback=True', 'servo_supply="uno", flyback=False'))
    res = build_model(pr.model_path, pr.out_dir)
    assert res.ok
    c = checks_of(res)
    assert c["rail budget 5V (UNO.5V)"]["severity"] == "fail" and c["flyback diode K1 (relay coil)"]["severity"] == "fail"
    bad = project(tmp_path, MODEL.replace('c.resistor("R1", p.led_ohm)', 'c.resistor("R1", 0)'), name="bad")
    res = build_model(bad.model_path, bad.out_dir)
    assert not res.ok and "more than 0" in res.error


def test_project_edits(tmp_path):
    pr = project(tmp_path)
    assert pr.build(note="first", render=False).ok
    pr.add_edit({"op": "set_part", "part": "R1", "ohm": 100})
    pr.add_edit({"op": "set_part", "part": "R1", "ohm": 120})                      # the same part: one edit
    assert len(pr.edits()) == 1
    res = pr.build(note="edits", render=False)
    assert res.ok and all(n["ok"] for n in res.edit_notes)
    assert checks_of(res)["pin current UNO.D13"]["severity"] == "fail"           # (5 - 1.85) / 120 = 26 mA
    r1 = next(p for p in res.domain["parts"] if p["name"] == "R1")
    assert r1["spec"]["ohm"] == 120


def test_tools(tmp_path):
    pr = Project(tmp_path / "t", name="t")
    tools = Tools(pr)
    res = tools.build_model(MODEL, "first")
    assert not res.is_error and "Circuit 'Uno arm electronics'" in res.text and res.images and "schematic" in res.text
    out = tools.inspect("rails()\ncurrents()\npart('Q1')\nnets()")
    assert not out.is_error, out.text
    assert "SERVO_5V: 5 V from PSU5.+" in out.text and "LED_A -> R1 -> LED1 -> GND: 9.55 mA" in out.text
    assert "rds_on" in out.text and "Infineon IRLZ44NPbF" in out.text and "COIL_LOW: switched" in out.text
    bad = tools.set_params({"flyback": False})
    assert "ATTENTION" in bad.text and "never delete or loosen a check" in bad.text
    assert not tools.view().is_error and "netlist" in tools.export([]).text
    for r in (tools.drawing(), tools.cam()):
        assert r.is_error and "circuit" in r.text
    ref = tools.electronics_reference().text
    assert ref.startswith("# Electronics reference") and "| MG996R | servo |" in ref and "{{" not in ref


def test_tool_schemas():
    import asyncio
    from eitri.agent.api_backend import TOOL_SCHEMAS
    from eitri.agent.mcp_server import make_server
    from eitri.agent.tools import TOOL_NAMES
    assert "electronics_reference" in TOOL_NAMES and "electronics_reference" in {t["name"] for t in TOOL_SCHEMAS}
    server = make_server(Tools(Project(Path("/tmp/eitri-elec-schema"), name="x")))
    assert "electronics_reference" in {t.name for t in asyncio.run(server.list_tools())}


def test_prompt_and_selection(tmp_path):
    pr = project(tmp_path)
    prompt = system_prompt(pr)
    assert "expert electronics engineer" in prompt and "# Electronics reference" in prompt and "Feature library" not in prompt
    assert "flyback diode" in prompt and "never by deleting or loosening a check" in prompt
    cad = system_prompt(None)
    assert "electronics_reference()" in cad
    pr.build(note="first", render=False)
    from eitri.agent.hub import AgentHub
    text = AgentHub(pr, lambda h: None).compose("brighter", ["part:R1", "net:SERVO_5V", "part:nope"])
    assert "- part R1: resistor 330 Ω" in text and "- net SERVO_5V (rail, 5 V)" in text and "- part:nope (no longer exists)" in text


def test_cli(tmp_path, capsys):
    from eitri.cli import main
    pr = project(tmp_path)
    assert main(["build", str(pr.model_path)]) == 0
    assert capsys.readouterr().out.startswith("Circuit 'Uno arm electronics'")
    main(["build", str(pr.model_path), "--config", "bad"])
    out = capsys.readouterr().out
    assert "Checks: 27 pass, 1 warn, 2 FAIL" in out
    assert main(["bom", str(pr.folder)]) == 0
    out = capsys.readouterr().out
    assert "# Bill of materials: Uno arm electronics" in out and "| 4 | S1 S2 S3 S4 | servo |" in out
