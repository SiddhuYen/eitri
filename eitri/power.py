"""How much current a development board's supply pins can give to what is wired to them, with the source of every number.

Shared by the wire-harness and the firmware domains (each grades its own model against it; neither imports the other).

    supply_limit(board, pin, powered="usb", vin_V=None, ambient_C=25) -> SupplyLimit
        board: "uno" | "nano" (others: unknown); pin: "5V" | "3V3"; powered: how the board itself is powered ("usb" or "vin").
        SupplyLimit.limit_A is None when no published figure exists: the check then says so instead of guessing.

The limits are for what the pin can source in total. The board's own draw (the ATmega328P, the USB bridge, the LEDs) comes out of the
same budget, so a load near the limit is reported as a warning.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------- sources (quoted; fetched 2026-10-26)

SRC_UNO_PINOUT = ("Arduino UNO R3 full pinout, docs.arduino.cc/resources/pinouts/A000066-full-pinout.pdf: "
                  "\"MAXIMUM current per +3.3V pin is 50mA\"")
SRC_NANO_PINOUT = ("Arduino Nano full pinout, docs.arduino.cc/resources/pinouts/A000005-full-pinout.pdf: "
                   "\"MAXIMUM current per +3.3V pin is 50mA\"")
SRC_UNO_IO = ("Arduino UNO R3 full pinout, docs.arduino.cc/resources/pinouts/A000066-full-pinout.pdf: \"MAXIMUM current per I/O pin is 20mA\" "
              "(the ATmega328P's absolute maximum is 40 mA; Arduino's figure is the one to design to)")
SRC_NANO_IO = ("Arduino Nano full pinout, docs.arduino.cc/resources/pinouts/A000005-full-pinout.pdf: \"MAXIMUM current per I/O pin is 20mA\"")
SRC_UNO_USB = ("Arduino UNO R3 schematic, docs.arduino.cc/resources/schematics/A000066-schematics.pdf: the USB 5V line runs through "
               "a MF-MSMF050-2 500mA resettable polyfuse; Arduino store, UNO Rev3: \"If more than 500 mA is applied to the USB port, "
               "the fuse will automatically break the connection\"")
SRC_USB2 = ("USB 2.0 Specification, section 7.2.1: a port supplies at most 5 unit loads of 100 mA (500 mA) to a configured high-power "
            "device; the Nano's 5V pin is fed from the host's USB port when it runs on USB")
SRC_UNO_REG = ("Arduino UNO R3 datasheet A000066 (rev. 06/2021), board topology: U1 SPX1117M3-L-5 regulator (the R3 schematic shows "
               "an NCP1117ST50T3G, rated 1.0 A; the lower rating is used). MaxLinear SPX1117 datasheet: guaranteed 0.8 A output; "
               "SOT-223 thermal resistance 15 C/W junction-to-tab + 31 C/W tab-to-ambient = 46 C/W; maximum junction temperature "
               "125 C; PD = (VIN - VOUT) x IOUT")
SRC_NANO_VIN = ("Arduino Nano datasheet A000005 (docs.arduino.cc/resources/datasheets/A000005-datasheet.pdf) names an LDO on the VIN "
                "path but publishes no current rating for it")

SPX1117_MAX_A = 0.8           # MaxLinear SPX1117 datasheet: guaranteed output current
SPX1117_THETA_JA = 46.0       # C/W, SOT-223, junction to ambient (15 + 31), same datasheet
SPX1117_TJ_MAX = 125.0        # C, same datasheet
REGULATED_V = 5.0


@dataclass(frozen=True)
class SupplyLimit:
    board: str
    pin: str
    powered: str
    limit_A: float | None       # None: no published figure (the check reports it ungraded)
    basis: str                  # one line: what limits it ("USB polyfuse", "regulator, thermal at 9 V in")
    source: str                 # where the number comes from


BOARDS = {"uno": "Arduino Uno", "nano": "Arduino Nano"}
SUPPLY_PINS = ("5V", "3V3")


def amps(x: float) -> str:
    """A current as people write it: "2.5 A", "0.02 A", and below a milliamp "50 µA" (never "5e-05 A")."""
    return f"{x * 1e6:.3g} µA" if 0 < abs(x) < 1e-3 else f"{x:g} A"


def board_key(text: str | None) -> str | None:
    """'uno', 'nano' from a board name or a harness connector type ('Arduino Uno', 'ARDUINO-NANO', 'uno')."""
    t = str(text or "").strip().lower().replace("arduino", "").replace("-", " ").replace("_", " ").strip()
    return t if t in BOARDS else None


def supply_pin_name(pin: str) -> str | None:
    """'5V' / '3V3' from a pin as written ('5V', '+5V', '3.3V', '3V3', '+3V3'), else None."""
    p = str(pin).strip().upper().lstrip("+").replace("3.3V", "3V3")
    return p if p in SUPPLY_PINS else None


def io_pin_limit(board: str) -> SupplyLimit:
    """What one I/O pin may source or sink (an LED, a relay coil, a buzzer driven straight from the pin)."""
    key = board_key(board)
    if key == "uno":
        return SupplyLimit(key, "I/O", "", 0.020, "an I/O pin's rating", SRC_UNO_IO)
    if key == "nano":
        return SupplyLimit(key, "I/O", "", 0.020, "an I/O pin's rating", SRC_NANO_IO)
    return SupplyLimit(str(board), "I/O", "", None, "no per-pin rating recorded for this board", "")


def supply_limit(board: str, pin: str, powered: str = "usb", vin_V: float | None = None, ambient_C: float = 25.0) -> SupplyLimit:
    key, name = board_key(board), supply_pin_name(pin)
    powered = (powered or "usb").lower()
    if key is None or name is None:
        return SupplyLimit(str(board), str(pin), powered, None, "no published supply limit for this board", "")
    if name == "3V3":
        return SupplyLimit(key, name, powered, 0.050, "the 3.3 V pin's rating", SRC_UNO_PINOUT if key == "uno" else SRC_NANO_PINOUT)
    if powered == "usb":
        if key == "uno":
            return SupplyLimit(key, name, powered, 0.5, "the USB polyfuse (500 mA)", SRC_UNO_USB)
        return SupplyLimit(key, name, powered, 0.5, "the host's USB port (500 mA)", SRC_USB2)
    if powered == "vin":
        if key == "nano":
            return SupplyLimit(key, name, powered, None, "Arduino publishes no rating for the Nano's VIN regulator", SRC_NANO_VIN)
        if vin_V is None:
            return SupplyLimit(key, name, powered, SPX1117_MAX_A, "the 5 V regulator's rating (give vin_V for its thermal limit)", SRC_UNO_REG)
        headroom = float(vin_V) - REGULATED_V
        if headroom <= 0:
            return SupplyLimit(key, name, powered, 0.0, f"{vin_V:g} V on VIN is not above the 5 V output: the regulator cannot regulate", SRC_UNO_REG)
        thermal = (SPX1117_TJ_MAX - float(ambient_C)) / (SPX1117_THETA_JA * headroom)
        if thermal < SPX1117_MAX_A:
            return SupplyLimit(key, name, powered, round(thermal, 3),
                               f"the 5 V regulator's heat at {vin_V:g} V in, {ambient_C:g} C ambient (rated {SPX1117_MAX_A:g} A)", SRC_UNO_REG)
        return SupplyLimit(key, name, powered, SPX1117_MAX_A, "the 5 V regulator's rating", SRC_UNO_REG)
    return SupplyLimit(key, name, powered, None, f"unknown power source {powered!r} (usb or vin)", "")


def grade(draw_A: float, limit: SupplyLimit) -> str:
    """fail over the limit, warn above 80 % (the board's own draw shares the budget), else pass; 'warn' when ungraded."""
    if limit.limit_A is None:
        return "warn"
    if draw_A > limit.limit_A + 1e-9:
        return "fail"
    return "warn" if draw_A > 0.8 * limit.limit_A else "pass"


def advice(pin: str) -> str:
    return (f"power the load from a separate supply (its own regulator or battery) and connect that supply's ground to the board's GND, "
            f"keeping only the signal wire on the board; the {pin} pin then carries nothing")
