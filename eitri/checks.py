"""The structured check every non-geometry domain reports: a verdict with a severity, a category and the ids it is about.

CheckResult (eitri.model) is what verify() produces: name, ok, measured, expected. A Check adds the severity (pass | warn |
fail: a warning is ok but worth a look), the category (which checker produced it) and refs (the ids of the things it is about,
so an editor can highlight them). Build extensions return Checks too (eitri.extensions.Check is this class).
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
    not_run: list[str] = field(default_factory=list)   # the model inputs left unset (None) that switch this check off; empty when it ran
    # the sources of its limit (eitri.check_sources), given by the domain's rule table (rules.py) after the checks run
    source: list[CheckSource] = field(default_factory=list, kw_only=True)
    rule: str = field(default="", kw_only=True)        # the rule-table id ("harness.wire-ampacity"); "" when no rule matched

    def line(self, numbers: dict | None = None) -> str:
        mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}[self.severity]
        exp = f" (expected {self.expected})" if self.expected else ""
        return f"[{mark}] {self.name}: {self.measured}{exp}{source_tag(self, numbers)}"


def check(category: str, name: str, severity: str, measured: str, expected: str = "", refs: Iterable[str] = (),
          not_run: Iterable[str] = (), source: Iterable[CheckSource] = (), kind: str = "rule") -> Check:
    return Check(name=name, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category=category,
                 refs=list(dict.fromkeys(refs)), not_run=list(not_run), source=list(source), kind=kind)


def switched_off(c: Check, *inputs: str) -> Check:
    """Mark a check as not run because these model inputs are unset (None): its text says "not run: ..." and the build
    report lists it under checks_not_run, so an edit that switches a check off is visible (hosted escalates on it)."""
    c.not_run = list(dict.fromkeys(list(c.not_run) + list(inputs)))
    if not c.measured.startswith("not run: "):
        c.measured = "not run: " + c.measured
    return c


def checks_not_run(checks: Iterable[Check]) -> list[dict]:
    """The checks a model switched off by leaving an input unset: [{name, category, severity, inputs, refs, why}], for the
    build report (report JSON key "checks_not_run")."""
    return [{"name": c.name, "category": c.category, "severity": c.severity, "inputs": list(c.not_run), "refs": list(c.refs),
             "why": c.measured} for c in checks if getattr(c, "not_run", None)]


def not_run_line(checks: Iterable[Check]) -> str | None:
    """One summary line naming every check not run and the input that switched it off (None when there are none)."""
    off = [c for c in checks if getattr(c, "not_run", None)]
    if not off:
        return None
    return "Not run (an input is unset): " + "; ".join(f"{c.name} ({', '.join(c.not_run)} not set)" for c in off)


def from_verify(r: CheckResult) -> Check:
    """A verify() result as a Check (category "verify")."""
    return Check(name=r.name, ok=r.ok, measured=r.measured, expected=r.expected, severity="pass" if r.ok else "fail", category="verify",
                 source=list(r.source), kind=r.kind, rule="verify")


def level(value: float, limit: float, warn_fraction: float = 0.8, above_is_bad: bool = True) -> str:
    """pass | warn | fail for a value against a limit: fail past the limit, warn within (1 - warn_fraction) of it."""
    if limit <= 0:
        return "fail" if (value > 0 if above_is_bad else value < 0) else "pass"
    if above_is_bad:
        return "fail" if value > limit else "warn" if value >= warn_fraction * limit else "pass"
    return "fail" if value < limit else "warn" if value <= limit / warn_fraction else "pass"
