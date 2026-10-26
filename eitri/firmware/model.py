"""The firmware model: a target board, a pinout table, peripherals, and the source files the agent writes.

    from eitri.firmware import *

    PARAMS = dict(sweep_ms=15)

    def build(p):
        fw = Firmware("Servo sweep", board="uno")
        fw.servo("PAN", pin="D9")                       # a peripheral declares its pins
        fw.pin("D2", "in", "digital_in", net="BUTTON")  # or declare a pin directly
        fw.source("sweep.ino", SKETCH)                  # the sketch; #include "eitri_pins.h" for PIN_PAN, PIN_BUTTON
        return fw

The kernel writes eitri_pins.h next to the sources (`const uint8_t PIN_<NET> = <number>;` for every declared pin) so the sketch
uses the pinout by name, checks the pinout against the board and the sources, and compiles the sketch with arduino-cli. It never
flashes hardware.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .boards import BOARDS, Board, board as find_board

ROLES = ("digital_in", "digital_out", "pwm", "analog_in", "dac", "interrupt", "servo", "serial_tx", "serial_rx", "i2c_sda", "i2c_scl")
DIRECTIONS = ("in", "out", "inout")
ROLE_DIRECTION = {"digital_in": "in", "analog_in": "in", "interrupt": "in", "serial_rx": "in", "digital_out": "out", "pwm": "out", "dac": "out",
                  "servo": "out", "serial_tx": "out", "i2c_sda": "inout", "i2c_scl": "inout"}
_NET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class FirmwareError(ValueError):
    """The firmware project is described wrongly (pin problems against the board are reported by the checks)."""


@dataclass(eq=False)
class PinDecl:
    pin: str                         # as written ("D9", "9", "GPIO25"); resolved against the board by the checks
    direction: str = "out"
    role: str = "digital_out"
    net: str = ""                    # the name the sketch uses: PIN_<net>
    peripheral: str = ""             # the peripheral that declared it
    pullup: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        self.pin = str(self.pin).strip()
        if not self.pin:
            raise FirmwareError("a pin needs a name (D9, A0, GPIO25)")
        if self.direction not in DIRECTIONS:
            raise FirmwareError(f"pin {self.pin}: direction must be one of {DIRECTIONS}, got {self.direction!r}")
        if self.role not in ROLES:
            raise FirmwareError(f"pin {self.pin}: role must be one of {ROLES}, got {self.role!r}")
        if not self.net:
            self.net = re.sub(r"[^A-Za-z0-9_]", "_", self.pin.upper())
        if not _NET_RE.match(self.net):
            raise FirmwareError(f"pin {self.pin}: net {self.net!r} must be a C identifier (letters, digits, _)")

    @property
    def symbol(self) -> str:
        return f"PIN_{self.net}"


@dataclass(eq=False)
class Peripheral:
    name: str
    kind: str                        # servo | pwm_motor | limit_switch | button | potentiometer | serial | load
    pins: dict[str, str] = field(default_factory=dict)       # function -> pin
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=False)
class Source:
    name: str                        # a file name: sketch.ino, helpers.h, motor.cpp
    code: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.(ino|h|hpp|c|cpp)", self.name or ""):
            raise FirmwareError(f"source file name {self.name!r} must be a plain .ino / .h / .c / .cpp file name")
        if not isinstance(self.code, str):
            raise FirmwareError(f"source {self.name}: code must be a string")


class Firmware:
    def __init__(self, name: str = "firmware", board: str = "uno", description: str = "", wifi: bool = False, libraries: list[str] | None = None,
                 powered: str = "usb", vin_V: float | None = None):
        self.name = name
        self.powered = str(powered or "usb").lower()           # how the board itself is powered: "usb" or "vin" (the power-budget check)
        if self.powered not in ("usb", "vin"):
            raise FirmwareError(f"powered must be 'usb' or 'vin', not {powered!r}")
        self.vin_V = None if vin_V is None else float(vin_V)
        self.description = description
        self.board_key = str(board)
        self.wifi = bool(wifi)
        self.pins: list[PinDecl] = []
        self.peripherals: list[Peripheral] = []
        self.sources: list[Source] = []
        self.libraries: list[str] = list(libraries or [])
        self.serial_baud: int | None = None
        self.layout: dict[str, list[float]] = {}

    @property
    def board(self) -> Board | None:
        return find_board(self.board_key)

    # -- the pinout -----------------------------------------------------------
    def pin(self, pin, direction: str = "out", role: str | None = None, net: str = "", pullup: bool = False, notes: str = "", _peripheral: str = "") -> PinDecl:
        role = role or ("digital_out" if direction == "out" else "digital_in")
        p = PinDecl(str(pin), direction, role, net, _peripheral, pullup, notes)
        if any(x.net == p.net for x in self.pins):
            raise FirmwareError(f"net {p.net!r} is already used by pin {next(x.pin for x in self.pins if x.net == p.net)}")
        self.pins.append(p)
        return p

    def servo(self, name: str, pin, min_us: int = 544, max_us: int = 2400, power: str | None = None, current_A: float | None = None) -> Peripheral:
        """A hobby servo on a digital pin (the Servo / ESP32Servo library drives it).

        power: where its supply comes from, "5V" / "3V3" (a board pin: graded by the power-budget check) or "external" (its own
        supply, ground shared with the board); current_A: what it draws at worst (its stall current, from its datasheet)."""
        params = {"min_us": int(min_us), "max_us": int(max_us)}
        params.update(_power_params(name, power, current_A))
        per = Peripheral(str(name), "servo", {"signal": str(pin)}, params)
        self.pin(pin, "out", "servo", net=str(name), _peripheral=per.name)
        self.peripherals.append(per)
        return per

    def pwm_motor(self, name: str, pwm, dir, dir2=None, enable=None) -> Peripheral:
        """A DC motor on a PWM driver (L298N / TB6612 / DRV8833 style): a PWM speed pin and one or two direction pins."""
        pins = {"pwm": str(pwm), "dir": str(dir)}
        self.pin(pwm, "out", "pwm", net=f"{name}_PWM", _peripheral=name)
        self.pin(dir, "out", "digital_out", net=f"{name}_DIR", _peripheral=name)
        if dir2 is not None:
            pins["dir2"] = str(dir2)
            self.pin(dir2, "out", "digital_out", net=f"{name}_DIR2", _peripheral=name)
        if enable is not None:
            pins["enable"] = str(enable)
            self.pin(enable, "out", "digital_out", net=f"{name}_EN", _peripheral=name)
        per = Peripheral(str(name), "pwm_motor", pins)
        self.peripherals.append(per)
        return per

    def button(self, name: str, pin, pullup: bool = True, active_low: bool = True, interrupt: bool = False) -> Peripheral:
        """A push button to ground (pull-up, active low by default): a user input, drawn and named as a button."""
        self.pin(pin, "in", "interrupt" if interrupt else "digital_in", net=str(name), pullup=pullup, _peripheral=str(name))
        per = Peripheral(str(name), "button", {"input": str(pin)}, {"pullup": pullup, "active_low": active_low, "interrupt": interrupt})
        self.peripherals.append(per)
        return per

    def potentiometer(self, name: str, pin) -> Peripheral:
        """A potentiometer's wiper on an analog input (its ends on the board's 5V / 3V3 and GND): analogRead gives its position."""
        self.pin(pin, "in", "analog_in", net=str(name), _peripheral=str(name))
        per = Peripheral(str(name), "potentiometer", {"wiper": str(pin)})
        self.peripherals.append(per)
        return per

    def limit_switch(self, name: str, pin, pullup: bool = True, active_low: bool = True, interrupt: bool = False) -> Peripheral:
        """A limit switch to ground (pull-up, active low by default); interrupt=True needs an interrupt-capable pin."""
        self.pin(pin, "in", "interrupt" if interrupt else "digital_in", net=str(name), pullup=pullup, _peripheral=str(name))
        per = Peripheral(str(name), "limit_switch", {"input": str(pin)}, {"pullup": pullup, "active_low": active_low, "interrupt": interrupt})
        self.peripherals.append(per)
        return per

    def load(self, name: str, supply: str = "5V", current_A: float | None = None, notes: str = "") -> Peripheral:
        """Anything powered from a board supply pin that is not a pin of the sketch (a sensor module, an LED strip, a display):
        supply "5V" / "3V3", current_A its worst-case draw from its datasheet. Graded by the power-budget check."""
        per = Peripheral(str(name), "load", {}, {**_power_params(name, supply, current_A), "notes": notes})
        self.peripherals.append(per)
        return per

    def serial(self, baud: int = 115200) -> Peripheral:
        """The board's hardware serial port (USB): its pins are taken while it is in use."""
        b = self.board
        self.serial_baud = int(baud)
        per = Peripheral("Serial", "serial", {"rx": b.serial_pins[0] if b else "", "tx": b.serial_pins[1] if b else ""}, {"baud": int(baud)})
        self.peripherals.append(per)
        return per

    def source(self, name: str, code: str) -> Source:
        if any(s.name == name for s in self.sources):
            raise FirmwareError(f"source {name!r} is already defined")
        if name == "eitri_pins.h":
            raise FirmwareError("eitri_pins.h is written by the kernel from the pinout; do not define it")
        s = Source(name, code)
        self.sources.append(s)
        return s

    def place(self, name: str, x: float, y: float) -> None:
        self.layout[name] = [float(x), float(y)]

    def apply_layout(self, layout) -> None:
        for k, v in (layout or {}).items():
            try:
                self.layout[str(k)] = [float(v[0]), float(v[1])]
            except (TypeError, ValueError, IndexError):
                continue

    # -- queries ------------------------------------------------------------------
    def get_pin(self, pin_or_net: str) -> PinDecl:
        for p in self.pins:
            if p.pin == pin_or_net or p.net == pin_or_net:
                return p
        raise FirmwareError(f"no pin {pin_or_net!r}; pins: {[p.pin for p in self.pins]}")

    @property
    def main_sketch(self) -> Source | None:
        inos = [s for s in self.sources if s.name.endswith(".ino")]
        return inos[0] if inos else None

    @property
    def sketch_name(self) -> str:
        m = self.main_sketch
        return m.name[:-4] if m else re.sub(r"[^A-Za-z0-9_]", "_", self.name) or "sketch"

    def pins_header(self) -> str:
        """eitri_pins.h: the pinout as constants (only pins that resolve on the board)."""
        b = self.board
        lines = ["// eitri_pins.h - written by eitri from the pinout in the model file; do not edit (edit the pinout instead)", "#pragma once",
                 "#include <stdint.h>", f"// board: {b.name if b else self.board_key}", ""]
        for p in self.pins:
            cap = b.resolve(p.pin) if b else None
            if cap is None:
                lines.append(f"// {p.symbol}: {p.pin} is not a pin of this board")
                continue
            lines.append(f"const uint8_t {p.symbol} = {cap.number};   // {cap.name}, {p.direction}, {p.role}" + (f", {p.peripheral}" if p.peripheral else ""))
        if self.serial_baud:
            lines.append(f"const unsigned long SERIAL_BAUD = {self.serial_baud}UL;")
        return "\n".join(lines) + "\n"

    def __repr__(self) -> str:
        return f"Firmware({self.name!r}: {self.board_key}, {len(self.pins)} pins, {len(self.sources)} source file(s))"


def _power_params(name, power, current_A) -> dict:
    if power is None:
        if current_A is not None:
            raise FirmwareError(f"{name}: current_A needs power= (5V, 3V3 or external)")
        return {}
    p = str(power).strip().upper().lstrip("+").replace("3.3V", "3V3")
    if p == "EXTERNAL":
        p = "external"
    elif p not in ("5V", "3V3"):
        raise FirmwareError(f"{name}: power must be 5V, 3V3 or external, not {power!r}")
    if current_A is not None and float(current_A) < 0:
        raise FirmwareError(f"{name}: current_A must not be negative")
    return {"power": p, "current_A": None if current_A is None else float(current_A)}


def is_firmware(obj: Any) -> bool:
    return isinstance(obj, Firmware)


BOARD_KEYS = tuple(BOARDS)
