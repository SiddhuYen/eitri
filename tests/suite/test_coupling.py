"""Mechanism <-> CAD coupling: link masses from the CAD bodies, the torque re-graded with them, self-collision with the CAD geometry."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eitri_suite import coupling
from eitri.model import load_model

DEMO = Path(__file__).resolve().parents[2] / "eitri_suite" / "demos" / "robot-arm"


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "arm"
    shutil.copytree(DEMO, f, ignore=shutil.ignore_patterns("__pycache__"))
    return f


def run(folder):
    lm = load_model(folder / "mech.py")
    return coupling.run(lm.build(), lm.module.COUPLING, folder, folder / "mech.py")


def named(checks, needle):
    hits = [c for c in checks if needle in c.name]
    assert hits, [c.name for c in checks]
    return hits[0]


def test_link_masses_come_from_the_cad_and_say_so(folder):
    checks, data = run(folder)
    c = named(checks, "link masses come from the CAD model")
    assert "turret: 92.1 g from CAD (declared 60 g)" in c.measured and "base: 223.6 g from CAD (none declared)" in c.measured
    assert data["link_masses_g"]["upper"] == pytest.approx(91.9, abs=0.2)      # MG996R at 80 g, TowerPro's higher figure
    assert any("MG996R" in s for s in data["sources"]["upper"]) and any("pla" in s for s in data["sources"]["upper"])   # purchased part: catalog mass; printed: volume x density
    assert named(checks, "torque joint shoulder").name.endswith("(masses from CAD)")


def test_the_cad_override_changes_the_torque_the_declaration_alone_would_give(folder):
    base_t = float(named(run(folder)[0], "torque joint shoulder").measured.split(" kg")[0])
    arm = (folder / "arm.py").read_text().replace("arm_h=24.0", "arm_h=90.0")             # much taller (heavier) printed plates
    (folder / "arm.py").write_text(arm)
    checks, data = run(folder)
    heavy = float(named(checks, "torque joint shoulder").measured.split(" kg")[0])
    assert heavy > base_t * 1.3
    declared_only = load_model(folder / "mech.py").build()
    from eitri.mechanism.checks import run_checks
    plain = [c for c in run_checks(declared_only)[0] if c.name.startswith("torque joint shoulder")][0]
    assert float(plain.measured.split(" kg")[0]) < heavy                                  # the public kernel alone never saw the CAD
    assert "declared 60 g" in named(checks, "link masses come from the CAD model").measured and named(checks, "link masses come from the CAD model").severity == "warn"


def test_a_part_in_the_arms_way_is_a_self_collision(folder):
    arm = (folder / "arm.py").read_text().replace("28.3, 54.4, 13.0", "28.3, 54.4, 70.0").replace("BASE_T + 6.5, 28.3", "BASE_T + 35, 28.3")
    (folder / "arm.py").write_text(arm)
    checks, data = run(folder)
    c = named(checks, "self-collision")
    assert c.severity == "fail" and "controller" in c.measured
    assert data["collisions"]["pairs"]


def test_unknown_parts_and_bad_declarations_are_checks_not_crashes(folder):
    lm = load_model(folder / "mech.py")
    mech = lm.build()
    checks, _ = coupling.run(mech, dict(cad="arm.py", links={"upper": ["upper_arm", "ghost"]}), folder, folder / "mech.py")
    assert "ghost" in named(checks, "every coupled CAD part has a mass").measured
    assert named(coupling.run(mech, dict(cad="arm.py", links={}), folder, folder / "mech.py")[0], "COUPLING is valid").severity == "fail"
    assert named(coupling.run(mech, dict(cad="nope.py", links={"upper": ["upper_arm"]}), folder, folder / "mech.py")[0], "CAD model for the mechanism").severity == "fail"
    assert named(coupling.run(mech, "x", folder, folder / "mech.py")[0], "COUPLING is a dict").severity == "fail"


def test_the_coupling_is_part_of_the_guard(folder):
    from eitri.project import Project
    pr = Project(folder, name="mech")
    res = pr.build(note="first", render=False)
    assert res.extensions["suite"]["declares"]["coupling"] == {"links": 4}
    code = (folder / "mech.py").read_text()
    pr.write_model(code[:code.index("COUPLING = dict(")] + code[code.index("def build(p)"):])      # an agent drops the coupling
    res = pr.build(note="drop it", render=False)
    assert any(c["name"] == "suite: declared checks are kept" and not c["ok"] and "coupling.links dropped from 4 to 0" in c["measured"] for c in res.domain_report["checks"])
