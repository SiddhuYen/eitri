"""The parts catalog: boards, modules, servos, regulators, diodes, transistors, relays and typical LEDs, each number with its source
id (eitri.electronics.sources). A part made from the catalog is a copy; the model file may override a number (then the model file is
its source).

    make_board(name, key)          a board Part (uno, nano, esp32)
    make_part(name, key, kind)     any other catalog Part (module, regulator, diode, mosfet, bjt, relay, servo)
    led_spec(color)                the typical 5 mm LED of that colour
    catalog_table()                the Markdown table the agent's reference shows
    nearest_e(value, "E24")        the nearest standard value (IEC 60063)
"""
from __future__ import annotations

import copy
import math

from .model import CircuitError, Logic, Part, PinSpec

# --------------------------------------------------------------------------- E-series (IEC 60063)

E12 = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)
E24 = (1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1)


def nearest_e(value: float, series: str = "E24") -> float:
    """The nearest value of an E-series to `value` (by ratio)."""
    vals = {"E12": E12, "E24": E24}[series.upper()]
    if value <= 0:
        raise ValueError("a resistance is positive")
    dec = math.floor(math.log10(value))
    cands = [m * 10 ** (dec + k) for k in (-1, 0, 1) for m in vals]
    best = min(cands, key=lambda c: abs(math.log(c / value)))
    return float(f"{best:.6g}")


# --------------------------------------------------------------------------- logic levels

ATMEGA_5V = Logic(vil=1.5, vih=3.0, vol=0.9, voh=4.2, vmax=5.5, src="atmega328p")          # 0.3VCC, 0.6VCC at 5 V; VCC + 0.5 abs max
ESP32_IO = Logic(vil=0.25, vih=0.75, vol=0.1, voh=0.8, vmax_over=0.3, frac=True, src="esp32")
PCA9685_IN = Logic(vil=0.3, vih=0.7, vmax=5.5, frac=True, src="nxp-pca9685")
PCA9685_OUT = Logic(voh=0.69, vmax=5.5, frac=True, src="nxp-pca9685")      # VOH min 1.6 V at VDD 2.3 V (the lowest ratio of its rows)
BME280_IO = Logic(vil=0.2, vih=0.8, vmax_over=0.3, frac=True, src="bosch-bme280")


# --------------------------------------------------------------------------- boards


def _board_pins(digital: int, analog: int, pwm=(), sda="", scl="", input_only=(), supply_3v3=True, vin=True, logic=ATMEGA_5V, prefix="D") -> dict:
    pins = {"5V": PinSpec("5V", "power_out", volts=5.0, label="+5V"),
            "3V3": PinSpec("3V3", "power_out", volts=3.3, label="3.3V")} if supply_3v3 else {}
    if vin:
        pins["VIN"] = PinSpec("VIN", "power_in")
    pins["GND"] = PinSpec("GND", "gnd", ref=True)
    for n in range(digital):
        roles = ("pwm",) if n in pwm else ()
        pins[f"{prefix}{n}"] = PinSpec(f"{prefix}{n}", "io", roles=roles, logic=logic)
    for n in range(analog):
        nm = f"A{n}"
        roles = ("adc",) + (("i2c_sda",) if nm == sda else ("i2c_scl",) if nm == scl else ()) + (("input_only",) if nm in input_only else ())
        pins[nm] = PinSpec(nm, "io", roles=roles, logic=logic)
    return pins


def _esp32_pins() -> dict:
    pins = {"3V3": PinSpec("3V3", "power_out", volts=3.3), "5V": PinSpec("5V", "power_out", volts=5.0, label="VBUS"), "GND": PinSpec("GND", "gnd", ref=True)}
    for n in list(range(0, 6)) + list(range(12, 24)) + [25, 26, 27, 32, 33, 34, 35, 36, 39]:
        if n in (20, 24):
            continue
        roles = ()
        if n == 21:
            roles = ("i2c_sda",)
        if n == 22:
            roles = ("i2c_scl",)
        if n >= 34:
            roles = ("input_only", "adc")
        pins[f"GPIO{n}"] = PinSpec(f"GPIO{n}", "io", roles=roles, logic=ESP32_IO)
    return pins


BOARDS = {
    "uno": dict(title="Arduino Uno R3", pins=lambda: _board_pins(14, 6, pwm=(3, 5, 6, 9, 10, 11), sda="A4", scl="A5"),
                spec=dict(key="uno", vdd=5.0, logic_rail="5V", pin_limit_A=0.020, total_limit_A=0.200, vin_range=(7.0, 12.0), v3_from_5v=True),
                srcs=dict(pin_limit_A="arduino-uno", total_limit_A="atmega328p", vin_range="arduino-uno", vdd="atmega328p", v3_from_5v="arduino-uno-sch")),
    "nano": dict(title="Arduino Nano", pins=lambda: _board_pins(14, 8, pwm=(3, 5, 6, 9, 10, 11), sda="A4", scl="A5", input_only=("A6", "A7")),
                 spec=dict(key="nano", vdd=5.0, logic_rail="5V", pin_limit_A=0.020, total_limit_A=0.200, vin_range=(7.0, 12.0)),
                 srcs=dict(pin_limit_A="arduino-nano", total_limit_A="atmega328p", vin_range="arduino-nano", vdd="atmega328p")),
    "esp32": dict(title="ESP32-DevKitC V4", pins=_esp32_pins,
                  spec=dict(key="esp32", vdd=3.3, logic_rail="3V3", pin_limit_A=0.028, total_limit_A=1.2, vin_range=None),
                  srcs=dict(pin_limit_A="esp32", total_limit_A="esp32", vdd="esp32")),
}
BOARD_ALIASES = {"arduino-uno": "uno", "arduino uno": "uno", "uno r3": "uno", "arduino-nano": "nano", "esp32dev": "esp32", "esp32-devkitc": "esp32",
                 "devkitc": "esp32", "esp32-devkit": "esp32"}


def board_key(text: str) -> str | None:
    k = str(text).strip().lower()
    return k if k in BOARDS else BOARD_ALIASES.get(k)


def make_board(name: str, key: str) -> Part:
    k = board_key(key)
    if k is None:
        raise CircuitError(f"{name}: unknown board {key!r}; boards: {', '.join(BOARDS)}")
    b = BOARDS[k]
    spec = copy.deepcopy(b["spec"])
    srcs = dict(b["srcs"])
    if k == "esp32":                       # the 3V3 pin: the board's AMS1117-3.3, less what the ESP32 itself needs (0.5 A, ESP32 datasheet)
        ams = PARTS["AMS1117-3.3"]["spec"]["i_max"]
        spec["rail_limits"] = {"3V3": (round(ams - 0.5, 3), f"the board's AMS1117-3.3 ({ams:g} A) less the 0.5 A the ESP32 needs", "esp32-devkitc")}
    return Part(name, "board", b["pins"](), spec, srcs, catalog=k, title=b["title"], part_number=b["title"])


# --------------------------------------------------------------------------- modules, servos and discrete parts


def _module_pca9685() -> dict:
    pins = {"GND": PinSpec("GND", "gnd", required=True),
            "OE": PinSpec("OE", "in", logic=PCA9685_IN, label="/OE"),
            "SCL": PinSpec("SCL", "in", roles=("i2c_scl",), logic=PCA9685_IN, pullup_ohm=10_000),
            "SDA": PinSpec("SDA", "od", roles=("i2c_sda",), logic=PCA9685_IN, pullup_ohm=10_000),
            "VCC": PinSpec("VCC", "power_in", current_A=0.010, v_range=(3.0, 5.0), required=True, src="adafruit-pca9685"),
            "V+": PinSpec("V+", "passive", label="servo power")}
    for i in range(16):
        pins[f"PWM{i}"] = PinSpec(f"PWM{i}", "out", current_A=0.010, logic=PCA9685_OUT, src="nxp-pca9685")
    return pins


MODULES = {
    "PCA9685": dict(title="16-channel 12-bit PWM / servo driver (Adafruit 815, NXP PCA9685)", pins=_module_pca9685,
                    spec=dict(address=0x40, addresses=tuple(range(0x40, 0x80))), srcs=dict(address="adafruit-pca9685"),
                    note="VCC 3-5 V logic supply (also its 10 kΩ SDA/SCL pull-ups); V+ servo power, not used by the chip; PWM0-15 source 10 mA (sink 25 mA)"),
    "BME280": dict(title="BME280 sensor on a plain breakout (no regulator, no pull-ups)", pins=lambda: {
                    "VCC": PinSpec("VCC", "power_in", current_A=0.000714, v_range=(1.71, 3.6), required=True, src="bosch-bme280"),
                    "GND": PinSpec("GND", "gnd", required=True),
                    "SCL": PinSpec("SCL", "in", roles=("i2c_scl",), logic=BME280_IO),
                    "SDA": PinSpec("SDA", "od", roles=("i2c_sda",), logic=BME280_IO)},
                   spec=dict(address=0x76, addresses=(0x76, 0x77)), srcs=dict(address="bosch-bme280"),
                   note="3.3 V only (1.71-3.6 V); I2C 0x76 (SDO to GND) or 0x77; no on-board pull-ups"),
    "SSD1306": dict(title="0.96\" 128x64 OLED, SSD1306 (Adafruit 326: regulator, level shifters, pull-ups)", pins=lambda: {
                    "VIN": PinSpec("VIN", "power_in", current_A=0.020, v_range=(3.0, 5.0), required=True, src="ssd1306"),
                    "GND": PinSpec("GND", "gnd", required=True),
                    "SCL": PinSpec("SCL", "in", roles=("i2c_scl",), pullup_ohm=10_000),
                    "SDA": PinSpec("SDA", "od", roles=("i2c_sda",), pullup_ohm=10_000)},
                    spec=dict(address=0x3D, addresses=(0x3C, 0x3D)), srcs=dict(address="ssd1306"),
                    note="3-5 V on VIN (5 V-ready), about 20 mA; I2C 0x3D (0x3C by jumper); on-board 10 kΩ pull-ups"),
    "HC-SR04": dict(title="HC-SR04 ultrasonic ranger (5 V)", pins=lambda: {
                    "VCC": PinSpec("VCC", "power_in", current_A=0.015, v_range=(4.5, 5.5), required=True, src="hc-sr04-range"),
                    "TRIG": PinSpec("TRIG", "in"),
                    "ECHO": PinSpec("ECHO", "out"),
                    "GND": PinSpec("GND", "gnd", required=True)},
                    spec={}, srcs={},
                    note="5 V, 15 mA; ECHO is a 5 V output: too high for a 3.3 V input. VCC range 4.5-5.5 V is an ASSUMPTION (the datasheet says DC 5 V)"),
}

SERVOS = {
    "SG90": dict(title="TowerPro SG90 micro servo", stall=0.5, v=(4.8, 6.0), src="towerpro-sg90"),
    "MG996R": dict(title="TowerPro MG996R servo", stall=1.4, v=(4.8, 6.6), src="towerpro-mg996r"),
}

def _two(a: str, b: str):
    return lambda: {a: PinSpec(a, "passive", required=True), b: PinSpec(b, "passive", required=True)}


def _reg_pins(vout: float, src: str):
    return lambda: {"IN": PinSpec("IN", "power_in", required=True), "OUT": PinSpec("OUT", "power_out", volts=vout, src=src),
                    "GND": PinSpec("GND", "gnd", required=True)}


_AMS = dict(i_max=1.0, dropout=1.3, theta_ja=90.0, tj_max=125.0, vin_max=15.0, iq=0.011)
_AMS_SRC = {k: "ams1117" for k in ("vout", "i_max", "dropout", "theta_ja", "tj_max", "vin_max", "iq")}

PARTS: dict[str, dict] = {
    "AMS1117-3.3": dict(kind="regulator", title="AMS1117-3.3 LDO regulator (SOT-223)", pins=_reg_pins(3.3, "ams1117"), spec=dict(vout=3.3, **_AMS), srcs=_AMS_SRC,
                        note="3.3 V, 1 A; dropout 1.3 V max; 90 C/W SOT-223; Tj 125 C; VIN max 15 V"),
    "AMS1117-5.0": dict(kind="regulator", title="AMS1117-5.0 LDO regulator (SOT-223)", pins=_reg_pins(5.0, "ams1117"), spec=dict(vout=5.0, **_AMS), srcs=_AMS_SRC,
                        note="5 V, 1 A; dropout 1.3 V max; 90 C/W SOT-223; Tj 125 C; VIN max 15 V"),
    "L7805": dict(kind="regulator", title="L7805 regulator (TO-220, no heatsink)", pins=_reg_pins(5.0, "st-l7805"),
                  spec=dict(vout=5.0, i_max=1.5, dropout=2.0, theta_ja=50.0, tj_max=125.0, vin_max=35.0, iq=0.0),
                  srcs={k: "st-l7805" for k in ("vout", "i_max", "dropout", "theta_ja", "tj_max", "vin_max")},
                  note="5 V, 1.5 A; dropout 2 V (typical: no maximum published); 50 C/W TO-220; Tj 125 C; VIN max 35 V"),
    "1N4148": dict(kind="diode", title="1N4148 small-signal diode", pins=_two("A", "K"), spec=dict(vf=1.0, if_avg=0.150, vrrm=100.0),
                   srcs=dict(vf="diode-1n4148", if_avg="diode-1n4148", vrrm="diode-1n4148"), note="150 mA average, 100 V"),
    "1N4007": dict(kind="diode", title="1N4007 rectifier diode", pins=_two("A", "K"), spec=dict(vf=0.93, if_avg=1.0, vrrm=1000.0),
                   srcs=dict(vf="diode-1n4007", if_avg="diode-1n4007", vrrm="diode-1n4007"), note="1 A average, 1000 V"),
    "1N5819": dict(kind="diode", title="1N5819 Schottky diode", pins=_two("A", "K"), spec=dict(vf=0.34, if_avg=1.0, vrrm=40.0),
                   srcs=dict(vf="diode-1n5819", if_avg="diode-1n5819", vrrm="diode-1n5819"), note="1 A average, 40 V"),
    "IRLZ44N": dict(kind="mosfet", title="IRLZ44N logic-level N-MOSFET (TO-220)",
                    pins=lambda: {"G": PinSpec("G", "in"), "D": PinSpec("D", "passive", required=True), "S": PinSpec("S", "passive", required=True)},
                    spec=dict(rds_on=((4.0, 0.035), (5.0, 0.025), (10.0, 0.022)), vgs_th_max=2.0, id_max=47.0, theta_ja=62.0, tj_max=175.0),
                    srcs={k: "infineon-irlz44n" for k in ("rds_on", "vgs_th_max", "id_max", "theta_ja", "tj_max")},
                    note="RDS(on) 35 mOhm at VGS 4 V, 25 at 5 V, 22 at 10 V; 62 C/W; Tj 175 C"),
    "IRF540N": dict(kind="mosfet", title="IRF540N N-MOSFET (TO-220; RDS(on) specified at 10 V only)",
                    pins=lambda: {"G": PinSpec("G", "in"), "D": PinSpec("D", "passive", required=True), "S": PinSpec("S", "passive", required=True)},
                    spec=dict(rds_on=((10.0, 0.044),), vgs_th_max=4.0, id_max=33.0, theta_ja=62.0, tj_max=175.0),
                    srcs={k: "infineon-irf540n" for k in ("rds_on", "vgs_th_max", "id_max", "theta_ja", "tj_max")},
                    note="RDS(on) 44 mOhm at VGS 10 V only: not for a 5 V or 3.3 V pin; 62 C/W; Tj 175 C"),
    "PN2222A": dict(kind="bjt", title="PN2222A NPN transistor (TO-92)",
                    pins=lambda: {"B": PinSpec("B", "in"), "C": PinSpec("C", "passive", required=True), "E": PinSpec("E", "passive", required=True)},
                    spec=dict(ic_max=0.6, forced_beta=10.0, vbe_on=0.6, vbe_max=1.2, vce_sat=0.3),
                    srcs={k: "onsemi-2222a" for k in ("ic_max", "forced_beta", "vbe_on", "vbe_max", "vce_sat")},
                    note="600 mA; saturated at IB >= IC/10 (VCE(sat) 0.3 V at 150 mA)"),
    "SRD-05VDC-SL-C": dict(kind="relay", title="Songle SRD-05VDC-SL-C relay, 5 V coil",
                           pins=lambda: {"COIL1": PinSpec("COIL1", "passive", required=True), "COIL2": PinSpec("COIL2", "passive", required=True),
                                         "COM": PinSpec("COM", "passive"), "NO": PinSpec("NO", "passive"), "NC": PinSpec("NC", "passive")},
                           spec=dict(coil_v=5.0, coil_ohm=69.4, contact_A=7.0, inductive=True),
                           srcs=dict(coil_v="songle-srd", coil_ohm="songle-srd", contact_A="songle-srd"),
                           note="5 V coil 69.4 Ohm (72 mA): needs a transistor and a flyback diode; contacts 7 A (the lowest variant's rating)"),
}

LEDS: dict[str, dict] = {
    "red": dict(spec=dict(vf=1.85, vf_max=2.5, if_max=0.030, if_rated=0.020, part_number="WP7113SRD/D"), srcs=dict(vf="kingbright-red", if_max="kingbright-red")),
    "green": dict(spec=dict(vf=2.2, vf_max=2.5, if_max=0.025, if_rated=0.020, part_number="WP7113SGD"), srcs=dict(vf="kingbright-green", if_max="kingbright-green")),
    "blue": dict(spec=dict(vf=3.3, vf_max=4.0, if_max=0.030, if_rated=0.020, part_number="WP7113QBC/D"), srcs=dict(vf="kingbright-blue", if_max="kingbright-blue")),
    "white": dict(spec=dict(vf=3.3, vf_max=4.0, if_max=0.030, if_rated=0.020, part_number="WP7113QWC/D"), srcs=dict(vf="kingbright-white", if_max="kingbright-white")),
}


def _servo(name: str, key: str) -> Part:
    s = SERVOS[key]
    pins = {"V+": PinSpec("V+", "power_in", current_A=s["stall"], v_range=s["v"], required=True, src=s["src"]),
            "GND": PinSpec("GND", "gnd", required=True),
            "SIG": PinSpec("SIG", "in", required=True)}
    return Part(name, "servo", pins, {"current_A": s["stall"], "v_min": s["v"][0], "v_max": s["v"][1]},
                {"current_A": s["src"], "v_min": s["src"]}, catalog=key, title=s["title"], part_number=key)


def _find(table: dict, key: str) -> str | None:
    k = str(key).strip().upper()
    for name in table:
        if name.upper() == k or name.upper().replace("-", "") == k.replace("-", ""):
            return name
    return None


def make_part(name: str, key: str, kind: str) -> Part:
    if kind == "module":
        k = _find(MODULES, key)
        if k is None:
            raise CircuitError(f"{name}: unknown module {key!r}; modules: {', '.join(MODULES)} (anything else: c.load(...) for its supply draw)")
        m = MODULES[k]
        return Part(name, "module", m["pins"](), copy.deepcopy(m["spec"]), dict(m["srcs"]), catalog=k, title=m["title"], part_number=k)
    if kind == "servo":
        k = _find(SERVOS, key)
        if k is None:
            raise CircuitError(f"{name}: unknown servo {key!r}; servos: {', '.join(SERVOS)}")
        return _servo(name, k)
    k = _find(PARTS, key)
    if k is None or PARTS[k]["kind"] != kind:
        known = [n for n, v in PARTS.items() if v["kind"] == kind]
        raise CircuitError(f"{name}: unknown {kind} {key!r}; known: {', '.join(known)}")
    e = PARTS[k]
    return Part(name, kind, e["pins"](), copy.deepcopy(e["spec"]), dict(e["srcs"]), catalog=k, title=e["title"], part_number=k)


def led_spec(color: str) -> tuple[dict, dict]:
    c = str(color).strip().lower()
    if c not in LEDS:
        raise CircuitError(f"no typical LED for colour {color!r}; colours: {', '.join(LEDS)} (or give vf= and if_max= from your LED's datasheet)")
    e = LEDS[c]
    return {"color": c, **copy.deepcopy(e["spec"])}, dict(e["srcs"])


def catalog_table() -> str:
    from .sources import cite
    rows = ["| key | kind | what | key figures (source) |", "|---|---|---|---|"]
    for k, b in BOARDS.items():
        s = b["spec"]
        rows.append(f"| {k} | board | {b['title']} | logic {s['vdd']:g} V, {s['pin_limit_A'] * 1000:g} mA per I/O pin, {s['total_limit_A'] * 1000:g} mA all pins"
                    + (f", VIN {s['vin_range'][0]:g}-{s['vin_range'][1]:g} V" if s.get("vin_range") else "") + f" ({cite(b['srcs']['pin_limit_A'])}) |")
    for k, m in MODULES.items():
        rows.append(f"| {k} | module | {m['title']} | {m['note']} |")
    for k, s in SERVOS.items():
        rows.append(f"| {k} | servo | {s['title']} | {s['v'][0]:g}-{s['v'][1]:g} V, draws {s['stall']:g} A at stall ({cite(s['src'])}) |")
    for k, e in PARTS.items():
        rows.append(f"| {k} | {e['kind']} | {e['title']} | {e['note']} ({cite(next(iter(e['srcs'].values()), ''))}) |")
    for k, e in LEDS.items():
        s = e["spec"]
        rows.append(f"| led(\"{k}\") | led | typical 5 mm {k} LED ({s.get('part_number', '')}) | Vf {s['vf']:g} V, {s['if_max'] * 1000:g} mA DC max ({cite(e['srcs']['if_max'])}) |")
    return "\n".join(rows)
