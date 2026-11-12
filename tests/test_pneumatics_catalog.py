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
    c.tube("T2", reg["OUT"], v["1"], part=tube, length_m=0.5)
    if fc:
        f1, f2 = c.flow_control("FC1", part="SMC AS2201F-01-06"), c.flow_control("FC2", part="SMC AS2201F-01-06")
        c.tube("T3", v["2"], f1["A"], part=tube, length_m=1)
        c.tube("T4", f1["B"], cy["ROD"], part=tube, length_m=0.05)
        c.tube("T5", v["4"], f2["A"], part=tube, length_m=1)
        c.tube("T6", f2["B"], cy["CAP"], part=tube, length_m=0.05)
    else:
        c.tube("T3", v["2"], cy["ROD"], part=tube, length_m=1)
        c.tube("T5", v["4"], cy["CAP"], part=tube, length_m=1)
    c.silencer(v["3"], part="SMC AN05-M5")
    c.silencer(v["5"], part="SMC AN05-M5")
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
    assert one(analyze(station(fc=True, mass=8.0)), "end-of-stroke energy C1 extend").severity == "pass"   # 1/2 x 8 x (2 x 0.1)^2 = 0.16 J
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


# ---------------------------------------------------------------------------- item 1: correctness


def test_end_of_stroke_energy_uses_twice_the_mean_speed():
    from eitri.pneumatics.checks import IMPACT_FACTOR
    assert IMPACT_FACTOR == 2.0                                                  # SMC RB_EU p.5 Note 1: collision speed = 2 x average
    rep = analyze(station(fc=True, mass=8.0, speed=100.0))
    e = one(rep, "end-of-stroke energy C1 extend")
    # 1/2 x 8 kg x (2 x 0.1 m/s)^2 = 0.16 J against the CQ2-32 bumper's 0.29 J: pass (0.8 x 0.29 = 0.232)
    assert e.severity == "pass" and "0.160 J" in e.measured and "2 x the mean speed" in e.measured
    # at the mean speed it would have been 0.04 J; at 150 mm/s, 2x gives 0.36 J > 0.29 J: a fail the old rule passed (0.09 J)
    assert one(analyze(station(fc=True, mass=8.0, speed=150.0)), "end-of-stroke energy C1 extend").severity == "fail"


def test_bom_counts_each_fitting_once():
    from eitri.model import load_model
    c = load_model(EXAMPLE).build()
    rows = outputs.bom(c)
    fits = {r["description"]: r for r in rows if r["category"] == "fitting"}
    # the valves sit on the manifold (no P/R/S fittings), their A/B and the AS2201F tube sides are built-in push-in, the AS2201F are
    # screwed into the cylinders: only the manifold's Rc1/8 P (a KQ2H06-01S) and the FRL / supply ends (ports not given) need fittings
    rc = next(r for d, r in fits.items() if "Rc1/8" in d)
    assert rc["qty"] == 1 and rc["part_number"] == "KQ2H06-01S"
    assert sum(r["qty"] for r in fits.values()) == 1 + 3
    base = next(r for r in rows if r["category"] == "manifold" and "base" in r["description"])
    assert base["part_number"] == "SMC SS5Y3-20-02-Q" and not any("blanking" in r["description"] for r in rows)   # 2 valves, 2 stations
    tube = next(r for r in rows if r["category"] == "tube" and r["part_number"] == "SMC TU0604")
    assert "6 mm OD" in tube["description"] and tube["qty"] == pytest.approx((0.5 + 1.2 + 1.2 + 1.0 + 1.0) * 1.1, abs=0.01)


def _two_valve_circuit(cls="5/2", returns="spring", center="closed", acting="double", **cyl):
    c = Circuit("fs")
    air = c.supply("AIR", pressure_bar=6, flow_l_min=100)
    v = c.valve("V1", cls, returns=returns, center=center, C=1, b=0.3)
    cy = c.cylinder("C1", 32, 12, 50, acting=acting, spring_N=40 if acting == "single" else None, **cyl)
    c.tube("T1", air["OUT"], v["1"])
    c.tube("T2", v["2"], cy["CAP"])
    if acting == "double":
        c.tube("T3", v["4"], cy["ROD"])
        c.silencer(v["5"])
    c.silencer(v["3"])
    return c


def test_failsafe_states_per_valve_function():
    from eitri.pneumatics import failsafe
    cases = [  # (valve, cylinder, power loss, air loss)
        (dict(cls="5/2", returns="spring"), {}, "extend", "release"),         # rest: P-A, A feeds CAP
        (dict(cls="5/2", returns="solenoid"), {}, "hold", "release"),         # double solenoid keeps its last position
        (dict(cls="5/3", center="closed"), {}, "trapped", "trapped"),
        (dict(cls="5/3", center="exhaust"), {}, "release", "release"),
        (dict(cls="5/3", center="pressure"), {}, "extend", "release"),
        (dict(cls="3/2", returns="spring"), dict(acting="single"), "retract", "retract"),
    ]
    for v, cyl, power, air in cases:
        c = _two_valve_circuit(**v, **cyl)
        assert failsafe.outcome(c, c.get("C1"), "power")[0] == power, (v, cyl)
        assert failsafe.outcome(c, c.get("C1"), "air")[0] == air, (v, cyl)


def test_failsafe_check_grades_the_declaration():
    rep = analyze(_two_valve_circuit())
    off = one(rep, "on loss of electrical power C1")
    assert off.severity == "warn" and off.not_run == ["C1.on_power_loss"] and "they give: extend" in off.measured
    c = _two_valve_circuit(on_power_loss="hold", on_air_loss="release")
    rep = analyze(c)
    assert one(rep, "on loss of electrical power C1").severity == "fail"          # single solenoid spring return extends
    assert one(rep, "on loss of air C1").severity == "pass"
    ok = analyze(_two_valve_circuit(returns="solenoid", on_power_loss="hold", on_air_loss="release"))
    assert one(ok, "on loss of electrical power C1").severity == "pass"
    trapped = analyze(_two_valve_circuit(cls="5/3", on_power_loss="hold", on_air_loss="hold"))
    assert one(trapped, "on loss of electrical power C1").severity == "warn" and "may not hold" in one(trapped, "on loss of air C1").measured
    with pytest.raises(PneumaticsError, match="on_power_loss"):
        _two_valve_circuit(on_power_loss="stop")


# ---------------------------------------------------------------------------- item 2: the customer drawing


def test_device_tags_follow_the_festo_en81346_codes():
    from eitri.model import load_model
    from eitri.pneumatics.drawing import tags
    c = load_model(EXAMPLE).build()
    tg = tags(c)
    assert tg["C1"] == "-MM1" and tg["C2"] == "-MM2" and tg["V1"] == "-QM1" and tg["V2"] == "-QM2"
    assert tg["FRL"] == "-AZ1" and tg["M1"] == "-XM1" and {tg[k] for k in ("FC1A", "FC1B")} == {"-RZ1", "-RZ2"}
    assert [tg[s] for s in ("B1", "B2", "B3", "B4")] == ["-BG1", "-BG2", "-BG3", "-BG4"]      # retracted before extended
    assert tg["S_M1_3"] == "-XM1:3"
    every = [n for n, comp in c.components.items() if comp.kind != "supply"] + list(c.sensors)
    assert all(n in tg for n in every) and len(set(tg.values())) == len(tg)


def test_drawing_svg_and_pdf(tmp_path):
    from eitri.model import load_model
    from eitri.pneumatics.drawing import drawing_scene, write_drawing
    from eitri.diagram import scene_to_svg
    lm = load_model(EXAMPLE)
    c = lm.build()
    rep = analyze(c, lm.verify(c))
    svg = scene_to_svg(drawing_scene(c, rep, today="2026-11-12"))
    for s in ("-MM1  C1", "-BG2 PART_AT_STOP", "14: PUSHER_EXTEND", "12: CLAMP_OPEN", "Ticket", "T-0001", "Revision", "Approved by",
              "Drawn by", "eitri ", "SMC CDQ2B32-20DZ", "2026-11-12", "EN 81346-2"):
        assert s in svg, s
    paths = write_drawing(c, rep, tmp_path, "fixture")
    pdf = Path(paths["drawing_pdf"]).read_bytes()
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF") and b"(Approved by) Tj" in pdf and b"/Helvetica-Bold" in pdf
    xref = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
    assert pdf[xref:xref + 4] == b"xref"


# ---------------------------------------------------------------------------- 0.8.1 item 2: valve manifolds (SMC SS5Y)


def manifold_station(stations=4, valves=2, sup_id=6.0, sup_len=0.5, speed=200.0, bore_part="SMC CDQ2B32-20DZ", seq=None):
    c = Circuit("manifold station")
    c.control_voltage(24)
    air = c.supply("AIR", pressure_bar=7.0, flow_l_min=500)
    reg = c.regulator("REG", set_bar=6.0, C=2.0, b=0.35)
    m = c.manifold("M1", part=f"SMC SS5Y3-20-{stations:02d}-Q")
    c.tube("T0", air["OUT"], reg["IN"], id_mm=6, length_m=1)
    c.tube("T1", reg["OUT"], m["1"], id_mm=sup_id, length_m=sup_len)
    c.silencer(m["3"], part="SMC AN10-01")
    c.silencer(m["5"], part="SMC AN10-01")
    for i in range(1, valves + 1):
        v = c.valve(f"V{i}", part="SMC SY3120-5LZ-C6", signal=f"Y{i}_EXTEND")
        c.mount(v, m)
        cy = c.cylinder(f"C{i}", part=bore_part, load_N=100, speed_mm_s=speed, cycles_per_min=6, duty="static", load_mass_kg=0.1,
                        on_power_loss="retract", on_air_loss="release")         # a light pad: unthrottled, 1/2 x 0.1 x 1.4^2 < 0.29 J
        c.tube(f"TA{i}", v["2"], cy["ROD"], part="SMC TU0604", length_m=1)
        c.tube(f"TB{i}", v["4"], cy["CAP"], part="SMC TU0604", length_m=1)
    return c


def test_manifold_part_and_mount():
    c = manifold_station()
    m = c.get("M1")
    assert (m.stations, m.series, m.C, m.b, m.C_exhaust, m.b_exhaust, m.blanking_plate) == (4, "SY3000", 0.72, 0.29, 0.80, 0.36, "SMC SY3000-26-19A-Q")
    v = c.get("V1")
    assert (v.manifold, v.station) == ("M1", 1) and (v.C, v.b, v.C_exhaust) == (0.72, 0.29, 0.80)   # the valve-on-manifold figures
    assert c.get("V2").station == 2
    with pytest.raises(PneumaticsError, match="SY5000"):
        c.mount(c.valve("V9", part="SMC SY5120-5LZ-01"), m)
    with pytest.raises(PneumaticsError, match="fed by manifold"):
        c.tube("TX", c.get("V1")["P"], c.get("REG")["OUT"])
    with pytest.raises(PneumaticsError, match="station 1 is taken"):
        c.mount(c.valve("V8", part="SMC SY3120-5LZ-C6"), m, station=1)
    with pytest.raises(catalog.CatalogError, match="stations"):
        catalog.lookup("manifold", "SS5Y3-20-21")
    assert catalog.lookup("manifold", "SS5Y3-20-0500F")[0]["connections"]["1"] == "G1/8"


def test_manifold_circuit_feeds_and_exhausts_through_the_gallery():
    rep = analyze(manifold_station())
    assert rep.ok, summarize(rep)
    fed = one(rep, "C1.CAP fed from supply")
    assert fed.severity == "pass" and "T1 -> V1" in fed.measured           # T1 feeds the manifold's P; the gallery feeds V1 (no step of its own:
                                                                             # the manifold's C, b replace the valve's)
    ex = one(rep, "C1.ROD can exhaust")
    assert "S_M1_3" in ex.measured or "S_M1_5" in ex.measured
    assert one(rep, "pressure ratings downstream of REG").severity == "pass" and "V" in one(rep, "pressure ratings downstream of REG").measured


def test_manifold_stations_check():
    ok = one(analyze(manifold_station(stations=4, valves=2)), "manifold M1 stations")
    assert ok.severity == "pass" and "2 empty station(s) take a blanking plate" in ok.measured
    c = manifold_station(stations=2, valves=2)
    c.mount(c.valve("V3", part="SMC SY3120-5LZ-C6"), "M1")
    bad = one(analyze(c), "manifold M1 stations")
    assert bad.severity == "fail" and "3 valve(s) on a 2-station base" in bad.measured
    big = one(analyze(manifold_station(stations=12, valves=2)), "manifold M1 stations")
    assert big.severity == "warn" and "both sides" in big.measured                   # SY_EU p.48 Note 1
    rows = outputs.bom(manifold_station(stations=4, valves=2))
    blank = next(r for r in rows if "blanking plate" in r["description"])
    assert blank["qty"] == 2 and blank["part_number"] == "SMC SY3000-26-19A-Q"
    assert next(r for r in rows if r["category"] == "manifold" and "base" in r["description"])["part_number"] == "SMC SS5Y3-20-04-Q"


def test_manifold_common_supply_vs_simultaneous_demand():
    # all cylinders at once (no sequence): 2 x CQ2-32 at 200 mm/s through 0.5 m of 6 mm bore passes
    good = one(analyze(manifold_station(valves=2)), "manifold M1 common supply")
    assert good.severity == "pass" and "all cylinders at once" in good.measured
    # six cylinders at 500 mm/s through 4 m of 2.5 mm bore: the common supply chokes
    bad = one(analyze(manifold_station(stations=6, valves=6, sup_id=2.5, sup_len=4.0, speed=500.0)), "manifold M1 common supply")
    assert bad.severity == "fail" and "more than the supply path" in bad.measured
    for name in ("manifold M1 stations", "manifold M1 common supply"):
        assert one(analyze(manifold_station()), name).rule.startswith("pneumatics.manifold")


def test_manifold_coils_in_the_io_list():
    c = manifold_station(stations=4, valves=3)
    rows = [r for r in outputs.io_rows(c) if r["io"] == "DO"]
    assert [(r["signal"], r["location"]) for r in rows] == [("Y1_EXTEND", "M1 station 1"), ("Y2_EXTEND", "M1 station 2"), ("Y3_EXTEND", "M1 station 3")]
    assert "location" in outputs.io_csv(outputs.io_rows(c)).splitlines()[0]


def test_manifold_drawing_and_tags(tmp_path):
    from eitri.pneumatics.drawing import tags, write_drawing
    c = manifold_station(valves=2)
    tg = tags(c)
    assert tg["M1"].startswith("-XM") and tg["S_M1_3"] == tg["M1"] + ":3"
    paths = write_drawing(c, analyze(c), tmp_path, "m")
    svg = Path(paths["drawing_svg"]).read_text()
    assert "SS5Y3-20-04-Q" in svg and "manifold" in svg


# ---------------------------------------------------------------------------- 0.8.1 item 3: sequence and cycle time


def fixture_with(steps):
    from eitri.model import load_model
    c = load_model(EXAMPLE).build()
    c.sequence.clear()
    for args, kw in steps:
        c.step(*args, **kw)
    return c


GOOD = [(("push", "C1+"), dict(until="B2")), (("clamp", "C2+"), dict(until="B4")), (("pusher back", "C1-"), dict(until="B1")),
        (("machine",), dict(dwell_s=8.0)), (("unclamp", "C2-"), dict(until="B3"))]


def test_sequence_plan_commands_and_estimate():
    from eitri.pneumatics import sequence as S
    c = fixture_with(GOOD)
    rep = analyze(c)
    assert all(ch.severity == "pass" for ch in rep.checks if ch.category in ("sequence", "cycle time")), summarize(rep)
    p = S.plan(c, rep.cylinders)
    s1, s2, s3, _s4, s5 = p.steps
    assert s1.signals["PUSHER_EXTEND"] == "on" and s2.signals["PUSHER_EXTEND"] == "on" and s3.signals["PUSHER_EXTEND"] == "off"
    assert s2.signals["CLAMP_CLOSE"] == "pulse" and s5.signals["CLAMP_OPEN"] == "pulse" and s3.signals["CLAMP_CLOSE"] == "off"
    # push: 50 mm at the set 150 mm/s + SY3120-5LZ 15 ms (Z type) + SMT-8M-A 1.3 ms
    assert s1.time_s == pytest.approx(50 / 150 + 0.015 + 0.0013, abs=1e-4)
    # clamp: 20 mm at 80 mm/s + SY3220 13 ms + D-M9B (no published time: 1.3 ms assumed)
    assert s2.time_s == pytest.approx(20 / 80 + 0.013 + 0.0013, abs=1e-4)
    assert p.cycle_s == pytest.approx(sum(sp.time_s for sp in p.steps)) and 9.0 < p.cycle_s < 9.5
    ct = one(rep, "cycle time estimate")
    assert "ESTIMATE" in ct.measured and "lower bound" in ct.measured and "15.00 s" in ct.measured


def test_sequence_step_must_end_on_a_real_sensor_at_the_right_end():
    rep = analyze(fixture_with([(("push", "C1+"), dict(until="B9"))]))
    assert one(rep, "step 1 push ends on a sensor").severity == "fail" and "not a sensor" in one(rep, "step 1 push ends on a sensor").measured
    rep = analyze(fixture_with([(("push", "C1+"), dict(until="B1"))]))                      # B1 is at C1's retracted end
    assert one(rep, "step 1 push ends on a sensor").severity == "fail"
    rep = analyze(fixture_with([(("push", "C1+"), {})]))
    assert one(rep, "step 1 push ends on a sensor").severity == "fail" and "no sensor" in one(rep, "step 1 push ends on a sensor").measured
    rep = analyze(fixture_with([(("wait",), {})]))
    assert one(rep, "step 1 wait ends on a sensor").severity == "fail"
    rep = analyze(fixture_with([(("both", "C1+", "C2+"), dict(until="B2"))]))               # C2's move is not confirmed
    assert one(rep, "step 1 both ends on a sensor").severity == "warn"


def test_sequence_valve_cannot_give_the_state():
    from eitri.pneumatics import PneumaticsError
    rep = analyze(fixture_with([(("bad", "C1+", "C1-"), dict(until="B2"))]))                 # one valve, two positions in one step
    assert one(rep, "step 1 bad valve commands").severity == "fail"
    c = fixture_with([(("push", "C1+"), dict(until="B2"))])
    c.get("V1").actuation = "manual"                                                       # a hand valve: the controller cannot switch it
    assert "cannot switch" in one(analyze(c), "step 1 push valve commands").measured
    rep = analyze(fixture_with([(("again", "C1-"), dict(until="B1"))]))                      # already retracted
    assert one(rep, "step 1 again valve commands").severity == "warn"
    assert one(rep, "step 1 again ends on a sensor").severity == "pass"
    open_end = analyze(fixture_with([(("push", "C1+"), dict(until="B2"))]))
    assert one(open_end, "sequence returns to its start").severity == "warn"
    with pytest.raises(PneumaticsError, match="not a cylinder"):
        fixture_with([(("x", "V1+"), {})])


def test_sequence_not_declared_is_not_run_and_drives_the_manifold_demand():
    from eitri.checks import status_of
    from eitri.pneumatics.checks import simultaneous_groups
    rep = analyze(fixture_with([]))
    nr = one(rep, "sequence declared")
    assert status_of(nr) == "not_run" and nr.not_run == ["c.step"] and nr.rule == "pneumatics.sequence-declared"
    c = manifold_station(valves=2)
    assert simultaneous_groups(c)[0] == [{"C1", "C2"}]
    c.step("one", "C1+")
    c.step("two", "C2+")
    assert simultaneous_groups(c)[0] == [{"C1"}, {"C2"}]
    assert "per step" in one(analyze(c), "manifold M1 common supply").measured


def test_sequence_outputs(tmp_path):
    from eitri.pneumatics import sequence as S
    c = fixture_with(GOOD)
    paths = S.write_sequence(c, S.plan(c, analyze(c).cylinders), tmp_path, "f")
    md = Path(paths["sequence_md"]).read_text()
    assert "Estimated cycle time" in md and "ESTIMATE" in md and "| 2 clamp | C2+ |" in md and "Assumptions:" in md
    svg = Path(paths["sequence_svg"]).read_text()
    assert "Displacement-step diagram" in svg and "CLAMP_CLOSE" in svg
    assert Path(paths["sequence_pdf"]).read_bytes().startswith(b"%PDF")


def test_new_0_8_1_checks_carry_rules_sources_and_status():
    from eitri.check_sources import KINDS
    from eitri.checks import STATUSES, status_of
    c = manifold_station(valves=2)
    c.step("one", "C1+")                                   # no until: a failing step check, still sourced
    reps = [analyze(c), analyze(fixture_with(GOOD)), analyze(fixture_with([]))]
    seen = set()
    for rep in reps:
        for ch in rep.checks:
            if ch.category in ("manifold", "sequence", "cycle time"):
                seen.add(ch.rule)
                assert ch.rule and ch.kind in KINDS and ch.source and status_of(ch) in STATUSES, ch.name
                assert all(s.basis and s.text for s in ch.source), ch.name
    assert {"pneumatics.manifold-stations", "pneumatics.manifold-supply", "pneumatics.step-ends-on-sensor", "pneumatics.step-valve-commands",
            "pneumatics.sequence-closes", "pneumatics.cycle-time", "pneumatics.sequence-declared"} <= seen
    ct = next(ch for ch in reps[1].checks if ch.name == "cycle time estimate")
    assert any(s.basis == "document" and "SY_EU.pdf" in s.url for s in ct.source) and any(s.basis == "assumption" for s in ct.source)


# ---------------------------------------------------------------------------- 0.8.1 item 5: more part numbers


def _example_number(s):
    st = s.strokes[0] if s.strokes else 50
    return (s.pattern.replace("(?:FESTO\\s+)?", "").replace("(?:SMC\\s+)?", "").replace("(?P<magnet>D?)", "D")
            .replace("(?P<stroke>\\d+)", str(st)))


def test_every_catalogue_cylinder_force_matches_its_datasheet():
    cyls = [s for s in catalog.SERIES if s.kind == "cylinder"]
    assert len(cyls) >= 7 + 10 + 6 + 3                                   # DSNU 16-63, CQ2 12-100, DSBC 32-100, MGPM 20-32
    for s in cyls:
        c = Circuit()
        cy = c.cylinder("C", part=_example_number(s))
        for key, (want, bar) in s.figures.items():
            got = cylinder_forces(cy, bar)["extend_theoretical_N" if "extend" in key else "retract_theoretical_N"]
            assert got == pytest.approx(want, rel=0.01, abs=0.5), (s.pattern, key)
        assert all(sid in catalog.SOURCES for sid in s.sources)


def test_new_families_fill_their_figures():
    c = Circuit()
    d = c.cylinder("D", part="FESTO DSNU-40-100-P-A")
    assert (d.bore_mm, d.rod_mm, d.allowable_ke_J, d.connections["CAP"]) == (40, 16, 0.7, "G1/4") and d.piston_mass_kg == pytest.approx(0.230 + 0.16)
    q = c.cylinder("Q", part="SMC CDQ2B63-50DZ")
    assert (q.rod_mm, q.allowable_ke_J, q.min_bar, q.connections["CAP"]) == (20, 1.54, 0.5, "Rc1/4")
    assert c.cylinder("Q2", part="SMC CQ2B12-10DZ").min_bar == pytest.approx(0.7)       # 0.07 MPa for dia 12
    i = c.cylinder("I", part="FESTO DSBC-50-200-PPVA-N3")
    assert (i.rod_mm, i.min_bar, i.max_bar, i.allowable_ke_J) == (20, 0.4, 12.0, 1.0)
    g = c.cylinder("G", part="SMC MGPM25-50Z")
    assert (g.rod_mm, g.magnet, g.allowable_ke_J, g.catalog["info"]["guided"]) == (10, True, None, True)
    with pytest.raises(PneumaticsError, match="standard stroke"):
        c.cylinder("G2", part="SMC MGPM32-30Z")
    with pytest.raises(PneumaticsError, match="outside"):
        c.cylinder("D2", part="DSNU-16-250-P-A")
    assert c.flow_control("F4", part="SMC AS1201F-M5-04").C == 0.3 and c.flow_control("F8", part="SMC AS2201F-02-08").C == 1.4
    t = Circuit()
    a, b = t.node("A"), t.node("B")
    assert t.tube("T", a, b, part="SMC TU0805").id_mm == 5.0 and t.tube("T4", a, b, part="SMC TU0425").od_mm == 4.0


def test_regulator_setting_against_its_regulation_range():
    c = station()
    c.components.pop("REG")
    c2 = Circuit("r")
    air = c2.supply("AIR", 7.0, flow_l_min=100)
    f = c2.frl("FRL", 6.0, part="FESTO MS4-LFR-1/4-D6-CRM-AS")
    c2.tube("T", air["OUT"], f["IN"])
    assert f.max_bar == 14.0 and one(analyze(c2), "regulator FRL setting").severity == "pass"
    f.set_bar = 8.0                                                        # MS4-LFR-...-D6: regulates 0.3-7 bar
    bad = one(analyze(c2), "regulator FRL setting")
    assert bad.severity == "fail" and "0.3-7 bar" in bad.measured and bad.rule == "pneumatics.regulator-setting"


# ---------------------------------------------------------------------------- ISO 5599 port numbers (SMC SY_EU.pdf PDF p.14; Festo VUVG p.12)


def _plumbed(valve_kw, cap_port, part=None):
    """One cylinder on one valve: valve port `cap_port` to the cap end, the other working port to the rod end."""
    c = Circuit("ports")
    air = c.supply("AIR", pressure_bar=6, flow_l_min=100)
    v = c.valve("V1", part=part, **valve_kw) if part else c.valve("V1", C=1, b=0.3, **valve_kw)
    cy = c.cylinder("C1", 32, 12, 50)
    rod_port = {"4": "2", "2": "4", "A": "B", "B": "A"}[cap_port]
    c.tube("T1", air["OUT"], v["1"])
    c.tube("T2", v[cap_port], cy["CAP"])
    c.tube("T3", v[rod_port], cy["ROD"])
    c.silencer(v["3"])
    c.silencer(v["5"])
    return c


def test_rest_position_is_1_to_2_and_coil_14_is_1_to_4():
    # the regression: a spring-return 5/2 rests with 1-2 and 4-5 (3 blocked); coil 14 gives 1-4 and 2-3
    for v in (Circuit().valve("V", "5/2"), Circuit().valve("V", part="SMC SY3120-5LZ-C6"), Circuit().valve("V", part="SMC SY5120-5LZ-01")):
        assert sorted(v.positions[v.rest_position]) == [("1", "2"), ("4", "5")], v.part_number
        assert sorted(v.positions[1 - v.rest_position]) == [("1", "4"), ("2", "3")], v.part_number
        assert v.coils[0][0] == "14"
    five3 = {c: Circuit().valve("V", "5/3", center=c) for c in ("closed", "exhaust", "pressure")}
    assert five3["closed"].positions[1] == [] and sorted(five3["exhaust"].positions[1]) == [("2", "3"), ("4", "5")]
    assert sorted(five3["pressure"].positions[1]) == [("1", "2"), ("1", "4")]
    assert sorted(five3["closed"].positions[-1]) == [("1", "4"), ("2", "3")] and sorted(five3["closed"].positions[0]) == [("1", "2"), ("4", "5")]


def test_smc_letters_are_smcs_and_generic_letters_are_refused():
    v = Circuit().valve("V", part="SMC SY3120-5LZ-C6")
    assert (v["A"].port, v["B"].port, v["P"].port, v["EA"].port, v["EB"].port) == ("4", "2", "1", "5", "3")   # SY_EU p.14 "4 (A) 2 (B)"
    with pytest.raises(PneumaticsError, match="ambiguous"):
        v["R"]
    with pytest.raises(PneumaticsError, match="SMC: A = 4"):
        Circuit().valve("G")["A"]
    m = Circuit().manifold("M", part="SMC SS5Y3-20-02-Q")
    assert (m["P"].port, m["EA"].port, m["EB"].port) == ("1", "5", "3")


CASES = [  # valve, port on the cap end, (state on loss of power, on loss of air)
    (dict(cls="5/2", returns="spring"), "4", ("retract", "release")),       # rest 1-2: the rod end is fed
    (dict(cls="5/2", returns="spring"), "2", ("extend", "release")),
    (dict(cls="5/2", returns="solenoid"), "4", ("hold", "release")),
    (dict(cls="5/2", returns="solenoid"), "2", ("hold", "release")),
    (dict(cls="5/3", center="closed"), "4", ("trapped", "trapped")),
    (dict(cls="5/3", center="closed"), "2", ("trapped", "trapped")),
    (dict(cls="5/3", center="exhaust"), "4", ("release", "release")),
    (dict(cls="5/3", center="exhaust"), "2", ("release", "release")),
    (dict(cls="5/3", center="pressure"), "4", ("extend", "release")),       # both ends fed: the larger cap area wins
    (dict(cls="5/3", center="pressure"), "2", ("extend", "release")),
]


@pytest.mark.parametrize("valve,cap,want", CASES, ids=[f"{v.get('cls')}-{v.get('returns', v.get('center'))}-{cap}cap" for v, cap, _w in CASES])
def test_failsafe_by_valve_type_and_plumbing(valve, cap, want):
    from eitri.pneumatics import failsafe
    c = _plumbed(valve, cap)
    assert (failsafe.outcome(c, c.get("C1"), "power")[0], failsafe.outcome(c, c.get("C1"), "air")[0]) == want


def test_smc_clamp_plumbed_by_letter_releases_on_power_loss():
    # SY3120 with SMC's port A (= 4) on the cap end: at rest 1-2 feeds B (= 2), the rod end: the clamp OPENS on a power cut.
    # Declaring "extend" (stays closed) must fail; plumbing B to the cap end passes.
    c = _plumbed({}, "A", part="SMC SY3120-5LZ-C6")
    c.get("C1").on_power_loss, c.get("C1").on_air_loss = "extend", "release"
    assert one(analyze(c), "on loss of electrical power C1").severity == "fail"
    ok = _plumbed({}, "B", part="SMC SY3120-5LZ-C6")
    ok.get("C1").on_power_loss, ok.get("C1").on_air_loss = "extend", "release"
    assert one(analyze(ok), "on loss of electrical power C1").severity == "pass"


def test_io_list_and_drawing_name_the_ports():
    from eitri.pneumatics.diagram import _ports, display_index, port_label
    c = _plumbed({}, "4", part="SMC SY3120-5LZ-C6")
    c.get("V1").signal = "C1_EXTEND"
    do = next(r for r in outputs.io_rows(c) if r["io"] == "DO")
    assert do["signal"] == "C1_EXTEND" and "connects 1-4, 2-3" in do["function"] and "pressurises 4 (C1 CAP)" in do["function"]
    v = c.get("V1")
    assert port_label(v, "4") == "4(A)" and port_label(v, "2") == "2(B)" and port_label(Circuit().valve("G"), "4") == "4"
    assert display_index(v, v.rest_position) == 1                         # the rest box is drawn on the spring side (right)
    pts = _ports(v, 0.0, 0.0)
    assert pts["4"][0] < pts["2"][0] and pts["5"][0] < pts["1"][0] < pts["3"][0]   # 4 left of 2, 5-1-3 as the makers draw them
