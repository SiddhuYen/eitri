"""Tickets and change packages on a combined project (eitri.tickets.combined): a ticket covers every model file, approval sees a
change to any of them, the package rebuilds them all together with the cross-domain checks, and refuses on any failing or
unwaived not-run check of any file.

The person's terminal is stood in by `_person` (approve/waive take a confirmer), as in test_tickets.py. The uno-arm test uses a
stand-in arduino-cli that reports a successful compile (this Mac has none): the compile check is the only thing it fakes."""
from __future__ import annotations

import asyncio
import csv
import json
import re
from pathlib import Path

import pytest

from eitri import tickets as T
from eitri.project import Project
from eitri_suite import workspace as W

ROOT = Path(__file__).resolve().parents[1]
UNO = ROOT / "eitri_suite" / "demos" / "uno-arm"
QUAD = (ROOT / "eitri" / "demos" / "quad-power" / "quad_power.py").read_text(encoding="utf-8")   # declares no power source
BOX = '''from eitri import *

PARAMS = dict(w=40)

def build(p):
    return Box(p.w, 30, 5)

def verify(parts, p, c):
    c.true("plate is wide enough", p.w >= 30)
'''
MECH_NO_TRAVEL = (ROOT / "tests" / "test_tickets.py").read_text(encoding="utf-8").split("MECH = '''")[1].split("'''")[0].replace("TRAVEL", "None")
FAKE_CLI = '''#!/bin/sh
echo "Sketch uses 4242 bytes (13%) of program storage space. Maximum is 32256 bytes."
echo "Global variables use 222 bytes (10%) of dynamic memory, leaving 1826 bytes for local variables. Maximum is 2048 bytes."
exit 0
'''


def _person(text, what):
    return {"tty": "test-terminal"}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    for k in T.store.AGENT_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EITRI_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("EITRI_NO_WARM", "1")
    monkeypatch.setenv("EITRI_WORKER", "0")
    monkeypatch.setenv("EITRI_ARDUINO_CLI", str(tmp_path / "no-arduino-cli"))
    monkeypatch.delenv("EITRI_SUITE", raising=False)
    T.set_signer(T.store.LocalKeySigner())
    return W.Workspace(tmp_path / "projects")


def _build(ws, pid):
    r = ws.build(pid, image=False, wait_s=900)
    assert r.get("complete"), r
    return r


def _bench(ws) -> tuple[str, Project]:
    pid = ws.new_project("Bench lead", "a plate and its supply lead")["project_id"]
    ws.put_files(pid, {"model.py": BOX, "wiring.py": QUAD})
    r = _build(ws, pid)
    return pid, Project(Path(r["folder"]))


def _validate(path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "eitri" / "tickets" / "package.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(json.loads(path.read_text(encoding="utf-8")), schema)


# ---------------------------------------------------------------------------------------------------- the ticket
def test_a_combined_ticket_covers_every_file_and_gates_them_all(ws):
    pid, p = _bench(ws)
    with pytest.raises(T.TicketError, match="covers every model file"):
        T.new_ticket(p, "t", "r", file="wiring.py")
    with pytest.raises(T.TicketError, match="latest good build of every file"):
        T.new_ticket(p, "t", "r", base=3)
    t = T.new_ticket(p, "Wider plate", "Make the plate 45 mm wide.", acceptance=["wide enough => plate is wide enough"])
    revs = {f: p.last_ok_revision(model=f).number for f in ("model.py", "wiring.py")}
    assert t.combined and t.target_file == "" and t.data["target"] == {
        "combined": True, "files": [{"file": "model.py", "revision": revs["model.py"]}, {"file": "wiring.py", "revision": revs["wiring.py"]}]}
    text = T.store.describe(p, t)
    assert "every model file checked together" in text and "wiring.py from revision" in text
    # the gate covers every file and every surface: the combined tools, Project, the viewer's writes
    for call in (lambda: ws.put_files(pid, {"wiring.py": QUAD + "\n"}), lambda: ws.build(pid, image=False)):
        with pytest.raises(W.ToolError, match="no plan yet"):
            call()
    with pytest.raises(T.ChangeRefused):
        p.set_model_file("wiring.py") or p.build("try")
    # the plan records every file; a change to the OTHER file before approval stops it
    T.set_plan(p, t.id, {"summary": "w 40 -> 45", "parameters": [{"file": "model.py", "name": "w", "from": 40, "to": 45}]})
    t = T.load(p, t.id)
    assert t.data["plan"]["files"] == ["model.py", "wiring.py"] and set(t.data["plan"]["models_sha256"]) == {"model.py", "wiring.py"}
    with pytest.raises(W.ToolError) as exc:
        ws.put_files(pid, {"model.py": BOX})
    assert "awaiting approval" in str(exc.value) and f"eitri ticket approve {t.id} -C " in str(exc.value)
    (p.folder / "wiring.py").write_text(QUAD + "# edited before approval\n", encoding="utf-8")       # an agent's own file tools
    with pytest.raises(T.TicketError, match=r"wiring\.py changed on disk after the plan"):
        T.approve(p, t.id, confirm=_person)
    (p.folder / "wiring.py").write_text(QUAD, encoding="utf-8")
    T.approve(p, t.id, confirm=_person)
    assert T.load(p, t.id).status == "approved"
    ws.put_files(pid, {"model.py": BOX.replace("w=40", "w=45")})                # now it may change


def test_combined_package_refuses_not_run_then_takes_a_waiver_per_file(ws):
    """The tilt servo's travel is unset, so mech.py's joint-limits check does not run: no package until a person waives it,
    named with its file (or by its bare name, when only one file has it). The package then has per-file diffs, the project's
    revision letter, and flags a file changed that the plan did not list."""
    pid = ws.new_project("Tilt plate", "a plate and the tilt it carries")["project_id"]
    ws.put_files(pid, {"model.py": BOX, "mech.py": MECH_NO_TRAVEL})
    p = Project(Path(_build(ws, pid)["folder"]))
    t = T.new_ticket(p, "Wider plate", "Make the plate 45 mm wide.", acceptance=["wide enough => model.py: plate is wide enough"])
    T.set_plan(p, t.id, {"summary": "w 40 -> 45", "files": ["model.py"], "parameters": [{"file": "model.py", "name": "w", "from": 40, "to": 45}]})
    T.approve(p, t.id, confirm=_person)
    with pytest.raises(T.PackageRefused, match="has no good build"):         # nothing built since the change
        ws.put_files(pid, {"model.py": BOX.replace("w=40", "w=45"), "mech.py": MECH_NO_TRAVEL + "# a comment: a change the plan did not list\n"})
        T.make_package(p, t.id)
    _build(ws, pid)
    with pytest.raises(T.PackageRefused) as exc:
        T.make_package(p, t.id)
    not_run = [r for r in exc.value.reasons if r.startswith("NOT RUN ")]
    assert not_run and all(r.startswith("NOT RUN mech.py: ") for r in not_run) and not [r for r in exc.value.reasons if r.startswith("FAIL")]
    assert "eitri ticket waive" in not_run[0]
    from eitri.tickets.combined import last_rows
    names = [r["name"] for r in last_rows(p) if r["status"] == "not_run"]
    assert len(names) == len(not_run) and all(n.startswith("mech.py: ") for n in names)
    with pytest.raises(T.TicketError, match="no check named"):
        T.waive(p, t.id, "model.py: " + names[0].split(": ", 1)[1], "a long enough reason here", confirm=_person)
    for i, n in enumerate(names):                         # the first by its bare name (only one file has it), the rest qualified
        T.waive(p, t.id, n.split(": ", 1)[1] if i == 0 else n, "The SG90's travel is 180 deg per TowerPro; the joint uses 90 deg.",
                by="Pat Person", confirm=_person)
    assert [w["check"] for w in T.load(p, t.id).data["waivers"]] == names
    pkg = T.make_package(p, t.id)
    _validate(Path(pkg["folder"], "package.json"))
    d = pkg["design"]
    assert d["kind"] == "combined" and d["combined"] and [f["file"] for f in d["files"]] == ["model.py", "mech.py"]
    assert {f["file"]: f["change"] for f in d["files"]} == {"model.py": "changed", "mech.py": "changed"}
    assert pkg["revisions"]["from"]["letter"] == "A" and pkg["revisions"]["to"]["letter"] == "B"
    assert set(pkg["revisions"]["to"]["builds"]) == {"model.py", "mech.py"}
    assert [x for x in pkg["parameters"] if x["change"] != "same"] == [{"file": "model.py", "name": "w", "from": 40, "to": 45, "change": "changed"}]
    assert pkg["unplanned_changes"] == ["mech.py (changed; not among the plan's files)"]
    assert pkg["acceptance"][0]["matched"] == ["model.py: plate is wide enough"] and pkg["acceptance"][0]["status"] == "pass"
    assert {w["check"] for w in pkg["waivers"]} == set(names) and pkg["checks"]["summary"]["to"]["not_run"] == len(names)
    assert all(r["name"] == f"{r['file']}: {r['check']}" for r in pkg["checks"]["rows"])
    folder = Path(pkg["folder"])
    assert folder.name == f"{t.id}-tilt-plate-rev-B" and Path(pkg["zip"]).is_file()
    assert sorted(x.name for x in (folder / "model" / "rev-A").iterdir()) == ["mech.py", "model.py"]
    diff = (folder / "source.diff").read_text(encoding="utf-8")
    assert "Rev A/model.py" in diff and "Rev B/mech.py" in diff and "+PARAMS = dict(w=45)" in diff
    with (folder / "checks.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert {r["file"] for r in rows} == {"model.py", "mech.py"} and {r["cross_domain"] for r in rows} <= {"yes", "no"}
    assert (folder / "parameters.csv").read_text(encoding="utf-8").startswith("file,parameter,")
    assert (folder / "outputs" / "model" / "model.step").is_file() and (folder / "outputs" / "model" / "model.drawing.svg").is_file()
    assert any((folder / "outputs" / "mech").iterdir())
    page1 = (folder / "report.md").read_text(encoding="utf-8").split("## Acceptance criteria")[0]
    assert "Combined project tilt-plate" in page1 and "| mech.py | mechanism | changed |" in page1 and "WAIVERS" in page1
    assert "not among the plan's files" in page1
    # the build package refuses while checks did not run (strict), so it is listed as missing, never silently skipped
    assert [m for m in pkg["outputs_missing"] if m["what"].startswith("build package")]
    revs = json.loads((p.folder / "tickets" / "revisions.json").read_text(encoding="utf-8"))
    assert [r["letter"] for r in revs["project"]] == ["A", "B"] and revs["project"][1]["builds"] == pkg["revisions"]["to"]["builds"]
    assert T.load(p, t.id).status == "packaged"


def test_combined_package_refuses_a_removed_check_until_a_removed_waiver(ws):
    """0.9.2's rule on a combined project: a verify() line the change deletes from one file is REMOVED; `--lost`
    (lost_since_base) finds it from the stored builds, the waiver records kind "removed", and the package lists it."""
    pid = ws.new_project("Tilt plate", "a plate and the tilt it carries")["project_id"]
    ws.put_files(pid, {"model.py": BOX, "mech.py": MECH_NO_TRAVEL})
    p = Project(Path(_build(ws, pid)["folder"]))
    t = T.new_ticket(p, "Drop the width check", "The plate width is fixed by the customer; drop its check.")
    T.set_plan(p, t.id, {"summary": "remove the width verify() line", "files": ["model.py"]})
    T.approve(p, t.id, confirm=_person)
    ws.put_files(pid, {"model.py": BOX.replace('    c.true("plate is wide enough", p.w >= 30)\n', '    pass\n')})
    _build(ws, pid)
    with pytest.raises(T.PackageRefused) as exc:
        T.make_package(p, t.id)
    assert [r for r in exc.value.reasons if r.startswith("REMOVED model.py: plate is wide enough")], exc.value.reasons
    lost = T.store.lost_since_base(p, T.load(p, t.id))
    assert [(x["kind"], x["check"]) for x in lost] == [("removed", "model.py: plate is wide enough")]
    T.waive(p, t.id, [x["check"] for x in lost], "The customer fixes the width on their drawing; no check needed.",
            by="Pat Person", confirm=_person)
    assert T.load(p, t.id).data["waivers"][0]["kind"] == "removed"
    from eitri.tickets.combined import last_rows
    not_run = [r["name"] for r in last_rows(p) if r["status"] == "not_run"]          # the tilt servo's travel is unset
    assert not_run
    T.waive(p, t.id, not_run, "The SG90's travel is 180 deg per TowerPro; the joint uses 90 deg.", confirm=_person)
    pkg = T.make_package(p, t.id)
    _validate(Path(pkg["folder"], "package.json"))
    assert pkg["removed_checks"] == ["model.py: plate is wide enough"] and pkg["switched_off_checks"] == []
    assert [w["kind"] for w in pkg["waivers"]] == ["removed"] + ["not_run"] * len(not_run)
    assert [x for x in pkg["not_checked"] if x["kind"] == "removed_check" and x["what"] == "model.py: plate is wide enough"]


# ---------------------------------------------------------------------------------------------------- end to end: the uno arm
def test_uno_arm_payload_change_end_to_end(ws, tmp_path, monkeypatch):
    """The reference arm (five files, 160+ checks, 60+ of them between the files). A change that overloads the shoulder fails
    a cross-domain check (the torque with the CAD bodies' masses) and is refused; the planned change packages, the criterion
    matches the shoulder torque checks in mech.py, the build package is in outputs/, and the next ticket starts from Rev B."""
    cli = tmp_path / "arduino-cli"
    cli.write_text(FAKE_CLI, encoding="utf-8")
    cli.chmod(0o755)
    monkeypatch.setenv("EITRI_ARDUINO_CLI", str(cli))
    pid = ws.new_project("Uno arm", "a desk robot arm with an Arduino Uno")["project_id"]
    ws.put_files(pid, {f.name: f.read_text(encoding="utf-8") for f in UNO.glob("*.py")})
    p = Project(Path(_build(ws, pid)["folder"]))
    t = T.new_ticket(p, "Lighter payload", "Make it carry 40 g, not 50 g.",
                     acceptance=["the shoulder holds it => torque joint shoulder*", "looks tidy => human"])
    T.set_plan(p, t.id, {"summary": "payload_g 50 -> 40 in model.py and mech.py", "files": ["model.py", "mech.py"],
                         "parameters": [{"file": f, "name": "payload_g", "from": 50, "to": 40} for f in ("model.py", "mech.py")]})
    T.approve(p, t.id, by="Pat Person", confirm=_person)

    def payload(g):
        ws.put_files(pid, {f: (UNO / f).read_text(encoding="utf-8").replace("payload_g=50.0", f"payload_g={g}") for f in ("model.py", "mech.py")})
        _build(ws, pid)
    payload(900.0)
    with pytest.raises(T.PackageRefused) as exc:
        T.make_package(p, t.id)
    assert any(r.startswith("FAIL mech.py: torque joint shoulder (MG996R) (masses from CAD)") for r in exc.value.reasons), exc.value.reasons
    payload(40.0)
    pkg = T.make_package(p, t.id)
    _validate(Path(pkg["folder"], "package.json"))
    s = pkg["checks"]["summary"]
    assert s["to"]["fail"] == 0 and s["to"]["not_run"] == 0 and s["to"]["total"] > 150 and s["cross_domain"]["to"] > 60
    assert [f["file"] for f in pkg["design"]["files"]] == ["model.py", "mech.py", "wiring.py", "fw.py", "elec.py"]
    assert {f["file"]: f["change"] for f in pkg["design"]["files"]} == {"model.py": "changed", "mech.py": "changed", "wiring.py": "same",
                                                                        "fw.py": "same", "elec.py": "same"}
    assert pkg["unplanned_changes"] == []
    ac = pkg["acceptance"][0]
    assert ac["status"] == "pass" and ac["matched"] == ["mech.py: torque joint shoulder (MG996R)", "mech.py: torque joint shoulder (MG996R) (masses from CAD)"]
    shoulder = next(r for r in pkg["checks"]["rows"] if r["name"] == "mech.py: torque joint shoulder (MG996R) (masses from CAD)")
    assert shoulder["cross_domain"] and shoulder["change"] in ("value", "status")
    folder = Path(pkg["folder"])
    assert (folder / "outputs" / "build_package" / "README.md").is_file() and (folder / "outputs" / "build_package" / "wiring_steps.md").is_file()
    assert not [m for m in pkg["outputs_missing"] if m["what"].startswith("build package")], pkg["outputs_missing"]
    html = (folder / "report.html").read_text(encoding="utf-8")
    assert "Model files" in html and "cross-domain" in html
    # the next ticket starts from the released Rev B
    T.close(p, t.id, confirm=_person)
    t2 = T.new_ticket(p, "Next", "next change")
    assert {f: r for f, r in t2.target_files.items()} == pkg["revisions"]["to"]["builds"]


# ---------------------------------------------------------------------------------------------------- 0.9.2 / 0.9.3 statuses
SENSOR_BOX = ROOT / "tests" / "suite" / "fixtures" / "sensor-box"
MEASURED = 'measured=dict(size=(25.4, 18.0, 1.6), hole_spacing=(20.32, 12.7), hole_d=2.5, by="calipers on my board, 2026-11-18")'
MOUNT = 'carrier=["base"], screw="M2.5", clearance_mm=0.5)'
SENSOR_EDITS = {
    "elec.py": [('c.module("BME280", "BME280-2652", address=0x77)', 'c.module("BME280", "BME280", address=0x77)'),
                ('bme["VIN"]', 'bme["VCC"]'), ('bme["SDI"]', 'bme["SDA"]'), ('bme["SCK"]', 'bme["SCL"]'),
                (MOUNT, MOUNT[:-1] + ",\n            " + MEASURED + ")"),
                ("PARAMS = dict()", "PARAMS = dict(max_draw=0.050)"), ("0.020 + 0.0012, 0.050)", "0.020 + 0.0012, p.max_draw)")],
    "wiring.py": [('pins=["VIN", "GND", "SCK", "SDI"]', 'pins=["VCC", "GND", "SCL", "SDA"]'),
                  ('bme["VIN"]', 'bme["VCC"]'), ('bme["SDI"]', 'bme["SDA"]'), ('bme["SCK"]', 'bme["SCL"]')],
}


def _sensor_box_files() -> dict[str, str]:
    files = {}
    for f in SENSOR_BOX.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for a, b in SENSOR_EDITS.get(f.name, []):
            assert a in text, (f.name, a)
            text = text.replace(a, b)
        files[f.name] = text
    return files


def test_combined_package_refuses_assumed_until_waived_and_na_blocks_nothing(ws, tmp_path, monkeypatch):
    """The sensor box (CAD case, harness, firmware, electronics; no mechanism) with a BME280 board the person measured: its three
    mounting checks are ASSUMED (0.9.3), so the combined package is refused until a person waives each, exactly like the
    single-file package; the electronics file's actuator checks are n/a (0.9.2: no mechanism file, nothing that moves), which
    blocks nothing and is listed under "not checked"."""
    from eitri.checks import MEASURED_BY_USER
    cli = tmp_path / "arduino-cli"
    cli.write_text(FAKE_CLI, encoding="utf-8")
    cli.chmod(0o755)
    monkeypatch.setenv("EITRI_ARDUINO_CLI", str(cli))
    pid = ws.new_project("Sensor box", "a weather sensor in a printed case")["project_id"]
    ws.put_files(pid, _sensor_box_files())
    p = Project(Path(_build(ws, pid)["folder"]))
    t = T.new_ticket(p, "Tighter rail budget", "Hold the 3V3 rail draw under 45 mA.", acceptance=["the rail holds => 3V3 rail draw*"])
    T.set_plan(p, t.id, {"summary": "max_draw 0.050 -> 0.045", "files": ["elec.py"],
                         "parameters": [{"file": "elec.py", "name": "max_draw", "from": 0.05, "to": 0.045}]})
    T.approve(p, t.id, by="Pat Person", confirm=_person)
    text = (p.folder / "elec.py").read_text(encoding="utf-8").replace("max_draw=0.050", "max_draw=0.045")
    ws.put_files(pid, {"elec.py": text})
    _build(ws, pid)
    with pytest.raises(T.PackageRefused) as exc:
        T.make_package(p, t.id)
    assumed = [r for r in exc.value.reasons if r.startswith("ASSUMED ")]
    assert {r.split(":", 2)[0] + ":" + r.split(":", 2)[1] for r in assumed} == {"ASSUMED elec.py: mounting"}, exc.value.reasons
    assert len(assumed) == 3 and all(MEASURED_BY_USER in r and "eitri ticket waive" in r for r in assumed)
    assert not [r for r in exc.value.reasons if r.startswith(("FAIL", "NOT RUN"))], exc.value.reasons
    for name in ("mounting: BME280 hole pattern matches the CAD", "mounting: BME280 outline is clear of the CAD bodies",
                 "mounting: BME280 clearance to the CAD bodies"):                  # bare names: only elec.py has them
        T.waive(p, t.id, name, "The customer measured their own BME280 board with calipers and accepts the figures",
                by="Pat Person", confirm=_person)
    pkg = T.make_package(p, t.id)
    _validate(Path(pkg["folder"], "package.json"))
    s = pkg["checks"]["summary"]["to"]
    assert s["fail"] == 0 and s["not_run"] == 0 and s["assumed"] == 3 and s["not_applicable"] > 0
    assert len(pkg["assumed"]) == 3 and all(a["check"].startswith("elec.py: mounting: BME280") and "calipers" in a["declared"]
                                            for a in pkg["assumed"])
    kinds = [x["kind"] for x in pkg["not_checked"]]
    assert kinds.count("assumed") == 3 and kinds.count("not_applicable") == s["not_applicable"]
    assert all(x["what"].startswith(("elec.py: ", "wiring.py: ", "fw.py: ", "model.py: ")) for x in pkg["not_checked"]
               if x["kind"] in ("assumed", "not_applicable"))
    assert pkg["acceptance"][0]["status"] in ("pass", "pass_with_warning") and pkg["acceptance"][0]["matched"]
    report = (Path(pkg["folder"]) / "report.md").read_text(encoding="utf-8")
    assert MEASURED_BY_USER in report and "not applicable" in report


def _row(name, status, file="elec.py", measured="m"):
    return {"name": f"{file}: {name}", "check": name, "file": file, "status": status, "measured": measured, "expected": "",
            "inputs_unset": [], "assumed": "calipers" if status == "assumed" else "", "category": "", "cross_domain": False}


def test_one_refusal_rule_for_single_file_and_combined(monkeypatch):
    """The combined package calls the single-file package's judge(), so a status added to one rule is in the other: n/a alone
    blocks nothing, but a criterion that maps only to n/a checks is not met; ASSUMED and NOT RUN need a waiver; FAIL never."""
    from eitri.tickets import combined as C
    from eitri.tickets import package as P
    t = SimpleTicket(acceptance=[{"id": "AC1", "text": "the arm moves", "check": "actuator*"}])
    rows = [_row("actuator: joints have actuators", "not_applicable", measured="not applicable: no mechanism file"),
            _row("rail budget", "pass"), _row("mounting: hole pattern", "assumed"), _row("i2c pull-ups", "not_run")]
    reasons, _waivers, criteria, _lost, _renamed = P.judge(t, rows, C.match, where="no file's build runs", they="they do")
    assert [r.split(" ", 1)[0] for r in reasons] == ["ASSUMED", "NOT", "AC1"], reasons
    assert criteria[0]["status"] == "not_applicable" and "does not apply" in reasons[-1]
    reasons, _w, criteria, _l, _r = P.judge(SimpleTicket(acceptance=[{"id": "AC1", "text": "budget", "check": "rail budget"}]),
                                    [rows[0], rows[1]], C.match)
    assert reasons == [] and criteria[0]["status"] == "pass"                            # n/a alone blocks nothing
    reasons, _w, _c, _l, _r = P.judge(t, [_row("x", "fail")] + rows[:2], C.match)
    assert reasons[0].startswith("FAIL elec.py: x") and "never waived" in reasons[0]
    # 0.9.2's lost checks (a check the base ran, removed or switched off by the change) are the same rule for every file
    base = [_row("rail budget", "pass"), _row("servo torque", "pass", file="mech.py"), _row("joint travel", "pass", file="mech.py")]
    after = [_row("rail budget", "pass"), {**_row("joint travel", "not_run", file="mech.py"), "inputs_unset": ["travel_deg"]}]
    reasons, _w, _c, lost, _r = P.judge(SimpleTicket(), after, C.match, rows_a=base)
    assert sorted((x["kind"], x["check"]) for x in lost) == [("removed", "mech.py: servo torque"), ("switched_off", "mech.py: joint travel")]
    assert [r.split(" mech.py")[0] for r in reasons] == ["REMOVED", "SWITCHED OFF"], reasons
    waivers = [{"check": "mech.py: servo torque", "kind": "removed"}, {"check": "mech.py: joint travel", "kind": "not_run"}]
    monkeypatch.setattr(P, "verify_waiver", lambda t, w: True)                       # stand-in signatures
    reasons, applied, _c, _l, _r = P.judge(SimpleTicket(waivers=waivers), after, C.match, rows_a=base)
    assert [r.split(" mech.py")[0] for r in reasons] == ["SWITCHED OFF"], reasons      # a "did not run" waiver does not cover a switch-off
    assert {w["check"] for w in applied} == {"mech.py: servo torque", "mech.py: joint travel"}
    import inspect
    assert "P.judge(" in inspect.getsource(C._make) and "P.status_not_checked(" in inspect.getsource(C._make)


class SimpleTicket:
    id = "T-0001"

    def __init__(self, acceptance=(), waivers=()):
        self.data = {"acceptance": list(acceptance), "waivers": list(waivers)}


# ---------------------------------------------------------------------------------------------------- the combined MCP server
def test_combined_server_has_ticket_tools_but_none_that_approve(ws):
    from mcp.server.mcpserver.exceptions import ToolError

    from eitri.agent.combined_server import CombinedSession, make_server
    session = CombinedSession(root=ws.root, open_browser=False)
    server = make_server(session)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"combined_ticket_new", "combined_ticket_show", "combined_ticket_plan", "combined_package"} <= names
    person_only = re.compile(r"approv|waiv|close|cancel|(^|_)sign|confirm|release|set_status|accept", re.IGNORECASE)
    assert not [n for n in names if person_only.search(n)], names

    def call(name, args):
        try:
            res = asyncio.run(server.call_tool(name, args))
        except ToolError as exc:
            return str(exc), True
        return "\n".join(getattr(c, "text", "") for c in res.content), bool(res.is_error)
    pid = session.ws.new_project("Plate", "a plate")["project_id"]
    session.ws.put_files(pid, {"model.py": BOX})
    _build(session.ws, pid)
    text, err = call("combined_ticket_new", {"project_id": pid, "title": "Wider", "request": "45 mm", "acceptance": ["wide => plate is wide enough"]})
    assert not err and "Opened T-0001" in text and "model.py: plate is wide enough" in text
    text, err = call("combined_ticket_plan", {"project_id": pid, "ticket": "T-0001", "summary": "w 40 -> 45"})
    assert not err and "STOP HERE" in text and "eitri ticket approve T-0001 -C " in text
    text, err = call("combined_put_files", {"project_id": pid, "files": {"model.py": BOX.replace("w=40", "w=45")}})
    assert err and "awaiting approval" in text
    text, err = call("combined_package", {"project_id": pid})
    assert err and "awaiting approval" in text
    text, err = call("combined_ticket_show", {"project_id": pid})
    assert not err and "AWAITING APPROVAL" in text
