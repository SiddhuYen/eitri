# Firmware reference

A firmware model file has the same contract as a CAD or harness model: `PARAMS`, `build(p)`, optional `verify(fw, p, c)`, optional
`CONFIGS`. `build(p)` returns a `Firmware`. Start the file with `from eitri.firmware import *`. The sources are strings in the model
file; everything else is plain Python data.

Every build writes the sketch folder (your sources plus `eitri_pins.h`, generated from the pinout), compiles it with arduino-cli for
the board, and checks the pinout against the board and the sources. The report gives pass / warn / fail per check and the review
image is the pinout. eitri never flashes hardware.

## The API

```
fw = Firmware("name", board="uno" | "nano" | "esp32", description="", wifi=False, powered="usb" | "vin", vin_V=None)

fw.pin(pin, direction="out" | "in" | "inout", role=None, net="", pullup=False)
    pin: "D9", "9", "A0" (Uno/Nano) or "GPIO25", "25" (ESP32); role: digital_in, digital_out, pwm, analog_in, dac, interrupt, servo,
    serial_tx, serial_rx, i2c_sda, i2c_scl (default: digital_out / digital_in by direction); net: the name the code uses as
    PIN_<net> (default: the pin's name)
fw.servo(name, pin, min_us=544, max_us=2400, power=None, current_A=None)   a hobby servo: pin role servo, net = name;
    power: "5V" / "3V3" (from the board's pin) or "external" (its own supply, ground shared with the board), current_A its stall current
fw.pwm_motor(name, pwm, dir, dir2=None, enable=None)             a DC motor driver: <name>_PWM (pwm), <name>_DIR (digital_out), ...
fw.button(name, pin, pullup=True, active_low=True, interrupt=False)          a push button to ground (a user input): digital_in (or interrupt)
fw.limit_switch(name, pin, pullup=True, active_low=True, interrupt=False)    a limit / end-stop switch the mechanism trips: digital_in (or interrupt)
fw.potentiometer(name, pin)                                       a potentiometer's wiper on an analog pin: analog_in
fw.serial(baud=115200)                                           the hardware serial port: its pins stay reserved; SERIAL_BAUD is defined
fw.load(name, supply="5V" | "3V3", current_A=None)               anything else powered from a board supply pin (a sensor module, a display)
fw.source("sketch.ino", code)                                    a source file (one .ino is the main sketch; .h / .c / .cpp beside it)
```

`eitri_pins.h` (generated; do not write it) holds `const uint8_t PIN_<NET> = <number>;` for every declared pin (and `SERIAL_BAUD`).
`#include "eitri_pins.h"` and use the constants: then a pin moved in the pinout moves in the code, and the usage check can follow
every call.

## Boards

{{BOARDS}}

## The checks every build runs

- target board: one of the boards above.
- each pin: exists on the board, is not forbidden (ESP32 flash pins), and can do its role and direction (PWM, ADC, DAC, interrupt,
  output, pull-up); warnings for ESP32 strapping pins, ADC2 inputs with Wi-Fi on, the Uno's LED pin used as an input.
- no pin assigned twice, and the hardware serial pins left free while Serial is used.
- each `PIN_<NET>` used exactly as declared: an output needs `pinMode(..., OUTPUT)` and is never read as an input; an input is never
  written; `analogWrite` / LEDC only on a pwm pin, `analogRead` only on an analog_in pin, `servo.attach` only on a servo pin,
  `attachInterrupt` only on an interrupt pin; a declared pin the code never uses fails, and so does a pull-up declared but configured
  as `INPUT`.
- no undeclared pins: a pin API call with a literal or a constant the pinout does not declare fails.
- power budget (when servos or loads declare `power=` / `current_A=`): the declared draw on the board's 5V and 3V3 pins against what
  they can supply (Uno / Nano 3V3 50 mA; 5V 500 mA on USB; the Uno on VIN its regulator, 0.8 A or less as it heats; others ungraded).
  A servo's stall current (an MG996R: 2.5 A) is far above any board pin: declare it `power="external"` and share the ground.
- compiles (arduino-cli): every compile error is listed as file:line: message; flash and RAM use are reported (a warning above 90%
  RAM); compiler warnings in your own files are a warning. Without arduino-cli or the board's core this check fails and says how to
  install them.
- your own `verify(fw, p, c)`: `c.equal`, `c.true`, `c.count`, ... Helpers: `fw.get_pin(net)`, `fw.board.resolve(pin)` (what the pin
  can do), `scan_calls(fw)` (every pin call in the sources).

## Outputs of every build (out/)

`<name>.pinout.svg`, `<name>.png` (the review image), `<name>.pinout.md` / `.csv`, `eitri_pins.h`, `sketch/<sketch>/` (the folder
that was compiled), `<name>.compile.log`.

## Direct edits

The user edits the pinout table in the browser editor. Their changes land in the model file as an `EDITS` list (`set_pin` (by net:
pin, direction, role, pullup, rename), `add_pin`, `remove_pin`, `set_board`) applied after `build()`. The sources are read-only in the
editor. Read the edits with `list_edits` and fold them into `build()` when convenient.

## Worked example: a servo sweep from a pinout

```python
from eitri.firmware import *

PARAMS = dict(step_ms=15, sweep_deg=180)

SKETCH = r'''
#include <Servo.h>
#include "eitri_pins.h"

Servo pan;
const int STEP_MS = {step_ms};
const int SWEEP = {sweep_deg};

void setup() {
  Serial.begin(SERIAL_BAUD);
  pan.attach(PIN_PAN);
  pinMode(PIN_START, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
}

void sweep(int from, int to) {
  int dir = from < to ? 1 : -1;
  for (int a = from; a != to + dir; a += dir) {
    pan.write(a);
    delay(STEP_MS);
  }
}

void loop() {
  if (digitalRead(PIN_START) == LOW) {        // the button pulls the pin low
    digitalWrite(PIN_LED, HIGH);
    Serial.println("sweep");
    sweep(0, SWEEP);
    sweep(SWEEP, 0);
    digitalWrite(PIN_LED, LOW);
  }
}
'''


def build(p):
    fw = Firmware("Servo sweep", board="uno", description="sweep a servo while a button is held")
    fw.servo("PAN", pin="D9")
    fw.button("START", pin="D2")
    fw.pin("D13", "out", "digital_out", net="LED")
    fw.serial(115200)
    fw.source("sweep.ino", SKETCH.replace("{step_ms}", str(p.step_ms)).replace("{sweep_deg}", str(p.sweep_deg)))
    return fw


def verify(fw, p, c):
    c.true("servo on a timer-friendly pin", fw.board.resolve(fw.get_pin("PAN").pin).number in (9, 10))
```
