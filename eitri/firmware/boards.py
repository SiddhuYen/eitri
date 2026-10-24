"""What each supported board's pins can do. Sources: the Arduino Uno Rev3 and Nano pinout sheets (ATmega328P: PWM on D3, D5, D6,
D9, D10, D11; external interrupts on D2, D3; A6/A7 on the Nano are analog-only inputs; Serial on D0/D1; I2C on A4/A5; SPI on
D10-D13; the LED on D13), and Espressif's ESP32 datasheet and ESP32-DevKitC pin layout (GPIO6-11 are wired to the module's flash
and must not be used; GPIO34-39 are input-only without pull-ups; ADC1 is GPIO32-39, ADC2 is GPIO0, 2, 4, 12-15, 25-27 and is
unusable while Wi-Fi runs; GPIO0, 2, 5, 12, 15 are boot strapping pins; LEDC PWM on any output-capable GPIO; DAC on GPIO25/26;
UART0 on GPIO1/3; default I2C on GPIO21/22)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PinCap:
    name: str                 # canonical name: "D9", "A0", "GPIO25"
    number: int               # the number the Arduino API takes (digitalWrite(9), A0 = 14 on the Uno)
    digital_in: bool = True
    digital_out: bool = True
    pwm: bool = False
    analog_in: bool = False
    dac: bool = False
    interrupt: bool = False
    pullup: bool = True
    notes: tuple[str, ...] = ()       # warnings when used (strapping pin, ADC2 with Wi-Fi, the LED)
    forbidden: str = ""               # why it must not be used at all


@dataclass(frozen=True)
class Board:
    key: str
    name: str
    fqbn: str
    core: str                         # the arduino-cli core to install
    pins: dict[str, PinCap] = field(default_factory=dict)
    serial_pins: tuple[str, ...] = ()
    i2c_pins: tuple[str, ...] = ()
    servo_lib: str = "Servo"          # the library a servo sketch includes
    wifi: bool = False
    aliases: tuple[str, ...] = ()

    def resolve(self, text) -> PinCap | None:
        """A pin by any of its spellings: 9, "9", "D9", "A0", 25, "GPIO25", "IO25"."""
        t = str(text).strip().upper().replace(" ", "")
        if t in self.pins:
            return self.pins[t]
        m = re.fullmatch(r"(?:GPIO|IO|D|P)?(\d+)", t)
        if m:
            n = int(m.group(1))
            for cand in (f"D{n}", f"GPIO{n}"):
                if cand in self.pins:
                    return self.pins[cand]
            for p in self.pins.values():                   # 14..19 on an AVR board are A0..A5
                if p.number == n:
                    return p
        return None


def _avr(key: str, name: str, fqbn: str, analog_only: tuple[str, ...] = ()) -> Board:
    pins: dict[str, PinCap] = {}
    for n in range(14):
        notes = []
        if n in (0, 1):
            notes.append("Serial RX/TX: in use whenever Serial is")
        if n == 13:
            notes.append("drives the on-board LED (an input here reads through the LED's resistor)")
        pins[f"D{n}"] = PinCap(f"D{n}", n, pwm=n in (3, 5, 6, 9, 10, 11), interrupt=n in (2, 3), notes=tuple(notes))
    for i in range(6):
        pins[f"A{i}"] = PinCap(f"A{i}", 14 + i, analog_in=True, notes=(("I2C SDA",) if i == 4 else ("I2C SCL",) if i == 5 else ()))
    for a in analog_only:
        i = int(a[1:])
        pins[a] = PinCap(a, 14 + i, digital_in=False, digital_out=False, analog_in=True, pullup=False, notes=("analog input only",))
    return Board(key, name, fqbn, "arduino:avr", pins, serial_pins=("D0", "D1"), i2c_pins=("A4", "A5"), servo_lib="Servo")


def _esp32() -> Board:
    pins: dict[str, PinCap] = {}
    adc1 = set(range(32, 40))
    adc2 = {0, 2, 4, 12, 13, 14, 15, 25, 26, 27}
    strapping = {0, 2, 5, 12, 15}
    for n in list(range(0, 24)) + [25, 26, 27, 32, 33, 34, 35, 36, 39]:
        notes = []
        forbidden = "wired to the module's SPI flash" if 6 <= n <= 11 else ""
        if n in strapping:
            notes.append("a boot strapping pin: its level at reset selects the boot mode")
        if n in adc2:
            notes.append("ADC2: analog reads fail while Wi-Fi is on")
        if n in (1, 3):
            notes.append("UART0 (USB serial) TX/RX")
        out = n < 34
        pins[f"GPIO{n}"] = PinCap(f"GPIO{n}", n, digital_out=out, pwm=out, analog_in=n in adc1 or n in adc2, dac=n in (25, 26), interrupt=True,
                                  pullup=n < 34, notes=tuple(notes) + (("input only, no internal pull-up",) if n >= 34 else ()), forbidden=forbidden)
    return Board("esp32", "ESP32 DevKit (ESP32-WROOM-32)", "esp32:esp32:esp32", "esp32:esp32", pins, serial_pins=("GPIO1", "GPIO3"),
                 i2c_pins=("GPIO21", "GPIO22"), servo_lib="ESP32Servo", wifi=True, aliases=("esp32dev", "esp32-devkit", "devkitc"))


BOARDS: dict[str, Board] = {
    "uno": _avr("uno", "Arduino Uno (ATmega328P)", "arduino:avr:uno"),
    "nano": _avr("nano", "Arduino Nano (ATmega328P)", "arduino:avr:nano", analog_only=("A6", "A7")),
    "esp32": _esp32(),
}


def board(key: str) -> Board | None:
    k = str(key).strip().lower()
    if k in BOARDS:
        return BOARDS[k]
    for b in BOARDS.values():
        if k in b.aliases or k == b.fqbn:
            return b
    return None


def boards_table() -> str:
    rows = ["| board | FQBN | core | PWM pins | analog in | notes |", "|---|---|---|---|---|---|"]
    for b in BOARDS.values():
        pwm = [p.name for p in b.pins.values() if p.pwm and not p.forbidden]
        ana = [p.name for p in b.pins.values() if p.analog_in and not p.forbidden]
        note = {"uno": "Serial on D0/D1, I2C A4/A5, LED D13", "nano": "as the Uno, plus A6/A7 analog-only",
                "esp32": "GPIO6-11 forbidden (flash), GPIO34-39 input-only, ADC2 unusable with Wi-Fi, strapping 0/2/5/12/15"}[b.key]
        rows.append(f"| {b.key} | {b.fqbn} | {b.core} | {', '.join(pwm) if len(pwm) < 10 else f'any output GPIO ({len(pwm)})'} | {', '.join(ana) if len(ana) < 10 else f'{len(ana)} pins'} | {note} |")
    return "\n".join(rows)
