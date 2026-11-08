"""The electronics model: parts (a board, supplies, regulators, modules, passives, switches, transistors, relays, motors and servos),
their pins, and the nets that join them. Breadboard and module level: no footprints, no layout.

    from eitri.electronics import *

    PARAMS = dict(led_ohm=220)

    def build(p):
        c = Circuit("Status LED")
        uno = c.board("UNO", "uno")
        r = c.resistor("R1", p.led_ohm)
        led = c.led("LED1", "red")
        c.connect(uno["D13"], r["1"])
        c.connect(r["2"], led["A"])
        c.connect(led["K"], uno["GND"])
        return c

Everything is plain data. A part's numbers come from the catalog (each with its source, eitri.electronics.sources) or from the
model file (then the model file is their source). The checks, the schematic, the BOM and the netlist are computed from it by the
rest of this package; nothing here judges the design.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

PIN_KINDS = ("power_out", "power_in", "gnd", "io", "in", "out", "od", "passive")
GPIO_MODES = ("io", "out", "in")
PART_KINDS = ("board", "supply", "regulator", "module", "resistor", "led", "diode", "capacitor", "switch", "mosfet", "bjt", "relay",
              "motor", "solenoid", "servo", "load")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_+\-]*$")
_NET_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_+\-]*$")


class CircuitError(ValueError):
    """A circuit was described wrongly (not an electrical problem: those are reported by the checks)."""


@dataclass(frozen=True)
class Logic:
    """A digital pin's thresholds. With frac=True the voltages are fractions of the part's supply (VDD); else volts.

    vil: highest input voltage read as low; vih: lowest read as high; vol: highest output low; voh: lowest output high (each the
    datasheet's guaranteed value); vmax: the highest voltage the pin may see (absolute maximum); vmax_over: instead of vmax, the
    absolute maximum as VDD + this many volts. src: the source id of these figures (eitri.electronics.sources)."""
    vil: float | None = None
    vih: float | None = None
    vol: float | None = None
    voh: float | None = None
    vmax: float | None = None
    vmax_over: float | None = None
    frac: bool = False
    src: str = ""

    def at(self, vdd: float | None) -> dict[str, float | None]:
        """The thresholds in volts at a supply voltage (None where unknown)."""
        k = (vdd if vdd is not None else None) if self.frac else 1.0

        def v(x):
            if x is None or k is None:
                return None
            return round(x * k, 4)
        vmax = self.vmax if self.vmax is not None else (round(vdd + self.vmax_over, 4) if self.vmax_over is not None and vdd is not None else None)
        return {"vil": v(self.vil), "vih": v(self.vih), "vol": v(self.vol), "voh": v(self.voh), "vmax": vmax}


@dataclass(frozen=True)
class PinSpec:
    """One pin of a part.

    kind: power_out (a source: volts), power_in (a consumer: current_A drawn, v_range accepted), gnd, io (a microcontroller GPIO),
    in, out, od (open drain, an I2C line), passive (a resistor's end, a coil's end, a contact).
    roles: what else the pin is ("i2c_sda", "i2c_scl", "pwm", "adc", "input_only", "forbidden: ..."); logic: its thresholds;
    pullup_ohm: an on-board pull-up resistor to the part's own supply (a breakout's I2C pull-ups); required: must be connected;
    ref: a gnd pin that is a reference (a supply's minus, a board's GND), not just a return."""
    name: str
    kind: str
    volts: float | None = None
    current_A: float | None = None
    v_range: tuple[float, float] | None = None
    roles: tuple[str, ...] = ()
    logic: Logic | None = None
    pullup_ohm: float | None = None
    required: bool = False
    ref: bool = False
    label: str = ""
    src: str = ""                          # the source of this pin's numbers (volts, current_A, v_range)

    def __post_init__(self) -> None:
        if self.kind not in PIN_KINDS:
            raise CircuitError(f"pin {self.name}: kind must be one of {PIN_KINDS}, not {self.kind!r}")


@dataclass(frozen=True)
class PinRef:
    """One pin of one part: what `part["D9"]` gives."""
    part: str
    pin: str
    owner: Any = field(default=None, compare=False, repr=False)

    def __str__(self) -> str:
        return f"{self.part}.{self.pin}"


@dataclass(eq=False)
class Part:
    """A part: its name (the reference designator: R1, LED1, UNO), its kind, its pins and its numbers.

    spec: the numbers the checks use (ohm, power_W, vf, if_max, volts, current_A, ...); srcs: each number's source id (a catalog
    source, or "model" when the model file gave it). catalog: the catalog key it came from ("" for a generic part)."""
    name: str
    kind: str
    pins: dict[str, PinSpec] = field(default_factory=dict)
    spec: dict[str, Any] = field(default_factory=dict)
    srcs: dict[str, str] = field(default_factory=dict)
    catalog: str = ""
    title: str = ""
    part_number: str = ""
    notes: str = ""
    modes: dict[str, str] = field(default_factory=dict)          # a board's GPIO modes: pin -> io | out | in
    pullups: set = field(default_factory=set)                    # a board's GPIOs with the internal pull-up on

    def __post_init__(self) -> None:
        if not self.name or not _NAME_RE.match(str(self.name)):
            raise CircuitError(f"part name {self.name!r} must start with a letter and hold only letters, digits, _, + and -")
        if self.kind not in PART_KINDS:
            raise CircuitError(f"{self.name}: kind must be one of {PART_KINDS}, not {self.kind!r}")

    def pin(self, key) -> PinRef:
        """A pin by name, by label, or by 1-based position."""
        if isinstance(key, bool):
            raise CircuitError(f"{self.name}: not a pin: {key!r}")
        if isinstance(key, int):
            names = list(self.pins)
            if not 1 <= key <= len(names):
                raise CircuitError(f"{self.name} has no pin #{key}; its pins are {names}")
            return PinRef(self.name, names[key - 1], self)
        k = str(key).strip()
        if k in self.pins:
            return PinRef(self.name, k, self)
        up = k.upper()
        for n, ps in self.pins.items():
            if n.upper() == up or (ps.label and ps.label.upper() == up):
                return PinRef(self.name, n, self)
        if self.kind == "board":                              # 9 -> D9, GPIO25 / IO25 / 25
            m = re.fullmatch(r"(?:GPIO|IO|D)?(\d+)", up)
            if m:
                for cand in (f"D{m.group(1)}", f"GPIO{m.group(1)}"):
                    if cand in self.pins:
                        return PinRef(self.name, cand, self)
        raise CircuitError(f"{self.name} has no pin {key!r}; its pins are {list(self.pins)}")

    __getitem__ = pin

    @property
    def value_text(self) -> str:
        """The part's value as a schematic writes it: 220 Ω, 100 µF, red LED, AMS1117-3.3."""
        s = self.spec
        if self.kind == "resistor":
            return ohms(s["ohm"]) + (f" {s['power_W']:g} W" if s.get("power_W") not in (None, 0.25) else "")
        if self.kind == "capacitor":
            return farads(s["farad"]) + (f" {s['v_rated']:g} V" if s.get("v_rated") else "")
        if self.kind == "led":
            return f"{s.get('color', '')} LED".strip()
        if self.kind == "supply":
            return (f"{s['volts']:g} V" + (f" (up to {s['v_max']:g} V)" if s.get("v_max") not in (None, s["volts"]) else "")
                    + (f" {s['current_A']:g} A" if s.get("current_A") is not None else ""))
        if self.kind in ("motor", "solenoid", "load"):
            return (f"{s['volts']:g} V " if s.get("volts") else "") + (f"{amps_text(s['current_A'])}" if s.get("current_A") is not None else "")
        if self.kind == "switch":
            return s.get("style", "switch")
        return self.part_number or self.catalog or self.kind

    def mode(self, pin: str) -> str:
        return self.modes.get(pin, "io")


@dataclass(eq=False)
class Net:
    name: str
    pins: list[PinRef] = field(default_factory=list)
    auto: bool = False                     # named by eitri (from its first pin), not by the model
    notes: str = ""


@dataclass
class I2CBus:
    """Settings of an I2C bus (found from the pins' roles): its SCL clock and the bus capacitance per line."""
    sda: str
    speed_hz: int = 100_000
    capacitance_pF: float | None = None
    internal_pullups: bool = True            # an AVR board's Wire.begin() turns on its pins' internal pull-ups; False: the firmware turns them off


def ohms(v: float) -> str:
    v = float(v)
    for k, u in ((1e6, "MΩ"), (1e3, "kΩ")):
        if abs(v) >= k:
            return f"{v / k:g} {u}"
    return f"{v:g} Ω"


def farads(v: float) -> str:
    v = float(v)
    for k, u in ((1e-3, "mF"), (1e-6, "µF"), (1e-9, "nF")):
        if abs(v) >= k:
            return f"{v / k:.3g} {u}"
    return f"{v * 1e12:.3g} pF"


def amps_text(x: float) -> str:
    """A current as people write it: 2.5 A, 13.6 mA, 50 µA."""
    x = float(x)
    if abs(x) >= 1:
        return f"{x:.3g} A"
    if abs(x) >= 1e-3:
        return f"{x * 1e3:.3g} mA"
    if x == 0:
        return "0 A"
    return f"{x * 1e6:.3g} µA"


# --------------------------------------------------------------------------- the circuit


class Circuit:
    def __init__(self, name: str = "circuit", description: str = "", ambient_C: float = 25.0):
        self.name = name
        self.description = description
        self.ambient_C = float(ambient_C)
        self.parts: dict[str, Part] = {}
        self.nets: dict[str, Net] = {}
        self.buses: dict[str, I2CBus] = {}
        self.layout: dict[str, list[float]] = {}
        self._net_of: dict[PinRef, str] = {}

    # -- adding parts -------------------------------------------------------------
    def add(self, part: Part) -> Part:
        if not isinstance(part, Part):
            raise CircuitError(f"cannot add a {type(part).__name__} to a Circuit")
        if part.name in self.parts:
            raise CircuitError(f"a part named {part.name!r} already exists; part names must be unique")
        self.parts[part.name] = part
        return part

    def part(self, name: str) -> Part:
        if name not in self.parts:
            raise CircuitError(f"no part {name!r}; parts: {list(self.parts)}")
        return self.parts[name]

    def board(self, name: str, board: str = "uno", powered: str = "usb", vin_V: float | None = None) -> Part:
        """A development board from the catalog (uno, nano, esp32): its supply pins, GND and every GPIO.

        powered: how the board itself is powered, "usb" or "vin" (then wire its VIN pin to a supply; vin_V defaults to that rail)."""
        from .catalog import make_board
        powered = str(powered or "usb").lower()
        if powered not in ("usb", "vin"):
            raise CircuitError(f"{name}: powered must be 'usb' or 'vin', not {powered!r}")
        p = make_board(name, board)
        p.spec["powered"] = powered
        if vin_V is not None:
            p.spec["vin_V"] = float(vin_V)
            p.srcs["vin_V"] = "model"
        return self.add(p)

    def gpio(self, pin, mode: str = "in", pullup: bool = False) -> PinRef:
        """Say how the firmware uses a board pin: "in" (it only reads), "out" (it drives), "io" (either; the default for a pin
        that is not declared). pullup=True: the internal pull-up is on (an input only)."""
        ref = self._pin(pin)
        part = self.parts[ref.part]
        if part.kind != "board" or part.pins[ref.pin].kind != "io":
            raise CircuitError(f"{ref} is not a GPIO of a board")
        if mode not in GPIO_MODES:
            raise CircuitError(f"{ref}: mode must be one of {GPIO_MODES}, not {mode!r}")
        if pullup and mode != "in":
            raise CircuitError(f"{ref}: the internal pull-up is for an input (mode='in')")
        part.modes[ref.pin] = mode
        if pullup:
            part.pullups.add(ref.pin)
        else:
            part.pullups.discard(ref.pin)
        return ref

    def supply(self, name: str, volts: float, current_A: float | None = None, part_number: str = "", notes: str = "",
               v_max: float | None = None, battery: bool = False) -> Part:
        """An external supply (a bench supply, a wall adapter, a BEC, a battery): pins "+" and "-".

        current_A: what it is rated to deliver (from its label or datasheet): the rail budget grades its loads against it.
        v_max: the highest voltage it reaches (a battery at full charge, a vehicle's charging system); a board's VIN is graded
        against it too. battery=True: an unregulated source (c.battery() sets both from the chemistry)."""
        v = _num(volts, f"{name}: volts")
        pins = {"+": PinSpec("+", "power_out", volts=v, src="model"), "-": PinSpec("-", "gnd", ref=True)}
        spec = {"volts": v, "current_A": None if current_A is None else _num(current_A, f"{name}: current_A", positive=True)}
        srcs = {"volts": "model", "current_A": "model"}
        if v_max is not None:
            vm = _num(v_max, f"{name}: v_max", positive=True)
            if vm < v:
                raise CircuitError(f"{name}: v_max ({vm:g} V) is below its volts ({v:g} V)")
            spec["v_max"], srcs["v_max"] = vm, "model"
        if battery:
            spec["battery"] = True
        return self.add(Part(name, "supply", pins, spec, srcs, title="battery" if battery else "supply", part_number=part_number, notes=notes))

    def battery(self, name: str, chemistry: str, cells: int, current_A: float | None = None, part_number: str = "", notes: str = "") -> Part:
        """A battery of `cells` cells in series: modelled at its nominal voltage, with its full-charge voltage as v_max (both per
        cell from a datasheet, see catalog.BATTERIES: "li-ion" / "lipo", "lead-acid"). Another chemistry: c.supply(name, volts,
        v_max=<full charge>, battery=True)."""
        from .catalog import BATTERIES
        key = str(chemistry).strip().lower().replace(" ", "-").replace("_", "-")
        key = {"liion": "li-ion", "li-po": "lipo", "lipo": "lipo", "sla": "lead-acid", "vrla": "lead-acid", "lead": "lead-acid"}.get(key, key)
        if key not in BATTERIES:
            raise CircuitError(f"{name}: unknown chemistry {chemistry!r}; known: {', '.join(BATTERIES)} (another: c.supply({name!r}, volts, "
                               "v_max=<its full-charge voltage>, battery=True))")
        if isinstance(cells, bool) or not isinstance(cells, int) or cells < 1:
            raise CircuitError(f"{name}: cells must be a whole number of cells in series (1 or more)")
        b = BATTERIES[key]
        p = self.supply(name, round(b["nominal"] * cells, 3), current_A, part_number, notes, v_max=round(b["full"] * cells, 3), battery=True)
        p.spec.update(chemistry=key, cells=cells)
        p.srcs["volts"] = p.srcs["v_max"] = b["src"]
        p.title = f"{key} battery, {cells} cell{'s' if cells != 1 else ''}"
        return p

    def regulator(self, name: str, part: str = "AMS1117-3.3") -> Part:
        """A linear regulator from the catalog (AMS1117-3.3, AMS1117-5.0, L7805): pins IN, OUT, GND."""
        from .catalog import make_part
        return self.add(make_part(name, part, "regulator"))

    def module(self, name: str, module: str, address: int | None = None) -> Part:
        """A breakout module from the catalog (PCA9685, BME280, SSD1306, HC-SR04, ...). address: its I2C address when changed
        from the default (address jumpers)."""
        from .catalog import make_part
        p = make_part(name, module, "module")
        if address is not None:
            if "addresses" in p.spec and int(address) not in p.spec["addresses"]:
                raise CircuitError(f"{name}: {p.catalog} can be at {', '.join(hex(a) for a in p.spec['addresses'])}, not {hex(int(address))}")
            p.spec["address"] = int(address)
            p.srcs["address"] = "model"
        return self.add(p)

    def resistor(self, name: str, ohm: float, power_W: float | None = None, part_number: str = "") -> Part:
        """A resistor: pins "1" and "2". power_W: its rating; default 1/4 W (a through-hole CFR-25 / MFR-25 size)."""
        r = _num(ohm, f"{name}: ohm", positive=True)
        spec = {"ohm": r, "power_W": 0.25 if power_W is None else _num(power_W, f"{name}: power_W", positive=True)}
        srcs = {"ohm": "model", "power_W": "yageo-cfr" if power_W is None else "model"}
        return self.add(Part(name, "resistor", {"1": PinSpec("1", "passive", required=True), "2": PinSpec("2", "passive", required=True)},
                             spec, srcs, title="resistor", part_number=part_number))

    def led(self, name: str, color: str = "red", vf: float | None = None, if_max: float | None = None, if_rated: float | None = None,
            part_number: str = "", part: str | None = None, vf_max: float | None = None) -> Part:
        """An LED: pins "A" (anode) and "K" (cathode). part: a catalog LED (WP7113SRD/D, WP7113SGD, WP7113QBC/D, WP7113QWC/D), graded
        on its own datasheet's figures. Without part, vf or if_max, the typical 5 mm part of that colour is used and the build says so;
        give your LED's datasheet figures when you have them (vf typical, vf_max maximum, if_max DC maximum)."""
        from .catalog import led_part, led_spec
        if part is not None:
            key = led_part(part)
            if key is None:
                from .catalog import LEDS
                raise CircuitError(f"{name}: no catalog LED {part!r}; catalog LEDs: {', '.join(e['spec']['part_number'] for e in LEDS.values())} "
                                   "(any other LED: give vf= and if_max= from its datasheet)")
            color = key
        spec, srcs = led_spec(color)
        for k, v in (("vf", vf), ("vf_max", vf_max), ("if_max", if_max), ("if_rated", if_rated)):
            if v is not None:
                spec[k] = _num(v, f"{name}: {k}", positive=True)
                srcs[k] = "model"
        if vf is not None and vf_max is None:
            spec.pop("vf_max", None)                 # the catalog's maximum belongs to the catalog part, not to this LED
        spec["generic"] = part is None and not (vf is not None and if_max is not None)
        if part is not None:
            spec["catalog_part"] = spec["part_number"]
        return self.add(Part(name, "led", {"A": PinSpec("A", "passive", required=True), "K": PinSpec("K", "passive", required=True)},
                             spec, srcs, title="LED", catalog=spec["part_number"] if part is not None else "",
                             part_number=part_number or spec.get("part_number", "")))

    def diode(self, name: str, part: str = "1N4007") -> Part:
        """A diode from the catalog (1N4148, 1N4007, 1N5819): pins "A" and "K"."""
        from .catalog import make_part
        return self.add(make_part(name, part, "diode"))

    def capacitor(self, name: str, farad: float, v_rated: float | None = None, polarized: bool = False, part_number: str = "") -> Part:
        """A capacitor: pins "1" and "2" ("+" and "-" when polarized). v_rated: its voltage rating (checked against its rail)."""
        f = _num(farad, f"{name}: farad", positive=True)
        names = ("+", "-") if polarized else ("1", "2")
        spec = {"farad": f, "v_rated": None if v_rated is None else _num(v_rated, f"{name}: v_rated", positive=True), "polarized": bool(polarized)}
        return self.add(Part(name, "capacitor", {n: PinSpec(n, "passive", required=True) for n in names}, spec,
                             {"farad": "model", "v_rated": "model"}, title="capacitor", part_number=part_number))

    def switch(self, name: str, style: str = "push button", part_number: str = "") -> Part:
        """A switch or push button (also a limit switch): pins "1" and "2". The checks take it closed (the worst case)."""
        return self.add(Part(name, "switch", {"1": PinSpec("1", "passive", required=True), "2": PinSpec("2", "passive", required=True)},
                             {"style": str(style)}, {}, title=str(style), part_number=part_number))

    def mosfet(self, name: str, part: str = "IRLZ44N") -> Part:
        """An N-channel MOSFET from the catalog (IRLZ44N, IRF540N) as a low-side switch: pins G, D, S."""
        from .catalog import make_part
        return self.add(make_part(name, part, "mosfet"))

    def bjt(self, name: str, part: str = "PN2222A") -> Part:
        """An NPN transistor from the catalog (PN2222A) as a low-side switch: pins B, C, E."""
        from .catalog import make_part
        return self.add(make_part(name, part, "bjt"))

    def relay(self, name: str, part: str = "SRD-05VDC-SL-C") -> Part:
        """A bare relay from the catalog: coil pins COIL1 / COIL2 (an inductive load: it needs a flyback diode when a transistor
        switches it) and contacts COM, NO, NC."""
        from .catalog import make_part
        return self.add(make_part(name, part, "relay"))

    def motor(self, name: str, volts: float, current_A: float, part_number: str = "") -> Part:
        """A brushed DC motor: pins "+" and "-". current_A: its worst-case draw (the datasheet's stall current for the budget)."""
        return self._inductive(name, "motor", volts, current_A, part_number)

    def solenoid(self, name: str, volts: float, current_A: float, part_number: str = "") -> Part:
        """A solenoid (or any coil load): pins "+" and "-"; current_A its rated current."""
        return self._inductive(name, "solenoid", volts, current_A, part_number)

    def _inductive(self, name, kind, volts, current_A, part_number) -> Part:
        v = _num(volts, f"{name}: volts", positive=True)
        i = _num(current_A, f"{name}: current_A", positive=True)
        pins = {"+": PinSpec("+", "passive", required=True), "-": PinSpec("-", "passive", required=True)}
        return self.add(Part(name, kind, pins, {"volts": v, "current_A": i, "inductive": True}, {"volts": "model", "current_A": "model"},
                             title=kind, part_number=part_number))

    def servo(self, name: str, part: str = "SG90", current_A: float | None = None) -> Part:
        """A hobby servo from the catalog (SG90, MG996R): pins V+, GND, SIG. Its supply draw is its stall current from the catalog
        (the worst case); current_A overrides it (say why in the model file)."""
        from .catalog import make_part
        p = make_part(name, part, "servo")
        if current_A is not None:
            i = _num(current_A, f"{name}: current_A", positive=True)
            p.spec["current_A"] = i
            p.srcs["current_A"] = "model"
            ps = p.pins["V+"]
            p.pins["V+"] = PinSpec(ps.name, ps.kind, current_A=i, v_range=ps.v_range, required=True, src="model")
        return self.add(p)

    def load(self, name: str, current_A: float, v_min: float | None = None, v_max: float | None = None, part_number: str = "", notes: str = "") -> Part:
        """Anything else that draws from a rail (an LED strip, a fan, a display not in the catalog): pins "+" and "-".
        current_A: its worst-case draw; v_min / v_max: the supply range it accepts (from its datasheet)."""
        i = _num(current_A, f"{name}: current_A", positive=True)
        rng = (float(v_min), float(v_max)) if v_min is not None and v_max is not None else None
        pins = {"+": PinSpec("+", "power_in", current_A=i, v_range=rng, required=True, src="model"), "-": PinSpec("-", "gnd", required=True)}
        return self.add(Part(name, "load", pins, {"current_A": i, "v_min": v_min, "v_max": v_max}, {"current_A": "model"}, title="load",
                             part_number=part_number, notes=notes))

    # -- nets ---------------------------------------------------------------------------
    def _pin(self, x) -> PinRef:
        if isinstance(x, PinRef):
            ref = x
            if ref.part not in self.parts and isinstance(ref.owner, Part):
                self.add(ref.owner)
        elif isinstance(x, Part):
            if x.name not in self.parts:
                self.add(x)
            if len(x.pins) != 1:
                raise CircuitError(f"{x.name} has {len(x.pins)} pins; say which: {x.name}[pin]")
            ref = x.pin(1)
        elif isinstance(x, str):
            name, dot, pin = x.partition(".")
            if not dot:
                raise CircuitError(f"{x!r}: write a pin as 'PART.PIN' (R1.2, UNO.D9) or part['pin']")
            if name not in self.parts:
                raise CircuitError(f"no part {name!r} (in {x!r}); parts: {list(self.parts)}")
            ref = self.parts[name].pin(pin)
        else:
            raise CircuitError(f"not a pin: {x!r}; use part['pin'] or 'PART.PIN'")
        if ref.part not in self.parts:
            raise CircuitError(f"part {ref.part!r} is not in the circuit")
        if ref.pin not in self.parts[ref.part].pins:
            raise CircuitError(f"{ref.part} has no pin {ref.pin!r}")
        return PinRef(ref.part, ref.pin, self.parts[ref.part])

    def net_of(self, pin) -> Net | None:
        ref = self._pin(pin)
        n = self._net_of.get(PinRef(ref.part, ref.pin))
        return self.nets.get(n) if n else None

    def _attach(self, net: Net, ref: PinRef) -> None:
        key = PinRef(ref.part, ref.pin)
        old = self._net_of.get(key)
        if old == net.name:
            return
        if old is not None:
            raise CircuitError(f"{key} is already on net {old!r}")
        net.pins.append(key)
        self._net_of[key] = net.name

    def _merge(self, into: Net, other: Net) -> None:
        for ref in other.pins:
            self._net_of[ref] = into.name
            into.pins.append(ref)
        del self.nets[other.name]
        if other.name in self.buses:
            self.buses[into.name] = self.buses.pop(other.name)
            self.buses[into.name].sda = into.name

    def net(self, name: str, *pins, notes: str = "") -> Net:
        """A named net with these pins (more can join it later: connect(..., net=name) or net(name, more pins)). Two named nets
        cannot be joined: that would short them."""
        name = str(name)
        if not _NET_RE.match(name):
            raise CircuitError(f"net name {name!r} must hold only letters, digits, _, + and - (5V, 3V3, SDA, LED_A)")
        refs = [self._pin(p) for p in pins]
        net = self.nets.get(name)
        if net is None:
            net = Net(name, notes=notes)
            self.nets[name] = net
        elif notes:
            net.notes = notes
        net.auto = False
        for ref in refs:
            cur = self._net_of.get(PinRef(ref.part, ref.pin))
            if cur is None:
                self._attach(net, ref)
            elif cur != name:
                other = self.nets[cur]
                if not other.auto:
                    raise CircuitError(f"{ref} is on net {cur!r}; joining it to {name!r} would short two named nets")
                self._merge(net, other)
        return net

    def connect(self, *pins, net: str | None = None) -> Net:
        """Join pins into one net (a wire, a breadboard row). net: name it (or join that named net). Pins already on nets merge
        their nets; two differently named nets are never merged (that would short them: it raises)."""
        if len(pins) < 2 and net is None:
            raise CircuitError("connect(a, b, ...) needs at least two pins (or net='NAME' to put one pin on a net)")
        refs = [self._pin(p) for p in pins]
        if net is not None:
            return self.net(net, *refs)
        current = {self._net_of.get(PinRef(r.part, r.pin)) for r in refs} - {None}
        named = sorted(n for n in current if not self.nets[n].auto)
        if len(named) > 1:
            raise CircuitError(f"connecting {', '.join(map(str, refs))} would short nets {' and '.join(named)}")
        if named:
            target = self.nets[named[0]]
        elif current:
            target = self.nets[sorted(current)[0]]
        else:
            first = refs[0]
            base = f"{first.part}_{first.pin}".replace("+", "P").replace("-", "N")
            nm, k = base, 2
            while nm in self.nets:
                nm, k = f"{base}_{k}", k + 1
            target = Net(nm, auto=True)
            self.nets[nm] = target
        for r in refs:
            cur = self._net_of.get(PinRef(r.part, r.pin))
            if cur is None:
                self._attach(target, r)
            elif cur != target.name:
                self._merge(target, self.nets[cur])
        return target

    def disconnect(self, pin) -> None:
        """Take a pin off its net (a net left with no pins is removed)."""
        ref = self._pin(pin)
        key = PinRef(ref.part, ref.pin)
        name = self._net_of.pop(key, None)
        if name is None:
            return
        net = self.nets[name]
        net.pins = [p for p in net.pins if p != key]
        if not net.pins:
            del self.nets[name]
            self.buses.pop(name, None)

    def rename_net(self, old: str, new: str) -> None:
        if old not in self.nets:
            raise CircuitError(f"no net {old!r}")
        if new == old:
            return
        if not _NET_RE.match(new):
            raise CircuitError(f"net name {new!r} must hold only letters, digits, _, + and - (5V, 3V3, SDA, LED_A)")
        if new in self.nets:
            raise CircuitError(f"a net named {new!r} already exists")
        net = self.nets.pop(old)
        net.name, net.auto = new, False
        self.nets[new] = net
        for ref in net.pins:
            self._net_of[ref] = new
        if old in self.buses:
            self.buses[new] = self.buses.pop(old)
            self.buses[new].sda = new

    def i2c(self, sda, speed_hz: int = 100_000, capacitance_pF: float | None = None, internal_pullups: bool = True) -> I2CBus:
        """Settings of the I2C bus whose SDA is this net (or pin): its clock (100 kHz Standard-mode, Arduino Wire's default; 400 kHz
        Fast-mode; 1 MHz Fast-mode Plus) and the capacitance of each line if you know it (default: an estimate, see the check).
        internal_pullups: on an AVR board (Uno, Nano), Wire.begin() turns on the pins' internal pull-ups to 5 V; False says the
        firmware turns them off after Wire.begin() (digitalWrite(SDA, LOW); digitalWrite(SCL, LOW);), as a 3.3 V-only bus needs."""
        if isinstance(sda, str) and sda in self.nets:
            name = sda
        else:
            n = self.net_of(sda)
            if n is None:
                raise CircuitError(f"{sda} is on no net; connect it first")
            name = n.name
        bus = I2CBus(name, int(speed_hz), None if capacitance_pF is None else float(capacitance_pF), bool(internal_pullups))
        self.buses[name] = bus
        return bus

    # -- queries ----------------------------------------------------------------------------
    def pin_spec(self, ref: PinRef) -> PinSpec:
        return self.parts[ref.part].pins[ref.pin]

    def unconnected(self) -> list[PinRef]:
        return [PinRef(p.name, n) for p in self.parts.values() for n in p.pins if PinRef(p.name, n) not in self._net_of]

    def place(self, name: str, x: float, y: float) -> None:
        self.layout[name] = [float(x), float(y)]

    def apply_layout(self, layout) -> None:
        for k, v in (layout or {}).items():
            try:
                self.layout[str(k)] = [float(v[0]), float(v[1])]
            except (TypeError, ValueError, IndexError):
                continue

    def remove_part(self, name: str) -> None:
        p = self.part(name)
        for n in list(p.pins):
            self.disconnect(PinRef(name, n))
        del self.parts[name]
        self.layout.pop(name, None)

    def clone(self) -> "Circuit":
        return copy.deepcopy(self)

    def __repr__(self) -> str:
        return f"Circuit({self.name!r}: {len(self.parts)} parts, {len(self.nets)} nets)"


def _num(v, what: str, positive: bool = False) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise CircuitError(f"{what} must be a number, not {v!r}") from None
    if positive and not x > 0:
        raise CircuitError(f"{what} must be more than 0, not {v!r}")
    return x


def is_circuit(obj: Any) -> bool:
    return isinstance(obj, Circuit)
