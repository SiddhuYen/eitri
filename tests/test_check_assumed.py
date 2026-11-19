"""0.9.3: "assumed", a check that passed only on a figure the model file declares instead of a sourced one (a board size measured by
the user). It passes the combined export (not in BLOCKING) and blocks a change package (PACKAGE_BLOCKING) until a person waives it."""
from __future__ import annotations

import numpy as np

from eitri.checks import (
    BLOCKING,
    MEASURED_BY_USER,
    PACKAGE_BLOCKING,
    STATUSES,
    assumed,
    assumed_line,
    check,
    checks_assumed,
    from_verify,
    stamp_status,
    status_of,
    switched_off,
    tally,
)
from eitri.model import CheckResult


def test_assumed_is_its_own_status_and_blocks_only_packages():
    c = assumed(check("integration", "mounting: ENV hole pattern matches the CAD", "pass", "all 4 holes line up"), MEASURED_BY_USER)
    assert c.status == "assumed" and c.ok and c.line().startswith("[ASSUMED] mounting: ENV hole pattern")
    assert "assumed" in STATUSES and "assumed" not in BLOCKING and "assumed" in PACKAGE_BLOCKING
    assert checks_assumed([c]) == [{"name": c.name, "category": "integration", "why": MEASURED_BY_USER}]
    assert MEASURED_BY_USER in assumed_line([c]) and assumed_line([check("x", "a", "pass", "m")]) is None
    assert tally([c, check("x", "b", "pass", "m")]) == "2 pass, 0 warn, 0 FAIL, 1 assumed (on a measured figure)"


def test_a_failed_or_not_run_check_is_never_assumed():
    assert assumed(check("x", "a", "fail", "off by 1 mm"), "measured").status == "fail"
    assert assumed(switched_off(check("x", "a", "warn", "no figure"), "k"), "measured").status == "not_run"
    assert CheckResult("a", False, "m", assumed="measured").status == "fail"


def test_the_json_form_reads_back():
    c = assumed(check("integration", "n", "pass", "fine"), MEASURED_BY_USER)
    d = {"checks": [{"name": c.name, "ok": True, "severity": "pass", "measured": c.measured, "assumed": c.assumed}]}
    stamp_status(d, [c])
    assert d["checks"][0]["status"] == "assumed" and d["checks_assumed"][0]["why"] == MEASURED_BY_USER
    assert status_of({"ok": True, "severity": "pass", "assumed": "x", "measured": "m"}) == "assumed"
    assert status_of({"ok": False, "severity": "fail", "assumed": "x", "measured": "m"}) == "fail"
    assert status_of({"ok": True, "measured": "m"}) == "pass"                 # never set silently: no field, no status
    r = CheckResult("clearance", True, "fine", assumed="measured")
    assert r.status == "assumed" and from_verify(r).status == "assumed" and r.line().startswith("[ASSUMED]")


def test_defects_are_reported_where_they_are():
    from eitri.printexport import defect_places
    v = np.array([[0, 0, 0], [0, 0, 5], [50, 0, 0], [50, 0, 5]], dtype=float)
    places = defect_places(v, [(0, 1), (2, 3)])
    assert len(places) == 2 and {tuple(lo) for lo, _hi in places} == {(0, 0, 0), (50, 0, 0)}
    assert len(defect_places(v, [(0, 1), (1, 0)])) == 1
