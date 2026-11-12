"""Industrial connectors with sourced ratings (0.8.1): M8 / M12 A-coded cordset ends and the WAGO 2002-1201 2.5 mm^2 terminal."""
from __future__ import annotations

from eitri.harness import Cable, Connector, Harness, Wire
from eitri.harness.catalog import CATALOG, RATING_SOURCES, normalize_type
from eitri.harness.outputs import cutlist
from eitri.harness.report import analyze


def test_ratings_and_their_sources():
    want = {"M8-3": (4.0, 60.0), "M8-4": (3.0, 30.0), "M12-4": (4.0, 250.0), "M12-5": (4.0, 60.0), "WAGO-2002-1201": (20.0, 600.0)}
    for key, (amps, volts) in want.items():
        s = CATALOG[key]
        assert (s.rating_a, s.rated_V) == (amps, volts), key
        assert key in RATING_SOURCES and ("release date" in RATING_SOURCES[key] or "version" in RATING_SOURCES[key])
    assert "the lower, 3 A, is used" in RATING_SOURCES["M8-4"]                  # 3 A and 4 A in two P+F sheets
    assert "20 A and 600 V" in RATING_SOURCES["WAGO-2002-1201"]                 # UL 20 A / 600 V under IEC 24 A / 800 V
    assert CATALOG["WAGO-2002-1201"].mm2 == (0.25, 4.0)
    assert [normalize_type(t) for t in ("M8-3 FEMALE", "m12 5-pin", "WAGO 2002-1201")] == ["M8-3", "M12-5", "WAGO-2002-1201"]


def _h(cur=0.03, volts=24.0, sensor_type="M8-3 FEMALE", gauge="0.25mm2", term="WAGO-2002-1201"):
    h = Harness("field")
    x1 = h.add(Connector("X1", term, pins=["1", "2", "3"]))
    b1 = h.add(Connector("B1", sensor_type, pins=["1", "3", "4"], gender="female"))
    h.net("P24", current_A=cur, voltage_V=volts)
    h.net("0V", current_A=cur, voltage_V=volts, round_trip=False)
    h.net("B1_OUT", current_A=0.01, voltage_V=volts, signal=True)
    cord = Cable("CB1", [Wire(gauge, "BN"), Wire(gauge, "BU"), Wire(gauge, "BK")], length_mm=5000)
    h.connect_cable(cord, [x1["1"], b1["3"], b1["4"]], [b1["1"], x1["2"], x1["3"]], net=["P24", "0V", "B1_OUT"])
    h.sensor_output(b1["4"], part="Festo SMT-8M-A-PS-24V-E-0,3-M8D")
    return h


def _chk(h, start):
    return [c for c in analyze(h).checks if c.name.startswith(start)]


def test_cordset_and_terminal_are_graded_not_skipped():
    rep = analyze(_h())
    names = {c.name: c for c in rep.checks}
    assert names["connector rating B1 (M8-3, 4 A/pin)"].severity == "pass"
    assert names["connector rating X1 (WAGO-2002-1201, 20 A/pin)"].severity == "pass"
    assert names["connector voltage B1 (M8-3, 60 V)"].severity == "pass"
    assert names["wires fit the X1 terminals (WAGO-2002-1201, 0.25-4 mm²)"].severity == "pass"
    assert not [c for c in rep.checks if c.status == "not_run" and c.category == "ampacity"]
    for c in rep.checks:
        if c.name.startswith(("connector rating", "connector voltage", "wires fit")):
            assert any(s.basis == "document" and s.url.startswith("https://") for s in c.source), (c.name, c.source)


def test_limits_fail_when_exceeded():
    assert _chk(_h(cur=4.5, gauge="0.75mm2"), "connector rating B1")[0].severity == "fail"
    assert _chk(_h(volts=48, sensor_type="M8-4", cur=0.03), "connector voltage B1")[0].severity == "fail"     # 30 V M8 4-pin
    assert _chk(_h(gauge="0.14mm2"), "wires fit the X1")[0].severity == "fail"                                # under 0.25 mm2
    nv = _chk(_h(volts=None), "connector voltage B1")[0]
    assert nv.status == "not_run"


def test_cutlist_says_how_each_end_is_terminated():
    rows = cutlist(_h())
    text = " ".join(str(r) for r in rows)
    assert "moulded into B1" in text and "push-in into X1" in text


def test_a_return_core_without_its_own_current_says_what_to_declare():
    """The sample's first failure: a thin 0 V cable core with no current_A takes the whole 0 V net's current and fails "one gauge
    per net". The check still fails (the design is what is wrong); its text now says which wire and how to declare its current."""
    def build(own):
        h = Harness("ret")
        g1 = h.add(Connector("G1", "BARE", pins=["+", "-"]))
        b1 = h.add(Connector("B1", "M8-3", pins=["1", "3", "4"]))
        s0 = h.splice("S0V", kind="terminal")
        h.net("P24", current_A=0.03, voltage_V=24)
        h.net("0V", current_A=0.115, voltage_V=24, round_trip=False)
        h.connect(s0, g1["-"], Wire("0.75mm2", "BU", 150), net="0V", current_A=0.1)   # the trunk: its own (lower) current
        cord = Cable("CB1", [Wire("0.25mm2", "BN"), Wire("0.25mm2", "BU")], length_mm=5000)
        links = h.connect_cable(cord, [g1["+"], b1["3"]], [b1["1"], s0], net=["P24", "0V"])
        if own:
            links[1].current_A = 0.03
        h.nc(b1["4"])
        return h
    bad = _chk(build(False), "one gauge per net")[0]
    assert bad.severity == "fail" and "has no current_A of its own" in bad.measured and "links[1].current_A" in bad.measured
    assert "cable CB1" in bad.measured
    assert _chk(build(True), "one gauge per net")[0].severity == "pass"
