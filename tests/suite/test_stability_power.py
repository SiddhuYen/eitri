"""Two checks a 2026-11-12 latency run of the combined workspace (medium vs high effort) showed missing: a combined
design passed every check although it would tip over and its 4 A supply could not feed the four MG996Rs its firmware moves at once.

    eitri_suite.stability   the base does not tip over (free-standing: centre of mass inside the support polygon by h x tan 10 deg)
    eitri_suite.power       the supply covers the actuators the firmware moves together (+ the logic load)

The regression cases are that run's two finished projects, copied unchanged into tests/suite/fixtures/latency-combined-{medium,high}
(model.py, mech.py, fw.py, wiring.py). The medium one must fail both; the high one passes tipping and fails power until its firmware
declares what its sketch does (one joint at a time)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eitri.model import load_model
from eitri_suite import coupling as C
from eitri_suite import kinematics as K
from eitri_suite import power, projectchecks, stability

HERE = Path(__file__).resolve().parent
MEDIUM = HERE / "fixtures" / "latency-combined-medium"
HIGH = HERE / "fixtures" / "latency-combined-high"
UNO = HERE.parents[1] / "eitri_suite" / "demos" / "uno-arm"


def variant(tmp_path: Path, src: Path, name: str, edits: dict[str, list[tuple[str, str]]] | None = None, drop=()) -> Path:
    d = tmp_path / name
    shutil.copytree(src, d, ignore=shutil.ignore_patterns("__pycache__", "out", ".eitri"))
    for f in drop:
        (d / f).unlink()
    for f, subs in (edits or {}).items():
        text = (d / f).read_text(encoding="utf-8")
        for a, b in subs:
            assert a in text, (f, a)
            text = text.replace(a, b)
        (d / f).write_text(text, encoding="utf-8")
    return d


def tipping(d: Path, cad: str = "model.py", mech: str = "mech.py"):
    cl = load_model(d / cad)
    parts = cl.build()
    ml = load_model(d / mech)
    m = ml.build()
    cpl = dict(ml.module.COUPLING)
    hw = dict(getattr(cl.module, "HARDWARE", None) or {})
    b = K.actuator_bindings(parts, m, cpl, hw)
    links = K.effective_links(cpl["links"], b)
    seq = C.firmware_sequence(d, m, mech)
    return stability.check(parts, m, cpl, links, b, hw, getattr(cl.module, "PURCHASED", None), seq)


def supply(d: Path, fw: str = "fw.py"):
    lm = load_model(d / fw)
    f = lm.build()
    return power.firmware_check(lm.module, f, d, projectchecks._mechanism_for(lm.module, d))


# ---------------------------------------------------------------------------- geometry helpers

def test_hull_and_signed_distance():
    poly = stability.hull([(0, 0), (10, 0), (10, 10), (0, 10), (5, 5), (5, 0)])
    assert len(poly) == 4
    assert stability.inside_distance(poly, 5, 5) == pytest.approx(5.0)
    assert stability.inside_distance(poly, 9, 5) == pytest.approx(1.0)
    assert stability.inside_distance(poly, 12, 5) == pytest.approx(-2.0)
    assert stability.inside_distance(poly, 13, 14) == pytest.approx(-5.0)


# ---------------------------------------------------------------------------- tipping

def test_the_medium_latency_arm_tips_over():
    chk, data = tipping(MEDIUM)
    assert chk.severity == "fail" and chk.category == "integration"
    assert "taken as free-standing" in chk.measured and "OUTSIDE" in chk.measured and "tips over" in chk.measured
    assert data["worst"]["inside_mm"] < -15                       # ~23 mm past the plate's front edge (plate_x1 = 30)
    assert "IEC 62368-1" in chk.expected and "ASSUMPTION" in chk.expected
    for src in ("catalog MG996R", "pla 1.24 g/cm3", "payload 20 g", "fill 0.5"):     # where each mass comes from
        assert src in chk.measured, src


def test_the_high_latency_arm_stands():
    chk, data = tipping(HIGH)
    assert chk.severity == "pass", chk.measured
    assert data["worst"]["inside_mm"] > data["worst"]["needed_mm"] > 0
    assert data["poses"] > 1000


def test_a_bolted_base_passes_on_that_basis():
    chk, data = tipping(UNO)
    assert chk.severity == "pass" and "declared bolted" in chk.measured and "4 vertical through hole" in chk.measured
    assert data["basis"] == "declared"


def test_the_uno_arm_free_standing_would_tip(tmp_path):
    d = variant(tmp_path, UNO, "free", {"mech.py": [('base_fixing="bolted"', 'base_fixing="free"')]})
    chk, _ = tipping(d)
    assert chk.severity == "fail" and "declared free-standing" in chk.measured


def test_bolted_without_holes_warns(tmp_path):
    d = variant(tmp_path, UNO, "noholes", {"model.py": [("    for bx_, by_ in BASE_BOLTS:\n", "    for bx_, by_ in []:\n")]})
    chk, _ = tipping(d)
    assert chk.severity == "warn" and "no vertical through hole" in chk.measured


def test_holes_but_nothing_declared_is_not_run_never_a_pass(tmp_path):
    d = variant(tmp_path, UNO, "undeclared", {"mech.py": [('    base_fixing="bolted",', '')]})
    chk, _ = tipping(d)
    assert chk.severity == "warn" and chk.measured.startswith("not run:") and "may be bolted down" in chk.measured
    assert "it would tip" in chk.measured                       # the free-standing result, for information


def test_a_clamped_or_wall_mounted_base_passes(tmp_path):
    d = variant(tmp_path, MEDIUM, "clamped", {"mech.py": [("    print_fill=0.5,", "    print_fill=0.5, base_fixing='clamped',")]})
    chk, _ = tipping(d)
    assert chk.severity == "pass" and "declared clamped" in chk.measured
    d = variant(tmp_path, MEDIUM, "wall", {"mech.py": [('m.mount("table")', 'm.mount("wall")')]})
    chk, _ = tipping(d)
    assert chk.severity == "pass" and "mounted on a wall" in chk.measured


def test_a_bad_declaration_fails(tmp_path):
    d = variant(tmp_path, MEDIUM, "bad", {"mech.py": [("    print_fill=0.5,", "    print_fill=0.5, base_fixing='glued',")]})
    chk, _ = tipping(d)
    assert chk.severity == "fail" and "base_fixing" in chk.measured


def test_a_moving_body_without_a_mass_is_not_run(tmp_path):
    d = variant(tmp_path, MEDIUM, "nomass", {"model.py": [('"forearm": "pla", ', '')]})
    chk, _ = tipping(d)
    assert chk.severity == "warn" and chk.measured.startswith("not run:") and "forearm" in chk.measured


def test_a_wider_base_fixes_the_medium_arm(tmp_path):
    # the fix belongs in the design: the plate carried 120 mm further forward under the reach (and 60 mm wider) stands
    d = variant(tmp_path, MEDIUM, "wider", {"model.py": [("plate_x1=30.0,", "plate_x1=150.0,"), ("plate_w=110.0,", "plate_w=170.0,")]})
    chk, _ = tipping(d)
    assert chk.severity == "pass", chk.measured


def test_the_mechanism_and_cad_builds_both_run_it():
    lm = load_model(MEDIUM / "mech.py")
    checks, data = projectchecks.extras("mechanism", lm.module, lm.build(), MEDIUM / "mech.py")
    assert [c.severity for c in checks if c.name == stability.NAME] == ["fail"]
    assert data["coupling"]["stability"]["worst"]["inside_mm"] < 0
    cl = load_model(MEDIUM / "model.py")
    checks, data = projectchecks.extras("cad", cl.module, cl.build(), MEDIUM / "model.py")
    assert [c.severity for c in checks if c.name == stability.NAME] == ["fail"]


# ---------------------------------------------------------------------------- supply vs what moves together

def test_the_medium_latency_arm_overloads_its_supply():
    checks, data = supply(MEDIUM)
    (chk,) = checks
    assert chk.severity == "fail" and chk.category == "integration"
    assert "4 actuator(s) move together at power-up" in chk.measured and "10.00 A" in chk.measured and "Adafruit 1466" in chk.measured and "adafruit.com/product/1466" in chk.measured
    assert "TowerPro MG996R" in chk.measured and "unverified" not in chk.measured          # every figure cited (the 4 A from Adafruit 1466)
    assert data["worst"]["total_A"] == pytest.approx(10.0)


def test_the_medium_arm_fails_on_its_sequence_alone(tmp_path):
    # even without the power-up move (sequential would not apply: it moves 3 together in "above_place")
    _sets, _basis, _p = power.move_sets(*_fw(MEDIUM))
    moving = [ms for label, ms in _sets if label == "step 'above_place'"]
    assert moving == [["YAW", "SHOULDER", "ELBOW"]]


def _fw(d: Path):
    lm = load_model(d / "fw.py")
    f = lm.build()
    return lm.module, f, projectchecks._mechanism_for(lm.module, d)


def test_the_high_arm_fails_until_its_firmware_declares_one_joint_at_a_time(tmp_path):
    (chk,), _ = supply(HIGH)
    assert chk.severity == "fail" and "power-up" in chk.measured and "motion='sequential'" in chk.measured
    d = variant(tmp_path, HIGH, "seq", {"fw.py": [('    servo_zero=', "    motion='sequential',\n    servo_zero=")]})
    (chk,), data = supply(d)
    assert chk.severity == "pass" and "alone" in chk.measured and data["worst"]["total_A"] == pytest.approx(2.5)


def test_the_uno_arm_supply_covers_its_moves():
    (chk,), data = supply(UNO)
    assert chk.severity == "pass" and data["worst"]["total_A"] == pytest.approx(7.5) and data["rating_A"] == 10.0
    assert "the board is powered separately" in chk.measured


def test_no_sequence_counts_every_actuator(tmp_path):
    d = variant(tmp_path, UNO, "noseq", {"fw.py": [("SEQUENCE = [", "_SEQ = ["), ("in SEQUENCE", "in _SEQ")]})
    (chk,), _ = supply(d)
    assert "no SEQUENCE" in chk.measured and "all at once" in chk.measured


def test_a_board_powered_from_the_supply_adds_its_logic_load(tmp_path):
    d = variant(tmp_path, MEDIUM, "logic", {"wiring.py": [('pins=["D9", "D10", "D11", "D6", "GND"]', 'pins=["D9", "D10", "D11", "D6", "GND", "5V"]')]})
    (chk,), data = supply(d)
    assert data["worst"]["logic_A"] == pytest.approx(0.2) and "ATmega328P" in chk.measured


def test_no_rated_supply_is_not_run(tmp_path):
    d = variant(tmp_path, MEDIUM, "nopsu", {"model.py": [('PURCHASED = [("PSU-5V-4A", 1)]', "PURCHASED = []")]})
    (chk,), _ = supply(d)
    assert chk.severity == "warn" and chk.measured.startswith("not run:")


def test_a_bad_motion_declaration_fails(tmp_path):
    d = variant(tmp_path, MEDIUM, "badmotion", {"fw.py": [('    mechanism="mech.py",', '    mechanism="mech.py", motion="smooth",')]})
    (chk,), _ = supply(d)
    assert chk.severity == "fail" and "motion" in chk.measured


def test_the_firmware_build_runs_it():
    lm = load_model(MEDIUM / "fw.py")
    checks, data = projectchecks.extras("firmware", lm.module, lm.build(), MEDIUM / "fw.py")
    assert [c.severity for c in checks if c.name == power.NAME] == ["fail"]
    assert data["power"]["supply"] == "PSU-5V-4A"


def test_the_circuit_version_fails_a_small_supply(tmp_path):
    from eitri_suite import electronics as E
    d = variant(tmp_path, UNO, "small", {"elec.py": [("psu_A=10.0", "psu_A=6.0")]})
    lm = load_model(d / "elec.py")
    checks, data = E.run(lm.module, lm.build(), d / "elec.py")
    chk = next(c for c in checks if c.name == power.NAME + " (circuit)")
    assert chk.severity == "fail" and "7.50 A" in chk.measured


# ---------------------------------------------------------------------------- catalog figures: read from a datasheet, or not run
def test_every_figure_the_checks_read_cites_its_datasheet():
    lm = load_model(HIGH / "mech.py")
    checks, _ = projectchecks.extras("mechanism", lm.module, lm.build(), HIGH / "mech.py")
    chk = next(c for c in checks if c.name == stability.NAME)
    assert "[source: TowerPro MG996R product page" in chk.measured and "towerpro.com.tw/product/mg996r" in chk.measured
    assert any(s.basis == "assumption" and s.text.startswith("ASSUMPTION: tipping margin") and "IEC 62368-1" in s.text for s in chk.source)
    assert "IEC 62368-1" in chk.line() and "ASSUMPTION" in chk.line()                     # on the printed line a customer reads
    (p,), _ = supply(UNO)
    assert "MEAN WELL LRS-50-5" in p.measured and "LRS-50-SPEC" in p.measured and "2025-04-07" in p.measured and "p.2" in p.measured


def _unverify(monkeypatch, part, key):
    from dataclasses import replace
    from eitri_suite import catalog as C
    cp = C.PARTS[part]
    monkeypatch.setitem(C.PARTS, part, replace(cp, specs={**cp.specs, key: replace(cp.specs[key], verified=False)}))


def test_an_unverified_supply_rating_is_not_run_and_blocks(monkeypatch):
    _unverify(monkeypatch, "PSU-5V-10A", "output_current_a")
    (p,), _ = supply(UNO)
    assert p.status == "not_run" and "ASSUMPTION: unverified catalog figure" in p.measured


def test_an_unverified_moving_mass_is_not_run_and_a_base_one_is_left_out(monkeypatch, tmp_path):
    _unverify(monkeypatch, "MG996R", "mass_g")
    chk, _ = tipping(HIGH)
    assert chk.status == "not_run" and "ASSUMPTION: unverified catalog figure for a moving body" in chk.measured
    monkeypatch.undo()
    _unverify(monkeypatch, "MG996R", "mass_g_low")                                   # only the base's figure unknown: left out
    chk, _ = tipping(HIGH)
    assert chk.status == "pass" and "only an unverified catalog mass: left out" in chk.measured


def test_an_unverified_catalog_entry_blocks_a_strict_export(monkeypatch, tmp_path):
    """End to end: the supply's rating unverified -> the firmware build's supply check is not run -> the export rule refuses."""
    from dataclasses import asdict
    from types import SimpleNamespace
    from eitri_suite import workspace as W
    _unverify(monkeypatch, "PSU-5V-10A", "output_current_a")
    (chk,), _ = supply(UNO)
    row = {**asdict(chk), "status": chk.status}
    f = tmp_path / "fw.py"
    f.write_text((UNO / "fw.py").read_text(encoding="utf-8"), encoding="utf-8")

    class P:
        folder, history = tmp_path, [SimpleNamespace(number=1, model="fw.py", ok=True, summary="", note="", created=0.0)]
        reload = lambda self: None
        model_files = lambda self: ["fw.py"]
        build_result = lambda self, rev, history=None: {"report": {"checks": [row]}}
    rows = W.blocking(P())
    assert [(r["name"], r["status"]) for r in rows] == [(chk.name, "not_run")]
    assert "ASSUMPTION: unverified catalog figure" in rows[0]["measured"] and "refused" in W.blocking_message(rows)
