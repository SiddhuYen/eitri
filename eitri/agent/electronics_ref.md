# Electronics reference

An electronics model file has the same contract as a CAD or harness model: `PARAMS`, `build(p)`, optional `verify(c, p, chk)`,
optional `CONFIGS`. `build(p)` returns a `Circuit`. Start the file with `from eitri.electronics import *`. Breadboard and module level:
parts, their pins and the nets that join them; no PCB layout, no footprints (the netlist CSV is the hand-off to a layout tool).

Every build classifies each net (a rail at a voltage, a ground, a pin a board drives, a transistor's switched low side, a plain
signal), solves the series strings of two-terminal parts between them (a pin, a resistor and an LED to ground; a rail, a relay coil
and a MOSFET's drain), and grades the result against datasheet limits. Each check's text names the document its limit comes from.
The review image is the schematic.

## The API

```
c = Circuit("name", description="", ambient_C=25)

c.board(name, "uno" | "nano" | "esp32", powered="usb" | "vin", vin_V=None)
    pins: 5V, 3V3 (supplies), VIN, GND, and every GPIO (D0-D13, A0-A5 on the Uno; GPIO0-39 on the ESP32 DevKitC, without 6-11)
c.gpio(board["D2"], "in" | "out" | "io", pullup=False)   how the firmware uses a pin (default io: either level, the worst case)
c.supply(name, volts, current_A=None)            an external supply (adapter, bench supply, BEC, battery): pins "+" and "-";
                                                 current_A its rating, the budget of everything on its rail
c.regulator(name, "AMS1117-3.3" | "AMS1117-5.0" | "L7805")      pins IN, OUT, GND
c.module(name, "PCA9685" | "BME280" | "SSD1306" | "HC-SR04", address=None)   a breakout from the catalog (its pins below)
c.servo(name, "SG90" | "MG996R", current_A=None)  pins V+, GND, SIG; draws its stall current (catalog) unless current_A says otherwise
c.resistor(name, ohm, power_W=None)               pins "1", "2"; 1/4 W unless given
c.led(name, color="red", vf=None, if_max=None)    pins "A", "K"; typical 5 mm figures by colour unless vf / if_max are given
c.diode(name, "1N4148" | "1N4007" | "1N5819")     pins "A", "K"
c.capacitor(name, farad, v_rated=None, polarized=False)   pins "1", "2" (or "+", "-")
c.switch(name, style="push button")               pins "1", "2" (graded closed, the worst case)
c.mosfet(name, "IRLZ44N" | "IRF540N")             N-channel low-side switch: pins G, D, S
c.bjt(name, "PN2222A")                            NPN low-side switch: pins B, C, E
c.relay(name, "SRD-05VDC-SL-C")                   coil COIL1 / COIL2, contacts COM, NO, NC
c.motor(name, volts, current_A) / c.solenoid(name, volts, current_A)   pins "+", "-"; current_A from its datasheet
c.load(name, current_A, v_min=None, v_max=None)   anything else on a rail: pins "+" (draws current_A) and "-"

c.connect(a, b, ...)                              join pins into one net (a wire, a breadboard row); pins as part["pin"] or "R1.2"
c.net("NAME", a, b, ...)                          a named net (add more pins later the same way); two named nets never merge
c.i2c(sda_net_or_pin, speed_hz=100000, capacitance_pF=None)   I2C bus settings (default 100 kHz, Arduino Wire's default)
c.disconnect(pin), c.part(name), c.net_of(pin), c.place(name, x, y)
```

Name the nets that matter (5V, GND, SDA, SCL, SERVO_5V); an unnamed net is named after its first pin.

## Catalog

{{CATALOG}}

The PCA9685 breakout's pins: GND, OE, SCL, SDA, VCC (logic 3-5 V), V+ (servo power, passed to the servo headers), PWM0-PWM15.
BME280: VCC, GND, SCL, SDA. SSD1306: VIN, GND, SCL, SDA. HC-SR04: VCC, TRIG, ECHO, GND.

## The checks every build runs

- **connectivity**: every required pin (supply, ground, both ends of a passive) on a net; no one-pin nets; a branch point that is not
  a rail is reported as not solved (its currents are not graded).
- **power**: no rail tied to ground or driven by two supplies; every part's supply pin within its range (a BME280 on 5 V fails); the
  rail budget of every source (a board's 5V pin: 500 mA on USB, the Uno's regulator on VIN; 3V3: 50 mA; a supply: its current_A; a
  regulator: its rating), counting modules, servos at stall, resistor strings, regulators' outputs and a board's own I/O current;
  regulator headroom (dropout) and junction temperature (PD x theta-JA); a VIN-powered board's VIN in 7-12 V; one common ground
  for every part that exchanges a signal.
- **current**: per GPIO (Uno / Nano 20 mA, ESP32 28 mA) and per chip (ATmega328P 200 mA, ESP32 1.2 A); module outputs (PCA9685
  25 mA); every LED against its DC maximum; every resistor's I²R against its rating (WARN above 80 %); nothing between two fixed
  voltages without a resistance (an LED with no resistor, a switch from a driven pin straight to ground).
- **logic**: no input above its absolute maximum (5 V into an ESP32 pin fails); the driver's guaranteed VOH against the receiver's
  VIH (FAIL when the driver's supply is below it, WARN when only VOH min is); no input left floating (a switch to ground needs a pull-up).
- **i2c** (UM10204): pull-ups present on SDA and SCL, their parallel value between Rp(min) = (VDD - 0.4 V) / 3 mA and
  Rp(max) = tr / (0.8473 Cb) for the clock (Cb estimated 10 pF per device + 50 pF wiring unless given), no two devices at one address.
- **inductive**: a coil (relay, solenoid, motor) switched by a transistor or a pin has a diode across it, cathode to the supply side,
  rated for the coil current.
- **switching**: a MOSFET's gate voltage at or above the lowest VGS its RDS(on) is specified at (an IRF540N from 5 V fails), its
  I²·RDS(on) heat against Tj max; a BJT's base current at least IC/10 (the datasheet's saturation condition) and IC under its rating.
- **parts**: resistor values from E24 (a WARN otherwise, with the nearest); LEDs graded on typical figures are a WARN until you give
  vf / if_max from your LED's datasheet; a capacitor's voltage against its rating.
- your own `verify(c, p, chk)`: `chk.equal`, `chk.true`, `chk.at_most`, ... Helpers: `analyze_dc(c)` (`.strings`, `.nets`,
  `.part_current("R1")`), `c.part("R1").spec`, `nearest_e(value)`.

Conventions: currents use a driven pin at its full supply voltage (the most a string can draw); thresholds use the datasheet's
guaranteed VOH min / VIH min (the least margin). Where vendors disagree the lower figure is used, and both are quoted in the
sources. A figure marked ASSUMPTION has no fetchable source; the check says so.

## Outputs of every build (out/)

`<name>.schematic.svg`, `<name>.png` (the review image), `<name>.bom.md` / `.csv`, `<name>.netlist.md` / `.csv`, `<name>.sources.md`
(every source the build's parts use).

## Direct edits

The user edits the circuit in the browser editor. Their changes land in the model file as an `EDITS` list applied after `build()`:
`set_part` (values: ohm, power_W, vf, if_max, color, volts, current_A, farad, v_rated, address, part_number; rename), `set_net`
(rename; speed_hz, capacitance_pF on an SDA net), `connect` (a pin onto a net), `disconnect`, `set_gpio`, `add_part` (resistor, led,
diode, capacitor, switch), `remove_part`. Read them with `list_edits` and fold them into `build()` when convenient.

## Worked example: a status LED and a button

```python
from eitri.electronics import *

PARAMS = dict(led_ohm=330, pullup_ohm=10000)


def build(p):
    c = Circuit("Status LED and button", "an Uno lights an LED on D13 and reads a button on D2")
    uno = c.board("UNO", "uno")
    c.net("5V", uno["5V"])
    c.net("GND", uno["GND"])
    r1 = c.resistor("R1", p.led_ohm)                    # (5 V - 1.9 V) / 330 = 9.4 mA
    led = c.led("LED1", "red")
    c.net("LED_A", uno["D13"], r1["1"])
    c.connect(r1["2"], led["A"])
    c.connect(led["K"], "UNO.GND")
    c.gpio(uno["D2"], "in")                             # it only reads: the switch to ground cannot short it
    sw = c.switch("SW1", "push button")
    r2 = c.resistor("R2", p.pullup_ohm)
    c.net("BTN", uno["D2"], sw["1"], r2["2"])
    c.connect(r2["1"], "UNO.5V")
    c.connect(sw["2"], "UNO.GND")
    return c


def verify(c, p, chk):
    chk.at_most("LED current (A)", analyze_dc(c).part_current("LED1"), 0.012)
```

## Worked example: servos on their own supply, a relay through a MOSFET

```python
from eitri.electronics import *

PARAMS = dict(servos=4, supply_A=10.0)


def build(p):
    c = Circuit("Servo driver and gripper relay")
    uno = c.board("UNO", "uno")
    c.net("5V", uno["5V"])
    c.net("GND", uno["GND"])
    pwm = c.module("PWM", "PCA9685")                     # 10 kΩ pull-ups on SDA / SCL to its VCC
    c.connect(pwm["VCC"], "UNO.5V"); c.connect(pwm["GND"], "UNO.GND")
    c.net("SDA", uno["A4"], pwm["SDA"]); c.net("SCL", uno["A5"], pwm["SCL"])
    psu = c.supply("PSU", 5.0, current_A=p.supply_A)     # servos never run from the board's 5V pin
    c.net("SERVO_5V", psu["+"], pwm["V+"])
    c.connect(psu["-"], "UNO.GND")                       # the supplies share a ground; their + rails stay apart
    for i in range(p.servos):
        s = c.servo(f"S{i + 1}", "MG996R")
        c.connect(s["V+"], "PSU.+"); c.connect(s["GND"], "UNO.GND")
        c.connect(s["SIG"], pwm[f"PWM{i}"])
    k = c.relay("K1", "SRD-05VDC-SL-C")
    q = c.mosfet("Q1", "IRLZ44N")
    d = c.diode("D1", "1N4148")                          # flyback: cathode to the supply side
    rg, rpd = c.resistor("R1", 220), c.resistor("R2", 10000)
    c.connect(k["COIL1"], "PSU.+"); c.connect(d["K"], "PSU.+")
    c.net("COIL_LOW", k["COIL2"], d["A"], q["D"])
    c.connect(q["S"], "UNO.GND")
    c.gpio(uno["D7"], "out")
    c.connect(uno["D7"], rg["1"])
    c.net("GATE", rg["2"], q["G"], rpd["1"])
    c.connect(rpd["2"], "UNO.GND")
    return c
```

## Sources

Every document a catalog figure comes from (each build writes the ones its parts use to `<name>.sources.md`):

{{SOURCES}}
