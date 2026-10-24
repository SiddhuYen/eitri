"""The structured check every non-geometry domain reports: a verdict with a severity, a category and the ids it is about.

CheckResult (eitri.model) is what verify() produces: name, ok, measured, expected. A Check adds the severity (pass | warn |
fail: a warning is ok but worth a look), the category (which checker produced it) and refs (the ids of the things it is about,
so an editor can highlight them). Build extensions return Checks too (eitri.extensions.Check is this class).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .model import CheckResult


@dataclass
class Check(CheckResult):
    severity: str = "pass"                       # pass | warn | fail
    category: str = ""
    refs: list[str] = field(default_factory=list)      # ids of what the check is about (an editor highlights them)

    def line(self) -> str:
        mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}[self.severity]
        exp = f" (expected {self.expected})" if self.expected else ""
        return f"[{mark}] {self.name}: {self.measured}{exp}"


def check(category: str, name: str, severity: str, measured: str, expected: str = "", refs: Iterable[str] = ()) -> Check:
    return Check(name=name, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category=category,
                 refs=list(dict.fromkeys(refs)))


def from_verify(r: CheckResult) -> Check:
    """A verify() result as a Check (category "verify")."""
    return Check(name=r.name, ok=r.ok, measured=r.measured, expected=r.expected, severity="pass" if r.ok else "fail", category="verify")


def level(value: float, limit: float, warn_fraction: float = 0.8, above_is_bad: bool = True) -> str:
    """pass | warn | fail for a value against a limit: fail past the limit, warn within (1 - warn_fraction) of it."""
    if limit <= 0:
        return "fail" if (value > 0 if above_is_bad else value < 0) else "pass"
    if above_is_bad:
        return "fail" if value > limit else "warn" if value >= warn_fraction * limit else "pass"
    return "fail" if value < limit else "warn" if value <= limit / warn_fraction else "pass"
