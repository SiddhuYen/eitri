"""A firmware project through the rest of eitri: the build subprocess (with the compile, or the explicit "compiler unavailable"
failure), the project and its direct edits, the agent's tools and prompt, editor selections for the agent, and the CLI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eitri.agent.prompt import system_prompt
from eitri.agent.tools import Tools
from eitri.firmware import find_cli
from eitri.kernel import build_model
from eitri.project import Project

HAVE_CLI = find_cli() is not None
DEMO = Path(__file__).resolve().parents[1] / "eitri" / "demos" / "servo-sweep" / "servo_sweep.py"
MODEL = DEMO.read_text(encoding="utf-8")


def project(tmp_path, code=MODEL, name="sweep") -> Project:
    pr = Project(tmp_path / name, name=name)
    pr.write_model(code)
    return pr


def checks_of(res):
    return {c["name"]: c for c in res.domain_report["checks"]}


def test_kind_and_build_subprocess(tmp_path):
    pr = project(tmp_path)
    assert pr.kind == "firmware"
    res = build_model(pr.model_path, pr.out_dir)
    assert res.ok and res.kind == "firmware" and res.report is None
    assert {"svg", "pinout_md", "pinout_csv", "header", "compile_log"} <= set(res.outputs) and all(Path(v).is_file() for v in res.outputs.values())
    assert res.summary.startswith("Firmware 'Servo sweep': uno (arduino:avr:uno), 3 pin(s)")
    assert "const uint8_t PIN_PAN = 9;" in Path(res.outputs["header"]).read_text()
    assert (pr.out_dir / "sketch" / "servo_sweep" / "servo_sweep.ino").is_file() or any(pr.out_dir.glob("**/servo_sweep.ino"))
    data = json.loads((pr.out_dir / "result.json").read_text())
    assert data["kind"] == "firmware" and data["firmware"]["fqbn"] == "arduino:avr:uno" and data["firmware"]["scene"]
    c = checks_of(res)
    assert c["sweep step (ms)"]["category"] == "verify" and c["PIN_PAN used as declared"]["severity"] == "pass"
    comp = c["compiles (arduino-cli)"]
    if HAVE_CLI:
        assert comp["severity"] == "pass" and "flash" in comp["measured"]
    else:
        assert comp["severity"] == "fail" and comp["measured"].startswith("compiler unavailable")


def test_compiler_unavailable_through_the_build(tmp_path, monkeypatch):
    monkeypatch.setenv("EITRI_ARDUINO_CLI", str(tmp_path / "nope"))
    monkeypatch.setenv("EITRI_WORKER", "0")                         # a fresh build process sees the patched environment
    pr = project(tmp_path)
    res = build_model(pr.model_path, pr.out_dir)
    assert res.ok                                                   # a failed check, not a failed build
    comp = checks_of(res)["compiles (arduino-cli)"]
    assert comp["severity"] == "fail" and "compiler unavailable" in comp["measured"] and "arduino-cli core install arduino:avr" in comp["measured"]


def test_failing_checks_do_not_fail_the_build(tmp_path):
    pr = project(tmp_path, MODEL.replace('fw.pin("D13", "out", "digital_out", net="LED")', 'fw.pin("D13", "out", "pwm", net="LED")'))
    res = build_model(pr.model_path, pr.out_dir)
    assert res.ok and checks_of(res)["pin D13 (LED) can be pwm"]["severity"] == "fail"
    bad = project(tmp_path, MODEL.replace('fw.pin("D13", "out", "digital_out", net="LED")', 'fw.pin("D13", "out", "laser", net="LED")'), name="bad")
    res = build_model(bad.model_path, bad.out_dir)
    assert not res.ok and "role must be" in res.error


def test_project_edits(tmp_path):
    pr = project(tmp_path)
    assert pr.build(note="first", render=False).ok
    pr.add_edit({"op": "set_pin", "net": "LED", "pin": "D12"})
    pr.add_edit({"op": "set_pin", "net": "LED", "role": "digital_out"})            # the same pin: one edit
    assert len(pr.edits()) == 1
    pr.add_edit({"op": "set_board", "board": "nano"})
    res = pr.build(note="edits", render=False)
    assert res.ok and all(n["ok"] for n in res.edit_notes)
    led = next(p for p in res.domain["pins"] if p["net"] == "LED")
    assert led["canonical"] == "D12" and res.domain["board"] == "nano"
    hdr = next(s for s in res.domain["sources"] if s["name"] == "eitri_pins.h")
    assert "const uint8_t PIN_LED = 12;" in hdr["code"]


def test_tools(tmp_path):
    pr = Project(tmp_path / "t", name="t")
    tools = Tools(pr)
    res = tools.build_model(MODEL, "first")
    assert not res.is_error and "Firmware 'Servo sweep'" in res.text and res.images and "pinout" in res.text
    assert ("ATTENTION" not in res.text) == HAVE_CLI
    out = tools.inspect("pins()\nboard()\nprint(board_info('nano').fqbn)\nheader()\nsources()")
    assert not out.is_error, out.text
    assert "PIN_PAN = D9 (#9): out servo, PAN" in out.text and "D3 #3: pwm, interrupt" in out.text and "arduino:avr:nano" in out.text
    assert "const uint8_t PIN_START = 2;" in out.text and "servo_sweep.ino:11: attach(PIN_PAN)" in out.text
    bad = tools.set_params({"step_ms": 80})
    assert "ATTENTION" in bad.text and "the pinout or the sources" in bad.text
    assert not tools.view().is_error and "header" in tools.export([]).text
    for r in (tools.drawing(), tools.cam()):
        assert r.is_error and "firmware project" in r.text
    assert tools.firmware_reference().text.startswith("# Firmware reference")


def test_prompt_and_selection(tmp_path):
    pr = project(tmp_path)
    prompt = system_prompt(pr)
    assert "expert embedded firmware engineer" in prompt and "# Firmware reference" in prompt and "arduino:avr:uno" in prompt and "Feature library" not in prompt
    assert "Never flash or upload to a board" in prompt
    pr.build(note="first", render=False)
    from eitri.agent.hub import AgentHub
    text = AgentHub(pr, lambda h: None).compose("faster", ["pin:D9", "peripheral:PAN", "source:eitri_pins.h", "pin:nope"])
    assert "- pin D9 (PIN_PAN): out servo, PAN; used: attach @ servo_sweep.ino:11" in text
    assert "- peripheral PAN: servo on signal D9" in text and "(generated from the pinout)" in text and "- pin:nope (no longer exists)" in text


def test_cli(tmp_path, capsys):
    from eitri.cli import main
    pr = project(tmp_path)
    rc = main(["build", str(pr.model_path)])
    out = capsys.readouterr().out
    assert out.startswith("Firmware 'Servo sweep'") and rc == 0
    assert main(["bom", str(pr.folder)]) == 2                      # no bill of materials for firmware: a message, exit 2
    cap = capsys.readouterr()
    assert "firmware" in (cap.out + cap.err).lower()
