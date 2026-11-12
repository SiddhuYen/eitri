"""Every build's checks as one row shape, for the change package's before/after table and its refusal rule.

    {"name", "status": pass | warn | fail | not_run, "category", "measured", "expected", "source", "inputs_unset"}

Non-CAD domains report Checks (eitri.checks: severity, category, not_run). A check switched off by an unset input is `not_run`
(its text starts "not run:"). CAD reports verify() results (ok or not) and geometry findings: an invalid solid, a negative
volume or an interference between parts is a fail; the other findings (a thin wall, a knife edge, a free edge, disjoint solids)
are warnings a person reads. A check's source is the "[source: ...]" its text cites; a check that cites none says where its
inputs come from instead (the model file, or eitri's own method).
"""
from __future__ import annotations

import re

_SRC = re.compile(r"\[source:\s*([^\]]+)\]")
_FAIL_ISSUE = ("INVALID", "NEGATIVE volume", "INTERFERENCE", "no faces at all")


def _source(text: str, category: str, kind: str) -> str:
    found = list(dict.fromkeys(m.strip() for m in _SRC.findall(text or "")))
    if found:
        return "; ".join(found)
    if category == "verify":
        return "the design's own verify() requirement (model file)"
    if kind == "cad":
        return "eitri geometry report (eitri/report.py): measured from the solid"
    return f"eitri {kind} check `{category or 'check'}` (eitri/{kind}/checks.py); figures from the model file, no document cited"


def normalise(result: dict) -> list[dict]:
    """The rows of one build's result.json (as written by eitri._runner)."""
    rep = result.get("report") or {}
    kind = result.get("kind") or "cad"
    rows: list[dict] = []
    for c in rep.get("checks") or []:
        text = f"{c.get('measured', '')} {c.get('expected', '')}"
        if "severity" in c:
            status = c["severity"]
        else:
            status = "pass" if c.get("ok") else "fail"
        unset = list(c.get("not_run") or [])
        if unset or str(c.get("measured", "")).startswith("not run:"):
            status = "fail" if status == "fail" else "not_run"
        cat = c.get("category") or ("verify" if kind == "cad" else "")
        rows.append({"name": str(c.get("name", "check")), "status": status, "category": cat,
                     "measured": str(c.get("measured", "")), "expected": str(c.get("expected", "")),
                     "source": _source(text, cat, kind), "inputs_unset": unset})
    if kind == "cad":
        for p in rep.get("parts") or []:
            name = p.get("name", "part")
            bad = [i for i in p.get("issues") or [] if any(k in str(i) for k in _FAIL_ISSUE)]
            rows.append({"name": f"geometry valid: {name}", "status": "pass" if p.get("valid", True) and not bad else "fail",
                         "category": "geometry", "measured": "valid solid" if p.get("valid", True) and not bad else "; ".join(bad) or "INVALID",
                         "expected": "a valid, closed solid", "source": _source("", "geometry", kind), "inputs_unset": []})
            for i in p.get("issues") or []:
                if i in bad:
                    continue
                rows.append({"name": f"geometry: {name}: {_issue_key(str(i))}", "status": "warn", "category": "geometry",
                             "measured": str(i), "expected": "", "source": _source("", "geometry", kind), "inputs_unset": []})
        for i in rep.get("issues") or []:
            fail = any(k in str(i) for k in _FAIL_ISSUE)
            rows.append({"name": f"geometry: {_issue_key(str(i))}", "status": "fail" if fail else "warn", "category": "geometry",
                         "measured": str(i), "expected": "no interference" if fail else "",
                         "source": _source("", "geometry", kind), "inputs_unset": []})
    return rows


def _issue_key(text: str) -> str:
    """A stable name for a geometry finding (its numbers removed, so the before/after table pairs the same finding)."""
    t = re.sub(r"\(?[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?\)?", "#", text.split(" at ")[0])
    return re.sub(r"\s+", " ", t).strip()[:80]


def counts(rows: list[dict]) -> dict:
    out = {"pass": 0, "warn": 0, "fail": 0, "not_run": 0}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    out["total"] = len(rows)
    return out
