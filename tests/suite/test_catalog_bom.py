"""The parts catalog and the consolidated BOM."""
from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path

import pytest

from eitri_suite import bom as B
from eitri_suite import catalog as C

DEMO = Path(__file__).resolve().parents[2] / "eitri_suite" / "demos" / "robot-arm"


def test_seeded_with_the_arm_parts():
    for pid in ("MG996R", "PCA9685", "ESP32-DEVKIT", "PSU-5V-4A", "M3x8-SHCS", "M3x12-SHCS", "M3-NUT", "625ZZ"):
        p = C.PARTS[pid]
        assert p.part_number and p.source.startswith("https://") and p.price_usd and p.specs, pid
    assert C.lookup("mg996r") is C.PARTS["MG996R"] and C.lookup("625") is C.PARTS["625ZZ"] and C.lookup("esp32") is C.PARTS["ESP32-DEVKIT"]
    assert C.lookup("m3 x 8") is None or C.lookup("M3X8").id == "M3x8-SHCS"
    assert C.lookup("nonsense") is None
    with pytest.raises(KeyError):
        C.require("nonsense")


def test_harness_connectors_are_reachable_through_the_catalog():
    from eitri.harness.catalog import spec_for
    xt = C.lookup("xt60")
    # the rating comes from the core's harness catalog: since core 0.5.1 it is the continuous rating (XT60: 30 A, Amass), not the burst
    assert xt.category == "connector" and xt.value("rating_a_per_pin") == spec_for("XT60").rating_a == 30 and xt.price_usd is None


def test_servo_specs_and_the_torque_curve():
    s = C.PARTS["MG996R"]
    assert s.value("mass_g") == 80 and s.value("mass_g_low") == 55 and s.spec("mass_g").verified and "towerpro.com.tw" in s.spec("mass_g").cite and s.value("dims_mm") == (40.7, 19.7, 42.9) and s.value("horn_spline") == "25T"
    assert s.at_voltage("stall_torque_kgcm", 4.8)[0] == pytest.approx(9.4)
    assert s.at_voltage("stall_torque_kgcm", 6.0)[0] == pytest.approx(11.0)
    assert s.at_voltage("stall_torque_kgcm", 5.0)[0] == pytest.approx(9.4 + (0.2 / 1.2) * 1.6)       # linear between the published points
    assert s.at_voltage("stall_torque_kgcm", 7.0) is None and s.at_voltage("stall_torque_kgcm", 4.0) is None    # never extrapolated
    assert s.at_voltage("mass_g", 5.0) is None


def test_unverified_is_the_default_and_visible():
    # only the figures the cross-domain checks read are verified (each with its document); everything else stays unverified, and prices always
    verified = {(p.id, k) for p in C.PARTS.values() for k, sp in p.specs.items() if sp.verified}
    assert verified == {("MG996R", "mass_g"), ("MG996R", "mass_g_low"), ("MG996R", "stall_current_a"), ("PCA9685", "mass_g"), ("PCA9685", "mass_g_low"),
                        ("PSU-5V-4A", "output_voltage_v"), ("PSU-5V-4A", "output_current_a"), ("PSU-5V-10A", "output_voltage_v"),
                        ("PSU-5V-10A", "output_current_a"), ("PSU-5V-14A", "output_voltage_v"), ("PSU-5V-14A", "output_current_a"),
                        ("PSU-5V-18A", "output_voltage_v"), ("PSU-5V-18A", "output_current_a"),
                        ("PSU-5V-10A", "mass_g"), ("PSU-5V-14A", "mass_g"), ("PSU-5V-18A", "mass_g")}, verified
    assert (C.PARTS["PSU-5V-10A"].mass_g, C.PARTS["PSU-5V-14A"].mass_g, C.PARTS["PSU-5V-18A"].mass_g) == (210.0, 230.0, 330.0)
    for p in C.PARTS.values():
        assert "price" in p.unverified
        for k, sp in p.specs.items():
            assert (sp.cite.startswith("ASSUMPTION") if not sp.verified else (sp.doc and sp.url and sp.rev)), (p.id, k)
    d = C.PARTS["MG996R"].to_dict()
    assert d["specs"]["stall_torque_kgcm"]["verified"] is False and "stall_torque_kgcm" in d["unverified"]
    assert "continuous_fraction" in d["unverified"] and "ESTIMATE" in d["specs"]["continuous_fraction"]["note"]
    assert "| MG996R |" in C.catalog_table()


def test_a_verified_spec_drops_off_the_unverified_list():
    from dataclasses import replace
    p = C.PARTS["625ZZ"]
    specs = dict(p.specs)
    specs["bore_mm"] = C.Spec(5.0, "mm", True, "checked against the SKF datasheet")
    q = replace(p, specs=specs)
    assert "bore_mm" not in q.unverified and "outer_diameter_mm" in q.unverified


@pytest.fixture
def arm(tmp_path):
    dst = tmp_path / "arm"
    shutil.copytree(DEMO, dst)
    return dst


def test_consolidated_bom_covers_cad_harness_and_catalog(arm):
    bom = B.consolidated(arm, arm / "arm.py")
    assert bom["model_files"] == {"cad": "arm.py", "harness": "wiring.py"}
    items = bom["items"]
    by_id = lambda i: next(r for r in items if r["id"] == i)
    assert by_id("MG996R")["qty"] == 3 and by_id("MG996R")["origin"] == "catalog" and by_id("MG996R")["line_total_usd"] == pytest.approx(16.5)
    assert by_id("625ZZ")["qty"] == 2 and by_id("M3x8-SHCS")["qty"] == 16 and by_id("PSU-5V-10A")["qty"] == 1
    assert by_id("PCA9685")["qty"] == 1 and by_id("ESP32-DEVKIT")["qty"] == 1                       # CAD-referenced hardware: counted once each
    assert not any(r["id"] in ("yaw_servo", "pwm_board") for r in items)                           # not listed as printed parts
    printed = [r for r in items if r["category"] == "printed"]
    assert {"base", "turret"} <= {r["id"] for r in printed} and all(r["unit_price_usd"] is None for r in printed)
    pla = by_id("PLA-1KG")
    assert pla["unit"] == "kg" and 0.15 < pla["qty"] < 0.2 and pla["line_total_usd"] == pytest.approx(round(22 * pla["qty"], 2), abs=0.01)
    har = [r for r in items if r["origin"] == "harness"]
    assert {"connector", "terminal", "wire", "label"} <= {r["category"] for r in har}
    # totals add up
    assert bom["totals"]["priced_total_usd"] == pytest.approx(sum(r["line_total_usd"] for r in items if r["line_total_usd"] is not None), abs=0.01)
    assert bom["totals"]["lines"] == len(items) and [r["item"] for r in items] == list(range(1, len(items) + 1))


def test_bom_flags_what_is_unverified(arm):
    bom = B.consolidated(arm, arm / "arm.py")
    assert "MG996R" in bom["unverified"]["parts"] and bom["unverified"]["specs"] > 30
    servo = next(r for r in bom["items"] if r["id"] == "MG996R")
    assert "stall_torque_kgcm" in servo["unverified"] and servo["specs"]["stall_torque_kgcm"]["value"] == {"4.8": 9.4, "6.0": 11.0}
    md = B.to_markdown(bom)
    assert md.startswith("# Bill of materials: arm") and "| MG996R |" in md and "Unverified" in md and "$16.50" in md


def test_bom_json_and_csv_round_trip(arm):
    bom = B.consolidated(arm, arm / "arm.py")
    again = json.loads(B.to_json(bom))
    assert again["items"][0]["id"] == bom["items"][0]["id"] and again["totals"] == bom["totals"]
    rows = list(csv.DictReader(io.StringIO(B.to_csv(bom))))
    assert len(rows) == len(bom["items"]) and {"id", "qty", "unit_price_usd", "source", "unverified_specs"} <= set(rows[0])
    servo = next(r for r in rows if r["id"] == "MG996R")
    assert servo["qty"] == "3" and servo["source"].startswith("https://") and "stall_torque_kgcm" in servo["unverified_specs"]


def test_unknown_catalog_ids_are_reported_not_hidden(arm):
    code = (arm / "arm.py").read_text().replace('("625ZZ", 2)', '("625ZZ", 2), ("MYSTERY-9", 4)')
    (arm / "arm.py").write_text(code)
    bom = B.consolidated(arm, arm / "arm.py")
    row = next(r for r in bom["items"] if r["id"] == "MYSTERY-9")
    assert row["category"] == "unknown" and row["qty"] == 4 and row["line_total_usd"] is None
    assert any("MYSTERY-9" in n for n in bom["notes"])


def test_harness_only_project_has_no_cad_lines(tmp_path):
    (tmp_path / "wiring.py").write_text((DEMO / "wiring.py").read_text())
    bom = B.consolidated(tmp_path, tmp_path / "wiring.py")
    assert bom["model_files"] == {"cad": None, "harness": "wiring.py"} and all(r["origin"] == "harness" for r in bom["items"])
