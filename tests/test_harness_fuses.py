"""Overcurrent protection in the harness domain: fuses, PTCs and breakers (eitri.harness.protection, eitri.harness.fuses).

Every check has a passing and a failing harness; every input that can be left unset shows as "not run" (never a silent pass).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eitri.harness import Breaker, Connector, Fuse, Harness, HarnessError, Wire
from eitri.harness.fuses import FUSES, MFR_PARTS, PLACEMENT_MAX_MM, resolve, suggest
from eitri.harness.report import analyze, summarize

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "eitri" / "demos" / "servo-arm" / "servo_arm_harness.py"


def rail(fuse=("ATO", 5), lead_mm=80, load_a=2.0, gauge=22, volts=12.0, isc=500.0, limit=None, ambient=25.0, src=True,
         fuse_kw=None):
    """A battery -> (fuse) -> load rail with its return; fuse=None leaves the fuse out."""
    h = Harness("rail", ambient_C=ambient)
    bat = h.add(Connector("BAT", "BARE", pins=["+", "-"]))
    ld = h.add(Connector("LOAD", "BARE", pins=["+", "-"]))
    h.net("VB", current_A=load_a, voltage_V=volts)
    h.net("GND", current_A=load_a, voltage_V=volts, round_trip=False)
    if src:
        h.source(bat["+"], volts, max_current_A=limit, short_circuit_A=isc, kind="battery")
    if fuse is None:
        h.connect(bat["+"], ld["+"], Wire(gauge, "RD", 500), net="VB")
    else:
        f = h.add(Fuse("F1", *fuse, **(fuse_kw or {})))
        h.connect(bat["+"], f["LINE"], Wire(gauge, "RD", lead_mm), net="VB")
        h.connect(f["LOAD"], ld["+"], Wire(gauge, "RD", 500), net="VB")
    h.connect(ld["-"], bat["-"], Wire(gauge, "BK", 500), net="GND")
    return h


def checks(h):
    rep = analyze(h)
    return rep, {c.name: c for c in rep.checks if c.category == "protection"}


def sev(h, name):
    _rep, cs = checks(h)
    return cs[name].severity


# --------------------------------------------------------------------------- 1. every branch from a source is protected


def test_unfused_battery_branch_fails():
    rep, cs = checks(rail(fuse=None))
    c = cs["overcurrent protection from BAT.+"]
    assert c.severity == "fail" and "no protective device" in c.measured and "Fuse(" in c.expected
    assert not rep.ok


def test_fused_branch_passes():
    rep, cs = checks(rail())
    assert cs["overcurrent protection from BAT.+"].severity == "pass"
    assert rep.ok, summarize(rep)


def test_unknown_source_capability_is_treated_as_unlimited():
    rep, cs = checks(rail(fuse=None, isc=None, limit=None))
    c = cs["overcurrent protection from BAT.+"]
    assert c.severity == "fail" and "more than any wire can carry" in c.measured


def test_current_limited_supply_below_ampacity_needs_no_fuse():
    rep, cs = checks(rail(fuse=None, isc=None, limit=3.0))       # 3 A limit, 22 AWG 7 A
    assert cs["overcurrent protection from BAT.+"].severity == "pass"


def test_fuse_bypassed_by_a_parallel_wire_fails():
    h = rail()
    h.connect(h.connectors["BAT"]["+"], h.connectors["LOAD"]["+"], Wire(22, "RD", 500), net="VB")     # around the fuse
    _rep, cs = checks(h)
    assert cs["overcurrent protection from BAT.+"].severity == "fail"


# --------------------------------------------------------------------------- 2. rating within the wire's ampacity


def test_fuse_rating_above_wire_ampacity_fails():
    _rep, cs = checks(rail(fuse=("ATO", 10)))                      # 10 A on 22 AWG (7 A)
    c = cs["F1 rating within the wire it protects"]
    assert c.severity == "fail" and "7 A" in c.measured and 'Fuse(\'F1\', "ATO", 5)' in c.expected     # 7.5 A is above 7 A


def test_fuse_rating_within_wire_ampacity_passes():
    assert sev(rail(fuse=("ATO", 5)), "F1 rating within the wire it protects") == "pass"


def test_ptc_is_graded_on_its_trip_current():
    assert sev(rail(fuse=("MF-R300",), load_a=1.0), "F1 rating within the wire it protects") == "pass"       # trip 6 A <= 7 A
    assert sev(rail(fuse=("MF-R400",), load_a=1.0), "F1 rating within the wire it protects") == "fail"       # trip 8 A > 7 A


# --------------------------------------------------------------------------- 3. continuous load within the derating


def test_steady_load_over_the_derating_fails():
    rep, cs = checks(rail(fuse=("ATO", 5), load_a=4.5))
    c = cs["F1 continuous load"]
    assert c.severity == "fail" and "x 5 A" in c.measured


def test_steady_load_within_the_derating_passes():
    assert sev(rail(fuse=("ATO", 5), load_a=2.0), "F1 continuous load") == "pass"


def test_hot_ambient_derates_further():
    cool = sev(rail(fuse=("MF-R110",), load_a=0.95, ambient=23), "F1 continuous load")
    hot = sev(rail(fuse=("MF-R110",), load_a=0.95, ambient=70), "F1 continuous load")        # Ihold 0.67 A at 70 C
    assert (cool, hot) == ("pass", "fail")


def test_default_ambient_is_an_assumption_said_in_the_report():
    _rep, cs = checks(rail(ambient=None))
    assert "ASSUMPTION" in cs["F1 continuous load"].measured


# --------------------------------------------------------------------------- 4. voltage rating


def test_voltage_above_the_device_rating_fails():
    _rep, cs = checks(rail(fuse=("MF-R110",), load_a=0.5, volts=48, isc=30))        # MF-R110: 30 V
    assert cs["F1 voltage rating"].severity == "fail"


def test_voltage_within_the_device_rating_passes():
    assert sev(rail(fuse=("ATO", 5), volts=24), "F1 voltage rating") == "pass"


# --------------------------------------------------------------------------- 5. interrupting rating


def test_fault_current_above_interrupting_rating_fails():
    _rep, cs = checks(rail(fuse=("MF-R250",), load_a=1.0, isc=500))              # Imax 40 A
    assert cs["F1 interrupting rating"].severity == "fail"


def test_fault_current_within_interrupting_rating_passes():
    assert sev(rail(fuse=("MF-R250",), load_a=1.0, isc=30), "F1 interrupting rating") == "pass"


def test_no_short_circuit_figure_is_not_run_never_a_pass():
    rep, cs = checks(rail(isc=None, limit=20))
    c = cs["F1 interrupting rating"]
    assert c.severity == "warn" and c.measured.startswith("not run: ") and c.not_run == ["BAT.+.short_circuit_A"]
    assert any(d["name"] == "F1 interrupting rating" for d in rep.to_dict()["checks_not_run"])


# --------------------------------------------------------------------------- 6. placement at the source end


def test_fuse_far_from_the_source_fails():
    _rep, cs = checks(rail(lead_mm=PLACEMENT_MAX_MM + 100))
    c = cs["F1 at the source end"]
    assert c.severity == "fail" and "unprotected" in c.measured


def test_fuse_at_the_source_passes():
    assert sev(rail(lead_mm=50), "F1 at the source end") == "pass"


def test_a_long_lead_rated_for_everything_the_source_can_deliver_passes():
    assert sev(rail(lead_mm=600, isc=None, limit=6.0, load_a=2), "F1 at the source end") == "pass"       # 6 A source, 22 AWG 7 A


def test_branch_fuse_fed_through_a_main_fuse():
    h = Harness("tree", ambient_C=25)
    bat = h.add(Connector("BAT", "BARE", pins=["+", "-"]))
    ld = h.add(Connector("LOAD", "BARE", pins=["+", "-"]))
    h.net("VB", current_A=2, voltage_V=12)
    h.net("GND", current_A=2, voltage_V=12, round_trip=False)
    h.source(bat["+"], 12, short_circuit_A=500, kind="battery")
    main, branch = h.add(Fuse("F1", "ATO", 15)), h.add(Fuse("F2", "MINI", 5))
    h.connect(bat["+"], main["LINE"], Wire(14, "RD", 60), net="VB")
    h.connect(main["LOAD"], branch["LINE"], Wire(14, "RD", 800), net="VB")        # protected by F1 (15 A <= 14 AWG 32 A)
    h.connect(branch["LOAD"], ld["+"], Wire(22, "RD", 300), net="VB")
    h.connect(ld["-"], bat["-"], Wire(22, "BK", 1100), net="GND")
    rep, cs = checks(h)
    assert cs["F2 at the source end"].severity == "pass" and "fed through F1" in cs["F2 at the source end"].measured
    assert cs["F1 rating within the wire it protects"].severity == "pass"
    assert rep.ok, summarize(rep)


# --------------------------------------------------------------------------- scope: DC up to 60 V


def test_ac_source_is_refused():
    h = Harness("mains")
    c = h.add(Connector("MAINS", "BARE", pins=["L", "N"]))
    with pytest.raises(HarnessError, match="out of scope"):
        h.source(c["L"], 230, ac=True)


def test_source_above_60_v_is_refused():
    h = Harness("hv")
    c = h.add(Connector("PACK", "BARE", pins=["+", "-"]))
    with pytest.raises(HarnessError, match="60 V"):
        h.source(c["+"], 72)


def test_net_above_60_v_fails_as_out_of_scope():
    h = rail(volts=48)
    h.net("HV", current_A=1, voltage_V=120)
    x = h.add(Connector("X", "BARE", pins=["1", "2"]))
    h.connect(x["1"], x["2"], Wire(18, "RD", 100), net="HV")
    _rep, cs = checks(h)
    assert cs["in scope: net HV"].severity == "fail"


# --------------------------------------------------------------------------- nothing switched off by a None


def _not_run(h) -> dict[str, list[str]]:
    return {d["name"]: d["inputs"] for d in analyze(h).to_dict()["checks_not_run"] if d["category"] == "protection"}


@pytest.mark.parametrize("make, check, inputs", [
    (lambda: rail(isc=None), "F1 interrupting rating", ["BAT.+.short_circuit_A"]),
    (lambda: rail(fuse=("FUSE", 5), fuse_kw=dict(interrupt_A=1000, continuous_fraction=0.75)), "F1 voltage rating", ["F1.voltage_rating_V"]),
    (lambda: rail(fuse=("FUSE", 5), fuse_kw=dict(voltage_rating_V=32, continuous_fraction=0.75)), "F1 interrupting rating", ["F1.interrupt_A"]),
    (lambda: rail(fuse=("FUSE", 5), fuse_kw=dict(voltage_rating_V=32, interrupt_A=1000)), "F1 continuous load", ["F1.continuous_fraction"]),
    (lambda: rail(fuse=("PTC", 1.1), fuse_kw=dict(voltage_rating_V=30, interrupt_A=40, continuous_fraction=1.0)),
     "F1 rating within the wire it protects", ["F1.trip_A"]),
    (lambda: rail(load_a=None), "F1 continuous load", ["VB.current_A"]),
    (lambda: rail(src=False), "F1 on a branch from a declared source", ["F1.source"]),
    (lambda: rail(fuse=None, src=False), "every supply net fed by a declared source", ["VB.source"]),
])
def test_a_missing_input_shows_as_not_run(make, check, inputs):
    nr = _not_run(make())
    assert nr.get(check) == inputs, nr


def test_voltage_none_on_the_source_is_not_run():
    h = Harness("v", ambient_C=25)
    bat = h.add(Connector("BAT", "BARE", pins=["+", "-"]))
    ld = h.add(Connector("LOAD", "BARE", pins=["+", "-"]))
    h.net("VB", current_A=1)
    h.source(bat["+"], None, short_circuit_A=100)
    f = h.add(Fuse("F1", "ATO", 5))
    h.connect(bat["+"], f["LINE"], Wire(22, "RD", 50), net="VB")
    h.connect(f["LOAD"], ld["+"], Wire(22, "RD", 50), net="VB")
    assert _not_run(h)["F1 voltage rating"] == ["BAT.+.voltage_V"]


def test_every_protection_check_with_a_missing_input_is_a_warning_never_a_pass():
    """Regression: no combination of None inputs yields a protection check that passes while saying "not run"."""
    for h in [rail(isc=None), rail(load_a=None), rail(src=False), rail(fuse=("FUSE", 5)), rail(fuse=("PTC", 1)), rail(fuse=("BREAKER", 5))]:
        for c in analyze(h).checks:
            if c.category == "protection" and c.measured.startswith("not run"):
                assert c.severity == "warn" and c.not_run, c


def test_a_harness_with_no_fuse_and_no_source_does_not_pass_clean():
    rep = analyze(rail(fuse=None, src=False))
    assert rep.warnings and rep.to_dict()["checks_not_run"]


# --------------------------------------------------------------------------- signal nets


def test_signal_net_within_a_logic_pin_passes_and_a_supply_marked_signal_fails():
    def h_(cur):
        h = Harness("sig")
        a, b = h.add(Connector("MCU", "BARE", pins=["D9"])), h.add(Connector("SV", "BARE", pins=["SIG"]))
        h.net("SIG", current_A=cur, voltage_V=5, signal=True)
        h.connect(a["D9"], b["SIG"], Wire(22, "OG", 200), net="SIG")
        return h
    assert sev(h_(0.001), "signal net SIG") == "pass"
    assert sev(h_(1.0), "signal net SIG") == "fail"


# --------------------------------------------------------------------------- the catalog and the API


def test_catalog_parts_refuse_overridden_figures_and_unmade_ratings():
    with pytest.raises(HarnessError, match="made in"):
        Fuse("F1", "ATO", 6)
    with pytest.raises(HarnessError, match="catalog"):
        Fuse("F1", "ATO", 5, interrupt_A=10_000)
    with pytest.raises(HarnessError, match="Bourns"):
        Fuse("F1", "MF-R110", 2)


def test_every_catalog_figure_has_a_source():
    for key, fam in FUSES.items():
        for r in fam.ratings:
            d = resolve(key, r)
            for fig in ("voltage", "interrupt", "continuous", "rating"):
                assert d.sources.get(fig), (key, r, fig)
            assert d.voltage_rating_V and d.interrupt_A and d.continuous_fraction
    for pn in MFR_PARTS:
        d = resolve(pn)
        assert d.trip_A > d.rating_A and d.sources.get("rerate")


def test_suggest_picks_the_largest_rating_within_the_wire():
    assert suggest("ATO", 7.0) == 'Fuse(name, "ATO", 5)' and suggest("ATO", 7.5) == 'Fuse(name, "ATO", 7.5)'
    assert suggest("MF-R", 7.0) == 'Fuse(name, "MF-R300")'


def test_breaker_and_fuse_pins_are_fixed():
    cb = Breaker("CB1", 10, voltage_rating_V=48, interrupt_A=5000, continuous_fraction=0.8)
    assert cb.kind == "breaker" and cb.pin_names == ["LINE", "LOAD"]
    with pytest.raises(HarnessError):
        cb.add_pin("X")


def test_bom_lists_the_fuse_and_its_holder():
    from eitri.harness.outputs import bom
    rows = bom(rail())
    prot = [r for r in rows if r["category"] == "protection"]
    assert prot and "5 A" in prot[0]["description"] and prot[0]["notes"] == "F1"
    assert not any(r["category"] == "connector" and "F1" in r["notes"] for r in rows)


def test_editor_cannot_retype_a_fuse():
    from eitri.harness.edits import apply_edits
    h = rail()
    notes = apply_edits(h, [{"op": "set_connector", "connector": "F1", "type": "XT60"}])
    assert not notes[0].ok


def test_source_survives_a_connector_rename():
    from eitri.harness.edits import apply_edits
    h = rail()
    apply_edits(h, [{"op": "set_connector", "connector": "BAT", "name": "PACK"}])
    _rep, cs = checks(h)
    assert "overcurrent protection from PACK.+" in cs


# --------------------------------------------------------------------------- the servo arm example


def test_servo_arm_example_passes_with_its_fuse():
    from eitri.model import load_model
    m = load_model(DEMO)
    h = m.build()
    rep = analyze(h, m.verify(h))
    assert rep.ok, summarize(rep)
    nr = rep.to_dict()["checks_not_run"]
    assert [d["name"] for d in nr] == ["F1 interrupting rating"], nr         # the PSU publishes no short-circuit current
    assert len(rep.warnings) == 1


def test_servo_arm_example_without_its_fuse_fails():
    from eitri.model import load_model
    src = DEMO.read_text(encoding="utf-8")
    import re
    src = re.sub(r'^.*fuse\["LINE"\].*\n|^.*= h\.add\(Fuse\(.*\n', "", src, flags=re.M)          # the fuse and its lead gone
    src = src.replace('h.connect(fuse["LOAD"], v5,', 'h.connect(psu["+"], v5,')
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "m.py"
        f.write_text(src, encoding="utf-8")
        m = load_model(f)
        rep = analyze(m.build())
    assert not rep.ok and any(c.name == "overcurrent protection from PSU.+" and c.severity == "fail" for c in rep.checks)
