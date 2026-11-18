"""Every build's checks as one row shape, for the change package's before/after table and its refusal rule.

    {"name", "status": pass | warn | fail | not_run, "category", "measured", "expected", "source", "inputs_unset"}

Non-CAD domains report Checks (eitri.checks: severity, category, not_run). A check switched off by an unset input is `not_run`
(its text starts "not run:"). CAD reports verify() results (ok or not) and geometry findings: an invalid solid, a negative
volume or an interference between parts is a fail; the other findings (a thin wall, a knife edge, a free edge, disjoint solids)
are warnings a person reads. A check's source is its structured source (eitri.check_sources: every check in every domain carries one from its rule table,
with the document's URL and revision): the row's "source" is their text joined, "sources" the records, "rule" the rule id. A build
from before 0.8.0 has none: then the "[source: ...]" its text cites, or where its inputs come from (the model file, or eitri's own
method). Status is the check's own (eitri.checks.status_of: pass | warn | fail | not_run).
"""
from __future__ import annotations

import re

from ..checks import status_of

_SRC = re.compile(r"\[source:\s*([^\]]+)\]")
_FAIL_ISSUE = ("INVALID", "NEGATIVE volume", "INTERFERENCE", "no faces at all")


def _structured(c: dict) -> list[dict]:
    keys = ("text", "url", "rev", "basis")
    return [{k: str(x.get(k) or "") for k in keys} for x in (c.get("source") or []) if isinstance(x, dict) and x.get("text")]


def source_text(sources: list[dict]) -> str:
    """One cell for a source column: each source's text, its revision when the text doesn't already carry it, its URL."""
    out = []
    for x in sources:
        t = x["text"]
        if x.get("rev") and x["rev"] not in t:
            t += f" ({x['rev']})"
        if x.get("url"):
            t += f" <{x['url']}>"
        out.append(t)
    return "; ".join(out)


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
        status = status_of(c)
        unset = list(c.get("not_run") or [])
        cat = c.get("category") or ("verify" if kind == "cad" else "")
        srcs = _structured(c)
        rows.append({"name": str(c.get("name", "check")), "status": status, "category": cat,
                     "measured": str(c.get("measured", "")), "expected": str(c.get("expected", "")),
                     "source": source_text(srcs) if srcs else _source(text, cat, kind), "sources": srcs,
                     "rule": str(c.get("rule") or ""), "inputs_unset": unset, "refs": [str(x) for x in c.get("refs") or []]})
    if kind == "cad":
        for p in rep.get("parts") or []:
            name = p.get("name", "part")
            bad = [i for i in p.get("issues") or [] if any(k in str(i) for k in _FAIL_ISSUE)]
            rows.append({"name": f"geometry valid: {name}", "status": "pass" if p.get("valid", True) and not bad else "fail",
                         "category": "geometry", "measured": "valid solid" if p.get("valid", True) and not bad else "; ".join(bad) or "INVALID",
                         "expected": "a valid, closed solid", "source": _source("", "geometry", kind), "sources": [], "rule": "", "inputs_unset": []})
            for i in p.get("issues") or []:
                if i in bad:
                    continue
                rows.append({"name": f"geometry: {name}: {_issue_key(str(i))}", "status": "warn", "category": "geometry",
                             "measured": str(i), "expected": "", "source": _source("", "geometry", kind), "sources": [], "rule": "", "inputs_unset": []})
        for i in rep.get("issues") or []:
            fail = any(k in str(i) for k in _FAIL_ISSUE)
            rows.append({"name": f"geometry: {_issue_key(str(i))}", "status": "fail" if fail else "warn", "category": "geometry",
                         "measured": str(i), "expected": "no interference" if fail else "",
                         "source": _source("", "geometry", kind), "sources": [], "rule": "", "inputs_unset": []})
    return rows


def _issue_key(text: str) -> str:
    """A stable name for a geometry finding (its numbers removed, so the before/after table pairs the same finding)."""
    t = re.sub(r"\(?[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?\)?", "#", text.split(" at ")[0])
    return re.sub(r"\s+", " ", t).strip()[:80]


def is_finding(row: dict) -> bool:
    """A CAD geometry finding (a thin wall, a free edge): a warning about the solid, not a check. It goes away when the design
    is fixed, so its disappearing is never a lost check. "geometry valid: <part>" is a check."""
    return row.get("category") == "geometry" and row["name"].startswith("geometry: ")


def _ran(row: dict) -> bool:
    return row["status"] != "not_run" and not row.get("inputs_unset")


def _stem(name: str) -> str:
    """The name without a trailing "(...)": "torque joint tilt (SG90)" -> "torque joint tilt" (the actuator in brackets changes
    when the actuator does)."""
    return re.sub(r"\s*\([^()]*\)\s*$", "", " ".join(name.lower().split()))


def _same_check(x: dict, y: dict) -> str | None:
    """Why y is x under a new name, or None: the same rule of the same domain about the same things (rule id and refs), or the
    same name once a trailing "(...)" is dropped, in the same category."""
    if x.get("rule") and x.get("rule") == y.get("rule") and x.get("rule") != "verify" and x.get("refs") and set(x["refs"]) == set(y.get("refs") or []):
        return f"same rule {x['rule']} about {', '.join(x['refs'])}"
    if x.get("category") == y.get("category") and _stem(x["name"]) == _stem(y["name"]) and _stem(x["name"]) != " ".join(x["name"].lower().split()):
        return f"same check ({_stem(x['name'])}), its bracket changed"
    return None


def lost_checks(before: list[dict] | None, after: list[dict]) -> tuple[list[dict], list[dict]]:
    """The checks the base ran that the result does not, and the renames that explain the rest.

    lost: [{kind: "removed" | "switched_off", check, from, to, inputs, renamed_from}]
      removed       a check of the base with no counterpart in the result (dropped, a verify() line deleted, or renamed in a
                    way eitri cannot pair: a harness wire id changed); "check" is its name on the base
      switched_off  it ran on the base (pass, warn or fail, no input unset) and on the result it is "not run" or passes on a
                    declaration with an input unset (Actuator(travel_deg=None), continuous=True, a rating set to None); "check"
                    is its name on the result
    renamed: [{from, to, why}]: a removed name paired one to one with an added name (same rule about the same ids, or the same
      name with a different bracket); those are compared as one check, so a rename that also switches it off is still caught.
    Geometry findings are left out (is_finding). A new design (no base) loses nothing."""
    if before is None:
        return [], []
    bmap = {r["name"]: r for r in before if not is_finding(r)}
    amap = {r["name"]: r for r in after if not is_finding(r)}
    gone = [n for n in bmap if n not in amap]
    new = [n for n in amap if n not in bmap]
    cands = {g: [n for n in new if _same_check(bmap[g], amap[n])] for g in gone}
    claimed: dict[str, list[str]] = {}
    for g, ns in cands.items():
        for n in ns:
            claimed.setdefault(n, []).append(g)
    pairs = {g: ns[0] for g, ns in cands.items() if len(ns) == 1 and len(claimed[ns[0]]) == 1}
    renamed = [{"from": g, "to": n, "why": _same_check(bmap[g], amap[n])} for g, n in pairs.items()]
    lost: list[dict] = []
    for g in gone:
        if g not in pairs:
            x = bmap[g]
            lost.append({"kind": "removed", "check": g, "from": _brief(x), "to": None, "inputs": [], "renamed_from": None})
    for name, x in bmap.items():
        to = name if name in amap else pairs.get(name)
        if to is None:
            continue
        y = amap[to]
        if _ran(x) and not _ran(y):
            lost.append({"kind": "switched_off", "check": to, "from": _brief(x), "to": _brief(y), "inputs": list(y.get("inputs_unset") or []),
                         "renamed_from": name if to != name else None})
    return lost, renamed


def _brief(r: dict) -> dict:
    return {k: r.get(k) for k in ("name", "status", "measured", "expected", "inputs_unset")}


def counts(rows: list[dict]) -> dict:
    out = {"pass": 0, "warn": 0, "fail": 0, "not_run": 0}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    out["total"] = len(rows)
    return out
