"""Wire harness kernel: the typed API, every check firing on a crafted bad harness and passing on the fixed one,
voltage-drop numbers against hand computation, the BOM and cut list, the diagram (SVG + PNG), the WireViz export."""
from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from eitri.harness import (Cable, Connector, Gauge, Harness, HarnessError, Net, Splice, Wire, awg_ampacity,
                           net_stats, voltage_drop)
from eitri.harness import diagram, outputs
from eitri.harness.catalog import CATALOG, RATING_SOURCES, catalog_table, normalize_color, normalize_type, spec_for
from eitri.harness.report import analyze, summarize


def check(report, needle: str):
    """The one check whose name contains `needle`."""
    hits = [c for c in report.checks if needle in c.name]
    assert len(hits) == 1, f"{needle!r} matched {[c.name for c in hits]} in {[c.name for c in report.checks]}"
    return hits[0]


def checks(report, needle: str):
    return [c for c in report.checks if needle in c.name]


def sev(h: Harness, needle: str) -> str:
    return check(analyze(h), needle).severity


def lead(gauge=22, length=300, current=1.0, voltage=5.0) -> Harness:
    """A good two-wire lead: nothing fails, nothing warns."""
    h = Harness("lead")
    a = h.add(Connector("J1", "JST-XH", pins=["+", "-"]))
    b = h.add(Connector("J2", "JST-XH", pins=["+", "-"]))
    h.net("V", current_A=current, voltage_V=voltage)
    h.net("G", current_A=current, voltage_V=voltage, round_trip=False)
    h.connect(a["+"], b["+"], Wire(gauge, "RD", length), net="V")
    h.connect(a["-"], b["-"], Wire(gauge, "BK", length), net="G")
    return h


# --------------------------------------------------------------------------- the typed API


def test_gauge_parse():
    assert Gauge.parse(18).awg == 18 and Gauge.parse("18").awg == 18 and Gauge.parse("18 AWG").awg == 18
    g = Gauge.parse("0.5mm2")
    assert g.awg is None and g.mm2 == 0.5 and g.equiv_awg == 20
    assert Gauge.parse("0.75 mm²").mm2 == 0.75
    assert Gauge.parse("4/0").awg == -3
    for bad in (18.5, "thick", "0mm2", 99, True):
        with pytest.raises(ValueError):
            Gauge.parse(bad)


def test_awg_tables():
    assert awg_ampacity(14) == 32 and awg_ampacity(22) == 7 and awg_ampacity(10) == 55
    assert 22 < awg_ampacity(15) < 32                    # an odd size is between its neighbours
    assert Gauge.parse(12).circular_mils == pytest.approx(6530, rel=1e-3)
    assert Gauge.parse(20).circular_mils == pytest.approx(1021.5, rel=2e-3)
    assert Gauge.parse("0.5mm2").circular_mils == pytest.approx(986.8, rel=1e-3)


def test_colors():
    assert normalize_color("red") == "RD" and normalize_color("rd") == "RD"
    assert normalize_color("RD/WH") == "RD/WH" and normalize_color("RDWH") == "RD/WH" and normalize_color("green-yellow") == "GN/YE"
    with pytest.raises(ValueError):
        normalize_color("mauve")


def test_connector_pins_and_lookup():
    c = Connector("J1", "jst xh", pins=3)
    assert c.type == "JST-XH" and c.pin_names == ["1", "2", "3"] and c.pins == 3
    assert c[2].pin == "2" and c["3"].pin == "3"
    d = Connector("J2", "JST-PH", pins={"1": "GND", "2": "5V"})
    assert d["5V"].pin == "2" and d.pin_labels == {"1": "GND", "2": "5V"}
    e = Connector("J3", "XT60", pins=["+", "-"], nc=["-"])
    assert e[1].pin == "+" and e.nc == ["-"]
    with pytest.raises(HarnessError):
        c[9]
    with pytest.raises(HarnessError):
        Connector("J 4", "XT30")
    with pytest.raises(HarnessError):
        Connector("J5", "XT30", pins=["a", "a"])


def test_catalog_types_and_unknown_allowed():
    for t in ("JST-XH", "JST-PH", "DUPONT-2.54", "MOLEX-MICROFIT-3.0", "XT30", "XT60", "RING", "SPADE-6.3", "BARE", "SOLDER"):
        assert spec_for(t) is not None, t
    assert normalize_type("xt-60") == "XT60" and normalize_type("micro-fit 3.0") == "MOLEX-MICROFIT-3.0" and normalize_type("ring terminal") == "RING"
    assert spec_for("XT60").rating_a == 30 and spec_for("XT60").burst_a == 60 and spec_for("JST-PH").pitch_mm == 2.0
    odd = Connector("X", "ACME-9", pins=4, rating_a=5, pitch_mm=3.5)
    assert not odd.known and odd.rating == 5 and odd.pitch == 3.5
    assert "XT60" in catalog_table()


def test_connect_validation():
    h = Harness()
    a = h.add(Connector("A", "BARE", pins=2))
    s = h.splice("S")
    w = Wire(18, "RD", 100)
    h.connect(a[1], s, w)
    h.connect("A.2", "S", w)                                    # strings work too
    assert [l.id for l in h.links] == ["W1", "W2"]
    with pytest.raises(HarnessError):
        h.connect(a[1], "NOPE.1", w)
    with pytest.raises(HarnessError):
        h.connect(a[1], s, Wire(18, "RD"))                      # no length anywhere
    with pytest.raises(HarnessError):
        h.connect(a[1], s, w, net="undeclared")
    with pytest.raises(HarnessError):
        h.connect(a[1], s, 18)
    assert h.connect(a[1], s, Wire(18, "RD"), length_mm=50, name="CUSTOM").length_mm == 50
    with pytest.raises(HarnessError):
        h.connect(a[1], s, w, name="CUSTOM")                    # ids are unique


def test_connect_adds_a_connector_on_first_use():
    h = Harness()
    a, b = Connector("A", "BARE"), Connector("B", "BARE")
    h.connect(a, b, Wire(20, "BK", 100))                       # single-pin connectors, never added explicitly
    assert set(h.connectors) == {"A", "B"}
    c = Connector("C", "XT30", pins=["+", "-"])
    h.connect(c["+"], b, Wire(20, "RD", 100))                  # added through the pin it was reached by
    assert "C" in h.connectors


def test_wire_is_a_template():
    h = Harness()
    a = h.add(Connector("A", "BARE", pins=2))
    b = h.add(Connector("B", "BARE", pins=2))
    w = Wire(18, "RD", 100)
    l1, l2 = h.connect(a[1], b[1], w), h.connect(a[2], b[2], w)
    l1.length_mm = 999
    assert l2.length_mm == 100 and w.length_mm == 100


# --------------------------------------------------------------------------- voltage drop, hand computed


def test_voltage_drop_hand_computed():
    # 10 A over 3 m of 12 AWG (6530 CM): V = 2 * 10.75 * 10 * (3000 / 304.8 ft) / 6530
    expected = 2 * 10.75 * 10 * (3000 / 304.8) / 6530
    assert expected == pytest.approx(0.3241, abs=1e-3)
    assert voltage_drop(10, 3000, Gauge.parse(12)) == pytest.approx(expected, rel=2e-3)
    # one way is half of the round trip
    assert voltage_drop(10, 3000, Gauge.parse(12), round_trip=False) == pytest.approx(expected / 2, rel=2e-3)
    # 2 A over 500 mm of 0.5 mm2 (986.8 CM): 2 * 10.75 * 2 * 1.6404 / 986.8
    assert voltage_drop(2, 500, Gauge.parse("0.5mm2")) == pytest.approx(2 * 10.75 * 2 * (500 / 304.8) / 986.8, rel=1e-3)


def test_net_drop_percent_matches_hand_computation():
    h = lead(gauge=20, length=1000, current=2.0, voltage=5.0)
    # 20 AWG: 1021.5 CM (standard); 2 A, 1 m round trip, 5 V
    volts = 2 * 10.75 * 2.0 * (1000 / 304.8) / 1021.5
    st = net_stats(h)
    assert st["V"].drop_V == pytest.approx(volts, rel=3e-3)
    assert st["V"].drop_pct == pytest.approx(volts / 5.0 * 100, rel=3e-3)       # about 2.76 %
    assert st["G"].drop_pct == pytest.approx(volts / 2 / 5.0 * 100, rel=3e-3)   # the return net counts one way
    c = check(analyze(h), "voltage drop V ")
    assert c.severity == "pass" and "2.7" in c.measured


def test_drop_sums_along_a_path_not_across_branches():
    h = Harness()
    src = h.add(Connector("SRC", "BARE"))
    l1 = h.add(Connector("L1", "BARE"))
    l2 = h.add(Connector("L2", "BARE"))
    s = h.splice("S")
    h.net("V", current_A=10, voltage_V=12)
    h.connect(src, s, Wire(14, "RD", 1000), net="V")                           # trunk 10 A
    h.connect(s, l1, Wire(16, "RD", 500), net="V", current_A=6)
    h.connect(s, l2, Wire(16, "RD", 800), net="V", current_A=4)
    trunk, b1, b2 = (voltage_drop(i, n, Gauge.parse(g)) for i, n, g in ((10, 1000, 14), (6, 500, 16), (4, 800, 16)))
    st = net_stats(h)["V"]
    assert st.drop_V == pytest.approx(max(trunk + b1, trunk + b2), rel=1e-9)
    assert st.worst_path[0] == "W1" and len(st.worst_path) == 2


# --------------------------------------------------------------------------- the checks: fire on bad, pass on fixed


def test_good_lead_is_clean():
    rep = analyze(lead())
    assert rep.ok and not rep.failed and not rep.warnings, summarize(rep)


def test_unconnected_pins():
    h = lead()
    h.add(Connector("J3", "JST-XH", pins=3))
    h.connect("J3.1", "J2.+", Wire(22, "RD", 100), net="V")           # hmm: also a second wire on J2.+
    c = check(analyze(h), "all pins connected")
    assert c.severity == "fail" and "J3.2" in c.measured and "J3.3" in c.measured and "J3" in c.refs
    h.nc("J3.2", "J3.3")                                                # declared NC
    assert sev(h, "all pins connected") == "pass"


def test_multiple_drivers_on_one_pin():
    h = Harness()
    a = h.add(Connector("A", "BARE", pins=1))
    b = h.add(Connector("B", "BARE", pins=1))
    c = h.add(Connector("C", "BARE", pins=1))
    h.net("V", current_A=1, voltage_V=5)
    l1 = h.connect(a, b, Wire(20, "RD", 100), net="V")
    l2 = h.connect(a, c, Wire(20, "RD", 100), net="V")
    bad = check(analyze(h), "one wire per pin")
    assert bad.severity == "fail" and set(bad.refs) >= {l1.id, l2.id, "A"}
    # fixed: the wires meet at a splice
    h2 = Harness()
    a2, b2, c2 = (h2.add(Connector(n, "BARE", pins=1)) for n in "ABC")
    s = h2.splice("S")
    h2.net("V", current_A=1, voltage_V=5)
    h2.connect(a2, s, Wire(20, "RD", 100), net="V")
    h2.connect(s, b2, Wire(20, "RD", 100), net="V")
    h2.connect(s, c2, Wire(20, "RD", 100), net="V")
    assert sev(h2, "one wire per pin") == "pass"


def test_duplicate_wires():
    h = lead()
    h.connect("J1.+", "J2.+", Wire(22, "RD", 300), net="V")
    c = check(analyze(h), "no duplicate wires")
    assert c.severity == "fail" and "W1" in c.refs and "W3" in c.refs
    h.remove_link("W3")
    assert sev(h, "no duplicate wires") == "pass"


def test_dangling_splice():
    h = lead()
    s = h.splice("S")
    h.connect("J1.+", s, Wire(22, "RD", 50), net="V")                  # one wire only
    c = check(analyze(h), "no dangling splices")
    assert c.severity == "fail" and "S" in c.refs
    h.connect(s, "J2.-", Wire(22, "RD", 50), net="V")                  # (the other end now joins a second wire)
    assert sev(h, "no dangling splices") == "pass"


def test_splice_must_join_one_net():
    h = Harness()
    a, b, c = (h.add(Connector(n, "BARE", pins=1)) for n in "ABC")
    s = h.splice("S")
    h.net("V1", current_A=1, voltage_V=5)
    h.net("V2", current_A=1, voltage_V=5)
    h.connect(a, s, Wire(20, "RD", 100), net="V1")
    h.connect(s, b, Wire(20, "RD", 100), net="V1")
    h.connect(s, c, Wire(20, "RD", 100), net="V2")
    bad = check(analyze(h), "splices join one net")
    assert bad.severity == "fail" and "S" in bad.refs
    h.link("W3").net = "V1"
    assert sev(h, "splices join one net") == "pass"


def test_wire_length_required_positive():
    h = lead()
    h.link("W1").length_mm = 0
    assert sev(h, "every wire has a length") == "fail"
    h.link("W1").length_mm = 100
    assert sev(h, "every wire has a length") == "pass"


def test_wire_without_net_warns():
    h = lead()
    h.connect("J1.+", "J2.-", Wire(22, "WH", 100))
    c = check(analyze(h), "belongs to a net")
    assert c.severity == "warn" and "W3" in c.refs
    h.link("W3").net = "V"
    assert not checks(analyze(h), "belongs to a net") or sev(h, "belongs to a net") == "pass"


def test_ampacity_levels():
    # 14 AWG is 32 A in the table
    def level(amps):
        h = lead(gauge=14, current=amps, voltage=12)
        h.connectors["J1"].rating_a = h.connectors["J2"].rating_a = 100          # leave the connector out of it
        return check(analyze(h), "ampacity W1")
    assert level(20).severity == "pass"
    warn = level(26)                                                  # 81 % of the limit: within 20 %
    assert warn.severity == "warn" and "81%" in warn.measured
    fail = level(33)
    assert fail.severity == "fail" and fail.refs == ["W1"] and "33 A of 32 A" in fail.measured
    assert level(32).severity == "warn"                               # exactly at the limit is not over it


def test_ampacity_fixed_by_a_thicker_wire():
    h = lead(gauge=18, current=20, voltage=12)
    h.connectors["J1"].rating_a = h.connectors["J2"].rating_a = 100
    assert check(analyze(h), "ampacity W1").severity == "fail"
    for l in h.links:
        l.gauge = Gauge.parse(12)                                      # 41 A
    assert check(analyze(h), "ampacity W1").severity == "pass"


def test_connector_pin_rating():
    h = lead(gauge=22, current=2.5, voltage=5)                         # JST-XH: 3 A per pin
    c = check(analyze(h), "connector rating J1")
    assert c.severity == "warn" and "J1.+" in c.measured             # 83 %
    h.connectors["J1"].rating_a = 2.0
    assert check(analyze(h), "connector rating J1").severity == "fail"
    h.connectors["J1"].rating_a = 10.0
    assert check(analyze(h), "connector rating J1").severity == "pass"


def test_connector_continuous_and_short_time_ratings():
    # XT60: 30 A continuous, 60 A short-time (Amass datasheet V1.2); a current between the two is a peak-only warning
    def rated(amps, **kw):
        h = lead(gauge=8, current=amps, voltage=14.8)
        for c in h.connectors.values():
            c.type = "XT60"
            for k, v in kw.items():
                setattr(c, k, v)
        return check(analyze(h), "connector rating J1")
    assert rated(20).severity == "pass"
    peak = rated(45)
    assert peak.severity == "warn" and "above the 30 A continuous rating" in peak.measured and "60 A short-time" in peak.measured
    assert rated(61).severity == "fail"                               # past the short-time rating too
    assert rated(45, rating_a=40.0).severity == "fail"                # a rating given in the model has no short-time allowance
    for key, spec in CATALOG.items():                                 # every graded catalog type says where its rating comes from
        if spec.rating_a is not None:
            assert RATING_SOURCES.get(key), key


def test_connector_rating_sums_wires_on_a_pin():
    h = Harness()
    big = h.add(Connector("BIG", "JST-PH", pins=1))                    # 2 A per pin
    s = h.splice("S")
    x = h.add(Connector("X", "BARE"))
    y = h.add(Connector("Y", "BARE"))
    h.net("V", current_A=3, voltage_V=5)
    h.connect(big, s, Wire(18, "RD", 100), net="V")
    h.connect(s, x, Wire(18, "RD", 100), net="V", current_A=1.5)
    h.connect(s, y, Wire(18, "RD", 100), net="V", current_A=1.5)
    assert check(analyze(h), "connector rating BIG").severity == "fail"       # 3 A on a 2 A pin


def test_unknown_connector_type_warns_until_rated():
    h = Harness()
    a = h.add(Connector("A", "ACME-9", pins=1))
    b = h.add(Connector("B", "BARE", pins=1))
    h.net("V", current_A=2, voltage_V=5)
    h.connect(a, b, Wire(18, "RD", 100), net="V")
    c = check(analyze(h), "connector rating A")
    assert c.severity == "warn" and "rating_a" in c.expected
    a.rating_a = 5
    assert check(analyze(h), "connector rating A").severity == "pass"


def test_voltage_drop_fails_then_passes_with_a_thicker_wire():
    h = lead(gauge=22, length=2000, current=3.0, voltage=5.0)         # 22 AWG over 2 m at 3 A: a big drop
    h.connectors["J1"].rating_a = h.connectors["J2"].rating_a = 10
    for l in h.links:
        l.gauge = Gauge.parse(22)
    bad = check(analyze(h), "voltage drop V ")
    assert bad.severity == "fail" and bad.refs == ["W1"]
    pct = net_stats(h)["V"].drop_pct
    assert pct > 3
    for l in h.links:
        l.gauge = Gauge.parse(14)
    good = check(analyze(h), "voltage drop V ")
    assert good.severity == "pass" and net_stats(h)["V"].drop_pct < 3


def test_net_without_voltage_is_ungraded_with_a_warning():
    h = lead()
    h.nets["V"].voltage_V = None
    c = check(analyze(h), "voltage drop V")
    assert c.severity == "warn" and "voltage_V" in c.expected


def test_gauge_consistency():
    h = Harness()
    a, b, c = (h.add(Connector(n, "BARE", pins=1)) for n in "ABC")
    s = h.splice("S")
    h.net("V", current_A=10, voltage_V=12)
    h.connect(a, s, Wire(18, "RD", 100), net="V")                       # thin trunk ...
    h.connect(s, b, Wire(12, "RD", 100), net="V", current_A=5)        # ... feeding a thicker branch that carries less
    h.connect(s, c, Wire(12, "RD", 100), net="V", current_A=5)
    bad = check(analyze(h), "one gauge per net")
    assert bad.severity == "fail" and {"W1"} <= set(bad.refs)          # the thinner W1 carries 10 A, more than the 5 A branches
    h.link("W1").gauge = Gauge.parse(10)                                # now the trunk is the thicker wire
    assert sev(h, "one gauge per net") == "pass"
    # same current but a changed gauge: only a warning
    h2 = lead()
    h2.connect("J1.+", "J2.-", Wire(18, "RD", 100), net="V")
    assert sev(h2, "one gauge per net") == "warn"


def test_colour_consistency_warns():
    h = lead()
    h.link("W1").color = "RD"
    h.connect("J1.-", "J2.+", Wire(22, "YE", 100), net="V")
    c = check(analyze(h), "one colour per net")
    assert c.severity == "warn" and "V" in c.measured
    h.link("W3").color = "RD"
    assert sev(h, "one colour per net") == "pass"


def test_shield_must_be_terminated():
    def cable_harness(shield_end):
        h = Harness()
        a = h.add(Connector("A", "BARE", pins=3))
        b = h.add(Connector("B", "BARE", pins=2))
        cab = Cable("C1", [Wire(24, "RD"), Wire(24, "BK")], shield=True, jacket_color="GY", length_mm=800)
        h.connect_cable(cab, [a[1], a[2]], [b[1], b[2]], shield_a=a[3] if shield_end else None)
        if not shield_end:
            h.nc("A.3")
        return h
    bad = check(analyze(cable_harness(False)), "shield terminated")
    assert bad.severity == "fail" and "C1" in bad.refs
    good = check(analyze(cable_harness(True)), "shield terminated")
    assert good.severity == "pass" and "A.3" in good.measured
    assert sev(cable_harness(True), "all pins connected") == "pass"       # the shield drain pin counts as connected


def test_verify_checks_are_reported_and_a_failure_fails_the_report():
    from eitri.model import Checker
    h = lead()
    c = Checker()
    c.at_most("drop", 4.0, 3.0)
    c.at_least("lead length", 300, 250)
    rep = analyze(h, c)
    assert not rep.ok and [x.name for x in rep.failed] == ["drop"]
    assert check(rep, "lead length").severity == "pass" and check(rep, "lead length").category == "verify"


def test_summary_lists_failures_first_and_nets():
    h = lead(gauge=22, length=2000, current=3.0)
    text = summarize(analyze(h))
    assert text.splitlines()[1].startswith("Checks:") and "FAIL" in text.splitlines()[1]
    first_check = next(l for l in text.splitlines() if l.startswith("["))
    assert first_check.startswith("[FAIL]")
    assert "V: 5 V, 3 A, drop" in text


# --------------------------------------------------------------------------- BOM and cut list


def bom_harness() -> Harness:
    h = Harness("bom")
    a = h.add(Connector("A", "JST-PH", pins=4, part_number="", gender="female"))       # PHR-4, 3 pins populated
    b = h.add(Connector("B", "JST-PH", pins=4, gender="female"))
    x = h.add(Connector("X", "XT60", pins=["+", "-"]))
    y = h.add(Connector("Y", "XT60", pins=["+", "-"]))
    s = h.splice("S")
    h.nc("A.4", "B.4")
    h.net("V", current_A=1, voltage_V=5)
    for i in (1, 2, 3):
        h.connect(a[i], b[i], Wire(24, "RD", 200), net="V")                           # three 200 mm wires
    h.connect(x["+"], s, Wire(12, "RD", 500))
    h.connect(s, y["+"], Wire(12, "RD", 300))
    h.connect(x["-"], y["-"], Wire(12, "BK", 800))
    return h


def test_bom_math():
    rows = {r["description"]: r for r in outputs.bom(bom_harness())}
    ph = next(r for r in rows.values() if r["description"].startswith("JST-PH connector, 4 pos"))
    assert ph["qty"] == 2 and ph["part_number"] == "PHR-4"
    xt = next(r for r in rows.values() if r["description"].startswith("XT60 connector"))
    assert xt["qty"] == 2
    term = next(r for r in rows.values() if r["category"] == "terminal")
    assert term["qty"] == 6 and term["part_number"] == "SPH-002T-P0.5S"               # 2 connectors x 3 populated pins
    solder = next(r for r in rows.values() if r["category"] == "termination")
    assert solder["qty"] == 4                                                       # 2 XT60 x 2 cups
    red24 = next(r for r in rows.values() if r["category"] == "wire" and "24 AWG" in r["description"])
    assert red24["unit"] == "m" and red24["qty"] == pytest.approx(3 * 0.200 * 1.10, abs=1e-3)     # 0.6 m + 10 %
    red12 = next(r for r in rows.values() if r["category"] == "wire" and "12 AWG red" in r["description"])
    assert red12["qty"] == pytest.approx((0.5 + 0.3) * 1.1, abs=1e-3)
    black12 = next(r for r in rows.values() if r["category"] == "wire" and "12 AWG black" in r["description"])
    assert black12["qty"] == pytest.approx(0.8 * 1.1, abs=1e-3)
    splice = next(r for r in rows.values() if r["category"] == "splice")
    assert splice["qty"] == 1 and "2 wires" in splice["description"]
    label = next(r for r in rows.values() if r["category"] == "label")
    assert label["qty"] == 2 * 6                                                    # one per wire end


def test_bom_csv_and_markdown():
    h = bom_harness()
    rows = outputs.bom(h)
    parsed = list(csv.DictReader(io.StringIO(outputs.bom_csv(rows))))
    assert len(parsed) == len(rows) and parsed[0]["item"] == "1" and {"category", "description", "qty", "unit", "part_number"} <= set(parsed[0])
    md = outputs.bom_markdown(h)
    assert md.startswith("# Bill of materials") and "| PHR-4 |" in md


def test_cutlist():
    rows = outputs.cutlist(bom_harness())
    assert len(rows) == 6
    w1 = rows[0]
    assert (w1["id"], w1["from"], w1["to"], w1["cut_mm"]) == ("W1", "A.1", "B.1", 200.0)
    assert w1["strip_a_mm"] == 2.0 and "crimp SPH-002T-P0.5S into A pos 1" in w1["term_a"]
    xt = next(r for r in rows if r["from"] == "X.-")
    assert xt["strip_a_mm"] == 8.0 and "solder to X - cup" in xt["term_a"]
    sp = next(r for r in rows if r["to"] == "S")
    assert "crimp splice S" in sp["term_b"]
    parsed = list(csv.DictReader(io.StringIO(outputs.cutlist_csv(rows))))
    assert parsed[0]["cut_mm"] == "200.0" and parsed[0]["gauge"] == "24 AWG"


def test_cable_in_the_bom_and_cutlist():
    h = Harness()
    a = h.add(Connector("A", "BARE", pins=3))
    b = h.add(Connector("B", "BARE", pins=2))
    cab = Cable("USB", [Wire(28, "RD"), Wire(28, "BK")], shield=True, jacket_color="GY", length_mm=1000, part_number="Belden 9501")
    h.connect_cable(cab, [a[1], a[2]], [b[1], b[2]], shield_a=a[3])
    rows = outputs.bom(h)
    cable_row = next(r for r in rows if r["category"] == "cable")
    assert cable_row["qty"] == pytest.approx(1.1) and cable_row["part_number"] == "Belden 9501" and "shielded" in cable_row["description"]
    assert not any(r["category"] == "wire" for r in rows)                            # the cores are not bought twice
    assert outputs.cutlist(h)[0]["cable"] == "USB core 1"


# --------------------------------------------------------------------------- the diagram


def test_svg_is_well_formed_and_addressable(tmp_path):
    h = lead(gauge=22, length=2000, current=3.0)             # a failing harness: the SVG marks it
    rep = analyze(h)
    svg = diagram.scene_to_svg(diagram.build_scene(h, diagram.status_from(rep)))
    root = ET.fromstring(svg)                                  # renders without error: valid XML
    ns = "{http://www.w3.org/2000/svg}"
    assert root.tag == ns + "svg" and float(root.get("width")) > 100
    ids = {(g.get("data-kind"), g.get("data-id")) for g in root.iter(ns + "g") if g.get("data-id")}
    assert {("wire", "W1"), ("wire", "W2"), ("conn", "J1"), ("conn", "J2")} <= ids
    paths = [p for p in root.iter(ns + "path")]
    assert any(p.get("class") == "hit" for p in paths) and any(p.get("class") == "halo" for p in paths)   # the failing wire has a halo
    texts = " ".join("".join(t.itertext()) for t in root.iter(ns + "text"))
    assert "W1" in texts and "22 AWG" in texts and "J1 · JST-XH" in texts


def test_svg_stripe_splice_and_escaping():
    h = Harness('A & <B> "x"')
    a = h.add(Connector("A", "BARE", pins={"1": "<in>"}))
    b = h.add(Connector("B", "BARE"))
    s = h.splice("S")
    h.connect(a, s, Wire(20, "RD/WH", 100))
    h.connect(s, b, Wire(20, "RD/WH", 100))
    svg = diagram.scene_to_svg(diagram.build_scene(h))
    ET.fromstring(svg)
    assert "&lt;in&gt;" in svg and "stroke-dasharray" in svg and "<circle" in svg


def test_layout_hints_move_a_node():
    h = lead()
    s0 = diagram.build_scene(h)
    h.place("J2", 700, 300)
    s1 = diagram.build_scene(h)
    assert (s1.nodes["J2"]["x"], s1.nodes["J2"]["y"]) == (700, 300) and s0.nodes["J2"]["x"] != 700
    assert s1.nodes["J1"] == s0.nodes["J1"]


def test_layout_puts_sources_left_loads_right():
    h = Harness()
    batt = h.add(Connector("BATT", "XT60", pins=["+", "-"]))
    load = h.add(Connector("LOAD", "XT30", pins=["+", "-"]))
    h.connect(batt["+"], load["+"], Wire(14, "RD", 100))
    h.connect(load["-"], batt["-"], Wire(14, "BK", 100))                # written load -> source: still one column apart
    sc = diagram.build_scene(h)
    assert sc.nodes["BATT"]["x"] < sc.nodes["LOAD"]["x"]


def test_png_review_image(tmp_path):
    from PIL import Image
    h = lead(gauge=22, length=2000, current=3.0)
    rep = analyze(h)
    out = tmp_path / "review.png"
    diagram.scene_to_png(diagram.build_scene(h, diagram.status_from(rep), footer=diagram.footer_from(rep)), out)
    img = Image.open(out)
    assert img.size[0] > 300 and img.size[1] > 150
    colors = {c for _n, c in img.convert("RGB").getcolors(maxcolors=1 << 20)}
    assert any(r > 200 and g < 80 and b < 80 for r, g, b in colors)             # the failing wire/check is drawn red
    clean = tmp_path / "clean.png"
    ok = analyze(lead())
    diagram.scene_to_png(diagram.build_scene(lead(), {}, footer=diagram.footer_from(ok)), clean)
    assert Image.open(clean).size[0] > 300


def test_empty_harness_renders():
    h = Harness("empty")
    svg = diagram.scene_to_svg(diagram.build_scene(h))
    ET.fromstring(svg)
    rep = analyze(h)
    assert rep.ok and rep.wires == 0


# --------------------------------------------------------------------------- WireViz export


def parse_yaml(text: str):
    """A parser for the YAML subset our export writes (block mappings and sequences, flow lists, scalars)."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]

    def scalar(t: str):
        t = t.strip()
        if t.startswith('"') and t.endswith('"'):
            return t[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if t in ("true", "false"):
            return t == "true"
        try:
            return int(t)
        except ValueError:
            pass
        try:
            return float(t)
        except ValueError:
            return t

    def flow(t: str):
        inner = t.strip()[1:-1].strip()
        return [scalar(x) for x in re.split(r",\s*(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", inner)] if inner else []

    def value(t: str):
        return flow(t) if t.strip().startswith("[") else scalar(t)

    def block(i: int, indent: int):
        if lines[i].lstrip().startswith("-"):
            out = []
            while i < len(lines) and len(lines[i]) - len(lines[i].lstrip()) == indent and lines[i].lstrip().startswith("-"):
                rest = lines[i].lstrip()[1:].strip()
                if rest:
                    k, _, v = rest.partition(":")
                    out.append({scalar(k): value(v)})
                    i += 1
                else:
                    i += 1
                    sub, i = block(i, len(lines[i]) - len(lines[i].lstrip()))
                    out.append(sub)
            return out, i
        out = {}
        while i < len(lines) and len(lines[i]) - len(lines[i].lstrip()) == indent:
            k, _, v = lines[i].lstrip().partition(":")
            if v.strip():
                out[scalar(k)] = value(v)
                i += 1
            else:
                i += 1
                sub, i = block(i, len(lines[i]) - len(lines[i].lstrip()))
                out[scalar(k)] = sub
        return out, i

    return block(0, 0)[0]


def test_wireviz_yaml_round_trips_through_the_schema_shape():
    h = bom_harness()
    cab = Cable("SH", [Wire(24, "GN"), Wire(24, "WH/BU")], shield=True, jacket_color="GY", length_mm=600)
    p = h.add(Connector("P", "DUPONT-2.54", pins={"1": "A", "2": "B", "3": "SHLD"}, gender="male", part_number="DUP-3"))
    q = h.add(Connector("Q", "DUPONT-2.54", pins=2, gender="female"))
    h.connect_cable(cab, [p[1], p[2]], [q[1], q[2]], shield_a=p[3])
    text = outputs.wireviz_yaml(h)
    doc = parse_yaml(text)
    assert set(doc) == {"connectors", "cables", "connections"}
    # connectors: type, pincount, pins, optional pinlabels / subtype / mpn
    for name, c in doc["connectors"].items():
        assert isinstance(c["type"], str) and isinstance(c["pincount"], int) and len(c["pins"]) == c["pincount"]
        if "pinlabels" in c:
            assert len(c["pinlabels"]) == c["pincount"]
    assert doc["connectors"]["P"]["subtype"] == "male" and doc["connectors"]["P"]["mpn"] == "DUP-3"
    assert doc["connectors"]["S"]["pincount"] == 1                                   # a splice is a one-pin connector
    assert [str(x) for x in doc["connectors"]["P"]["pins"]] == ["1", "2", "3"] and doc["connectors"]["P"]["pinlabels"] == ["A", "B", "SHLD"]
    # cables: gauge, length in metres, colours as WireViz codes, wirecount == len(colors)
    cables = doc["cables"]
    assert cables["SH"]["wirecount"] == 2 and cables["SH"]["colors"] == ["GN", "WHBU"] and cables["SH"]["shield"] is True
    assert cables["SH"]["length"] == pytest.approx(0.6) and cables["W1"]["length"] == pytest.approx(0.2)
    assert cables["W1"]["gauge"] == "24 AWG" and cables["W1"]["category"] == "bundle"
    for c in cables.values():
        assert len(c["colors"]) == c["wirecount"]
    # connections: [connector side, cable, connector side], each {name: [pins]}, naming only known things
    assert len(doc["connections"]) == len(h.links) - 2 + 1                           # 2 cores became one cable connection
    for trio in doc["connections"]:
        assert len(trio) == 3
        names = [next(iter(side)) for side in trio]
        assert names[0] in doc["connectors"] and names[1] in cables and names[2] in doc["connectors"]
        pins = [next(iter(side.values())) for side in trio]
        assert len(pins[0]) == len(pins[1]) == len(pins[2])
    # PyYAML (when installed) reads the same document
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(text) == doc


# --------------------------------------------------------------------------- the demo


def test_quadcopter_demo_builds_green():
    from eitri.model import load_model
    demo = Path(__file__).resolve().parents[1] / "eitri" / "demos" / "quad-power" / "quad_power.py"
    m = load_model(demo)
    h = m.build()
    rep = analyze(h, m.verify(h))
    assert rep.ok and not rep.failed, summarize(rep)
    # the only warnings are the connectors at or past their continuous rating: the XT60 carries the 44.5 A peak (30 A
    # continuous, 60 A short-time), each XT30 its ESC's full 20 A (20 A rating)
    warned = {c.name: c for c in rep.warnings}
    assert set(warned) == {"connector rating BATT (XT60, 30 A/pin)"} | {f"connector rating ESC{i} (XT30, 20 A/pin)" for i in range(1, 5)}, summarize(rep)
    assert "short-time rating" in warned["connector rating BATT (XT60, 30 A/pin)"].measured
    assert len(h.connectors) == 6 and len(h.splices) == 2 and len(h.links) == 12
    st = net_stats(h)
    assert st["VBAT"].current_A == pytest.approx(44.5) and st["VBAT"].drop_pct < 1.0
    esc = [l for l in h.links if l.b.__class__.__name__ == "PinRef" and l.b.connector.startswith("ESC") and l.net == "VBAT"]
    assert len(esc) == 4 and all(h.wire_current(l) == 20 for l in esc)
    # the checks are meaningful: a thinner branch wire or a heavier ESC fails them
    h.link(esc[0].id).gauge = Gauge.parse(18)
    assert check(analyze(h), f"ampacity {esc[0].id}").severity == "fail"
    h2 = m.build()
    h2.connectors["ESC1"].type = "JST-XH"
    assert check(analyze(h2), "connector rating ESC1").severity == "fail"
