"""Where each check's limit comes from: a structured source on every check result, the rule table each domain keeps, and the
text form (a short citation on the check line, the documents' URLs in a numbered list under the checks).

    CheckSource(text, url, rev, basis)   one source: what a check line names, the document's URL and revision / date
    basis                                document     a datasheet, standard or catalogue page the tables cite
                                         requirement  a number the person gave as a requirement ("design requirement")
                                         model        a part figure the model file gives (the designer's own datasheet value)
                                         rule         a definition with no external figure (connectivity, topology, a convention)
                                         tool         a deterministic tool's verdict (the arduino-cli compiler)
                                         assumption   a figure with no source (ASSUMPTION / ESTIMATE): listed, never hidden
    Rule(id, category, name, ...)        one entry of a domain's rule table (eitri/<domain>/rules.py): which checks it covers
                                         (category + a regex on the check's name), its sources, and a resolver for sources that
                                         depend on the model (a connector family's datasheet, an actuator's spec)
    attach(checks, rules, model)         gives every check its rule id, kind and sources (an unmatched check keeps none: the
                                         registry test fails on it)
    check_lines(checks)                  the report's check lines with their sources, then "Sources:" and the numbered URLs

Every check is a deterministic rule (kind "rule"); a check that were a model's judgement would say kind "judgement".
No heavy imports here: the plugin server imports this path (see EITRI_LAZY_IMPORT).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

BASES = ("document", "requirement", "model", "rule", "tool", "assumption")
KINDS = ("rule", "judgement")


@dataclass(frozen=True)
class CheckSource:
    text: str                      # the short citation a check line shows
    url: str = ""
    rev: str = ""                  # the document's revision / date, where the table gives one
    basis: str = "document"        # one of BASES

    def __post_init__(self) -> None:
        if self.basis not in BASES:
            raise ValueError(f"source basis must be one of {BASES}, got {self.basis!r}")


def doc(text: str, url: str = "", rev: str = "") -> CheckSource:
    return CheckSource(text, url, rev, "document")


def requirement(what: str = "") -> CheckSource:
    return CheckSource("design requirement" + (f" ({what})" if what else ""), basis="requirement")


def model_file(what: str = "") -> CheckSource:
    return CheckSource("the model file" + (f" ({what})" if what else ""), basis="model")


def rule(what: str) -> CheckSource:
    return CheckSource(f"eitri rule: {what}", basis="rule")


def tool(what: str) -> CheckSource:
    return CheckSource(what, basis="tool")


def assumption(what: str) -> CheckSource:
    return CheckSource(f"ASSUMPTION: {what}", basis="assumption")


VERIFY = requirement("verify()")          # a check the model file states in verify(): its own requirement


def verify_sources() -> list[CheckSource]:
    return [VERIFY]


@dataclass(frozen=True)
class Rule:
    id: str                                      # "<domain>.<name>", stable: a website or change package keys on it
    category: str                                # a regex the check's whole category must match (usually the name itself)
    name: str                                    # a regex the check's name must match from its start
    source: tuple[CheckSource, ...]              # every source the rule may cite (the registry test needs one at least)
    resolve: Callable[[Any, Any], list[CheckSource]] | None = None    # (check, model) -> the sources for this check
    kind: str = "rule"

    def matches(self, category: str, name: str) -> bool:
        return re.fullmatch(self.category, category) is not None and re.match(self.name, name) is not None


def find_rule(rules: Iterable[Rule], category: str, name: str) -> Rule | None:
    return next((r for r in rules if r.matches(category, name)), None)


def attach(checks: Iterable[Any], rules: Iterable[Rule], model: Any = None) -> None:
    """Give each check its rule id, kind and sources from the domain's rule table (a check that already has sources, or a
    verify() result, keeps its own). A resolver that raises falls back to the rule's listed sources."""
    rules = list(rules)
    for c in checks:
        if getattr(c, "source", None):
            continue
        if getattr(c, "category", "") == "verify":
            c.source, c.rule = verify_sources(), "verify"
            continue
        r = find_rule(rules, getattr(c, "category", ""), c.name)
        if r is None:
            continue
        c.rule, c.kind = r.id, r.kind
        got: list[CheckSource] = []
        if r.resolve is not None:
            try:
                got = list(r.resolve(c, model) or [])
            except Exception:
                got = []
        c.source = _dedupe(got or list(r.source))


def _dedupe(srcs: Iterable[CheckSource]) -> list[CheckSource]:
    return list(dict.fromkeys(srcs))


INLINE = "[source:"            # a check whose text already names its source (electronics, the board supply checks) shows only the numbers


def source_tag(c: Any, numbers: dict | None = None) -> str:
    """The text a check line ends with: " [source: A [1]; B]", or only " [1]" when the check's own text already names its source."""
    srcs = list(getattr(c, "source", None) or [])
    if not srcs:
        return ""
    refs = [numbers[_key(s)] if numbers is not None and s.url else None for s in srcs]
    if INLINE in f"{c.measured} {c.expected}":
        nums = [str(n) for n in dict.fromkeys(refs) if n]
        return f" [{', '.join(nums)}]" if nums else ""
    return " [source: " + "; ".join(s.text + (f" [{n}]" if n else "") for s, n in zip(srcs, refs)) + "]"


def _key(s: CheckSource) -> tuple:
    return (s.url, s.rev or s.text)


def check_lines(checks: Iterable[Any]) -> list[str]:
    """Each check's line with its source, then the documents behind them: "Sources:" and one "[n] document: URL" per URL."""
    checks = list(checks)
    numbers: dict[tuple, int] = {}
    notes: list[CheckSource] = []
    for c in checks:
        for s in getattr(c, "source", None) or []:
            if s.url and _key(s) not in numbers:
                numbers[_key(s)] = len(numbers) + 1
                notes.append(s)
    lines = [c.line(numbers) for c in checks]
    if notes:
        lines.append("Sources:")
        lines += [f"  [{i}] {s.rev or s.text}: {s.url}" for i, s in enumerate(notes, 1)]
    return lines


def unsourced(c: Any) -> bool:
    """True when a check has no source, or one of its figures is an ASSUMPTION."""
    srcs = getattr(c, "source", None) or []
    return not srcs or any(s.basis == "assumption" for s in srcs)


def source_kwargs(d: dict) -> dict:
    """A check's source and kind from its JSON form (result.json), for rebuilding it: {} when the JSON has none (an older build)."""
    keys = ("text", "url", "rev", "basis")
    srcs = [CheckSource(**{k: v for k, v in s.items() if k in keys}) for s in (d.get("source") or []) if isinstance(s, dict) and s.get("text")]
    return {"source": srcs, "kind": d.get("kind", "rule")} if srcs else {}
