"""Catalogue parts (eitri.pneumatics.catalog), the checks that use their datasheet figures, and the I/O list."""
from __future__ import annotations

import csv
import io
import math
from pathlib import Path

import pytest

from eitri.pneumatics import P_ATM, Circuit, PneumaticsError, consumption_per_cycle_l, cylinder_forces, find_path, outputs, required_flow_l_s
from eitri.pneumatics import catalog
from eitri.pneumatics.report import analyze, summarize

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "pneumatic-clamp-fixture" / "clamp_fixture.py"


def one(rep, name):
    hits = [c for c in rep.checks if c.name == name]
    assert len(hits) == 1, (name, [c.name for c in rep.checks])
    return hits[0]


def station(set_bar=6.0, valve="SMC SY3120-5LZ-C6", cyl="SMC CDQ2B32-20DZ", fc=True, mass=0.3, speed=100.0, supply_bar=7.0, tube="SMC TU0604"):
    c = Circuit("station")
    c.control_voltage(24)
    air = c.supply("AIR", pressure_bar=supply_bar, flow_l_min=200)
    reg = c.regulator("REG", set_bar=set_bar, C=2.0, b=0.35)
    v = c.valve("V1", part=valve)
    cy = c.cylinder("C1", part=cyl, load_N=100, speed_mm_s=speed, load_mass_kg=mass, cycles_per_min=6, duty="static")
    c.tube("T1", air["OUT"], reg["IN"], id_mm=6, length_m=1)
    c.tube("T2", reg["OUT"], v["P"], part=tube, length_m=0.5)
    if fc:
        f1, f2 = c.flow_control("FC1", part="SMC AS2201F-01-06"), c.flow_control("FC2", part="SMC AS2201F-01-06")
        c.tube("T3", v["A"], f1["A"], part=tube, length_m=1)
        c.tube("T4", f1["B"], cy["ROD"], part=tube, length_m=0.05)
        c.tube("T5", v["B"], f2["A"], part=tube, length_m=1)
        c.tube("T6", f2["B"], cy["CAP"], part=tube, length_m=0.05)
    else:
        c.tube("T3", v["A"], cy["ROD"], part=tube, length_m=1)
        c.tube("T5", v["B"], cy["CAP"], part=tube, length_m=1)
    c.silencer(v["R"], part="SMC AN05-M5")
    c.silencer(v["S"], part="SMC AN05-M5")
    return c


def test_catalogue_lookup_and_sources():
    f, s = catalog.lookup("cylinder", "Festo DSNU-20-50-P-A")
    assert (f["bore_mm"], f["rod_mm"], f["stroke_mm"], f["min_bar"], f["max_bar"], f["port"]) == (20, 8, 50, 1.0, 10.0, "G1/8")
    assert f["piston_mass_kg"] == pytest.approx(0.044 + 0.0004 * 50)
    f, _ = catalog.lookup("cylinder", "CDQ2B32-75DZ")
    assert f["stroke_mm"] == 75 and f["magnet"] is True and catalog.lookup("cylinder", "SMC CQ2B32-20DZ")[0]["magnet"] is False
    with pytest.raises(catalog.CatalogError, match="standard stroke"):
        catalog.lookup("cylinder", "SMC CDQ2B32-60DZ")
    with pytest.raises(catalog.CatalogError, match="outside"):
        catalog.lookup("cylinder", "DSNU-20-400-P-A")
    with pytest.raises(catalog.CatalogError, match="not a known valve"):
        catalog.lookup("valve", "VUVG-L14-M52")
    v, _ = catalog.lookup("valve", "SMC SY3120-5L-C6")                      # without the light / surge suppressor: 0.35 W
    assert v["coil_W"] == 0.35 and catalog.lookup("valve", "SY3120-5LZ-C6")[0]["coil_W"] == 0.4
    for s in catalog.SERIES:                                                # every series cites known sources with URLs
        assert s.sources and all(catalog.SOURCES[x].url.startswith("https://") for x in s.sources)
    assert "smc-sy" in catalog.sources_table() and "DSNU-20" in catalog.parts_table()


def test_part_fills_the_model_and_a_mismatch_is_an_error():
    c = Circuit()
    cy = c.cylinder("C1", part="SMC CDQ2B32-20DZ")
    assert (cy.bore_mm, cy.rod_mm, cy.stroke_mm, cy.part_number, cy.speed_max_mm_s, cy.allowable_ke_J) == (32, 16, 20, "SMC CDQ2B32-20DZ", 500, 0.29)
    assert c.cylinder("C2", 32, 16, 20, part="CDQ2B32-20DZ").bore_mm == 32          # agreeing figures are fine
    with pytest.raises(PneumaticsError, match="bore_mm"):
        c.cylinder("C3", bore_mm=40, part="SMC CDQ2B32-20DZ")
    with pytest.raises(PneumaticsError, match="give bore_mm"):
        c.cylinder("C4")
    v = c.valve("V1", part="SMC SY3120-5LZ-C6")
    assert (v.cls, v.returns, v.C, v.b, v.C_exhaust, v.b_exhaust, v.min_bar, v.max_bar) == ("5/2", "spring", 0.76, 0.30, 0.65, 0.39, 1.5, 7.0)
    assert [x[:2] for x in v.coils] == [("14", "V1_14")]
    v2 = c.valve("V2", part="SMC SY3220-5LZ-C6", signal="CLOSE")
    assert v2.returns == "solenoid" and [x[1] for x in v2.coils] == ["CLOSE", "V2_12"] and v2.min_bar == 1.0
    with pytest.raises(PneumaticsError, match="cls"):
        c.valve("V3", "3/2", part="SMC SY3120-5LZ-C6")
    assert c.valve("V4").cls == "5/2"                                                 # the old defaults still hold without a part


def test_eitri_force_matches_the_catalogues():
    c = Circuit()
    dsnu, cq2 = c.cylinder("C1", part="DSNU-20-50-P-A"), c.cylinder("C2", part="SMC CDQ2B32-20DZ")
    f = cylinder_forces(dsnu, 6.0)
    assert f["extend_theoretical_N"] == pytest.approx(188.5, abs=0.1) and f["retract_theoretical_N"] == pytest.approx(158.3, abs=0.1)
    for mpa, out_n, in_n in ((0.3, 241, 181), (0.5, 402, 302), (0.7, 563, 422)):      # SMC CQ2 p.606 Theoretical Output, ø32
        f = cylinder_forces(cq2, mpa * 10)
        assert f["extend_theoretical_N"] == pytest.approx(out_n, abs=1.0) and f["retract_theoretical_N"] == pytest.approx(in_n, abs=1.0)


def test_air_matches_smc_worked_examples():
    # SMC Best Pneumatics technical data, Data 2: 2 m of 6 mm bore piping at 0.5 MPa takes about 0.56 L ANR per cycle (both lines)
    c = Circuit()
    cy = c.cylinder("C1", 50, 20, 600, cycles_per_min=5)
    line = math.pi / 4 * 6 ** 2 * 2000.0
    cons = consumption_per_cycle_l(cy, 5.0, dead_volume_mm3=2 * line)
    assert cons["dead_l"] == pytest.approx(0.56, abs=0.01)
    assert cons["swept_l"] == pytest.approx(12.9, abs=0.1)                       # SMC's Graph (20) reads "about 13 L" for ø50 x 600 at 0.5 MPa
    # the required air volume: ø50 at 0.5 MPa and 500 mm/s is 350 L/min ANR
    assert required_flow_l_s(math.pi / 4 * 50 ** 2, 500, 5.0) * 60 == pytest.approx(350, rel=0.01)


def test_the_station_passes_with_catalogue_parts():
    rep = analyze(station())
    assert rep.ok, summarize(rep)
    assert one(rep, "minimum pressure V1").severity == "pass" and one(rep, "minimum pressure C1").severity == "pass"
    assert one(rep, "datasheet force C1 extend").severity == "pass"
    assert one(rep, "piston speed range C1 extend").severity == "pass"
    assert one(rep, "coil voltage V1").severity == "pass"
    ex = find_path(station(), station().get("C1").port("ROD"), "exhaust")
    assert [round(s.C, 2) for s in ex.steps if s.name == "V1"] == [0.65]           # the valve's own exhaust figure, not its P->A one


def test_minimum_pressure_and_tube_ratings_fail():
    rep = analyze(station(set_bar=1.2))
    assert one(rep, "minimum pressure V1").severity == "fail"                     # SY3120: 0.15 MPa internal pilot
    assert one(rep, "minimum pressure C1").severity == "pass"                     # CQ2: 0.05 MPa
    hot = analyze(station(set_bar=7.0, supply_bar=7.0, valve="SMC SY5120-5LZ-01"))
    assert one(hot, "pressure ratings downstream of REG").severity == "pass"
    c = station()
    c.get("REG").set_bar = 9.0
    bad = one(analyze(c), "pressure ratings downstream of REG")
    assert bad.severity == "fail" and "V1" in bad.refs and any(r.startswith("T") for r in bad.refs)   # TU0604 is 8 bar


def test_speed_range_and_end_of_stroke_energy():
    assert one(analyze(station(speed=600)), "piston speed range C1 extend").severity == "fail"
    assert one(analyze(station(speed=30)), "piston speed range C1 extend").severity == "warn"
    # no speed controllers: the stroke runs as fast as the open paths allow, so a heavy load overloads the rubber bumper
    rep = analyze(station(fc=False, mass=8.0))
    e = one(rep, "end-of-stroke energy C1 extend")
    assert e.severity == "fail" and "no speed controller" in e.measured
    assert one(analyze(station(fc=True, mass=8.0)), "end-of-stroke energy C1 extend").severity == "pass"   # 1/2 x 8 x 0.1^2 = 0.04 J
    off = one(analyze(station(mass=None)), "end-of-stroke energy C1 extend")
    assert off.severity == "warn" and off.not_run == ["C1.load_mass_kg"]


def test_sensors_and_coil_voltage():
    c = station(cyl="SMC CQ2B32-20DZ")                                            # no magnet
    c.sensor("B1", "C1", "extended", part="SMC D-M9B")
    assert one(analyze(c), "sensor B1 target").severity == "fail"
    c = station()
    c.sensor("B1", "C1", "extended", part="SMC D-M9B")
    c.control_voltage(48)
    rep = analyze(c)
    assert one(rep, "sensor B1 voltage").severity == "fail" and one(rep, "coil voltage V1").severity == "fail"
    with pytest.raises(PneumaticsError, match="not a cylinder"):
        c.sensor("B2", "V1", "extended")
    with pytest.raises(PneumaticsError, match="position"):
        c.sensor("B3", "C1", "middle")


def test_io_list():
    c = station()
    c.sensor("B1", "C1", "extended", part="SMC D-M9B", terminal="X1:3")
    c.sensor("B2", "C1", "retracted", part="Festo SMT-8M-A-PS-24V-E-0,3-M8D", signal="C1_HOME")
    rows = outputs.io_rows(c)
    assert [(r["tag"], r["io"], r["signal"]) for r in rows] == [("DI01", "DI", "C1_EXTENDED"), ("DI02", "DI", "C1_HOME"), ("DO01", "DO", "V1_14")]
    do = rows[-1]
    assert do["voltage_V"] == 24 and do["current_mA"] == pytest.approx(16.7) and "L plug" in do["connector"]
    assert rows[0]["terminal"] == "X1:3" and "leakage" in rows[0]["notes"] and rows[1]["current_mA"] == "<= 100"
    table = list(csv.DictReader(io.StringIO(outputs.io_csv(rows))))
    assert table[0]["signal"] == "C1_EXTENDED" and set(table[0]) >= {"tag", "io", "voltage_V", "current_mA", "connector", "terminal"}
    md = outputs.io_markdown(c, rows)
    assert "Control voltage: 24 V DC" in md and "17 mA" in md
    bom = outputs.bom(c)
    assert any(r["category"] == "sensor" and r["part_number"] == "SMC D-M9B" for r in bom)


def test_clamp_fixture_example_builds_green(tmp_path):
    from eitri.model import load_model
    lm = load_model(EXAMPLE)
    c = lm.build()
    rep = analyze(c, lm.verify(c))
    assert rep.ok, summarize(rep)
    assert [w.name for w in rep.warnings] == ["flow data estimated"], summarize(rep)
    assert len(outputs.io_rows(c)) == 7


def test_example_writes_the_io_list(tmp_path):
    from eitri.pneumatics.build import write_outputs
    from eitri.model import load_model
    c = load_model(EXAMPLE).build()
    paths, _ = write_outputs(c, analyze(c), tmp_path, "fixture", render=False)
    assert Path(paths["io_csv"]).read_text().startswith("tag,io,signal") and "CLAMP_CLOSE" in Path(paths["io_md"]).read_text()
