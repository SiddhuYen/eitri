"""0.9.2: "not applicable" as a check status, distinct from "not run": the check's subject provably does not exist in the project.
It is ok, says why, prints as N/A, is counted apart from the passes, and blocks neither an export nor a change package."""
from __future__ import annotations

from eitri.checks import (
    BLOCKING,
    STATUSES,
    check,
    checks_not_applicable,
    checks_not_run,
    from_verify,
    not_applicable,
    not_applicable_line,
    stamp_status,
    status_of,
    switched_off,
    tally,
)
from eitri.model import CheckResult


def test_not_applicable_is_a_status_of_its_own():
    c = not_applicable(check("integration", "actuators: stall current vs the drivers", "warn", "no mechanism"), "nothing is actuated")
    assert c.status == "not_applicable" and c.ok and c.severity == "pass" and c.not_run == []
    assert c.measured == "not applicable: nothing is actuated"
    assert c.line().startswith("[N/A] actuators: stall current vs the drivers: not applicable: nothing is actuated")
    assert "not_applicable" in STATUSES and "not_applicable" not in BLOCKING
    assert checks_not_run([c]) == []                                   # not a check that was switched off
    assert checks_not_applicable([c]) == [{"name": c.name, "category": "integration", "why": "nothing is actuated"}]
    assert "actuators: stall current vs the drivers (nothing is actuated)" in not_applicable_line([c])
    assert not_applicable_line([check("x", "a", "pass", "m")]) is None


def test_a_failed_check_is_never_not_applicable():
    f = check("x", "a", "fail", "not applicable: written by hand")
    assert f.status == "fail"
    r = CheckResult("a", False, "m", not_applicable="anything")
    assert r.status == "fail"
    # a warning whose text happens to start so stays a warning
    assert check("x", "a", "warn", "not applicable: x").status == "warn"


def test_the_json_form_reads_back_and_older_readers_see_an_ok_check():
    c = not_applicable(check("integration", "n", "pass", ""), "no harness file")
    d = {"checks": [{"name": c.name, "ok": c.ok, "severity": c.severity, "measured": c.measured, "not_applicable": c.not_applicable}]}
    stamp_status(d, [c])
    assert d["checks"][0]["status"] == "not_applicable"
    assert d["checks_not_applicable"][0]["why"] == "no harness file" and d["checks_not_run"] == []
    # an older build has no "status": derived from the field, or from the text
    assert status_of({"ok": True, "severity": "pass", "not_applicable": "x", "measured": "m"}) == "not_applicable"
    assert status_of({"ok": True, "severity": "pass", "measured": "not applicable: x"}) == "not_applicable"
    assert status_of({"ok": True, "measured": "not applicable: x"}) == "not_applicable"        # a CAD verify() row
    assert status_of({"ok": False, "severity": "fail", "measured": "not applicable: x"}) == "fail"


def test_verify_results_and_cad_reports_carry_it():
    r = CheckResult("clearance to the lid", True, "not applicable: the design has no lid", not_applicable="the design has no lid")
    assert r.status == "not_applicable" and r.line().startswith("[N/A] clearance to the lid")
    assert from_verify(r).status == "not_applicable"
    from eitri.kernel import _report_from_dict as report_from_dict
    from eitri.report import Report
    rep = Report(parts=[], bbox_min=(0, 0, 0), bbox_max=(0, 0, 0), size=(0, 0, 0), volume=0.0, checks=[r])
    back = report_from_dict(rep.to_dict())
    assert back.checks[0].status == "not_applicable"


def test_the_checks_line_counts_it_apart_from_the_passes():
    checks = [check("x", "a", "pass", "m"), check("x", "b", "warn", "m"), check("x", "c", "fail", "m"),
              not_applicable(check("x", "d", "pass", ""), "absent")]
    assert tally(checks) == "1 pass, 1 warn, 1 FAIL, 1 n/a"
    assert tally(checks[:3]) == "1 pass, 1 warn, 1 FAIL"                # unchanged when nothing is n/a (the smoke-test lines)


def test_a_switched_off_check_stays_not_run():
    c = switched_off(check("protection", "F1", "warn", "no figure"), "PSU.short_circuit_A")
    assert c.status == "not_run" and "not_run" in BLOCKING


def test_ticket_rows_count_it_and_a_criterion_on_it_is_not_met():
    from eitri.tickets.checks import counts, normalise
    c = not_applicable(check("integration", "mounting: boards and modules fit their CAD mounts", "pass", ""), "no CAD file")
    rows = normalise({"kind": "electronics", "report": {"checks": [{"name": c.name, "ok": True, "severity": "pass",
                                                                     "measured": c.measured, "status": c.status}]}})
    assert rows[0]["status"] == "not_applicable"
    n = counts(rows)
    assert n["not_applicable"] == 1 and n["pass"] == 0 and n["total"] == 1
