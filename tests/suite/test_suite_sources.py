"""Every cross-domain check of eitri_suite carries a rule id, a kind and structured sources (core 0.8.0 eitri.check_sources), so no
check shows "extension suite gives no source"; a check that did not run has the first-class not-run status.

Two walks: a static scan of every check name the suite's modules can make (each must match a rule of eitri_suite.rules), and a
runtime walk of every check the suite returns for every model file of the demos and the regression fixtures."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from eitri.check_sources import BASES, KINDS
from eitri.domains import kind_of_source
from eitri.model import load_model
from eitri_suite import projectchecks, rules

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "eitri_suite"
# module -> how its check constructor is called: ("arg", i) = the category is positional arg i; a string = a fixed category
MODULES = {"assembly.py": ("arg", 0), "crossdomain.py": ("arg", 0), "coupling.py": ("arg", 0), "kinematics.py": "coupling",
           "electronics.py": "integration", "power.py": "integration", "stability.py": "integration"}
CONSTRUCTORS = {"_check", "not_run", "HCheck"}


def _literal_prefix(node) -> str | None:
    """A string or f-string's text up to its first placeholder."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = ""
        for v in node.values:
            if isinstance(v, ast.Constant):
                out += str(v.value)
            else:
                break
        return out
    return None


def _names():
    found = []
    for mod, how in MODULES.items():
        tree = ast.parse((SUITE / mod).read_text(encoding="utf-8"))
        consts = {t.id: _literal_prefix(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name) and _literal_prefix(n.value)}
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in CONSTRUCTORS):
                continue
            kw = {k.arg: k.value for k in n.keywords}
            if n.func.id == "HCheck":
                name, cat = kw.get("name"), kw.get("category")
                cat = _literal_prefix(cat) if cat is not None else None
            elif isinstance(how, tuple):
                if len(n.args) < 2:
                    continue
                cat, name = _literal_prefix(n.args[0]), n.args[1]
            else:
                cat = how
                if mod in ("power.py", "stability.py"):
                    name = kw.get("name") or (n.args[0] if n.func.id == "_check" and mod == "power.py" and n.args else None)
                    name = name if name is not None else ast.Name("NAME")
                else:
                    name = n.args[0] if n.args else kw.get("name")
            if isinstance(name, ast.Name):
                text = consts.get(name.id)
            else:
                text = _literal_prefix(name) if name is not None else None
            if not text or not cat:
                continue                                    # a name built at runtime: the runtime walk below covers it
            found.append((mod, cat, text))
    return sorted(set(found))


NAMES = _names()


def test_the_scan_finds_the_suite_checks():
    assert len(NAMES) > 40
    assert ("stability.py", "integration", "stability: the base does not tip over") in NAMES


@pytest.mark.parametrize("mod,category,name", NAMES, ids=[f"{m}:{n[:40]}" for m, _c, n in NAMES])
def test_every_check_name_has_a_rule(mod, category, name):
    r = rules.rule_for(category, name)
    assert r is not None, f"{mod}: no eitri_suite.rules rule covers {category!r} {name!r}"
    assert r.source and all(s.basis in BASES for s in r.source) and r.kind in KINDS


FIXTURES = HERE = Path(__file__).resolve().parent
FILES = sorted([p for d in (SUITE / "demos").iterdir() if d.is_dir() for p in d.glob("*.py")]
               + list((HERE / "fixtures").glob("*/*.py")))


@pytest.mark.parametrize("path", FILES, ids=[f"{p.parent.name}/{p.name}" for p in FILES])
def test_every_check_the_suite_returns_has_a_source(path):
    lm = load_model(path)
    kind = kind_of_source(path.read_text(encoding="utf-8"))
    checks, _data = projectchecks.extras(kind, lm.module, lm.build(), path)
    for c in checks:
        srcs = list(c.source or [])
        assert srcs, (c.name, "no source")
        assert not any("gives no source" in s.text for s in srcs), c.name
        assert all(s.basis in BASES and s.text for s in srcs), (c.name, srcs)
        assert c.kind in KINDS and c.rule.startswith("suite."), (c.name, c.rule)
        if c.status == "not_run":
            assert c.not_run, (c.name, "a not-run check names what it is missing")
        if "[source:" in f"{c.measured} {c.expected}":
            assert any(s.basis in ("document", "model", "assumption") for s in srcs), (c.name, "its cited document is structured")


def test_a_not_run_check_is_first_class(tmp_path):
    import shutil
    d = tmp_path / "uno"
    shutil.copytree(SUITE / "demos" / "uno-arm", d, ignore=shutil.ignore_patterns("__pycache__"))
    (d / "mech.py").write_text((d / "mech.py").read_text().replace('    base_fixing="bolted",', ""), encoding="utf-8")
    lm = load_model(d / "mech.py")
    checks, _ = projectchecks.extras("mechanism", lm.module, lm.build(), d / "mech.py")
    c = next(c for c in checks if c.name.startswith("stability:"))
    assert c.status == "not_run" and c.not_run and c.severity == "warn" and c.measured.startswith("not run: ")
    assert any(s.basis == "assumption" and "IEC 62368" in s.text for s in c.source)
