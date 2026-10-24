"""A harness through the rest of eitri: the build subprocess, the project (kind, revisions, direct edits, layout hints),
the agent's tools and system prompt, and the CLI."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from eitri.agent.prompt import system_prompt
from eitri.agent.tools import Tools
from eitri.kernel import build_model
from eitri.harness.report import analyze, summarize
from eitri.project import Project

MODEL = '''from eitri.harness import *

PARAMS = dict(length_mm=300, gauge=22)


def build(p):
    h = Harness("Lead")
    a = h.add(Connector("J1", "JST-XH", pins={"1": "GND", "2": "5V"}))
    b = h.add(Connector("J2", "JST-XH", pins={"1": "GND", "2": "5V"}))
    h.net("5V", current_A=1.0, voltage_V=5.0)
    h.net("GND", current_A=1.0, voltage_V=5.0, round_trip=False)
    h.connect(a["2"], b["2"], Wire(p.gauge, "RD", p.length_mm), net="5V")
    h.connect(a["1"], b["1"], Wire(p.gauge, "BK", p.length_mm), net="GND")
    return h


def verify(h, p, c):
    c.at_most("5V drop %", net_stats(h)["5V"].drop_pct, 3.0)
'''

BAD = MODEL.replace('Wire(p.gauge, "RD", p.length_mm)', 'Wire(p.gauge, "RD", p.length_mm)').replace("length_mm=300", "length_mm=4000")

CAD = "from eitri import *\n\nPARAMS = dict(w=10)\n\ndef build(p):\n    return Box(p.w, 10, 10)\n"


def project(tmp_path, code=MODEL, name="lead") -> Project:
    pr = Project(tmp_path / name, name=name)
    pr.write_model(code)
    return pr


def checks_of(res):
    return {c["name"]: c for c in res.harness_report["checks"]}


# --------------------------------------------------------------------------- the build subprocess


def test_build_model_subprocess(tmp_path):
    pr = project(tmp_path)
    res = build_model(pr.model_path, pr.out_dir)
    assert res.ok and res.kind == "harness" and res.report is None
    assert {"svg", "png", "csv", "bom_md", "cutlist_md", "cutlist_csv", "wireviz"} <= set(res.outputs)
    assert all(Path(v).is_file() for v in res.outputs.values())
    assert res.harness_report["name"] == "Lead" and res.harness_report["wires"] == 2
    assert res.harness["connectors"][0]["name"] == "J1" and set(res.harness["scene"]["nodes"]) == {"J1", "J2"}
    assert res.summary.startswith("Harness 'Lead'")
    data = json.loads((pr.out_dir / "result.json").read_text())
    assert data["kind"] == "harness" and data["ok"] and data["report"]["checks"][0]["severity"] in ("pass", "warn", "fail")


def test_build_no_render_skips_the_png_only(tmp_path):
    pr = project(tmp_path)
    res = build_model(pr.model_path, pr.out_dir, render=False)
    assert res.ok and "png" not in res.outputs and "svg" in res.outputs


def test_build_errors_are_readable(tmp_path):
    pr = project(tmp_path, MODEL.replace('h.connect(a["2"], b["2"]', 'h.connect(a["9"], b["2"]'))
    res = build_model(pr.model_path, pr.out_dir)
    assert not res.ok and "J1 has no pin #9" in res.error or "no pin '9'" in res.error
    assert "Traceback" not in (res.error or "")


def test_failing_checks_do_not_fail_the_build(tmp_path):
    pr = project(tmp_path, BAD)
    res = build_model(pr.model_path, pr.out_dir)
    assert res.ok                                           # the design is built and graded; the checks carry the verdict
    assert checks_of(res)["5V drop %"]["severity"] == "fail"
    assert any(c["severity"] == "fail" and c["category"] == "voltage drop" for c in res.harness_report["checks"])


def test_param_override(tmp_path):
    pr = project(tmp_path)
    res = build_model(pr.model_path, pr.out_dir, params={"length_mm": "4000"})
    assert res.ok and res.params["length_mm"] == 4000
    assert any(c["severity"] == "fail" for c in res.harness_report["checks"])


def test_cad_models_are_untouched(tmp_path):
    pr = project(tmp_path, CAD, name="plate")
    assert pr.kind == "cad"
    res = build_model(pr.model_path, pr.out_dir, render=False)
    assert res.ok and res.kind == "cad" and res.report is not None and res.harness_report is None


# --------------------------------------------------------------------------- project: kind, revisions, edits, layout


def test_project_kind(tmp_path):
    assert project(tmp_path).kind == "harness"
    assert project(tmp_path, CAD, name="plate").kind == "cad"
    assert Project(tmp_path / "blank").kind == "cad"           # a blank project is CAD until a harness is written


def test_project_build_records_a_revision(tmp_path):
    pr = project(tmp_path)
    res = pr.build(note="first")
    assert res.ok and pr.revision == 1
    rev = pr.history[-1]
    assert rev.ok and "svg" in rev.outputs and Path(rev.outputs["png"]).is_file()
    assert rev.summary.startswith("Harness 'Lead'")


def test_direct_edits_apply_and_merge(tmp_path):
    pr = project(tmp_path)
    pr.add_edit({"op": "set_wire", "wire": "W1", "gauge": 14})
    pr.add_edit({"op": "set_wire", "wire": "W1", "color": "OG"})      # the same wire: one edit
    pr.add_edit({"op": "set_wire", "wire": "W2", "length_mm": 450})
    edits = pr.edits()
    assert len(edits) == 2 and edits[0] == {"op": "set_wire", "wire": "W1", "gauge": 14, "color": "OG"}
    res = pr.build(note="edits")
    assert res.ok
    wires = {w["id"]: w for w in res.harness["wires"]}
    assert wires["W1"]["gauge"] == 14 and wires["W1"]["color"] == "OG" and wires["W2"]["length_mm"] == 450
    assert [n["ok"] for n in res.edit_notes] == [True, True]
    assert "Direct edits" in res.summary


def test_direct_edit_connector_and_pins(tmp_path):
    pr = project(tmp_path)
    for e in ({"op": "set_connector", "connector": "J2", "type": "jst ph", "part_number": "PHR-2"},
              {"op": "rename_pin", "connector": "J2", "pin": "2", "to": "VCC"},
              {"op": "add_pin", "connector": "J1", "pin": "3", "label": "SIG"},
              {"op": "nc", "pin": "J1.3"}):
        pr.add_edit(e)
    res = pr.build()
    assert res.ok and all(n["ok"] for n in res.edit_notes), res.edit_notes
    conns = {c["name"]: c for c in res.harness["connectors"]}
    assert conns["J2"]["type"] == "JST-PH" and conns["J2"]["pins"] == ["1", "VCC"] and conns["J2"]["part_number"] == "PHR-2"
    assert conns["J1"]["pins"] == ["1", "2", "3"] and conns["J1"]["nc"] == ["3"]
    wire = next(w for w in res.harness["wires"] if w["id"] == "W1")
    assert wire["b"] == {"connector": "J2", "pin": "VCC"}                  # the wire followed the renamed pin
    assert checks_of(res)["all pins connected or declared NC"]["severity"] == "pass"


def test_direct_edit_rename_connector_add_wire_net(tmp_path):
    pr = project(tmp_path)
    pr.add_edit({"op": "set_connector", "connector": "J1", "name": "SRC"})
    pr.add_edit({"op": "add_pin", "connector": "SRC", "pin": "3"})
    pr.add_edit({"op": "add_pin", "connector": "J2", "pin": "3"})
    pr.add_edit({"op": "set_net", "net": "SIG", "current_A": 0.01, "voltage_V": 5})
    pr.add_edit({"op": "add_wire", "a": "SRC.3", "b": "J2.3", "gauge": 24, "color": "YE", "length_mm": 300, "net": "SIG"})
    pr.add_edit({"op": "remove_wire", "wire": "W2"})
    res = pr.build()
    assert res.ok and all(n["ok"] for n in res.edit_notes), res.edit_notes
    assert {c["name"] for c in res.harness["connectors"]} == {"SRC", "J2"}
    assert [w["id"] for w in res.harness["wires"]] == ["W1", "W3"]
    assert any(n["name"] == "SIG" for n in res.harness["nets"])


def test_a_stale_edit_is_reported_not_lost(tmp_path):
    pr = project(tmp_path)
    pr.add_edit({"op": "set_wire", "wire": "W9", "gauge": 10})
    res = pr.build()
    assert res.ok                                                       # the build still succeeds
    assert [n["ok"] for n in res.edit_notes] == [False] and "W9" in res.edit_notes[0]["detail"]
    assert any("direct edit failed" in i for i in res.harness_report["issues"])
    assert pr.edits()[0]["wire"] == "W9"                               # and the edit is still in the file


def test_edits_change_the_checks(tmp_path):
    pr = project(tmp_path)
    assert pr.build().ok
    pr.add_edit({"op": "set_wire", "wire": "W1", "gauge": 30})
    res = pr.build()
    assert checks_of(res)["ampacity W1 J1.2->J2.2 30 AWG [5V]"]["severity"] == "warn" or any(
        c["severity"] in ("warn", "fail") for c in res.harness_report["checks"])


def test_layout_hints_persist_in_the_model_file(tmp_path):
    pr = project(tmp_path)
    pr.set_layout({"J2": [640.04, 120.0]})
    pr.set_layout({"J1": [40, 40]})
    assert pr.layout() == {"J2": [640.0, 120.0], "J1": [40.0, 40.0]}
    assert "LAYOUT = " in pr.read_model() and pr.kind == "harness"
    res = pr.build()
    assert res.ok and res.harness["layout"]["J2"] == [640.0, 120.0]
    assert res.harness["scene"]["nodes"]["J2"]["x"] == 640.0
    assert res.harness_report["wires"] == 2                            # layout is not wiring: nothing else changed


def test_set_params_on_a_harness(tmp_path):
    pr = project(tmp_path)
    pr.set_params({"length_mm": 800})
    assert pr.current_params()["length_mm"] == 800
    res = pr.build()
    assert {w["length_mm"] for w in res.harness["wires"]} == {800}


def test_compare_revisions_for_harnesses(tmp_path):
    pr = project(tmp_path)
    pr.build(note="one")
    pr.set_params({"length_mm": 500})
    pr.build(note="two")
    cmp = pr.compare()
    assert "2 wire(s)/connector(s) differ" in cmp["summary"] or "differ" in cmp["summary"]
    assert any(r["status"] == "changed" and r["name"] == "W1" for r in cmp["parts"])
    assert "length_mm" in cmp["source_diff"]


# --------------------------------------------------------------------------- the agent's tools


def test_tools_build_edit_set_params_read(tmp_path):
    pr = Project(tmp_path / "t", name="t")
    tools = Tools(pr)
    res = tools.build_model(MODEL, "first")
    assert not res.is_error and "Harness 'Lead'" in res.text and res.images and Path(res.images[0]).is_file()
    assert "harness diagram" in res.text and "ATTENTION" not in res.text
    assert pr.kind == "harness"
    bad = tools.set_params({"length_mm": 4000})
    assert not bad.is_error and "ATTENTION" in bad.text and "FAIL" in bad.text and "never delete or loosen a check" in bad.text
    fixed = tools.edit_model("gauge=22", "gauge=14", "thicker")
    assert not fixed.is_error and "ATTENTION" not in fixed.text.split("Checks:")[0]
    assert "PARAMS" in tools.read_model().text
    broke = tools.edit_model('a["2"]', 'a["7"]')
    assert broke.is_error and "BUILD FAILED" in broke.text and "no pin" in broke.text


def test_tools_inspect_has_the_harness_scope(tmp_path):
    pr = project(tmp_path)
    tools = Tools(pr)
    out = tools.inspect("nets()\nwires()\nconnectors()\ndrop('J1.2', 'J2.2')\nlengths()\nprint(len(h.links), p.length_mm)")
    assert not out.is_error, out.text
    assert "5V: 5 V, 1 A, drop" in out.text and "W1: J1.2 -> J2.2" in out.text and "J1: JST-XH, 2 pins" in out.text
    assert re.search(r"W1: [\d.]+ V = [\d.]+% of 5 V", out.text) or "V = " in out.text
    assert "hookup wire" in out.text and "2 300" in out.text


def test_tools_inspect_cad_scope_is_unchanged(tmp_path):
    pr = project(tmp_path, CAD, name="plate")
    out = Tools(pr).inspect("print(round(parts.size.X, 1), p.w)")
    assert not out.is_error and out.text.strip() == "10.0 10"


def test_tools_list_and_update_edits_on_a_harness(tmp_path):
    pr = project(tmp_path)
    pr.add_edit({"op": "set_wire", "wire": "W1", "gauge": 14})
    tools = Tools(pr)
    listing = tools.list_edits()
    assert "wire W1: gauge = 14" in listing.text
    res = tools.update_edit(1, {"gauge": 12})
    assert not res.is_error and pr.edits()[0]["gauge"] == 12 and pr.edits()[0]["by"] == "claude"
    assert not tools.remove_edit(1).is_error and pr.edits() == []


def test_tools_cad_only_tools_refuse_a_harness(tmp_path):
    tools = Tools(project(tmp_path))
    for res in (tools.drawing(), tools.cam(), tools.print_prep(), tools.cut_file()):
        assert res.is_error and "wire harness" in res.text


def test_tools_view_and_export_on_a_harness(tmp_path):
    tools = Tools(project(tmp_path))
    v = tools.view()
    assert not v.is_error and v.images and v.images[0].endswith(".png")
    e = tools.export(["step"])
    assert not e.is_error and "bom_md" in e.text and "wireviz" in e.text and "cutlist_csv" in e.text


def test_harness_reference_tool(tmp_path):
    text = Tools(project(tmp_path, CAD, name="plate")).harness_reference().text
    assert text.startswith("# Wire harness reference") and "connect_cable" in text and "| XT60 |" in text


def test_mcp_server_exposes_the_harness_tool(tmp_path):
    import asyncio
    from eitri.agent.mcp_server import make_server
    server = make_server(project(tmp_path))
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"build_model", "inspect", "harness_reference", "list_edits"} <= names


def test_api_backend_advertises_the_harness_tool():
    from eitri.agent.api_backend import TOOL_SCHEMAS
    t = next(t for t in TOOL_SCHEMAS if t["name"] == "harness_reference")
    assert "Harness" in t["description"]


# --------------------------------------------------------------------------- the reference doc's examples build green


def test_reference_examples_build_green(tmp_path):
    from eitri.agent.prompt import harness_reference_text
    from eitri.model import load_model
    text = harness_reference_text()
    assert "{{CATALOG}}" not in text and "| XT60 |" in text
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    assert len(blocks) == 3
    for i, code in enumerate(blocks):
        f = tmp_path / f"example{i}" / "model.py"
        f.parent.mkdir()
        f.write_text(code, encoding="utf-8")
        m = load_model(f)
        h = m.build()
        rep = analyze(h, m.verify(h))
        assert rep.ok and not rep.warnings, f"example {i + 1}:\n{summarize(rep)}"


def test_reference_example_configs(tmp_path):
    from eitri.agent.prompt import harness_reference_text
    from eitri.model import load_model
    code = re.findall(r"```python\n(.*?)```", harness_reference_text(), re.S)[2]
    f = tmp_path / "cfg.py"
    f.write_text(code, encoding="utf-8")
    for cfg in ("bench", "vehicle"):
        m = load_model(f, config=cfg)
        h = m.build()
        rep = analyze(h, m.verify(h))
        assert rep.ok, summarize(rep)
        assert {l.length_mm for l in h.links} == {m.configs[cfg]["length_mm"]}


# --------------------------------------------------------------------------- the system prompt


def test_system_prompt_for_a_harness_project(tmp_path):
    prompt = system_prompt(project(tmp_path))
    assert "wire-harness" in prompt and "# Wire harness reference" in prompt and "Current model file" in prompt
    assert "from eitri.harness import *" in prompt
    assert "build123d" not in prompt.split("# Wire harness reference")[0]
    assert "Feature library" not in prompt and "BuildPart" not in prompt        # no CAD reference for a harness
    assert "| JST-XH |" in prompt and "{{CATALOG}}" not in prompt


def test_system_prompt_for_cad_projects_only_points_at_the_harness_tool(tmp_path):
    prompt = system_prompt(project(tmp_path, CAD, name="plate"))
    assert "harness_reference()" in prompt                                      # a pointer, so a blank project can become a harness
    assert "# Wire harness reference" not in prompt and "BuildPart" in prompt
    assert "# Wire harness reference" not in system_prompt(None)


# --------------------------------------------------------------------------- the CLI


def test_cli_build_on_a_harness_file(tmp_path, capsys):
    from eitri.cli import main
    pr = project(tmp_path)
    assert main(["build", str(pr.model_path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Harness 'Lead'") and "png:" in out and "wireviz:" in out


def test_cli_bom(tmp_path, capsys):
    from eitri.cli import main
    pr = project(tmp_path)
    assert main(["bom", str(pr.folder)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Bill of materials: Lead") and "XHP-2" in out and "hookup wire" in out and "# Cut list" not in out
    assert main(["bom", str(pr.model_path), "--cutlist"]) == 0
    assert "# Cut list: Lead" in capsys.readouterr().out
    assert main(["bom", str(pr.folder), "--csv"]) == 0
    assert capsys.readouterr().out.startswith("item,category,description")


def test_cli_bom_refuses_cad_and_missing(tmp_path, capsys):
    from eitri.cli import main
    pr = project(tmp_path, CAD, name="plate")
    assert main(["bom", str(pr.folder)]) == 2
    assert "CAD model" in capsys.readouterr().err
    assert main(["bom", str(tmp_path / "nope")]) == 2


def test_chat_selection_is_described_for_the_agent(tmp_path):
    from eitri.agent.hub import AgentHub
    from eitri.harness.report import describe_selection
    pr = project(tmp_path, MODEL.replace("length_mm=300", "length_mm=4000"))
    pr.build(note="first")
    composed = AgentHub(pr, lambda handler: None).compose("make this thicker", ["wire:W1"])
    assert composed.startswith("Selected in the editor (revision 1):\n- wire W1: J1.2 -> J2.2") and composed.endswith("make this thicker")
    result = json.loads((pr.out_dir / "result.json").read_text())
    text = describe_selection(result["harness"], ["wire:W1", "conn:J2", "wire:W9"], result["report"]["checks"])
    assert "- wire W1: J1.2 -> J2.2, 22 AWG RD, 4000 mm, net 5V, 1 A" in text
    assert "- connector J2: JST-XH, pins 1, 2, rating 3 A/pin; wires: J1.2->J2.2 (W1); J1.1->J2.1 (W2)" in text
    assert "FAIL: voltage drop 5V" in text or "WARN" in text or "FAIL" in text
    assert "- wire:W9 (no longer exists)" in text
