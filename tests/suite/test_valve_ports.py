"""Valve ports after the core's ISO 5599 fix (a9ad907, in core 0.8.0 from 2dcbb64): a single-solenoid SMC SY3120 clamp, both ways round,
by number and by SMC's letters, through what the combined workspace reads of a pneumatics build (the package verdict, the
export rule), and the migration step for pre-0.8.0 designs.

SMC CAT.EUS11-83 SY p.14: the spring (rest) box joins 1-2 and 4-5, coil 14 joins 1-4; SMC marks 4 "A" and 2 "B". So a clamp that must
stay closed (extended) on a loss of 24 V has its cap end on port 2 (B). On port 4 (A) it opens: the fail-safe check must fail."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eitri_suite import buildpkg
from eitri_suite import workspace as W

CLAMP = '''from eitri.pneumatics import *


def build(p):
    c = Circuit("SY clamp", "single-acting-on-power clamp: closed (extended) when the coil is off")
    air = c.supply("AIR", pressure_bar=7.0, flow_l_min=500)
    frl = c.frl("FRL", set_bar=6.0, C=2.5, b=0.35)
    v = c.valve("V1", part="SMC SY3120-5LZ-C6", signal="CLAMP_OPEN")
    cyl = c.cylinder("C1", bore_mm=32, rod_mm=12, stroke_mm=25, load_N=150, speed_mm_s=100, cycles_per_min=6,
                     on_power_loss="extend", on_air_loss="release")
    c.tube("T1", air["OUT"], frl["IN"], id_mm=8, length_m=2.0)
    c.tube("T2", frl["OUT"], v["{supply}"], id_mm=6, length_m=1.0)
    c.tube("T3", v["{cap}"], cyl["CAP"], id_mm=6, length_m=1.0)
    c.tube("T4", v["{rod}"], cyl["ROD"], id_mm=6, length_m=1.0)
    c.silencer(v["{ex1}"])
    c.silencer(v["{ex2}"])
    return c
'''
RIGHT_NUM = dict(supply="1", cap="2", rod="4", ex1="3", ex2="5")          # rest feeds 2 -> cap: closed on a power loss
WRONG_NUM = dict(supply="1", cap="4", rod="2", ex1="5", ex2="3")          # coil 14 feeds 4 -> cap: opens on a power loss
RIGHT_SMC = dict(supply="P", cap="B", rod="A", ex1="EB", ex2="EA")        # SMC's letters, SMC's meaning (B = 2)
WRONG_SMC = dict(supply="P", cap="A", rod="B", ex1="EA", ex2="EB")        # the sales-sample mistake: A (= 4) on the cap end


def _build(tmp_path: Path, name: str, ports: dict) -> list[dict]:
    from eitri.kernel import build_model
    f = tmp_path / f"{name}.py"
    f.write_text(CLAMP.format(**ports), encoding="utf-8")
    res = build_model(f, tmp_path / f"out-{name}", formats=(), render=False)
    assert res.ok, res.error
    return json.loads((tmp_path / f"out-{name}" / "result.json").read_text(encoding="utf-8"))["report"]["checks"]


def _failsafe(checks):
    return next(c for c in checks if c["name"] == "on loss of electrical power C1")


@pytest.mark.parametrize("name,ports,ok", [("right_num", RIGHT_NUM, True), ("wrong_num", WRONG_NUM, False),
                                           ("right_smc", RIGHT_SMC, True), ("wrong_smc", WRONG_SMC, False)])
def test_a_single_solenoid_sy_clamp_both_ways_round(tmp_path, name, ports, ok):
    checks = _build(tmp_path, name, ports)
    fs = _failsafe(checks)
    assert fs["status"] == ("pass" if ok else "fail"), fs
    v = buildpkg._verdict(checks)                                              # what the suite's build package grades
    assert (v["fail"] == 0) is ok
    app = SimpleNamespace(project=_Project(tmp_path, f"{name}.py", checks), folder=tmp_path)
    rows = W.blocking(app.project)                                             # what the combined workspace's exports grade
    assert (not any(r["name"] == "on loss of electrical power C1" for r in rows)) is ok


class _Project:
    """One stored build of one model file (workspace.blocking reads the newest revision of each file)."""

    def __init__(self, folder: Path, file: str, checks: list[dict]):
        self.folder = folder
        for p in folder.glob("*.py"):
            if p.name != file:
                p.unlink()
        self.history = [SimpleNamespace(number=1, model=file, ok=True, summary="", note="", created=0.0)]
        self._checks = checks

    def reload(self):
        pass

    def model_files(self):
        return [p.name for p in self.folder.glob("*.py")]

    def build_result(self, rev, history=None):
        return {"report": {"checks": self._checks}}


def test_letters_on_a_generic_valve_stop_with_the_cores_message(tmp_path):
    from eitri.model import load_model
    f = tmp_path / "old.py"
    f.write_text(CLAMP.replace('part="SMC SY3120-5LZ-C6", signal="CLAMP_OPEN"', '"5/2", actuation="solenoid", returns="spring"')
                 .format(supply="P", cap="A", rod="B", ex1="R", ex2="S"), encoding="utf-8")
    with pytest.raises(Exception, match="ISO 5599"):
        load_model(f).build()


def test_old_letters_are_found_and_explained():
    old = 'v = c.valve("V1", "5/2", actuation="solenoid")\nc.tube("T", v["A"], cyl["CAP"])\n'
    smc = 'v = c.valve("V1", part="SMC SY3120-5LZ-C6")\nc.tube("T", v["A"], cyl["CAP"])\n'
    assert W.old_valve_ports(old) == [{"valve": "v", "letters": ["A"], "smc": False}]
    assert W.old_valve_ports(smc)[0]["smc"] is True
    assert W.old_valve_ports(CLAMP.format(**RIGHT_NUM)) == []
    assert "P→1, A→2, B→4, R→3, S→5" in W.VALVE_MESSAGE


def test_a_build_error_from_old_letters_gets_the_migration_step():
    files = [{"file": "air.py", "kind": "pneumatics", "built": False, "blank": False,
              "error": f"PneumaticsError: V1: {W.VALVE_ERROR_MARK} ['1', '2', '3', '4', '5'] ..."}]
    steps, _missing = W.next_steps(files, {}, "")
    assert any("P→1, A→2, B→4" in s for s in steps), steps


def test_the_cores_error_carries_the_mark(tmp_path):
    """The next step keys on the core's own error text: if the core rewords it, this fails first."""
    from eitri.model import load_model
    f = tmp_path / "old.py"
    f.write_text(CLAMP.replace('part="SMC SY3120-5LZ-C6", signal="CLAMP_OPEN"', '"5/2", actuation="solenoid", returns="spring"')
                 .format(supply="P", cap="A", rod="B", ex1="R", ex2="S"), encoding="utf-8")
    with pytest.raises(Exception) as e:
        load_model(f).build()
    assert W.VALVE_ERROR_MARK in str(e.value)
