"""The firmware rule table: every check a firmware build is graded on, its source, and how a check's source follows the model
(the board's pinout sheet, the board power limits in eitri.power). See eitri.check_sources.

A check whose name matches no rule here gets no source, and tests/test_check_sources.py fails on it.
"""
from __future__ import annotations

from .. import power
from ..check_sources import Rule, doc, model_file, requirement, rule, tool

# boards.py's sources (its docstring), as structured records. The Arduino pinout sheets are the ones eitri.power cites by URL. The
# ESP32 pin facts were checked on 2026-11-17 against the datasheet v5.3 (strapping pins Table 3-1 p.22; GPIO34-39 input-only, Table
# 6-1 note 2 p.61; ADC / DAC Table 4-6 p.46; LEDC and I2C on any GPIO via the GPIO matrix, s4.8.8 / s4.8.4), the DevKitC V4 guide
# (flash pins: "Avoid using these pins"), ESP-IDF's GPIO summary (ADC2 not with Wi-Fi) and the Arduino-ESP32 core (I2C 21/22).
PINOUT_DOCS = {
    "uno": doc("Arduino Uno Rev3 pinout sheet (ATmega328P pins)", "https://docs.arduino.cc/resources/pinouts/A000066-full-pinout.pdf",
               "Arduino UNO R3 full pinout A000066"),
    "nano": doc("Arduino Nano pinout sheet (ATmega328P pins)", "https://docs.arduino.cc/resources/pinouts/A000005-full-pinout.pdf",
                "Arduino Nano full pinout A000005"),
    "esp32": doc("Espressif ESP32 Series Datasheet v5.3 (pin functions, strapping pins, input-only GPIO34-39)",
                 "https://documentation.espressif.com/esp32_datasheet_en.pdf",
                 "ESP32 Series Datasheet v5.3 (2026.07): Tables 2-1, 3-1, 4-6, 6-1; read 2026-11-17"),
}
ESP32_MORE = [
    doc("Espressif ESP32-DevKitC V4 user guide, pin layout (GPIO6-11 wired to the flash: avoid)",
        "https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html",
        "ESP32-DevKitC V4 user guide (esp-dev-kits latest, no revision printed), Header Block notes [2], [3]; read 2026-11-17"),
    doc("ESP-IDF GPIO summary for ESP32 (ADC2 pins cannot be used when Wi-Fi is used)",
        "https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/gpio.html",
        "ESP-IDF Programming Guide v6.1.1, GPIO & RTC GPIO, GPIO Summary; read 2026-11-17"),
    doc("Arduino-ESP32 core, variants/esp32/pins_arduino.h: SDA = 21, SCL = 22, TX = 1, RX = 3 (a software default, not hardware)",
        "https://github.com/espressif/arduino-esp32/blob/master/variants/esp32/pins_arduino.h",
        "arduino-esp32 master, variants/esp32/pins_arduino.h lines 6-10; read 2026-11-17"),
]
ARDUINO_CLI = tool("arduino-cli compile for the board (its compiler's own errors and warnings)")
MG996R_STALL = doc("MG996R stall current 2.5 A (6 V), distributor datasheet; TowerPro's page says 1400 mA: the higher draw is used",
                   "https://towerpro.com.tw/product/mg996r/", "TowerPro MG996R product page and Electronicos Caldas MG996R datasheet (2014-08-19)")


def _board_doc(c, fw):
    b = fw.board
    if b is None or b.key not in PINOUT_DOCS:
        return []
    return [PINOUT_DOCS[b.key]] + (ESP32_MORE if b.key == "esp32" else [])


def _pin_current(c, fw):
    lim = power.io_pin_limit(fw.board_key)
    pin = next((d for d in fw.pins if c.name.startswith(f"pin {d.pin} (")), None)
    return [model_file(f"{pin.net} current_A") if pin is not None else model_file("current_A")] + (
        power.power_sources(lim.source) or [rule(f"not graded: {lim.basis}")])


def _supply(c, fw):
    pin = "3V3" if c.name.startswith("supply 3V3 ") else "5V"
    lim = power.supply_limit(fw.board_key, pin, fw.powered, fw.vin_V)
    return [model_file("peripheral current_A")] + (power.power_sources(lim.source) or [rule(f"not graded: {lim.basis}")])


def _servo_supply(c, fw):
    return [rule("every servo's supply is declared"), MG996R_STALL] + power.power_sources(power.SRC_UNO_USB)


F = "firmware."
RULES = [
    Rule(F + "board", "board", r"target board$", (rule("the board is one eitri knows: uno, nano, esp32"),)),
    Rule(F + "pin-exists", "pins", r"pin .+ \(", tuple(PINOUT_DOCS.values()) + tuple(ESP32_MORE), _board_doc),
    Rule(F + "pin-once", "pins", r"no pin assigned twice$", (rule("one role per physical pin; the Serial pins stay free while Serial runs"),)
         + tuple(PINOUT_DOCS.values()), lambda c, fw: [rule("one role per physical pin; the Serial pins stay free while Serial runs")] + _board_doc(c, fw)),
    Rule(F + "pin-capability", "capability", r"pin .+ can be ", tuple(PINOUT_DOCS.values()) + tuple(ESP32_MORE), _board_doc),
    Rule(F + "sources", "usage", r"sources$", (rule("the project has a main .ino sketch (arduino-cli needs one)"),)),
    Rule(F + "used-as-declared", "usage", r".+ used as declared$", (rule("the sketch uses each declared pin as its role says (eitri's source scan)"),)),
    Rule(F + "no-undeclared-pins", "usage", r"no undeclared pins$", (rule("the sketch uses no pin the pinout does not declare (eitri's source scan)"),)),
    Rule(F + "pin-constants", "usage", r"pins named by their PIN_ constants$", (rule("declared pins are written as their PIN_ constants"),)),
    Rule(F + "calls-resolved", "usage", r"pin calls resolved$", (rule("every pin call resolves to a declared pin (eitri's source scan)"),)),
    Rule(F + "compiles", "compile", r"compiles \(arduino-cli\)$", (ARDUINO_CLI,)),
    Rule(F + "compiler-warnings", "compile", r"compiler warnings in the sources$", (ARDUINO_CLI,)),
    Rule(F + "pin-current", "power", r"pin .+ current$", (model_file("current_A"),) + tuple(dict.fromkeys(power.POWER_DOCS.values())), _pin_current),
    Rule(F + "board-supply-pin", "power", r"supply \S+ pin \(", (model_file("peripheral current_A"),) + tuple(dict.fromkeys(power.POWER_DOCS.values())),
         _supply),
    Rule(F + "servo-supply", "power", r"servo supply declared$", (rule("every servo's supply is declared"), MG996R_STALL), _servo_supply),
    Rule(F + "external-supply", "power", r"loads on their own supply$", (rule("a load on its own supply shares the board's ground"),)),
]
