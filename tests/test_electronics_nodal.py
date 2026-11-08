"""The nodal solver (MNA with an ideal-diode iteration) for branched networks, its agreement with the closed-form series solver,
and the 0.6.0 electronics decisions: the conservative-figure rule, catalog LEDs as sourced parts, Wire's internal pull-ups on AVR."""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from eitri.electronics import Circuit, CircuitError, analyze_dc
from eitri.electronics import catalog, edits as E
from eitri.electronics.analysis import series_by_nodal
from eitri.electronics.nodal import Elem, solve
from eitri.electronics.report import analyze
from eitri.electronics.sources import SOURCES

ROOT = Path(__file__).resolve().parents[1]


def uno(name="t"):
    c = Circuit(name)
    b = c.board("UNO", "uno")
    c.net("5V", b["5V"])
    c.net("GND", b["GND"])
    return c, b


def check(c, needle, sev=None):
    hits = [x for x in analyze(c).checks if needle in x.name]
    assert len(hits) == 1, (needle, [x.name for x in analyze(c).checks])
    if sev:
        assert hits[0].severity == sev, (hits[0].severity, hits[0].measured)
    return hits[0]


def parallel_leds(colors=("green", "green"), ohm=150, pin="D9", **led):
    """An output pin, one resistor, then LEDs in parallel to ground."""
    c, b = uno()
    c.gpio(b[pin], "out")
    r = c.resistor("R1", ohm)
    c.net("PIN", b[pin], r["1"])
    c.net("LED_A", r["2"])
    for k, col in enumerate(colors):
        d = c.led(f"LED{k + 1}", col, **led)
        c.connect(d["A"], net="LED_A")
        c.connect(d["K"], "UNO.GND")
    return c


# --------------------------------------------------------------------------- the solver itself


def test_solver_basics():
    s = solve([Elem("R1", "R", "V", "X", 1000), Elem("R2", "R", "X", "G", 1000), Elem("R3", "R", "X", "G", 2000)], {"V": 5.0, "G": 0.0})
    assert s.v["X"] == pytest.approx(5 * (2000 / 3) / (1000 + 2000 / 3), abs=1e-6)
    # a reverse-biased diode carries nothing
    s = solve([Elem("R", "R", "V", "X", 100), Elem("D", "DIODE", "G", "X", 0.7), Elem("R2", "R", "X", "G", 100)], {"V": 5.0, "G": 0.0})
    assert s.i["D"] == 0.0
    # a load at its rated current through a resistor that would allow more
    s = solve([Elem("M", "LIMIT", "V", "X", 0.5), Elem("R", "R", "X", "G", 10)], {"V": 12.0, "G": 0.0})
    assert s.i["M"] == pytest.approx(0.5) and s.v["X"] == pytest.approx(5.0, abs=1e-6)
    # a switch straight onto a node an LED takes to ground: nothing limits it
    s = solve([Elem("S", "WIRE", "V", "X"), Elem("L", "DIODE", "X", "G", 1.85), Elem("R", "R", "X", "G", 100)], {"V": 5.0, "G": 0.0})
    assert s.i["S"] == float("inf") and s.i["L"] == float("inf")


def test_solver_parallel_diodes_share_or_hog():
    fixed = {"V": 5.0, "G": 0.0}
    same = solve([Elem("R", "R", "V", "X", 150), Elem("A", "DIODE", "X", "G", 2.2), Elem("B", "DIODE", "X", "G", 2.2)], fixed)
    assert same.i["A"] == pytest.approx(same.i["B"], rel=1e-6) and same.i["A"] + same.i["B"] == pytest.approx((5 - 2.2) / 150, rel=1e-6)
    mixed = solve([Elem("R", "R", "V", "X", 150), Elem("A", "DIODE", "X", "G", 1.85), Elem("B", "DIODE", "X", "G", 2.2)], fixed)
    assert mixed.i["A"] == pytest.approx((5 - 1.85) / 150, rel=1e-6) and mixed.i["B"] == 0.0


# --------------------------------------------------------------------------- branched circuits in the kernel


def test_parallel_matched_leds_on_one_resistor_are_solved_and_graded_on_the_worst_case():
    c = parallel_leds(("green", "green"), ohm=150)
    an = analyze_dc(c)
    assert not an.branches and an.networks and "LED_A" in an.networks[0]["nodes"]
    total = (5 - 2.2) / 150                                                     # 18.7 mA through R1
    assert an.part_current("R1") == pytest.approx(total, rel=1e-6)
    s1 = an.through("LED1")[0]
    assert s1.typical == pytest.approx(total / 2, rel=1e-6)                     # typical Vf on both: an even split
    assert s1.current == pytest.approx(total, rel=1e-6)                         # LED1 typical, LED2 at its 2.5 V max: all of it
    rep = analyze(c)
    assert "currents solved on every net" in {x.name for x in rep.checks if x.severity == "pass"}
    led = check(c, "LED current LED1", "pass")
    assert "shares its current with LED2" in led.measured and "18.7 mA of 25 mA" in led.measured
    assert check(c, "pin current UNO.D9", "warn").measured.startswith("18.7 mA of 20 mA")       # 93 %: within 20 % of the pin limit
    # a resistor that lets each LED risk more than its rating fails it, though the even split looks fine
    c2 = parallel_leds(("green", "green"), ohm=100)
    assert check(c2, "LED current LED1", "fail").measured.startswith("28 mA of 25 mA")


def test_parallel_mismatched_leds_show_current_hogging():
    c = parallel_leds(("red", "green"), ohm=220)
    an = analyze_dc(c)
    red, green = an.through("LED1")[0], an.through("LED2")[0]
    assert red.typical == pytest.approx((5 - 1.85) / 220, rel=1e-6) and green.typical == 0.0
    w = check(c, "LED current LED2", "warn")
    assert "current hogging" in w.measured and "LED1 (1.85 V)" in w.measured and "one series resistor per LED" in w.measured
    assert check(c, "LED current LED1", "pass")
    assert not [x for x in analyze(c).checks if "not solved" in x.measured]


def test_divider_feeding_two_loads():
    c, b = uno()
    r1, r2 = c.resistor("R1", 10_000), c.resistor("R2", 10_000)
    rl1, rl2 = c.resistor("RL1", 20_000), c.resistor("RL2", 20_000)
    c.connect(r1["1"], "UNO.5V")
    c.net("MID", r1["2"], r2["1"], rl1["1"], rl2["1"])
    for r in (r2, rl1, rl2):
        c.connect(r["2"], "UNO.GND")
    an = analyze_dc(c)
    g = 1 / 10_000 + 2 / 20_000
    v = (5 / 10_000) / (1 / 10_000 + g)                                        # 1.667 V
    assert an.part_current("R1") == pytest.approx((5 - v) / 10_000, rel=1e-6)
    assert an.part_current("RL1") == pytest.approx(v / 20_000, rel=1e-6) and an.part_current("R2") == pytest.approx(v / 10_000, rel=1e-6)
    assert max(x for x, *_ in an.voltages_at("MID")) == pytest.approx(v, abs=1e-3)
    rail = check(c, "rail budget 5V")
    assert "R1" in rail.measured
    assert not an.branches


def test_two_strings_off_one_pin():
    # straight off the pin: two series strings (closed form), the pin carries both
    c, b = uno()
    c.gpio(b["D5"], "out")
    c.net("PIN", b["D5"])
    for k in (1, 2):
        r, d = c.resistor(f"R{k}", 330), c.led(f"LED{k}", part="WP7113SRD/D")
        c.connect(r["1"], net="PIN"); c.connect(r["2"], d["A"]); c.connect(d["K"], "UNO.GND")
    an = analyze_dc(c)
    assert not an.networks and len(an.strings) == 2
    one = (5 - 1.85) / 330
    assert check(c, "pin current UNO.D5").measured.startswith("19.1 mA of 20 mA")
    for s in an.strings:
        for va, vb, i, _n in s.combos:
            assert series_by_nodal(c, s, va, vb) == pytest.approx(i, rel=1e-6, abs=1e-9)
    assert an.part_current("LED1") == pytest.approx(one, rel=1e-9)
    # through a shared series resistor first: a branched network, the same total rule
    c2, b2 = uno()
    c2.gpio(b2["D5"], "out")
    r0 = c2.resistor("R0", 100)
    c2.net("PIN", b2["D5"], r0["1"])
    c2.net("N", r0["2"])
    for k in (1, 2):
        r, d = c2.resistor(f"R{k}", 470), c2.led(f"LED{k}", part="WP7113SRD/D")
        c2.connect(r["1"], net="N"); c2.connect(r["2"], d["A"]); c2.connect(d["K"], "UNO.GND")
    an2 = analyze_dc(c2)
    vn = (5 / 100 + 2 * 1.85 / 470) / (1 / 100 + 2 / 470)
    assert an2.part_current("R0") == pytest.approx((5 - vn) / 100, rel=1e-6)
    assert an2.through("LED1")[0].typical == pytest.approx((vn - 1.85) / 470, rel=1e-6)
    assert an2.part_current("LED1") > an2.through("LED1")[0].typical         # with LED2 at its Vf max, LED1 takes more
    assert not an2.branches


def test_closed_form_and_nodal_agree_on_every_series_string():
    from eitri.model import load_model
    circuits = [load_model(ROOT / "eitri" / "demos" / "uno-arm-circuit" / "uno_arm_circuit.py", config=cfg).build() for cfg in (None, "bad")]
    c, b = uno()
    c.gpio(b["D3"], "out")
    q, k, d = c.bjt("Q1", "PN2222A"), c.relay("K1"), c.diode("D1", "1N4007")
    rb = c.resistor("RB", 1000)
    c.connect(b["D3"], rb["1"]); c.net("BASE", rb["2"], q["B"]); c.connect(q["E"], "UNO.GND")
    c.connect(k["COIL1"], "UNO.5V"); c.net("CL", k["COIL2"], q["C"], d["A"]); c.connect(d["K"], "UNO.5V")
    psu = c.supply("P12", 12, 2.0)
    m = c.motor("M1", 12, 0.8)
    c.net("12V", psu["+"], k["COM"]); c.connect(k["NO"], m["+"]); c.connect(m["-"], "UNO.GND"); c.connect(psu["-"], "UNO.GND")
    circuits.append(c)
    n = 0
    for circ in circuits:
        an = analyze_dc(circ)
        for s in an.strings:
            if s.network is not None:
                continue
            for va, vb, i, _node in s.combos:
                got = series_by_nodal(circ, s, va, vb)
                assert got == pytest.approx(i, rel=1e-6, abs=1e-9), (s.describe(), va, vb, i, got)
                n += 1
    assert n >= 20


def test_unlimited_current_inside_a_network_fails():
    c, b = uno()
    sw, d, r = c.switch("SW1"), c.led("LED1", part="WP7113SRD/D"), c.resistor("R1", 1000)
    c.connect(sw["1"], "UNO.5V")
    c.net("X", sw["2"], d["A"], r["1"])
    c.connect(d["K"], "UNO.GND"); c.connect(r["2"], "UNO.GND")
    r2 = c.resistor("R2", 1000)
    c.connect(r2["1"], net="X"); c.connect(r2["2"], "UNO.GND")
    bad = {x.name for x in analyze(c).failed if x.name.startswith("current limited")}
    assert bad == {"current limited: SW1", "current limited: LED1"}, bad


# --------------------------------------------------------------------------- decisions: catalog, LEDs, Wire pull-ups


def test_conservative_figure_rule():
    assert catalog.SERVOS["MG996R"]["stall"] == 2.5                       # the higher draw (TowerPro 1.4 A, distributor 2.5 A at 6 V)
    assert catalog.SERVOS["MG996R"]["v"] == (4.8, 6.6)                     # the narrower limit
    assert "higher figure for a draw" in __import__("eitri.electronics.sources", fromlist=["x"]).__doc__
    assert SOURCES["towerpro-sg90"].assumption and catalog.SERVOS["SG90"]["stall"] == 2.0              # the higher draw of "0.5A - 2A"
    assert catalog.PARTS["L7805"]["spec"]["iq"] == 0.008 and catalog.PARTS["L7805"]["srcs"]["iq"] == "st-l7805"
    # the other domains use the same MG996R draw where they quote one
    fw = (ROOT / "eitri" / "agent" / "firmware_ref.md").read_text()
    assert "MG996R: 2.5 A" in fw
    from eitri import power
    assert "conservative figure" in power.__doc__


def test_catalog_led_part_is_sourced():
    c, b = uno()
    c.gpio(b["D13"], "out")
    r, d = c.resistor("R1", 330), c.led("LED1", part="WP7113SGD")
    c.connect(b["D13"], r["1"]); c.connect(r["2"], d["A"]); c.connect(d["K"], "UNO.GND")
    assert d.spec["vf"] == 2.2 and d.spec["if_max"] == 0.025 and not d.spec["generic"] and d.catalog == "WP7113SGD"
    assert not [x for x in analyze(c).checks if x.name == "LED data from the LED's own datasheet"]
    assert c.led("LED2", part="wp7113srd").spec["color"] == "red"                    # case and the /D suffix do not matter
    with pytest.raises(CircuitError, match="no catalog LED"):
        c.led("LED3", part="5MM-GENERIC")
    c.led("LED4", "blue")
    assert check(c, "LED data from the LED's own datasheet", "warn")
    # changing the colour in the editor makes it a typical part again
    E.apply_edits(c, [{"op": "set_part", "part": "LED1", "color": "red"}])
    assert c.parts["LED1"].spec["generic"] and not c.parts["LED1"].catalog
    # vf given without vf_max: the catalog maximum is not kept
    assert "vf_max" not in c.led("LED5", "red", vf=2.0, if_max=0.02).spec


def _bme_on_uno(pullup_rail="3V3", ohm=4700, internal=None):
    c, b = uno()
    c.net("3V3", b["3V3"])
    bme = c.module("ENV", "BME280")
    c.connect(bme["VCC"], "UNO.3V3"); c.connect(bme["GND"], "UNO.GND")
    c.net("SDA", b["A4"], bme["SDA"]); c.net("SCL", b["A5"], bme["SCL"])
    for line in ("SDA", "SCL"):
        r = c.resistor(f"RP_{line}", ohm)
        c.connect(r["1"], f"UNO.{pullup_rail}"); c.connect(r["2"], net=line)
    if internal is not None:
        c.i2c("SDA", internal_pullups=internal)
    return c


def test_avr_wire_pullups_warn_on_a_3v3_device():
    c = _bme_on_uno()
    w = check(c, "AVR internal pull-ups on I2C SDA (UNO)", "warn")
    v = (3.3 / 4700 + 5 / 20_000) / (1 / 4700 + 1 / 20_000)                       # 3.62 V with the 20 kOhm end
    assert f"SDA rises to {v:.2f} V" in w.measured and "ENV.SDA tolerates at most 3.6 V" in w.measured
    assert "level shifter" in w.measured and "internal_pullups=False" in w.measured and "ENV" in w.refs
    assert "arduino-wire-avr" in SOURCES and "LGPL" in SOURCES["arduino-wire-avr"].doc
    # declared off (a 3.3 V-only bus): it passes, and so does the rest of the I2C bus
    ok = _bme_on_uno(internal=False)
    assert "declared off" in check(ok, "AVR internal pull-ups", "pass").measured
    rep = analyze(ok)
    assert not [x.line() for x in rep.failed + rep.warnings if x.category == "i2c"]
    # the editor's net panel can switch it
    E.apply_edits(c, [{"op": "set_net", "net": "SDA", "internal_pullups": "off"}])
    assert check(c, "AVR internal pull-ups", "pass")


def test_avr_wire_pullups_pass_on_5v_devices_and_skip_esp32():
    c, b = uno()
    pwm = c.module("PWM", "PCA9685")
    c.connect(pwm["VCC"], "UNO.5V"); c.connect(pwm["GND"], "UNO.GND")
    c.net("SDA", b["A4"], pwm["SDA"]); c.net("SCL", b["A5"], pwm["SCL"])
    assert "every device on the bus takes 5.5 V" in check(c, "AVR internal pull-ups", "pass").measured
    e = Circuit("e")
    esp = e.board("ESP", "esp32")
    e.net("3V3", esp["3V3"]); e.net("GND", esp["GND"])
    bme = e.module("ENV", "BME280")
    e.connect(bme["VCC"], "ESP.3V3"); e.connect(bme["GND"], "ESP.GND")
    e.net("SDA", esp["GPIO21"], bme["SDA"]); e.net("SCL", esp["GPIO22"], bme["SCL"])
    assert not [x for x in analyze(e).checks if "AVR internal pull-ups" in x.name]


def test_requested_agent_circuit_can_reach_all_pass():
    """What the 0.6.0 agent run asks for, written by hand: two LEDs in parallel on one resistor and a 3.3 V I2C sensor on an Uno."""
    c = _bme_on_uno(internal=False)
    c.gpio(c.parts["UNO"]["D9"], "out")
    r = c.resistor("R1", 180)
    c.net("PIN", "UNO.D9", r["1"])
    c.net("LED_A", r["2"])
    for k in (1, 2):
        d = c.led(f"LED{k}", part="WP7113SGD")
        c.connect(d["A"], net="LED_A"); c.connect(d["K"], "UNO.GND")
    c.disconnect("UNO.5V")                                     # nothing here uses the 5 V pin
    rep = analyze(c)
    assert not rep.failed and not rep.warnings, [x.line() for x in rep.failed + rep.warnings]
    assert analyze_dc(c).networks
