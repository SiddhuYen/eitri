"""The structured check every non-geometry domain reports: a verdict with a severity, a category and the ids it is about.

CheckResult (eitri.model) is what verify() produces: name, ok, measured, expected. A Check adds the severity (pass | warn |
fail: a warning is ok but worth a look), the category (which checker produced it) and refs (the ids of the things it is about,
so an editor can highlight them). Build extensions return Checks too (eitri.extensions.Check is this class).

Status (pass | warn | fail | not_run | not_applicable) is derived from those. not_run: an input the check needs is missing, so
nothing was graded; it blocks an export and a change package. not_applicable: what the check is about provably does not exist in
the project (a cross-domain actuator check in a project with no mechanism file and no servo, motor or solenoid in its circuit);
it is ok, says why, and blocks nothing. A checker marks a check not applicable only on such proof, never for a missing input.
assumed: the check ran and passed, but on a figure the model file declares as measured by a person instead of one from a source
(a board's outline and holes measured with calipers): it does not block the free combined export, but blocks a change package
until a person waives it, like not_run. Only an explicit declaration in the model file makes a check assumed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .check_sources import CheckSource, source_tag
from .model import CheckResult


@dataclass
class Check(CheckResult):
    severity: str = "pass"                       # pass | warn | fail
    category: str = ""
    refs: list[str] = field(default_factory=list)      # ids of what the check is about (an editor highlights them)
    # not_run (inherited, keyword-only): the model inputs left unset (None) that switch this check off; empty when it ran
    # the sources of its limit (eitri.check_sources), given by the domain's rule table (rules.py) after the checks run
    source: list[CheckSource] = field(default_factory=list, kw_only=True)
    # rule (inherited): the rule-table id ("harness.wire-ampacity"); "" when no rule matched

    @property
    def status(self) -> str:
        """pass | warn | fail | not_run. A check switched off by an unset input is not_run: its severity stays "warn" and its text
        starts "not run: ", so a reader of the older form (severity + text) still sees a warning that says why. A check that
        passes on a declaration (Actuator(continuous=True): no travel to grade) stays a pass, though checks_not_run lists it.
        not_applicable: a passing check whose subject the project lacks (see not_applicable())."""
        if self.severity == "pass" and (self.not_applicable or str(self.measured).startswith("not applicable: ")):
            return "not_applicable"
        if self.severity == "fail":
            return "fail"
        if self.severity == "warn" and (self.not_run or str(self.measured).startswith("not run: ")):
            return "not_run"
        if self.assumed:
            return "assumed"
        return self.severity

    def line(self, numbers: dict | None = None) -> str:
        mark = {"not_applicable": "N/A", "assumed": "ASSUMED"}.get(self.status) or {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}[self.severity]
        exp = f" (expected {self.expected})" if self.expected else ""
        return f"[{mark}] {self.name}: {self.measured}{exp}{source_tag(self, numbers)}"


def check(category: str, name: str, severity: str, measured: str, expected: str = "", refs: Iterable[str] = (),
          not_run: Iterable[str] = (), source: Iterable[CheckSource] = (), kind: str = "rule") -> Check:
    return Check(name=name, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category=category,
                 refs=list(dict.fromkeys(refs)), not_run=list(not_run), source=list(source), kind=kind)


STATUSES = ("pass", "warn", "fail", "not_run", "not_applicable", "assumed")
BLOCKING = ("fail", "not_run")                            # the statuses that stop the combined export (and a change package)
PACKAGE_BLOCKING = ("fail", "not_run", "assumed")         # a change package also stops on an assumed figure (a person may waive it)
MEASURED_BY_USER = "board size measured by the user, not from a datasheet"


def status_of(c) -> str:
    """The status of a check object or of its JSON form (result.json; an older build has no "status": it is derived the
    same way, from the severity / ok, the not_run inputs and the "not run: " text)."""
    if not isinstance(c, dict):
        return c.status
    if c.get("status") in STATUSES:
        return c["status"]
    if c.get("ok", True) and c.get("severity", "pass") == "pass" and (
            c.get("not_applicable") or str(c.get("measured", "")).startswith("not applicable: ")):
        return "not_applicable"
    off = bool(c.get("not_run")) or str(c.get("measured", "")).startswith("not run: ")
    if c.get("assumed") and c.get("ok", True) and c.get("severity", "pass") != "fail" and not (off and c.get("severity") == "warn"):
        return "assumed"
    if "severity" in c:                                   # a domain Check: only a warning can be "not run" (see Check.status)
        return "not_run" if c["severity"] == "warn" and off else c["severity"]
    if not c.get("ok", True):                             # a verify() / CAD CheckResult
        return "fail"
    return "not_run" if off else "pass"


def stamp_status(d: dict, checks: Iterable) -> dict:
    """Write each check's status into a report's JSON form (asdict drops properties) and its checks_not_run list."""
    for cd, c in zip(d.get("checks") or [], checks):
        cd["status"] = c.status
    d["checks_not_run"] = checks_not_run(checks)
    d["checks_not_applicable"] = checks_not_applicable(checks)
    d["checks_assumed"] = checks_assumed(checks)
    return d


def assumed(c: CheckResult, why: str) -> CheckResult:
    """Mark a check that ran on a figure a person declared in the model file instead of a sourced one (why: what was declared, and by
    whom). It keeps its verdict (a fail still fails), prints [ASSUMED], is listed under checks_assumed, passes the combined export and
    blocks a change package until a person waives it. Only a checker reading an explicit declaration calls this."""
    c.assumed = str(why)[:300] or "a figure declared in the model file"
    return c


def checks_assumed(checks: Iterable[CheckResult]) -> list[dict]:
    """The checks that rest on a declared, unsourced figure: [{name, category, why}] (report JSON key "checks_assumed")."""
    return [{"name": c.name, "category": getattr(c, "category", "") or "verify", "why": c.assumed}
            for c in checks if c.status == "assumed"]


def assumed_line(checks: Iterable[CheckResult]) -> str | None:
    """One summary line naming every check that rests on a declared figure (None when there are none)."""
    a = [c for c in checks if c.status == "assumed"]
    if not a:
        return None
    return "Assumed (passes on a figure declared in the model file): " + "; ".join(f"{c.name} ({c.assumed})" for c in a)


def not_applicable(c: CheckResult, why: str) -> CheckResult:
    """Mark a check as not applicable: what it is about provably does not exist in the project (why says what is absent). It is
    ok (a pass to older readers), its text says "not applicable: <why>", the report lists it under checks_not_applicable, and it
    blocks nothing. Only for proof of absence: a missing input is not_run (switched_off), never this."""
    c.not_applicable = str(why)[:300] or "its subject is absent"
    c.not_run = []
    c.ok = True
    if hasattr(c, "severity"):
        c.severity = "pass"
    c.measured = "not applicable: " + c.not_applicable
    return c


def checks_not_applicable(checks: Iterable[CheckResult]) -> list[dict]:
    """The checks that did not apply: [{name, category, why}] (report JSON key "checks_not_applicable"), so an edit that makes a
    check not applicable (a file removed) is as visible as one that switches a check off."""
    return [{"name": c.name, "category": getattr(c, "category", "") or "verify", "why": c.not_applicable or c.measured}
            for c in checks if c.status == "not_applicable"]


def tally(checks: Iterable[CheckResult]) -> str:
    """"N pass, W warn, F FAIL" for a report's Checks line, plus ", K n/a" when some checks did not apply (they are not passes)."""
    checks = list(checks)
    sev = [getattr(c, "severity", "pass" if c.ok else "fail") for c in checks]
    n_fail, n_warn = sev.count("fail"), sev.count("warn")
    n_na = sum(1 for c in checks if c.status == "not_applicable")
    n_as = sum(1 for c in checks if c.status == "assumed")
    return (f"{len(checks) - n_fail - n_warn - n_na} pass, {n_warn} warn, {n_fail} FAIL" + (f", {n_na} n/a" if n_na else "")
            + (f", {n_as} assumed (on a measured figure)" if n_as else ""))


def not_applicable_line(checks: Iterable[CheckResult]) -> str | None:
    """One summary line naming every check that did not apply and why (None when there are none)."""
    na = [c for c in checks if c.status == "not_applicable"]
    if not na:
        return None
    return "Not applicable (its subject is absent): " + "; ".join(f"{c.name} ({c.not_applicable or c.measured})" for c in na)


def switched_off(c: CheckResult, *inputs: str) -> CheckResult:
    """Mark a check as not run because these model inputs are unset (None): its text says "not run: ..." and the build
    report lists it under checks_not_run, so an edit that switches a check off is visible (hosted escalates on it)."""
    c.not_run = list(dict.fromkeys(list(c.not_run) + list(inputs)))
    if not c.measured.startswith("not run: "):
        c.measured = "not run: " + c.measured
    return c


def checks_not_run(checks: Iterable[Check]) -> list[dict]:
    """The checks a model switched off by leaving an input unset: [{name, category, severity, inputs, refs, why}], for the
    build report (report JSON key "checks_not_run")."""
    return [{"name": c.name, "category": getattr(c, "category", "") or "verify",
             "severity": getattr(c, "severity", "pass" if c.ok else "fail"), "status": c.status, "inputs": list(c.not_run),
             "refs": list(getattr(c, "refs", []) or []), "why": c.measured} for c in checks if c.not_run or c.status == "not_run"]


def not_run_line(checks: Iterable[Check]) -> str | None:
    """One summary line naming every check not run and the input that switched it off (None when there are none)."""
    off = [c for c in checks if c.not_run or c.status == "not_run"]
    if not off:
        return None
    return "Not run (an input is unset): " + "; ".join(f"{c.name} ({', '.join(c.not_run) or 'an input'} not set)" for c in off)


def from_verify(r: CheckResult) -> Check:
    """A verify() result as a Check (category "verify")."""
    sev = "fail" if not r.ok else "warn" if r.status == "not_run" else "pass"      # a CheckResult that did not run is a warning
    return Check(name=r.name, ok=r.ok, measured=r.measured, expected=r.expected, severity=sev, category="verify",
                 source=list(r.source), kind=r.kind, rule=r.rule or "verify", not_run=list(r.not_run),
                 not_applicable=getattr(r, "not_applicable", ""), assumed=getattr(r, "assumed", ""))


def level(value: float, limit: float, warn_fraction: float = 0.8, above_is_bad: bool = True) -> str:
    """pass | warn | fail for a value against a limit: fail past the limit, warn within (1 - warn_fraction) of it."""
    if limit <= 0:
        return "fail" if (value > 0 if above_is_bad else value < 0) else "pass"
    if above_is_bad:
        return "fail" if value > limit else "warn" if value >= warn_fraction * limit else "pass"
    return "fail" if value < limit else "warn" if value <= limit / warn_fraction else "pass"
