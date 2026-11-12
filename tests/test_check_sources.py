"""Every check in every domain has a source (eitri.check_sources): the rule tables, every place a check is made, and what
the demo builds report, in text and JSON. A new check that no rule covers fails here."""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

from eitri.check_sources import BASES, KINDS, CheckSource, Rule, attach, check_lines, find_rule, source_kwargs

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ("harness", "mechanism", "pneumatics", "firmware", "electronics")


def _rules(domain: str) -> list[Rule]:
    return importlib.import_module(f"eitri.{domain}.rules").RULES


REGISTRY = [(d, r) for d in DOMAINS for r in _rules(d)]


# ---------------------------------------------------------------------------- the registry: every rule has a source


@pytest.mark.parametrize("domain,r", REGISTRY, ids=[r.id for _d, r in REGISTRY])
def test_every_registered_rule_has_a_source(domain, r):
    assert r.id.startswith(domain + "."), r.id
    assert r.kind in KINDS
    assert r.source, f"{r.id} lists no source"
    for s in r.source:
        assert isinstance(s, CheckSource) and s.text.strip(), r.id
        assert s.basis in BASES
        assert not s.url or s.url.startswith("https://"), (r.id, s.url)
    re.compile(r.name)


def test_rule_ids_are_unique():
    ids = [r.id for _d, r in REGISTRY]
    assert len(ids) == len(set(ids))


def test_extension_rules_have_sources():
    from eitri import extensions
    assert all(s.text and s.basis == "rule" for s in extensions._RULES.values())


# ---------------------------------------------------------------------------- every check made in the code matches a rule

_MAKERS = {"_check", "check", "_c"}


def _texts(node, assigns: dict) -> list[str]:
    """The texts a name or category argument can have: a literal, an f-string (placeholders as "X"), a local variable assigned one
    of those, or either side of a conditional."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        out = [""]
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts = [v.value]
            else:                                         # a placeholder: what a local variable holds, else any text
                parts = _texts(v.value, assigns) if isinstance(v.value, ast.Name) else []
                parts = parts or ["X"]
            out = [a + b for a in out for b in parts]
        return out
    if isinstance(node, ast.IfExp):
        return _texts(node.body, assigns) + _texts(node.orelse, assigns)
    if isinstance(node, ast.Name) and node.id in assigns:
        return [t for v in assigns[node.id] for t in _texts(v, assigns)]
    return []


def _sites(domain: str):
    path = ROOT / "eitri" / domain / "checks.py"
    tree = ast.parse(path.read_text())
    out = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        assigns: dict[str, list] = {}
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                assigns.setdefault(n.targets[0].id, []).append(n.value)
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _MAKERS and len(n.args) >= 3:
                if fn.name in _MAKERS:                       # the helpers themselves
                    continue
                out.append((f"{domain}:{n.lineno}", n.args[0], n.args[1], assigns))
    return out


SITES = [s for d in DOMAINS for s in _sites(d)]


def test_the_scan_finds_the_checks():
    assert len(SITES) > 150


@pytest.mark.parametrize("site,cat,name,assigns", SITES, ids=[s[0] for s in SITES])
def test_every_check_site_matches_a_rule(site, cat, name, assigns):
    domain = site.split(":")[0]
    cats, names = _texts(cat, assigns), _texts(name, assigns)
    assert cats and names, f"{site}: give the check a literal or f-string category and name (or a local variable set to one)"
    for c in cats:
        for n in names:
            assert find_rule(_rules(domain), c, n) is not None, f"{site}: no rule in eitri/{domain}/rules.py covers ({c!r}, {n!r})"


# ---------------------------------------------------------------------------- what a build reports

DEMOS = {"harness": "quad-power/quad_power.py", "mechanism": "pan-tilt/pan_tilt.py", "pneumatics": "air-clamp/air_clamp.py",
         "firmware": "servo-sweep/servo_sweep.py", "electronics": "uno-arm-circuit/uno_arm_circuit.py"}


def _report(domain: str):
    from eitri.model import Checker, Params
    path = ROOT / "eitri" / "demos" / DEMOS[domain]
    spec = importlib.util.spec_from_file_location(f"_demo_{domain}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = Params(getattr(mod, "PARAMS", {}))
    built = mod.build(p)
    ck = Checker()
    if hasattr(mod, "verify"):
        mod.verify(built, p, ck)
    rm = importlib.import_module(f"eitri.{domain}.report")
    return rm, rm.analyze(built, ck)


@pytest.mark.parametrize("domain", DOMAINS)
def test_every_demo_check_has_a_source(domain):
    rm, rep = _report(domain)
    assert rep.checks
    for c in rep.checks:
        assert c.source, f"{domain}: {c.name} has no source"
        assert c.kind in KINDS and c.rule, c.name
    text = rm.summarize(rep)
    lines = [ln for ln in text.splitlines() if re.match(r"\[(PASS|WARN|FAIL)\] ", ln)]
    assert len(lines) == len(rep.checks)
    for ln in lines:                                     # every check line names its source (inline or as the tag)
        assert "[source: " in ln, ln
    d = json.loads(json.dumps(rep.to_dict()))
    for c in d["checks"]:
        assert c["source"] and all(s["text"] and s["basis"] in BASES for s in c["source"]), c["name"]
        assert c["kind"] in KINDS and c["rule"]


def test_numbered_sources_list_urls_once():
    from eitri.checks import check
    a = CheckSource("Doc A", "https://example.org/a.pdf", "Doc A rev 2")
    b = CheckSource("Doc B")
    cs = [check("x", "one", "pass", "1", source=[a]), check("x", "two", "fail", "2", "<= 1", source=[a, b]),
          check("x", "three", "warn", "3 [source: Doc A]", source=[a])]
    assert check_lines(cs) == [
        "[PASS] one: 1 [source: Doc A [1]]",
        "[FAIL] two: 2 (expected <= 1) [source: Doc A [1]; Doc B]",
        "[WARN] three: 3 [source: Doc A] [1]",
        "Sources:",
        "  [1] Doc A rev 2: https://example.org/a.pdf",
    ]


def test_verify_results_are_design_requirements():
    from eitri.checks import from_verify
    from eitri.model import Checker
    ck = Checker()
    ck.at_most("span (mm)", 12, 10)
    r = ck.results[0]
    assert r.source[0].basis == "requirement" and r.source[0].text.startswith("design requirement")
    assert r.line() == "[FAIL] span (mm): 12 (expected <= 10) [source: design requirement (verify())]"
    c = from_verify(r)
    assert c.source == r.source and c.rule == "verify"


def test_unmatched_check_gets_no_source():
    from eitri.checks import check
    c = check("nowhere", "a check no rule knows", "pass", "")
    attach([c], _rules("harness"))
    assert c.source == [] and c.rule == ""


def test_sources_survive_the_json_round_trip():
    from eitri.model import CheckResult
    s = CheckSource("Doc A", "https://example.org/a.pdf", "rev 2", "document")
    d = json.loads(json.dumps({"name": "n", "ok": True, "measured": "m", "expected": "", "source": [s.__dict__], "kind": "rule"}))
    r = CheckResult(d["name"], d["ok"], d["measured"], d["expected"], **source_kwargs(d))
    assert r.source == [s]
    assert CheckResult("n", True, "m", **source_kwargs({"name": "n"})).source[0].basis == "requirement"


def test_extension_checks_without_a_source_say_so(tmp_path):
    from eitri import extensions
    from eitri.checks import check

    def ext(ctx):
        return [check("extension", "widgets", "pass", "2")]

    extensions.register("srcless", ext)
    try:
        ctx = extensions.BuildContext(tmp_path / "m.py", tmp_path, "cad", None, {}, None, None, {})
        checks, _data, _g = extensions.run(ctx)
    finally:
        extensions._REGISTRY.pop("srcless", None)
    got = next(c for c in checks if c.name == "widgets")
    assert got.source[0].basis == "assumption" and "srcless gives no source" in got.source[0].text


def test_an_assumption_named_in_electronics_text_is_structured():
    from eitri.checks import check
    c = check("i2c", "I2C pull-ups SDA (Standard-mode, 100 kHz)", "pass",
              "4.7 kΩ; Cb = 60 pF estimated: 1 x 10 pF per device pin (UM10204 Ci max) + 50 pF of wiring (ASSUMPTION: no source; give capacitance_pF) "
              "[source: NXP UM10204 Rev. 7.0, fetched 2026-11-05: Rp(min)]")
    attach([c], _rules("electronics"))
    assert [s.basis for s in c.source] == ["document", "assumption"]
    assert c.source[0].url.startswith("https://www.nxp.com/") and c.source[1].text == "ASSUMPTION: 50 pF of wiring: no source"
