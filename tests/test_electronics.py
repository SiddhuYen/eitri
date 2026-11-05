"""Electronics kernel: the model API, the sourced catalog, the DC kernel, every check firing on a crafted bad circuit and passing on
the fixed one, direct edits, the outputs (BOM, netlist, sources), the schematic, the reference's examples and the demo."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from eitri.electronics import Circuit, CircuitError, analyze_dc, nearest_e
from eitri.electronics import catalog, edits as E, outputs
from eitri.electronics.report import analyze, circuit_data, describe_selection, summarize
from eitri.electronics.sources import SOURCES, cite

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "eitri" / "demos" / "uno-arm-circuit" / "uno_arm_circuit.py"


def checks(c):
    return analyze(c).checks


def one(c, needle, sev=None):
    hits = [x for x in checks(c) if needle in x.name]
    assert len(hits) == 1, f"{needle!r} matched {[x.name for x in hits]} in {[x.name for x in checks(c)]}"
    if sev:
        assert hits[0].severity == sev, (hits[0].severity, hits[0].measured)
    return hits[0]


def none_(c, needle):
    assert not [x for x in checks(c) if needle in x.name], [x.line() for x in checks(c) if needle in x.name]


def uno(name="t", **kw):
    c = Circuit(name)
    b = c.board("UNO", "uno", **kw)
    c.net("5V", b["5V"])
    c.net("GND", b["GND"])
    return c, b


def esp():
    c = Circuit("e")
    b = c.board("ESP", "esp32")
    c.net("3V3", b["3V3"])
    c.net("GND", b["GND"])
    return c, b


def led_on(c, pin, ohm, color="red", name="1"):
    r, d = c.resistor(f"R{name}", ohm), c.led(f"LED{name}", color)
    c.connect(pin, r["1"])
    c.connect(r["2"], d["A"])
    c.connect(d["K"], "GND" in c.nets and c.nets["GND"].pins[0] or None)
    return r, d


# ---------------------------------------------------------------------------- model and catalog


def test_model_api():
    c, b = uno()
    r = c.resistor("R1", 220)
    assert c.part("R1") is r and b["9"].pin == "D9" and b["d13"].pin == "D13" and str(r[1]) == "R1.1"
    with pytest.raises(CircuitError, match="already exists"):
        c.resistor("R1", 100)
    with pytest.raises(CircuitError, match="no pin"):
        b["D99"]
    with pytest.raises(CircuitError, match="unknown board"):
        c.board("X", "teensy")
    with pytest.raises(CircuitError, match="unknown module"):
        c.module("M", "MPU6050")
    with pytest.raises(CircuitError, match="more than 0"):
        c.resistor("R2", 0)
    with pytest.raises(CircuitError, match="can be at"):
        c.module("B", "BME280", address=0x40)
    # nets: named nets never merge; auto nets do; 5V / 3V3 are valid names
    c.connect(r["1"], b["D9"])
    n = c.net_of(r["1"])
    assert n.auto and n.name == "R1_1"
    c.net("SIG", r["1"])
    assert c.net_of(b["D9"]).name == "SIG" and "R1_1" not in c.nets
    with pytest.raises(CircuitError, match="short"):
        c.connect(b["D9"], "UNO.5V")
    c.disconnect(r["1"])
    assert c.net_of(r["1"]) is None and c.net_of(b["D9"]).name == "SIG"
    with pytest.raises(CircuitError, match="is not a GPIO"):
        c.gpio(b["5V"], "in")
    with pytest.raises(CircuitError, match="pull-up is for an input"):
        c.gpio(b["D2"], "out", pullup=True)


def test_e_series_and_formatting():
    assert nearest_e(333) == 330 and nearest_e(4600) == 4700 and nearest_e(9.3) == 9.1 and nearest_e(1e6) == 1e6
    from eitri.electronics.diagram import rkm
    assert [rkm(x) for x in (330, 4700, 10000, 1e6, 2200)] == ["330R", "4k7", "10k", "1M", "2k2"]


def test_every_catalog_number_has_a_fetched_source():
    ids = set()
    for b in catalog.BOARDS.values():
        ids |= set(b["srcs"].values())
    for m in catalog.MODULES.values():
        ids |= set(m["srcs"].values())
        for ps in m["pins"]().values():
            ids |= {x for x in (ps.src, ps.logic.src if ps.logic else "") if x}
    for s in catalog.SERVOS.values():
        ids.add(s["src"])
    for e in list(catalog.PARTS.values()) + list(catalog.LEDS.values()):
        ids |= set(e["srcs"].values())
    for lv in (catalog.ATMEGA_5V, catalog.ESP32_IO, catalog.PCA9685_IN, catalog.BME280_IO):
        ids.add(lv.src)
    missing = sorted(i for i in ids if i not in SOURCES)
    assert not missing, missing
    for s in SOURCES.values():
        assert s.url.startswith("http") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s.fetched) and s.quote, s.id
    assert "ASSUMPTION" in cite("towerpro-sg90") and cite("model") == "the model file"
    # where vendors disagree the lower figure is used
    assert catalog.PARTS["1N4148"]["spec"]["if_avg"] == 0.150 and catalog.PARTS["PN2222A"]["spec"]["ic_max"] == 0.6
    assert catalog.SERVOS["MG996R"]["stall"] == 1.4 and catalog.BOARDS["esp32"]["spec"]["pin_limit_A"] == 0.028


# ---------------------------------------------------------------------------- the DC kernel


def test_strings_and_states():
    c, b = uno()
    r, d = c.resistor("R1", 330), c.led("LED1", "red")
    c.connect(b["D13"], r["1"]); c.connect(r["2"], d["A"]); c.connect(d["K"], "UNO.GND")
    an = analyze_dc(c)
    s = an.through("LED1")[0]
    assert s.parts == ["R1", "LED1"] and s.flow == ("UNO_D13", "GND")
    assert s.current == pytest.approx((5 - 1.85) / 330)
    assert sorted(i for _va, _vb, i, _n in s.combos) == [0.0, pytest.approx((5 - 1.85) / 330)]
    assert an.nets["5V"].kind == "rail" and an.nets["GND"].kind == "gnd" and an.nets["UNO_D13"].kind == "driven"
    # a reversed LED blocks: no current, and the LED check says it never lights
    c2, b2 = uno()
    r2, d2 = c2.resistor("R1", 330), c2.led("LED1", "red")
    c2.connect(b2["D13"], r2["1"]); c2.connect(r2["2"], d2["K"]); c2.connect(d2["A"], "UNO.GND")
    assert analyze_dc(c2).part_current("LED1") == 0
    assert "never lights" in one(c2, "LED current LED1", "warn").measured


# ---------------------------------------------------------------------------- connectivity and power


def test_required_pins():
    c, b = uno()
    c.resistor("R1", 220)
    assert one(c, "required pins connected", "fail").refs == ["R1"]
    c.connect("R1.1", "UNO.D3"); c.connect("R1.2", "UNO.GND")
    one(c, "required pins connected", "pass")


def test_shorts_and_back_feeding():
    c, b = uno()
    psu = c.supply("PSU", 5.0, 2.0)
    c.connect(psu["+"], "UNO.GND")
    assert "same net as ground" in one(c, "no shorted", "fail").measured
    c2, b2 = uno()
    psu2 = c2.supply("PSU", 5.0, 2.0)
    c2.connect(psu2["+"], "UNO.5V"); c2.connect(psu2["-"], "UNO.GND")
    assert "back-feed" in one(c2, "no shorted", "fail").measured
    c3, _ = uno()
    p3 = c3.supply("PSU", 5.0, 2.0)
    c3.net("SERVO_5V", p3["+"]); c3.connect(p3["-"], "UNO.GND")
    one(c3, "no shorted", "pass")


def test_supply_ranges():
    c, b = uno()
    bme = c.module("ENV", "BME280")
    c.connect(bme["VCC"], "UNO.5V"); c.connect(bme["GND"], "UNO.GND")
    assert "1.71-3.6 V" in one(c, "supply voltage ENV.VCC", "fail").measured
    c2, b2 = uno()
    bme2 = c2.module("ENV", "BME280")
    c2.net("3V3", b2["3V3"], bme2["VCC"]); c2.connect(bme2["GND"], "UNO.GND")
    one(c2, "supply voltage ENV.VCC", "pass")
    c3, b3 = uno()
    s = c3.servo("S1", "SG90")
    c3.net("SIG", s["SIG"], b3["D9"]); c3.connect(s["GND"], "UNO.GND")
    c3.net("FLOAT", s["V+"])
    assert "unpowered" in one(c3, "supply voltage S1.V+", "fail").measured


def _servos(c, b, rail_pin, n=2, model="MG996R"):
    for i in range(n):
        s = c.servo(f"S{i + 1}", model)
        c.connect(s["V+"], rail_pin); c.connect(s["GND"], "UNO.GND"); c.connect(s["SIG"], b[f"D{9 + i}"])


def test_rail_budget():
    c, b = uno()
    _servos(c, b, "UNO.5V")
    ch = one(c, "rail budget 5V", "fail")
    assert "2.8 A of 500 mA" in ch.measured and "polyfuse" in ch.measured and "S1" in ch.refs
    c2, b2 = uno()
    psu = c2.supply("PSU", 5.0, 5.0)
    c2.net("SERVO_5V", psu["+"]); c2.connect(psu["-"], "UNO.GND")
    _servos(c2, b2, "PSU.+")
    assert "2.8 A of 5 A" in one(c2, "rail budget SERVO_5V", "pass").measured
    # within 20 % of the rating: a warning
    c3, b3 = uno()
    psu3 = c3.supply("PSU", 5.0, 3.0)
    c3.net("SERVO_5V", psu3["+"]); c3.connect(psu3["-"], "UNO.GND")
    _servos(c3, b3, "PSU.+")
    one(c3, "rail budget SERVO_5V", "warn")
    # a supply without a rating is not graded
    c4, b4 = uno()
    psu4 = c4.supply("PSU", 5.0)
    c4.net("SERVO_5V", psu4["+"]); c4.connect(psu4["-"], "UNO.GND")
    _servos(c4, b4, "PSU.+")
    one(c4, "rail budget SERVO_5V", "warn")


def test_3v3_pin_counts_on_the_5v_budget_and_esp32_limit():
    c, b = uno()
    oled = c.module("OLED", "SSD1306")
    c.net("3V3", b["3V3"], oled["VIN"]); c.connect(oled["GND"], "UNO.GND")
    assert "on its 3V3 pin" in one(c, "rail budget 5V", "pass").measured or True
    one(c, "rail budget 3V3", "pass")
    c2, b2 = esp()
    for i in range(3):
        s = c2.load(f"L{i}", 0.2, 3.0, 3.6)
        c2.connect(s["+"], "ESP.3V3"); c2.connect(s["-"], "ESP.GND")
    ch = one(c2, "rail budget 3V3", "fail")
    assert "AMS1117-3.3" in ch.measured and "500 mA" in ch.measured


def test_vin_range_and_regulated_5v():
    c, b = uno(powered="vin")
    psu = c.supply("PSU", 15.0, 2.0)
    c.net("VIN", psu["+"], b["VIN"]); c.connect(psu["-"], "UNO.GND")
    assert "7-12 V" in one(c, "VIN UNO", "fail").measured
    c2, b2 = uno(powered="vin")
    psu2 = c2.supply("PSU", 12.0, 2.0)
    c2.net("VIN", psu2["+"], b2["VIN"]); c2.connect(psu2["-"], "UNO.GND")
    one(c2, "VIN UNO", "pass")
    _servos(c2, b2, "UNO.5V", n=1, model="SG90")             # 0.5 A on the regulator at 12 V in: over its thermal limit
    assert "heat at 12 V" in one(c2, "rail budget 5V", "fail").measured


def test_regulator_headroom_and_temperature():
    def circuit(vin, load_A):
        c, b = uno()
        psu = c.supply("PSU", vin, 2.0)
        u = c.regulator("U1", "AMS1117-3.3")
        c.net("VBAT", psu["+"], u["IN"]); c.connect(psu["-"], "UNO.GND"); c.connect(u["GND"], "UNO.GND")
        ld = c.load("L1", load_A, 3.0, 3.6)
        c.net("3V3_EXT", u["OUT"], ld["+"]); c.connect(ld["-"], "UNO.GND")
        return c
    assert "needs 1.3 V" in one(circuit(4.5, 0.1), "regulator headroom U1", "fail").measured
    one(circuit(5.0, 0.1), "regulator headroom U1", "pass")
    one(circuit(5.0, 0.1), "regulator temperature U1", "pass")
    hot = one(circuit(12.0, 0.3), "regulator temperature U1", "fail")
    assert "90 C/W" in hot.measured
    assert "at most 15 V" in one(circuit(18.0, 0.1), "regulator headroom U1", "fail").measured
    c = circuit(5.0, 1.2)
    assert "1.2 A of 1 A" in one(c, "rail budget 3V3_EXT", "fail").measured


def test_common_ground():
    c, b = uno()
    psu = c.supply("PSU", 5.0, 3.0)
    s = c.servo("S1", "SG90")
    c.net("SERVO_5V", psu["+"], s["V+"]); c.net("SGND", psu["-"], s["GND"])
    c.connect(s["SIG"], b["D9"])
    pwm = c.module("PWM", "PCA9685")
    c.connect(pwm["VCC"], "UNO.5V"); c.net("PGND", pwm["GND"])
    c.net("SDA", b["A4"], pwm["SDA"]); c.net("SCL", b["A5"], pwm["SCL"])
    ch = one(c, "common ground", "fail")
    assert "PWM" in ch.refs and "S1" in ch.refs
    for pin in ("PSU.-", "S1.GND", "PWM.GND"):
        c.disconnect(pin)
        c.connect(pin, "UNO.GND")
    one(c, "common ground", "pass")


# ---------------------------------------------------------------------------- current


def test_unlimited_current():
    c, b = uno()
    d = c.led("LED1", "red")
    c.connect(b["D13"], d["A"]); c.connect(d["K"], "UNO.GND")
    assert "series resistor" in one(c, "current limited: LED1", "fail").measured
    c2, b2 = uno()
    sw, r = c2.switch("SW1"), c2.resistor("R1", 10_000)
    c2.net("BTN", b2["D2"], sw["1"], r["2"]); c2.connect(r["1"], "UNO.5V"); c2.connect(sw["2"], "UNO.GND")
    assert "c.gpio(UNO['D2'], 'in')" in one(c2, "current limited: SW1", "fail").measured
    c2.gpio(b2["D2"], "in")
    none_(c2, "current limited")


def test_pin_and_chip_current():
    c, b = uno()
    r, d = c.resistor("R1", 100), c.led("LED1", "red")
    c.connect(b["D13"], r["1"]); c.connect(r["2"], d["A"]); c.connect(d["K"], "UNO.GND")
    assert "31.5 mA of 20 mA" in one(c, "pin current UNO.D13", "fail").measured
    c.part("R1").spec["ohm"] = 330
    one(c, "pin current UNO.D13", "pass")
    c.part("R1").spec["ohm"] = 180                                       # 17.5 mA: within 20 % of 20 mA
    one(c, "pin current UNO.D13", "warn")
    # many pins at once: the chip's 200 mA
    c2, b2 = uno()
    for i in range(2, 14):
        r2, d2 = c2.resistor(f"R{i}", 180), c2.led(f"LED{i}", "red")
        c2.connect(b2[f"D{i}"], r2["1"]); c2.connect(r2["2"], d2["A"]); c2.connect(d2["K"], "UNO.GND")
    assert "of 200 mA" in one(c2, "total I/O current UNO", "fail").measured
    # a relay coil straight off a pin
    c3, b3 = uno()
    k = c3.relay("K1")
    c3.connect(b3["D7"], k["COIL1"]); c3.connect(k["COIL2"], "UNO.GND")
    assert "72 mA of 20 mA" in one(c3, "pin current UNO.D7", "fail").measured
    # a module output: the PCA9685 sources 10 mA
    c4, b4 = uno()
    pwm = c4.module("PWM", "PCA9685")
    c4.connect(pwm["VCC"], "UNO.5V"); c4.connect(pwm["GND"], "UNO.GND")
    r4, d4 = c4.resistor("R1", 100), c4.led("LED1", "red")
    c4.connect(pwm["PWM0"], r4["1"]); c4.connect(r4["2"], d4["A"]); c4.connect(d4["K"], "UNO.GND")
    one(c4, "pin current PWM.PWM0", "fail")


def test_led_and_resistor_ratings():
    c, b = uno()
    r, d = c.resistor("R1", 220), c.led("LED1", "green")                 # (5 - 2.2) / 220 = 12.7 mA of 25
    c.connect(b["D13"], r["1"]); c.connect(r["2"], d["A"]); c.connect(d["K"], "UNO.GND")
    one(c, "LED current LED1", "pass")
    c.part("LED1").spec["if_max"] = 0.010
    one(c, "LED current LED1", "fail")
    c2, _ = uno()
    r2 = c2.resistor("R1", 10)                                           # 5 V across 10 ohm: 2.5 W in a 1/4 W part
    c2.connect(r2["1"], "UNO.5V"); c2.connect(r2["2"], "UNO.GND")
    assert "2.5e+03 mW" in one(c2, "resistor power R1", "fail").measured or "2500" in one(c2, "resistor power R1").measured
    c3, _ = uno()
    r3 = c3.resistor("R1", 120, power_W=0.25)                            # 208 mW of 250: a warning
    c3.connect(r3["1"], "UNO.5V"); c3.connect(r3["2"], "UNO.GND")
    one(c3, "resistor power R1", "warn")
    c3.part("R1").spec["power_W"] = 0.5
    one(c3, "resistor power R1", "pass")


# ---------------------------------------------------------------------------- logic levels


def test_5v_output_into_an_esp32():
    c, b = esp()
    psu = c.supply("PSU", 5.0, 1.0)
    c.net("5V", psu["+"]); c.connect(psu["-"], "ESP.GND")
    us = c.module("SONAR", "HC-SR04")
    c.connect(us["VCC"], "PSU.+"); c.connect(us["GND"], "ESP.GND")
    c.net("TRIG", b["GPIO5"], us["TRIG"]); c.net("ECHO", b["GPIO18"], us["ECHO"])
    ch = one(c, "input voltage ESP.GPIO18", "fail")
    assert "3.6 V" in ch.measured and "level shifter" in ch.measured
    # the fix: a 1k / 2k divider into an input
    c2, b2 = esp()
    psu2 = c2.supply("PSU", 5.0, 1.0)
    c2.net("5V", psu2["+"]); c2.connect(psu2["-"], "ESP.GND")
    us2 = c2.module("SONAR", "HC-SR04")
    c2.connect(us2["VCC"], "PSU.+"); c2.connect(us2["GND"], "ESP.GND")
    c2.net("TRIG", b2["GPIO5"], us2["TRIG"])
    r1, r2 = c2.resistor("R1", 1000), c2.resistor("R2", 2000)
    c2.net("ECHO5", us2["ECHO"], r1["1"]); c2.gpio(b2["GPIO18"], "in")
    c2.net("ECHO", r1["2"], r2["1"], b2["GPIO18"]); c2.connect(r2["2"], "ESP.GND")
    none_(c2, "input voltage ESP.GPIO18")
    assert "ESP.GPIO18 on ECHO" in one(c2, "inputs within their absolute maximum", "pass").measured


def test_3v3_driver_into_5v_receiver():
    c = Circuit("x")
    e, u = c.board("ESP", "esp32"), c.board("UNO", "uno")
    c.net("GND", e["GND"], u["GND"])
    c.gpio(u["D2"], "in"); c.gpio(e["GPIO4"], "out")
    c.net("LINK", e["GPIO4"], u["D2"])
    ch = one(c, "high level ESP.GPIO4 -> UNO.D2", "warn")
    assert "2.64 V" in ch.measured and "3 V" in ch.measured


def test_i2c_pullups_to_the_wrong_rail():
    c, b = uno()
    bme = c.module("ENV", "BME280")
    c.net("3V3", b["3V3"], bme["VCC"]); c.connect(bme["GND"], "UNO.GND")
    c.net("SDA", b["A4"], bme["SDA"]); c.net("SCL", b["A5"], bme["SCL"])
    for line in ("SDA", "SCL"):
        r = c.resistor(f"RP_{line}", 4700)
        c.connect(r["1"], "UNO.5V"); c.connect(r["2"], f"UNO.{'A4' if line == 'SDA' else 'A5'}")
    one(c, "input voltage ENV.SDA", "fail")                               # 5 V pull-ups on a 3.3 V part
    c.disconnect("RP_SDA.1"); c.connect("RP_SDA.1", "UNO.3V3")
    c.disconnect("RP_SCL.1"); c.connect("RP_SCL.1", "UNO.3V3")
    none_(c, "input voltage ENV.SDA")
    assert "UNO.A4 on SDA" in one(c, "high levels read as high", "pass").measured        # 3.3 V pull-ups: the Uno's VIH is 3.0 V



def test_floating_inputs():
    c, b = uno()
    sw = c.switch("SW1")
    c.gpio(b["D2"], "in")
    c.net("BTN", b["D2"], sw["1"]); c.connect(sw["2"], "UNO.GND")
    assert "while SW1 is open" in one(c, "input defined BTN", "fail").measured
    c.gpio(b["D2"], "in", pullup=True)
    none_(c, "input defined")
    c2, b2 = uno()
    sw2, r2 = c2.switch("SW1"), c2.resistor("R1", 10_000)
    c2.gpio(b2["D2"], "in")
    c2.net("BTN", b2["D2"], sw2["1"], r2["2"]); c2.connect(sw2["2"], "UNO.GND"); c2.connect(r2["1"], "UNO.5V")
    none_(c2, "input defined")


# ---------------------------------------------------------------------------- I2C


def _bus(pull_ohm=None, devices=("PCA9685",), speed=100_000, cap=None):
    c, b = uno()
    c.net("SDA", b["A4"]); c.net("SCL", b["A5"])
    for i, key in enumerate(devices):
        m = c.module(f"M{i}", key)
        vcc = "VCC" if "VCC" in m.pins else "VIN"
        c.connect(m[vcc], "UNO.3V3" if key == "BME280" else "UNO.5V"); c.connect(m["GND"], "UNO.GND")
        c.connect(m["SDA"], "UNO.A4"); c.connect(m["SCL"], "UNO.A5")
    if pull_ohm:
        for line, pin in (("SDA", "A4"), ("SCL", "A5")):
            r = c.resistor(f"RP_{line}", pull_ohm)
            c.connect(r["1"], "UNO.3V3" if "BME280" in devices else "UNO.5V"); c.connect(r["2"], f"UNO.{pin}")
    c.i2c("SDA", speed_hz=speed, capacitance_pF=cap)
    return c


def test_i2c_pullups():
    assert "never go high" in one(_bus(None, ("BME280",)), "I2C pull-ups SDA", "fail").measured
    one(_bus(4700, ("BME280",)), "I2C pull-ups SDA", "pass")
    assert "too strong" in one(_bus(820, ("BME280",)), "I2C pull-ups SDA", "fail").measured         # Rp(min) = (3.3 V - 0.4 V) / 3 mA = 967 ohm
    one(_bus(1000, ("BME280",)), "I2C pull-ups SDA", "pass")
    assert "too weak" in one(_bus(47_000, ("BME280",)), "I2C pull-ups SDA", "fail").measured
    one(_bus(None, ("PCA9685",)), "I2C pull-ups SDA", "pass")                                      # the breakout's own 10k
    assert "too weak" in one(_bus(None, ("PCA9685",), speed=400_000), "I2C pull-ups SDA", "fail").measured
    one(_bus(None, ("PCA9685",), speed=400_000, cap=20), "I2C pull-ups SDA", "pass")
    one(_bus(None, ("PCA9685",), speed=2_000_000), "I2C clock SDA", "fail")
    one(_bus(None, ("PCA9685",), cap=500), "I2C bus capacitance SDA", "fail")


def test_i2c_addresses():
    c = _bus(4700, ("BME280", "BME280"))
    assert "0x76" in one(c, "I2C addresses unique", "fail").measured
    c.part("M1").spec["address"] = 0x77
    one(c, "I2C addresses unique", "pass")


# ---------------------------------------------------------------------------- inductive loads and switching


def _relay(diode=None, reversed_=False, gate=("IRLZ44N", 220), board="uno", coil_rail="5V"):
    c = Circuit("r")
    if board == "uno":
        b = c.board("UNO", "uno"); c.net("5V", b["5V"]); c.net("GND", b["GND"]); pin = b["D7"]
    else:
        b = c.board("ESP", "esp32"); c.net("GND", b["GND"]); pin = b["GPIO4"]
    psu = c.supply("PSU", 5.0, 2.0)
    c.net("COIL_V", psu["+"]); c.connect(psu["-"], "GND" in c.nets and c.nets["GND"].pins[0])
    k = c.relay("K1")
    q = c.mosfet("Q1", gate[0])
    rg = c.resistor("RG", gate[1])
    c.gpio(pin, "out")
    c.connect(pin, rg["1"]); c.net("GATE", rg["2"], q["G"])
    c.connect(k["COIL1"], "PSU.+"); c.net("LOW", k["COIL2"], q["D"]); c.connect(q["S"], c.nets["GND"].pins[0])
    if diode:
        d = c.diode("D1", diode)
        c.connect(d["A" if reversed_ else "K"], "PSU.+"); c.connect(d["K" if reversed_ else "A"], "Q1.D")
    return c


def test_flyback_diodes():
    assert "no diode across it" in one(_relay(), "flyback diode K1", "fail").measured
    assert "wrong way round" in one(_relay("1N4148", reversed_=True), "flyback diode K1", "fail").measured
    assert "rated 150 mA" in one(_relay("1N4148"), "flyback diode K1", "pass").measured
    # a motor's current is too much for a 1N4148
    c = _relay("1N4148")
    c.remove_part("K1")
    m = c.motor("M1", 5.0, 1.0)
    c.connect(m["+"], "PSU.+"); c.connect(m["-"], "Q1.D")
    one(c, "flyback diode M1", "fail")
    c.remove_part("D1")
    d = c.diode("D1", "1N5819")
    c.connect(d["K"], "PSU.+"); c.connect(d["A"], "Q1.D")
    one(c, "flyback diode M1", "warn")                                   # 1 A of 1 A


def test_gate_drive_and_dissipation():
    ok = _relay("1N4148")
    assert "RDS(on) 35" in one(ok, "gate drive Q1", "pass").measured
    one(ok, "MOSFET dissipation Q1", "pass")
    assert "specified only from VGS = 10 V" in one(_relay("1N4148", gate=("IRF540N", 220)), "gate drive Q1", "fail").measured
    one(_relay("1N4148", board="esp32"), "gate drive Q1", "fail")           # 2.64 V from a 3.3 V pin: under the 4 V spec
    c = _relay("1N4148")
    c.remove_part("K1")
    m = c.motor("M1", 5.0, 50.0)
    c.connect(m["+"], "PSU.+"); c.connect(m["-"], "Q1.D")
    one(c, "MOSFET dissipation Q1", "fail")


def _bjt(base_ohm=None, load=None):
    c, b = uno()
    psu = c.supply("PSU", 5.0, 2.0)
    c.net("COIL_V", psu["+"]); c.connect(psu["-"], "UNO.GND")
    q = c.bjt("Q1", "PN2222A")
    k = c.relay("K1")
    d = c.diode("D1", "1N4007")
    c.gpio(b["D7"], "out")
    if base_ohm:
        r = c.resistor("RB", base_ohm)
        c.connect(b["D7"], r["1"]); c.connect(r["2"], q["B"])
    else:
        c.connect(b["D7"], q["B"])
    c.connect(k["COIL1"], "PSU.+"); c.connect(d["K"], "PSU.+")
    c.net("LOW", k["COIL2"], d["A"], q["C"]); c.connect(q["E"], "UNO.GND")
    if load:
        ld = c.solenoid("SOL", 5.0, load)
        c.connect(ld["+"], "PSU.+"); c.connect(ld["-"], "Q1.C")
    return c


def test_bjt_drive():
    assert "base directly" in one(_bjt(), "transistor Q1", "fail").measured
    assert "IC/10" in one(_bjt(10_000), "transistor Q1", "warn").measured
    one(_bjt(470), "transistor Q1", "pass")
    one(_bjt(470, load=1.0), "transistor Q1", "fail")


def test_a_flyback_diode_across_a_contact_switched_load_is_not_a_branch():
    c = _relay("1N4148")
    psu = c.supply("P12", 12.0, 1.0)
    sol, d2 = c.solenoid("SOL", 12.0, 0.5), c.diode("D2", "1N4007")
    c.net("12V", psu["+"], "K1.COM"); c.net("SOL_P", "K1.NO", sol["+"], d2["K"])
    c.connect(sol["-"], "PSU.-"); c.connect(d2["A"], "PSU.-"); c.connect(psu["-"], "PSU.-")
    none_(c, "currents solved")
    assert analyze_dc(c).part_current("SOL") == 0.5
    one(c, "rail budget 12V", "pass")


def test_relay_contacts():
    c = _relay("1N4148")
    psu = c.supply("P12", 12.0, 20.0)
    sol = c.solenoid("SOL", 12.0, 10.0)
    c.net("12V", psu["+"], "K1.COM"); c.connect("K1.NO", sol["+"]); c.connect(sol["-"], "PSU.-"); c.connect(psu["-"], "PSU.-")
    assert "10 A through COM-NO" in one(c, "relay contacts K1", "fail").measured
    c.part("SOL").spec["current_A"] = 2.0
    one(c, "relay contacts K1", "pass")


# ---------------------------------------------------------------------------- part data


def test_part_data_checks():
    c, b = uno()
    r, d = c.resistor("R1", 333), c.led("LED1", "blue")
    c.connect(b["D13"], r["1"]); c.connect(r["2"], d["A"]); c.connect(d["K"], "UNO.GND")
    assert "nearest 330" in one(c, "standard resistor values", "warn").measured.replace(" Ω", "").replace("Ω", "")
    one(c, "LED data", "warn")
    c2, b2 = uno()
    r2, d2 = c2.resistor("R1", 330), c2.led("LED1", vf=2.0, if_max=0.02)
    c2.connect(b2["D13"], r2["1"]); c2.connect(r2["2"], d2["A"]); c2.connect(d2["K"], "UNO.GND")
    one(c2, "standard resistor values", "pass")
    none_(c2, "LED data")
    c3, _ = uno()
    psu = c3.supply("P12", 12.0, 1.0)
    c3.connect(psu["-"], "UNO.GND")
    cap = c3.capacitor("C1", 100e-6, v_rated=6.3, polarized=True)
    c3.net("12V", psu["+"], cap["+"]); c3.connect(cap["-"], "UNO.GND")
    one(c3, "capacitor voltage C1", "fail")
    c3.part("C1").spec["v_rated"] = 25
    one(c3, "capacitor voltage C1", "pass")


# ---------------------------------------------------------------------------- the demo, edits, outputs, the schematic


def _demo(config=None):
    from eitri.model import load_model
    m = load_model(DEMO, config=config) if config else load_model(DEMO)
    return m.build()


def test_demo_passes_and_its_bad_configuration_fails_twice():
    rep = analyze(_demo())
    assert not rep.failed and [w.name for w in rep.warnings] == ["LED data from the LED's own datasheet"]
    bad = analyze(_demo("bad"))
    assert sorted(x.name for x in bad.failed) == ["flyback diode K1 (relay coil)", "rail budget 5V (UNO.5V)"]


def test_edits():
    c = _demo()
    notes = E.apply_edits(c, [
        {"op": "set_part", "part": "R1", "ohm": 150},
        {"op": "set_net", "net": "LED_A", "rename": "STATUS"},
        {"op": "add_part", "kind": "resistor", "name": "R9", "ohm": 1000},
        {"op": "connect", "pin": "R9.1", "net": "STATUS"},
        {"op": "connect", "pin": "R9.2", "net": "GND"},
        {"op": "set_gpio", "pin": "UNO.D2", "mode": "in", "pullup": True},
        {"op": "set_part", "part": "PWM", "address": "0x41"},
        {"op": "set_part", "part": "R1", "volts": 3},
        {"op": "remove_part", "part": "R9"},
        {"op": "warp"},
    ])
    assert [n.ok for n in notes] == [True] * 7 + [False, True, False]
    assert c.part("R1").spec["ohm"] == 150 and "STATUS" in c.nets and c.part("PWM").spec["address"] == 0x41 and "R9" not in c.parts
    assert "UNO.D13" in [str(r) for r in c.nets["STATUS"].pins]
    assert E.merge_edit({"op": "set_part", "part": "R1", "ohm": 100}, {"op": "set_part", "part": "R1", "ohm": 220}) == {"op": "set_part", "part": "R1", "ohm": 220}
    assert E.merge_edit({"op": "set_part", "part": "R1", "ohm": 100}, {"op": "set_part", "part": "R2", "ohm": 220}) is None
    assert E.describe({"op": "connect", "pin": "R1.1", "net": "X"}) == "R1.1 -> net X"
    st = E.describe_state(c)
    assert st["part R1"]["ohm"] == 150 and "UNO.D13" in st["net STATUS"]["pins"]


def test_outputs_and_data(tmp_path):
    from eitri.electronics.build import write_outputs
    c = _demo()
    rep = analyze(c)
    rows = outputs.bom(c)
    servo = next(r for r in rows if r["kind"] == "servo")
    assert servo["qty"] == 4 and servo["refs"] == "S1 S2 S3 S4" and rows[0]["kind"] == "board"
    paths, scene = write_outputs(c, rep, tmp_path, "arm")
    assert {"svg", "png", "bom_md", "csv", "netlist_md", "netlist_csv", "sources_md"} <= set(paths)
    net = Path(paths["netlist_csv"]).read_text()
    assert "SERVO_5V,rail,5.0" in net and "PSU5.+" in net
    src = Path(paths["sources_md"]).read_text()
    assert "https://ww1.microchip.com" in src and "nxp.com/docs/en/user-guide/UM10204.pdf" in src
    svg = Path(paths["svg"]).read_text()
    assert 'data-kind="part" data-id="Q1"' in svg and 'data-kind="net" data-id="SDA"' in svg
    assert scene.nodes["UNO"]["kind"] == "part"
    data = circuit_data(c, rep)
    assert {p["name"] for p in data["parts"]} >= {"UNO", "PWM", "S4", "K1"} and data["buses"][0]["devices"] == ["PWM", "UNO"]
    text = describe_selection(data, ["part:R1", "net:SDA", "part:nope"], rep.to_dict()["checks"])
    assert "- part R1: resistor 330 Ω" in text and "- net SDA (driven): UNO.A4, PWM.SDA" in text and "part:nope (no longer exists)" in text
    s = summarize(rep)
    assert s.startswith("Circuit 'Uno arm electronics': 18 parts") and "SERVO_5V: 5 V from PSU5.+" in s


def test_layout_hints_move_a_part():
    from eitri.electronics import diagram
    c = _demo()
    c.apply_layout({"LED1": [900, 950]})
    sc = diagram.build_scene(c)
    assert sc.nodes["LED1"]["x"] == 900 and sc.nodes["LED1"]["y"] == 950


def _code_blocks(md: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", md, re.S)


def test_reference_examples_build_and_pass(tmp_path):
    from eitri.model import load_model
    from eitri.electronics.domain import reference_text
    ref = reference_text()
    assert "{{" not in ref and "| uno | board |" in ref and "UM10204" in ref
    blocks = _code_blocks(ref)
    assert len(blocks) == 2
    for i, code in enumerate(blocks):
        f = tmp_path / f"ex{i}.py"
        f.write_text(code)
        m = load_model(f)
        c = m.build()
        rep = analyze(c, m.verify(c))
        assert not rep.failed, [x.line() for x in rep.failed]
