"""Tickets: a request in, a plan a PERSON approves, a checked change package out (eitri.tickets).

The person's terminal is stood in by `_person` (approve/waive/close take a confirmer) or by a fake /dev/tty (the CLI); the
agent-process markers are cleared for those calls, as they are in a person's own terminal."""
from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from eitri import tickets as T
from eitri.agent.tools import Tools
from eitri.project import Project

ROOT = Path(__file__).resolve().parents[1]
PAN_TILT = ROOT / "eitri" / "demos" / "pan-tilt" / "pan_tilt.py"

MECH = '''from eitri.mechanism import *

PARAMS = dict(camera_g=100.0, arm_mm=35.0)


def build(p):
    m = Mechanism("Tilt", "one servo, a camera on an arm")
    m.link("base", fixed=True)
    m.link("arm", length_mm=p.arm_mm, mass_g=10)
    m.joint("tilt", "base", "arm", axis=(0, -1, 0), limit_deg=(-30, 60),
            actuator=Actuator("SG90", stall_kgcm=1.8, speed_deg_s=500, travel_deg=TRAVEL, mass_g=9))
    m.mass("camera", "arm", p.camera_g, at=(p.arm_mm, 0, 0))
    m.tip("arm")
    return m


def verify(m, p, c):
    c.at_most("camera mass (g)", p.camera_g, 300)
'''


def mech(travel="180") -> str:
    return MECH.replace("TRAVEL", travel)


def _person(text, what):
    return {"tty": "test-terminal"}


@pytest.fixture(autouse=True)
def person_env(tmp_path, monkeypatch):
    """A person's terminal: no agent markers; eitri's config (the signing key) in a temporary folder."""
    for k in T.store.AGENT_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EITRI_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("EITRI_NO_WARM", "1")
    T.set_signer(T.store.LocalKeySigner())


def _project(tmp_path, code: str, name="tilt") -> Project:
    folder = tmp_path / name
    folder.mkdir()
    (folder / f"{name}.py").write_text(code, encoding="utf-8")
    return Project(folder)


PLAN = {"summary": "Raise the camera mass.", "parameters": [{"name": "camera_g", "from": 100, "to": 150}],
        "checks_affected": ["torque joint tilt (SG90)"], "not_checked": ["the camera's own mount"]}


# ---------------------------------------------------------------------------------------------------- ticket file
def test_ticket_file_criteria_and_history(tmp_path):
    p = _project(tmp_path, mech())
    with pytest.raises(T.TicketError, match="named check .* or mark"):
        T.new_ticket(p, "x", "y", acceptance=[{"text": "both", "check": "a", "human_verify": True}])
    with pytest.raises(T.TicketError, match="named check .* or mark"):
        T.new_ticket(p, "x", "y", acceptance=[{"text": "neither"}])
    t = T.new_ticket(p, "Heavier camera", "Our camera is 150 g now, can the tilt hold it?",
                     acceptance=["tilt holds it => torque joint tilt*", "looks tidy => human"], constraints=["keep the SG90 if possible"])
    assert t.id == "T-0001" and t.status == "draft" and t.data["target"] == {"file": "tilt.py", "revision": None}
    assert t.data["acceptance"][0] == {"id": "AC-1", "text": "tilt holds it", "check": "torque joint tilt*"}
    assert t.data["acceptance"][1]["human_verify"] is True and "human verifies" in t.data["acceptance"][1]["note"]
    text = (p.folder / "tickets" / "T-0001" / "ticket.yaml").read_text(encoding="utf-8")
    assert "Our camera is 150 g now" in text and "status: draft" in text
    assert T.active(p).id == "T-0001"
    with pytest.raises(T.TicketError, match="still draft"):            # one active ticket per project
        T.new_ticket(p, "another", "request")
    assert [e["event"] for e in t.events()] == ["created"]
    assert [x.id for x in T.list_tickets(p)] == ["T-0001"]


# ---------------------------------------------------------------------------------------------------- plan gate
def test_gate_refuses_until_a_person_approves(tmp_path):
    p = _project(tmp_path, mech())
    tools = Tools(p)
    T.new_ticket(p, "Heavier camera", "150 g camera")
    for res in (tools.set_params({"camera_g": 150}), tools.edit_model("camera_g=100.0", "camera_g=150.0"),
                tools.build_model(mech())):
        assert res.is_error and "no plan yet" in res.text
    with pytest.raises(T.ChangeRefused):
        p.build("try")
    with pytest.raises(T.ChangeRefused):
        p.write_model("x")
    T.set_plan(p, "T-0001", PLAN)
    res = tools.set_params({"camera_g": 150})
    assert res.is_error and "awaiting approval" in res.text and "eitri ticket approve T-0001" in res.text
    assert "camera_g=100.0" in p.read_model()                         # nothing was written
    with pytest.raises(T.TicketError, match="not the agent.s to set"):  # a plan field the agent may not set
        T.set_plan(p, "T-0001", {**PLAN, "status": "approved"})
    T.approve(p, "T-0001", by="Pat Person", confirm=_person)
    p.write_model(p.read_model().replace("camera_g=100.0", "camera_g=150.0"))   # now it may change
    t = T.load(p, "T-0001")
    assert t.status == "approved" and t.data["approval"]["by"] == "Pat Person" and t.data["approval"]["tty"] == "test-terminal"
    assert [e["event"] for e in t.events()] == ["created", "plan_written", "approved"]
    T.set_plan(p, "T-0001", {**PLAN, "summary": "a different plan"})   # a rewritten plan voids the approval
    assert T.load(p, "T-0001").status == "awaiting_approval"
    with pytest.raises(T.ChangeRefused):
        p.write_model("x")
    T.approve(p, "T-0001", confirm=_person)                           # the new plan recorded the file as it now is
    assert T.load(p, "T-0001").data["approval"]["despite_changes"] is False
    T.close(p, "T-0001", confirm=_person)
    assert T.active(p) is None
    p.write_model(mech())                                              # no active ticket: nothing is gated


def test_approval_refused_when_the_file_changed_after_the_plan(tmp_path):
    p = _project(tmp_path, mech())
    T.new_ticket(p, "t", "r")
    T.set_plan(p, "T-0001", PLAN)
    (p.folder / "tilt.py").write_text(mech().replace("100.0", "120.0"), encoding="utf-8")     # the agent's own file tools, before approval
    with pytest.raises(T.TicketError, match="changed on disk after the plan"):
        T.approve(p, "T-0001", confirm=_person)
    T.approve(p, "T-0001", confirm=_person, despite_changes=True)     # accepted out loud: recorded for the package's page 1
    assert T.load(p, "T-0001").data["approval"]["despite_changes"] is True


def test_editing_an_approved_ticket_or_forging_one_does_not_pass(tmp_path):
    import yaml
    p = _project(tmp_path, mech())
    T.new_ticket(p, "t", "the request")
    T.set_plan(p, "T-0001", PLAN)
    T.approve(p, "T-0001", confirm=_person)
    path = p.folder / "tickets" / "T-0001" / "ticket.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["request"] = "a bigger request"                               # changed after approval
    path.write_text(T.store.dump_yaml(data), encoding="utf-8")
    with pytest.raises(T.ChangeRefused, match="changed after it was approved"):
        p.write_model("x")
    with pytest.raises(T.PackageRefused, match="changed after it was approved"):
        T.make_package(p, "T-0001")
    data["request"] = "the request"                                    # a hand-made approval: right hash, no key
    data["approval"]["by"] = "someone else"
    path.write_text(T.store.dump_yaml(data), encoding="utf-8")
    with pytest.raises(T.ChangeRefused, match="signature does not verify"):
        p.write_model("x")


# ---------------------------------------------------------------------------------------------------- the agent cannot approve
def _call(server, name, args=None):
    from mcp.server.mcpserver.exceptions import ToolError
    try:
        res = asyncio.run(server.call_tool(name, args or {}))
    except ToolError as exc:
        return str(exc), True
    except Exception as exc:                                           # argument validation
        return f"{type(exc).__name__}: {exc}", True
    return "\n".join(getattr(c, "text", "") for c in res.content), bool(res.is_error)


def test_mcp_surface_cannot_approve_waive_or_close(tmp_path, monkeypatch):
    from eitri.agent.api_backend import TOOL_SCHEMAS
    from eitri.agent.mcp_server import make_plugin_server, make_server
    from eitri.agent.plugin import PluginSession
    p = _project(tmp_path, mech())
    session = PluginSession(root=tmp_path, open_browser=False)
    plugin = make_plugin_server(session)
    app = make_server(p)
    person_only = re.compile(r"approv|waiv|close|cancel|(^|_)sign|confirm|release|set_status", re.I)
    for server in (plugin, app):
        names = {t.name for t in asyncio.run(server.list_tools())}
        assert {"ticket_new", "ticket_show", "ticket_list", "ticket_plan", "package"} <= names
        assert not [n for n in names if person_only.search(n)], names
    assert not [t["name"] for t in TOOL_SCHEMAS if person_only.search(t["name"])]

    _call(plugin, "open_project", {"folder": str(p.folder)})
    text, err = _call(plugin, "ticket_new", {"title": "t", "request": "r", "acceptance": ["holds => torque joint tilt*"]})
    assert not err, text
    _call(plugin, "ticket_plan", {"ticket": "T-0001", "summary": "s", "status": "approved", "approval": {"by": "me"}})
    assert T.load(p, "T-0001").status != "approved" and not T.load(p, "T-0001").data.get("approval")   # extra fields do nothing
    text, err = _call(plugin, "ticket_plan", {"ticket": "T-0001", "summary": "s"})
    assert not err and "STOP HERE" in text and "eitri ticket approve T-0001" in text
    for name, args in (("set_params", {"params": {"camera_g": 150}}), ("build_model", {"code": mech()}), ("rebuild", {}),
                       ("package", {"ticket": "T-0001"})):
        text, err = _call(plugin, name, args)
        assert err and ("awaiting approval" in text or "awaiting_approval" in text), (name, text)
    # inspect runs the agent's Python: calling approve() there is refused (the process is marked as an agent's)
    code = ("from eitri.project import Project\nfrom eitri import tickets as T\nimport os\n"
            f"try:\n    T.approve(Project({str(p.folder)!r}), 'T-0001', confirm=lambda a, b: {{}})\n    print('APPROVED')\n"
            "except T.NotAPerson as e:\n    print('REFUSED', e)\n")
    text, err = _call(plugin, "inspect", {"code": code})
    assert "REFUSED" in text and "APPROVED" not in text, text
    text, err = _call(plugin, "ticket_show", {})
    assert "AWAITING APPROVAL" in text
    assert T.load(p, "T-0001").status == "awaiting_approval"
    # the reference-part acceptance is person-only the same way: no tool for it, and refused from the agent's Python
    for server in (plugin, app):                       # (the *_reference tools are API documentation, not reference parts)
        names = {t.name for t in asyncio.run(server.list_tools())}
        assert not [n for n in names if re.search(r"accept|reference_(add|remove|accept)|(add|remove)_reference", n, re.I)], names
    code = ("from eitri.project import Project\nfrom eitri.tickets import store\n"
            f"try:\n    Project({str(p.folder)!r}).accept_reference('inputs/x.step', confirm=lambda a, b: {{}})\n    print('ACCEPTED')\n"
            "except store.NotAPerson as e:\n    print('REFUSED', e)\nexcept Exception as e:\n    print('OTHER', type(e).__name__, e)\n")
    (p.folder / "inputs").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "tests" / "fixtures" / "reference" / "test_block.step", p.folder / "inputs" / "x.step")
    text, err = _call(plugin, "inspect", {"code": code})
    assert "REFUSED" in text and "ACCEPTED" not in text, text


def test_person_only_actions_refuse_in_agent_processes_and_without_a_terminal(tmp_path, monkeypatch):
    p = _project(tmp_path, mech())
    T.new_ticket(p, "t", "r")
    T.set_plan(p, "T-0001", PLAN)
    for marker in ("EITRI_AGENT_PROCESS", "CLAUDECODE"):
        monkeypatch.setenv(marker, "1")
        for fn in (lambda: T.approve(p, "T-0001", confirm=_person), lambda: T.close(p, "T-0001", confirm=_person),
                   lambda: T.waive(p, "T-0001", "x", "a reason that is long enough", confirm=_person)):
            with pytest.raises(T.NotAPerson, match=marker):
                fn()
        monkeypatch.delenv(marker)
    with pytest.raises(T.NotAPerson, match="interactive terminal"):    # pytest's stdin is not a terminal
        T.approve(p, "T-0001")
    # the CLI, from a pipe (how an agent's shell runs it): refused, nothing recorded
    proc = subprocess.run([sys.executable, "-m", "eitri", "ticket", "approve", "T-0001", "-C", str(p.folder)], input="",
                          capture_output=True, text=True, env={**__import__("os").environ, "PYTHONPATH": str(ROOT), "EITRI_LAZY_IMPORT": "1"}, timeout=120)
    assert proc.returncode == 1 and "terminal" in proc.stderr
    assert T.load(p, "T-0001").status == "awaiting_approval"
    # the same barriers on a reference part's acceptance (eitri reference accept): markers, then the terminal
    (p.folder / "inputs").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "tests" / "fixtures" / "reference" / "test_block.step", p.folder / "inputs" / "x.step")
    for marker in ("EITRI_AGENT_PROCESS", "CLAUDECODE"):
        monkeypatch.setenv(marker, "1")
        with pytest.raises(T.NotAPerson, match=marker):
            p.accept_reference("inputs/x.step", confirm=_person)
        monkeypatch.delenv(marker)
    with pytest.raises(T.NotAPerson, match="interactive terminal"):
        p.accept_reference("inputs/x.step")
    assert not p.references()


class _FakeTTY(io.StringIO):
    """A person at /dev/tty: reads back the one-time code they were shown (or a wrong one)."""

    def __init__(self, wrong=False):
        super().__init__()
        self.wrong = wrong

    def readline(self, *a):
        code = re.findall(r"type this code: (\w+)", self.getvalue())[-1]
        return ("XXXXXX" if self.wrong else code.lower()) + "\n"

    def fileno(self):
        raise OSError

    def close(self):                                                   # keep what the person saw, for the test
        self.seen = self.getvalue()


def test_cli_approve_with_the_one_time_code(tmp_path, monkeypatch, capsys):
    from eitri.cli import main
    p = _project(tmp_path, mech())
    assert main(["ticket", "new", "-C", str(p.folder), "--title", "Heavier camera", "--request", "camera is 150 g",
                 "--criterion", "tilt holds it => torque joint tilt*", "--criterion", "tidy => human"]) == 0
    T.set_plan(p, "T-0001", PLAN)
    monkeypatch.setattr(T.store, "_open_tty", lambda: _FakeTTY(wrong=True))
    assert main(["ticket", "approve", "T-0001", "-C", str(p.folder)]) == 1
    assert "did not match" in capsys.readouterr().err and T.load(p, "T-0001").status == "awaiting_approval"
    tty = _FakeTTY()
    monkeypatch.setattr(T.store, "_open_tty", lambda: tty)
    assert main(["ticket", "approve", "T-0001", "-C", str(p.folder), "--by", "Pat Person", "--note", "go ahead"]) == 0
    assert "Raise the camera mass." in tty.seen                        # the person saw the plan
    out = capsys.readouterr().out
    assert "approved T-0001 as Pat Person" in out and not re.search(r"type this code", out)   # the code went to the tty only
    assert main(["ticket", "list", "-C", str(p.folder)]) == 0
    assert "T-0001  approved" in capsys.readouterr().out
    assert main(["ticket", "show", "T-0001", "-C", str(p.folder)]) == 0
    assert "Approved by Pat Person" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------------- refusal rule, waivers
def test_package_refuses_failed_and_not_run_checks_and_takes_a_waiver(tmp_path):
    p = _project(tmp_path, mech(travel="None"))                        # travel unset: "joint limits tilt" does not run
    tools = Tools(p)
    assert p.build("base").ok
    T.new_ticket(p, "Heavier camera", "Camera is 280 g", acceptance=["tilt holds it => torque joint tilt*"])
    T.set_plan(p, "T-0001", {**PLAN, "parameters": [{"name": "camera_g", "from": 100, "to": 280}]})
    T.approve(p, "T-0001", confirm=_person)
    assert not tools.set_params({"camera_g": 600}).is_error             # past the SG90's stall: a FAIL
    with pytest.raises(T.PackageRefused) as exc:
        T.make_package(p, "T-0001")
    text = " ".join(exc.value.reasons)
    assert "FAIL torque joint tilt" in text and "never waived" in text and "NOT RUN joint limits tilt" in text
    assert (p.folder / "tickets" / "T-0001" / "refused.md").is_file()
    assert not list((p.folder / "tickets" / "T-0001").glob("T-0001-*-rev-*"))
    with pytest.raises(T.TicketError, match="never waived"):
        T.waive(p, "T-0001", "torque joint tilt (SG90)", "the customer accepts the risk of stalling", confirm=_person)
    with pytest.raises(T.TicketError, match="written reason"):
        T.waive(p, "T-0001", "joint limits tilt", "ok", confirm=_person)
    with pytest.raises(T.TicketError, match="no check named"):
        T.waive(p, "T-0001", "joint limit tilt", "a long enough reason here", confirm=_person)
    assert not tools.set_params({"camera_g": 280}).is_error
    with pytest.raises(T.PackageRefused) as exc:
        T.make_package(p, "T-0001")
    assert [r for r in exc.value.reasons if r.startswith("NOT RUN joint limits tilt")] and not [r for r in exc.value.reasons if r.startswith("FAIL")]
    T.waive(p, "T-0001", "joint limits tilt", "The SG90's travel is 180 deg per TowerPro; the joint uses 90 deg.", by="Pat Person",
            confirm=_person)
    pkg = T.make_package(p, "T-0001")
    assert pkg["waivers"][0]["check"] == "joint limits tilt" and pkg["checks"]["summary"]["to"]["not_run"] == 1
    page1 = Path(pkg["folder"], "report.md").read_text(encoding="utf-8").split("## Acceptance criteria")[0]
    assert "WAIVERS" in page1 and "joint limits tilt" in page1 and "180 deg per TowerPro" in page1
    html = Path(pkg["folder"], "report.html").read_text(encoding="utf-8")
    assert html.index("WAIVERS") < html.index("Acceptance criteria")
    assert any(n["kind"] == "waived" for n in pkg["not_checked"])
    assert T.load(p, "T-0001").status == "packaged"


def test_new_design_package_has_no_diff_sections(tmp_path):
    p = _project(tmp_path, mech())
    T.new_ticket(p, "New tilt arm", "Design a tilt arm for a 100 g camera", base="new",
                 acceptance=["holds it => torque joint tilt*", "speed => speed joint tilt"])
    T.set_plan(p, "T-0001", {"summary": "A one-joint tilt arm on an SG90.", "not_checked": ["the camera's mount"]})
    T.approve(p, "T-0001", confirm=_person, despite_changes=False)
    assert p.build("first").ok
    pkg = T.make_package(p, "T-0001")
    assert pkg["design"]["new_design"] and pkg["revisions"]["from"] is None and pkg["revisions"]["to"]["letter"] == "A"
    assert pkg["parameters"] is None and pkg["bom"] is None
    out = Path(pkg["folder"])
    assert not (out / "source.diff").exists() and not (out / "parameters.csv").exists() and not (out / "bom_diff.csv").exists()
    assert "## Parameter changes" not in (out / "report.md").read_text(encoding="utf-8")
    assert {c["status"] for c in pkg["acceptance"]} <= {"pass", "pass_with_warning"}
    _validate(out / "package.json")


def _export_sample(pkg_dir: Path) -> None:
    """EITRI_PACKAGE_SAMPLE_DIR=<folder>: copy the package (folder + zip) there (how the report's sample packages are made)."""
    import os
    dest = os.environ.get("EITRI_PACKAGE_SAMPLE_DIR")
    if not dest:
        return
    out = Path(dest) / pkg_dir.name
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(pkg_dir, out)
    if Path(str(pkg_dir) + ".zip").exists():
        shutil.copyfile(str(pkg_dir) + ".zip", str(out) + ".zip")


def _sources_are_structured(pkg: dict) -> None:
    """Every check row with a rule carries its structured sources, and none falls back to "no document cited"."""
    for r in pkg["checks"]["rows"]:
        if r["category"] == "geometry":
            continue                                         # CAD geometry findings are report issues, not ruled checks
        assert r["rule"] and r["sources"], r["name"]
        assert "no document cited" not in r["source"], (r["name"], r["source"])
        assert all(x["basis"] in ("document", "requirement", "model", "rule", "tool", "assumption") for x in r["sources"])


def _validate(path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "eitri" / "tickets" / "package.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(json.loads(path.read_text(encoding="utf-8")), schema)


# ---------------------------------------------------------------------------------------------------- end to end: the pan-tilt demo
NEW_TILT_SERVO = '''    servo = Actuator("SG90", stall_kgcm=1.8, speed_deg_s=500, travel_deg=180, mass_g=9)
    # TowerPro MG996R, https://www.towerpro.com.tw/product/mg996r/ (read 2026-11-12; the page shows no revision): "Stall torque:
    # 9.4kg/cm (4.8v)", "Operating speed: 0.19sec/60degree (4.8v)" = 315 deg/s, "Weight: 55g" but "Weight 0.080 kg" under
    # Additional information (the higher, 80 g, is used: it is a load); travel "0 - 159 degrees" (the page's own reply, 2022-03-02)
    tilt_servo = Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=315, travel_deg=159, mass_g=80)'''


def test_end_to_end_pan_tilt_servo_change(tmp_path):
    folder = tmp_path / "pan-tilt"
    folder.mkdir()
    shutil.copy(PAN_TILT, folder / "pan_tilt.py")
    p = Project(folder)
    tools = Tools(p)
    assert p.build("as delivered").ok                                  # revision 1: the customer's current design
    res = tools.ticket_new(
        "Tilt servo for the 200 g camera",
        "We're moving to the 200 g camera module. The SG90 on the tilt looks marginal - swap it for something that holds it "
        "properly at any angle, and keep the 180 deg/s tilt speed.",
        acceptance=[{"text": "the tilt servo holds the 200 g camera at its worst pose", "check": "torque joint tilt*"},
                    {"text": "tilt still moves at 180 deg/s", "check": "speed joint tilt"},
                    {"text": "the camera still reaches the 'ahead' target", "check": "target ahead reachable"},
                    {"text": "the MG996R fits the existing yoke bracket", "human_verify": True}],
        constraints=["keep the SG90 on the pan axis"], customer="Example Robotics")
    assert not res.is_error and "torque joint tilt (SG90)" in res.text            # the check names to map to
    res = tools.ticket_plan("T-0001", "Raise camera_g to 200 and put a TowerPro MG996R on the tilt joint (datasheet figures cited "
                            "in the model); the pan servo stays an SG90.",
                            files=["pan_tilt.py"], parameters=[{"name": "camera_g", "from": 90, "to": 200, "why": "the new camera"}],
                            checks_affected=["torque joint tilt", "speed joint tilt", "joint limits tilt", "mass rollup"],
                            not_checked=["the MG996R's 40.7 x 19.7 x 42.9 mm body against the yoke bracket (no CAD in this file)",
                                         "servo current against the supply (the electronics file)"],
                            open_questions=["Is the supply still 4.8-5 V? The figures used are the 4.8 V ones."])
    assert not res.is_error
    assert tools.set_params({"camera_g": 200}).is_error                 # not before a person approves
    T.approve(p, "T-0001", by="Pat Person (Example Robotics)", note="5 V supply, yes.", confirm=_person)

    assert not tools.set_params({"camera_g": 200}).is_error
    code = p.read_model()
    old = '    servo = Actuator("SG90", stall_kgcm=1.8, speed_deg_s=500, travel_deg=180, mass_g=9)'
    res = tools.edit_model(old, NEW_TILT_SERVO)
    assert not res.is_error, res.text
    res = tools.edit_model('limit_deg=(-30, 60), actuator=servo)', 'limit_deg=(-30, 60), actuator=tilt_servo)')
    assert not res.is_error and "0 FAIL" in res.text, res.text

    res = tools.package("T-0001")
    assert not res.is_error, res.text
    pkg_dir = next((folder / "tickets" / "T-0001").glob("T-0001-pan_tilt-rev-B"))
    pkg = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
    _validate(pkg_dir / "package.json")
    assert pkg["revisions"]["from"]["letter"] == "A" and pkg["revisions"]["to"]["letter"] == "B"
    assert [r["letter"] for r in pkg["revision_table"]] == ["A", "B"] and pkg["revision_table"][1]["ticket"] == "T-0001"
    assert {a["id"]: a["status"] for a in pkg["acceptance"]} == {"AC-1": "pass", "AC-2": "pass", "AC-3": "pass", "AC-4": "human_verify"}
    assert pkg["acceptance"][0]["matched"] == ["torque joint tilt (MG996R)"]
    assert {r["name"]: r["change"] for r in pkg["parameters"]}["camera_g"] == "changed"
    bom = {(r["table"], r["key"]): r["change"] for r in pkg["bom"]}
    assert bom[("mass", "MG996R (tilt)")] == "added" and bom[("mass", "SG90 (tilt)")] == "removed"
    assert bom[("joints", "tilt")] == "changed"
    rows = {r["name"]: r for r in pkg["checks"]["rows"]}
    assert rows["torque joint tilt (SG90)"]["change"] == "removed" and rows["torque joint tilt (MG996R)"]["change"] == "added"
    assert "torque joint tilt (SG90)" in pkg["removed_checks"]
    assert all(r["source"] for r in pkg["checks"]["rows"])
    _sources_are_structured(pkg)
    tilt = rows["torque joint tilt (MG996R)"]
    assert tilt["rule"] == "mechanism.joint-load" and tilt["sources"], tilt
    assert "rule" in (pkg_dir / "checks.csv").read_text(encoding="utf-8").splitlines()[0]
    assert pkg["checks"]["summary"]["to"]["fail"] == 0 and pkg["checks"]["summary"]["to"]["not_run"] == 0
    kinds = {n["kind"] for n in pkg["not_checked"]}
    assert {"plan", "human_verify", "removed_check", "domain", "scope"} <= kinds
    co = pkg["change_order"]
    assert co["from_revision"] == "A" and co["to_revision"] == "B" and co["disposition"] == "" and co["effectivity"] == ""
    assert "pan_tilt.py" in co["affected_items"] and "mass: MG996R (tilt)" in co["affected_items"]
    files = {f["path"] for f in pkg["files"]}
    assert {"report.md", "report.html", "checks.csv", "parameters.csv", "bom_diff.csv", "source.diff", "ticket.yaml",
            "model/rev-A/pan_tilt.py", "model/rev-B/pan_tilt.py", "images/rev-A.png", "images/rev-B.png",
            "outputs/pan_tilt.mechanism.svg", "outputs/pan_tilt.joints.csv", "outputs/pan_tilt.mass.csv"} <= files
    for f in pkg["files"]:
        if f["sha256"]:
            assert T.store.file_sha256(pkg_dir / f["path"]) == f["sha256"]
    md = (pkg_dir / "report.md").read_text(encoding="utf-8")
    page1 = md.split("## Acceptance criteria")[0]
    assert "Rev A -> Rev B" in page1 and "Pat Person" in page1 and "0 fail, 0 not run" in page1
    assert "Is the supply still 4.8-5 V?" in page1 and "5 V supply, yes." in page1
    assert "MG996R" in (pkg_dir / "source.diff").read_text(encoding="utf-8")
    html = (pkg_dir / "report.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," in html and "<script" not in html and not re.search(r"""(src|href)=['"]https?:""", html)
    with zipfile.ZipFile(str(pkg_dir) + ".zip") as z:
        assert "T-0001-pan_tilt-rev-B/package.json" in z.namelist()
    assert not (folder / "tickets" / "T-0001" / ".package-build").exists()
    revs = json.loads((folder / "tickets" / "revisions.json").read_text(encoding="utf-8"))
    assert [r["letter"] for r in revs["files"]["pan_tilt.py"]] == ["A", "B"]
    assert T.load(p, "T-0001").status == "packaged"
    T.close(p, "T-0001", confirm=_person)
    _export_sample(pkg_dir)
    # the next ticket starts from Rev B and would make Rev C
    t2 = T.new_ticket(p, "next", "a later change")
    assert t2.base_revision == pkg["revisions"]["to"]["build"]


# ---------------------------------------------------------------------------------------------------- CAD: a hole pattern
BRACKET = '''from eitri import *

PARAMS = dict(width=60.0, depth=40.0, thick=6.0, hole_d=5.5, hole_pitch=40.0)


def build(p):
    plate = Box(p.width, p.depth, p.thick)
    for x in (-p.hole_pitch / 2, p.hole_pitch / 2):
        plate -= Pos(x, 0, 0) * Cylinder(p.hole_d / 2, p.thick)
    return {"plate": plate}


def verify(parts, p, c):
    c.equal("plate width", parts.size.X, p.width)
    c.at_least("hole edge distance (mm)", (p.width - p.hole_pitch) / 2 - p.hole_d / 2, 4.0)
'''


def test_cad_hole_pattern_package_has_step_stl_3mf_and_drawing(tmp_path):
    p = _project(tmp_path, BRACKET, name="bracket")
    tools = Tools(p)
    assert p.build("base").ok
    T.new_ticket(p, "50 mm hole pitch", "Move the two holes to 50 mm apart for the new rail.",
                 acceptance=["edge distance kept => hole edge distance (mm)", "fits the rail => human"])
    T.set_plan(p, "T-0001", {"summary": "hole_pitch 40 -> 50; width 60 -> 70 to keep the 4 mm edge distance.",
                             "parameters": [{"name": "hole_pitch", "from": 40, "to": 50}]})
    T.approve(p, "T-0001", confirm=_person)
    assert not tools.set_params({"hole_pitch": 50}).is_error
    with pytest.raises(T.PackageRefused, match="FAIL hole edge distance"):
        T.make_package(p, "T-0001")
    assert not tools.set_params({"width": 70}).is_error
    pkg = T.make_package(p, "T-0001")
    _validate(Path(pkg["folder"]) / "package.json")
    outs = {f["path"] for f in pkg["files"] if f["role"] == "output"}
    assert {"outputs/bracket.step", "outputs/bracket.stl", "outputs/bracket.3mf", "outputs/bracket.drawing.svg", "outputs/bracket.dxf"} <= outs
    assert pkg["outputs_missing"] == []
    part = next(r for r in pkg["bom"] if r["table"] == "parts" and r["key"] == "plate")
    assert part["change"] == "changed" and "hole_centres" in part["fields"]
    assert pkg["unplanned_changes"] == ["width"]
    assert "did not list it: width" in Path(pkg["folder"], "report.md").read_text(encoding="utf-8")
    _sources_are_structured(pkg)
    _export_sample(Path(pkg["folder"]))


def test_harness_package_refuses_a_fuse_check_not_run_until_a_person_waives_it(tmp_path):
    """The overcurrent checks' "not run" (a supply that publishes no short-circuit current) is a first-class status: the package
    refuses on it, a person waives it with a reason, and every protection row cites its datasheet with its URL."""
    folder = tmp_path / "servo-arm"
    folder.mkdir()
    shutil.copy(ROOT / "eitri" / "demos" / "servo-arm" / "servo_arm_harness.py", folder / "servo_arm_harness.py")
    p = Project(folder)
    tools = Tools(p)
    assert p.build("as delivered").ok
    T.new_ticket(p, "Longer shoulder lead", "The shoulder servo moves 80 mm further out: lengthen its lead to 400 mm.",
                 acceptance=["the lead still carries the servo => ampacity SH_V*", "the fuse still protects it => F1 rating within the wire it protects",
                             "routing in the arm => human"], customer="Example Robotics")
    T.set_plan(p, "T-0001", {"summary": "lead_mm 320 -> 400.", "parameters": [{"name": "lead_mm", "from": 320, "to": 400}],
                             "checks_affected": ["voltage drop V5", "F1 continuous load"]})
    T.approve(p, "T-0001", by="Pat Person", confirm=_person)
    assert not tools.set_params({"lead_mm": 400}).is_error
    with pytest.raises(T.PackageRefused, match="F1 interrupting rating"):
        T.make_package(p, "T-0001")
    T.waive(p, "T-0001", "F1 interrupting rating", "Mean Well publishes no short-circuit current for the LRS-50-5; its hiccup "
            "protection limits the fault, accepted by the customer", by="Pat Person", confirm=_person)
    pkg = T.make_package(p, "T-0001")
    _validate(Path(pkg["folder"]) / "package.json")
    rows = {r["name"]: r for r in pkg["checks"]["rows"]}
    assert rows["F1 interrupting rating"]["to"]["status"] == "not_run" and pkg["waivers"][0]["check"] == "F1 interrupting rating"
    assert pkg["checks"]["summary"]["to"]["not_run"] == 1 and pkg["checks"]["summary"]["to"]["fail"] == 0
    cont = rows["F1 continuous load"]
    assert cont["rule"] == "harness.fuse-continuous-load"
    assert any(x["basis"] == "document" and x["url"].startswith("https://") and "Littelfuse" in x["text"] for x in cont["sources"]), cont
    assert "Littelfuse" in cont["source"] and "https://" in cont["source"]
    _sources_are_structured(pkg)
    assert "F1 interrupting rating" in Path(pkg["folder"], "report.md").read_text(encoding="utf-8").split("## Acceptance criteria")[0]
    _export_sample(Path(pkg["folder"]))


# ---------------------------------------------------------------------------------------------------- the command line, no agent
def test_cli_only_path_from_approval_to_package(tmp_path, monkeypatch, capsys):
    """0.8.1: a person with no agent applies an approved plan with `eitri build <model> --record --set k=v` and packages it. The
    plan gate refuses it before approval exactly as it refuses the agent, and an unplanned parameter is flagged on page 1."""
    from eitri.cli import main
    p = _project(tmp_path, mech())
    model = str(p.model_path)
    assert main(["build", model, "--record", "--note", "as delivered"]) == 0           # no ticket yet: a recorded revision 1
    assert "recorded revision 1" in capsys.readouterr().out
    assert Project(p.folder).history[-1].note == "as delivered"
    assert main(["ticket", "new", "-C", str(p.folder), "--title", "Heavier camera", "--request", "camera is 150 g",
                 "--criterion", "tilt holds it => torque joint tilt*"]) == 0
    T.set_plan(p, "T-0001", PLAN)
    before = p.model_path.read_text(encoding="utf-8")
    capsys.readouterr()
    assert main(["build", model, "--record", "--set", "camera_g=150"]) == 1           # awaiting approval: refused
    err = capsys.readouterr().err
    assert "refused" in err and "eitri ticket approve T-0001" in err
    assert p.model_path.read_text(encoding="utf-8") == before                        # nothing written
    assert main(["build", model, "--record"]) == 1                                    # a plain recorded build is gated too
    tty = _FakeTTY()
    monkeypatch.setattr(T.store, "_open_tty", lambda: tty)
    assert main(["ticket", "approve", "T-0001", "-C", str(p.folder), "--by", "Pat Person"]) == 0
    capsys.readouterr()
    assert main(["build", model, "--record", "--set", "camera_g=150", "--set", "arm_mm=36", "--note", "T-0001 plan"]) == 0
    out = capsys.readouterr().out
    assert "recorded revision 2" in out and "camera_g=150" in Project(p.folder).read_model().replace(" ", "")
    assert main(["build", model, "--record", "--set", "nope=1"]) == 2                 # an unknown parameter changes nothing
    assert main(["package", "T-0001", "-C", str(p.folder)]) == 0
    pkg = json.loads(next((p.folder / "tickets" / "T-0001").glob("T-0001-tilt-rev-B/package.json")).read_text(encoding="utf-8"))
    assert {r["name"]: r["change"] for r in pkg["parameters"]}["camera_g"] == "changed"
    assert pkg["unplanned_changes"] == ["arm_mm"]                                   # the same flag the agent's changes get
