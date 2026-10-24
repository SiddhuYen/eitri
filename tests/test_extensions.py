"""Build extensions (eitri.extensions): a registered hook's checks join the report, its data lands in the result, and its
guard keeps what a model declared from shrinking; a failing hook is a failed check, never a failed build."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eitri import extensions as X
from eitri.project import Project

CAD = "from eitri import *\n\nPARAMS = dict(w=10)\nWIDGETS = ['a', 'b']\n\ndef build(p):\n    return Box(p.w, 10, 10)\n"
HARNESS = '''from eitri.harness import *

PARAMS = dict(length_mm=300)
WIDGETS = ['x']

def build(p):
    h = Harness("Lead")
    a = h.add(Connector("J1", "JST-XH", pins=["+", "-"]))
    b = h.add(Connector("J2", "JST-XH", pins=["+", "-"]))
    h.net("V", current_A=1.0, voltage_V=5.0)
    h.net("G", current_A=1.0, voltage_V=5.0, round_trip=False)
    h.connect(a["+"], b["+"], Wire(22, "RD", p.length_mm), net="V")
    h.connect(a["-"], b["-"], Wire(22, "BK", p.length_mm), net="G")
    return h
'''
DUMMY = '''"""A dummy build extension: counts the model's WIDGETS and guards that count."""
from eitri.extensions import Check, Outcome, register


def hook(ctx):
    n = len(getattr(ctx.module, "WIDGETS", None) or [])
    checks = [Check(name="widgets declared", ok=n > 0, measured=f"{n} widget(s)", expected=">= 1",
                    severity="pass" if n else "fail", category="extension", refs=[])]
    if ctx.kind == "harness":
        checks.append(Check(name="dummy saw a harness", ok=True, measured=f"{len(ctx.built.links)} wires", expected="", severity="pass", category="extension", refs=[]))
    return Outcome(checks=checks, data={"widgets": n, "kind": ctx.kind, "params": ctx.params}, guard={"widgets": n})


register("dummy", hook)
'''


@pytest.fixture
def registry():
    """The in-process registry, restored afterwards (other tests must not see what a test registers)."""
    saved, loaded = dict(X._REGISTRY), X._LOADED
    X._REGISTRY.clear()
    X._LOADED = True                       # nothing from the environment during unit tests
    yield X._REGISTRY
    X._REGISTRY.clear()
    X._REGISTRY.update(saved)
    X._LOADED = loaded


def ctx(kind="cad", guards=None, module=None):
    return X.BuildContext(Path("/m.py"), Path("/out"), kind, module or type("M", (), {})(), {"w": 1}, None, None, {}, guards or {})


def check(name, ok=True, sev=None):
    return X.Check(name=name, ok=ok, measured="m", expected="", severity=sev or ("pass" if ok else "fail"), category="extension", refs=[])


# ---------------------------------------------------------------------------- the registry and run()


def test_register_and_run_outcomes(registry):
    X.register("a", lambda c: X.Outcome(checks=[check("a1"), check("a2", False)], data={"n": 1}, guard={"n": 1}))
    X.register("b", lambda c: [check("b1", True, "warn")])             # a bare iterable of checks
    X.register("c", lambda c: None)                                      # nothing to say
    assert set(X.registered()) == {"a", "b", "c"}
    checks, data, guards = X.run(ctx())
    assert [c.name for c in checks] == ["a1", "a2", "b1"] and [c.ok for c in checks] == [True, False, True] and checks[2].severity == "warn"
    assert data == {"a": {"n": 1}} and guards == {"a": {"n": 1}}
    X.unregister("b")
    assert "b" not in X.registered()
    with pytest.raises(ValueError):
        X.register("", None)


def test_a_raising_or_misbehaving_extension_is_a_failed_check(registry):
    def boom(c):
        raise RuntimeError("kaput")
    X.register("boom", boom)
    X.register("wrong", lambda c: ["not a check"])
    X.register("fine", lambda c: [check("ok")])
    checks, data, guards = X.run(ctx())
    names = {c.name: c for c in checks}
    assert names["extension boom ran"].severity == "fail" and "kaput" in names["extension boom ran"].measured and names["extension boom ran"].refs == ["boom"]
    assert names["extension wrong ran"].severity == "fail" and "not an eitri.extensions.Check" in names["extension wrong ran"].measured
    assert names["ok"].ok and data == {} and guards == {}


def test_guard_semantics():
    assert X.guard_check("e", None, {"n": 3}) is None                                          # nothing remembered: nothing to keep
    assert X.guard_check("e", {"n": 3}, {"n": 3}).severity == "pass"
    assert X.guard_check("e", {"n": 3}, {"n": 5}).severity == "pass"
    g = X.guard_check("e", {"n": 3, "kind": "x"}, {"n": 2, "kind": "x"})
    assert g.severity == "fail" and g.name == "e: declared checks are kept" and "n dropped from 3 to 2" in g.measured
    assert "n dropped from 3 to 0" in X.guard_check("e", {"n": 3}, {}).measured
    assert "kind is no longer declared" in X.guard_check("e", {"n": 3, "kind": "x"}, {"n": 3}).measured
    assert "nothing is declared any more" in X.guard_check("e", {"n": 3}, None).measured
    assert X.merge_guard(None, {"n": 2}) == {"n": 2} and X.merge_guard({"n": 3}, {"n": 2}) == {"n": 3} and X.merge_guard({"n": 3}, {"n": 5}) == {"n": 5}
    assert X.merge_guard({"n": 3, "kind": "x"}, {"kind": "y"}) == {"n": 3, "kind": "y"} and X.merge_guard({"n": 3}, None) == {"n": 3}


def test_run_emits_the_guard_check_from_the_context(registry):
    X.register("e", lambda c: X.Outcome(guard={"n": 1}))
    checks, _, _ = X.run(ctx(guards={"e": {"n": 2}}))
    assert len(checks) == 1 and checks[0].name == "e: declared checks are kept" and not checks[0].ok
    checks, _, _ = X.run(ctx(guards={"e": {"n": 1}}))
    assert len(checks) == 1 and checks[0].ok


def test_load_reports_a_bad_module(registry, monkeypatch):
    monkeypatch.setenv(X.ENV_VAR, "no_such_module_xyz, tests.test_extensions:not_there")
    problems = X.load(force=True)
    assert len(problems) == 2 and "no_such_module_xyz" in problems[0] and "not_there" in problems[1]
    checks, _, _ = X.run(ctx())
    assert [c.name for c in checks] == ["extension loaded", "extension loaded"] and all(not c.ok for c in checks)
    X._LOADED = True


# ---------------------------------------------------------------------------- through a real build


@pytest.fixture
def dummy_env(tmp_path, monkeypatch):
    """A dummy extension module that every build subprocess imports (EITRI_EXTENSIONS); builds run in a fresh
    subprocess so they see this environment."""
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "dummy_ext.py").write_text(DUMMY)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "ext"))
    monkeypatch.setenv(X.ENV_VAR, "dummy_ext")
    monkeypatch.setenv("EITRI_WORKER", "0")
    return tmp_path


def test_a_registered_hook_runs_on_every_cad_build_and_guards_what_it_saw(dummy_env):
    pr = Project(dummy_env / "box", name="box")
    pr.write_model(CAD)
    res = pr.build(note="first", render=False)
    assert res.ok
    names = {c.name: c for c in res.report.checks}
    assert names["widgets declared"].ok and names["widgets declared"].measured == "2 widget(s)"
    raw = json.loads((pr.out_dir / "result.json").read_text())
    stored = next(c for c in raw["report"]["checks"] if c["name"] == "widgets declared")
    assert stored["severity"] == "pass" and stored["category"] == "extension"                    # the structured verdict survives into result.json
    assert res.extensions == {"dummy": {"widgets": 2, "kind": "cad", "params": {"w": 10}}} and raw["extensions"] == res.extensions
    assert res.guards == {"dummy": {"widgets": 2}} and pr.guards() == {"dummy": {"widgets": 2}}
    assert json.loads(pr.state_path.read_text())["guards"] == {"box.py": {"dummy": {"widgets": 2}}}
    assert "[PASS] widgets declared: 2 widget(s)" in res.summary
    from eitri.viewer.server import _state
    assert "widgets declared" in [c["name"] for c in _state(pr)["checks"]]                      # the editor's checks card shows it

    # the model now declares less: the guard fails the check (not the build), and the remembered count stays
    pr.write_model(CAD.replace("WIDGETS = ['a', 'b']", "WIDGETS = ['a']"))
    res = pr.build(note="fewer", render=False)
    assert res.ok
    kept = next(c for c in res.report.checks if c.name == "dummy: declared checks are kept")
    assert not kept.ok and "widgets dropped from 2 to 1" in kept.measured
    assert pr.guards() == {"dummy": {"widgets": 2}}
    # removing the declaration altogether fails too
    pr.write_model(CAD.replace("WIDGETS = ['a', 'b']\n", ""))
    res = pr.build(note="none", render=False)
    assert not next(c for c in res.report.checks if c.name == "widgets declared").ok
    assert "widgets dropped from 2 to 0" in next(c for c in res.report.checks if c.name == "dummy: declared checks are kept").measured
    # a person releases the guard: the next build starts it afresh
    pr.release_guard("dummy")
    assert pr.guards() == {}
    pr.write_model(CAD.replace("WIDGETS = ['a', 'b']", "WIDGETS = ['a']"))
    res = pr.build(note="released", render=False)
    assert res.ok and not any(c.name.endswith("declared checks are kept") for c in res.report.checks)
    assert pr.guards() == {"dummy": {"widgets": 1}}


def test_the_hook_runs_on_harness_builds_too(dummy_env):
    pr = Project(dummy_env / "lead", name="lead")
    pr.write_model(HARNESS)
    res = pr.build(note="first", render=False)
    assert res.ok and res.kind == "harness"
    names = {c["name"]: c for c in res.harness_report["checks"]}
    assert names["dummy saw a harness"]["measured"] == "2 wires" and names["widgets declared"]["severity"] == "pass"
    assert res.extensions["dummy"]["kind"] == "harness" and pr.guards() == {"dummy": {"widgets": 1}}
    assert "[PASS] dummy saw a harness: 2 wires" in res.summary


def test_a_broken_extension_module_fails_a_check_not_the_build(dummy_env, monkeypatch):
    monkeypatch.setenv(X.ENV_VAR, "dummy_ext,no_such_module_xyz")
    pr = Project(dummy_env / "box2", name="box2")
    pr.write_model(CAD)
    res = pr.build(note="first", render=False)
    assert res.ok
    names = {c.name: c for c in res.report.checks}
    assert not names["extension loaded"].ok and "no_such_module_xyz" in names["extension loaded"].measured
    assert names["widgets declared"].ok                                                        # the good one still ran


def test_no_extensions_means_no_change(tmp_path, monkeypatch):
    monkeypatch.delenv(X.ENV_VAR, raising=False)
    monkeypatch.setenv("EITRI_WORKER", "0")
    pr = Project(tmp_path / "box", name="box")
    pr.write_model(CAD)
    res = pr.build(note="first", render=False)
    assert res.ok and res.report.checks == [] and res.extensions == {} and res.guards == {} and pr.guards() == {}
    assert "guards" not in json.loads(pr.state_path.read_text())
