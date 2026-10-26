# Wire harness reference

A harness model file has the same contract as a CAD model: `PARAMS`, `build(p)`, optional `verify(h, p, c)`, optional
`CONFIGS`. `build(p)` returns a `Harness`. Start the file with `from eitri.harness import *` (not `from eitri import *`:
that one brings build123d's own `Wire` class). Everything is plain Python data, deterministic, no file reads.

Every build runs the checks below and returns a report (pass / warn / fail per check) and a review image (the diagram,
failing wires and connectors outlined red, warnings amber, the failing checks listed under it). Fix the design until
every check passes and the diagram matches the request. Never delete or loosen a check to make it pass.

## The API

```
h = Harness("name", description="")

Connector(name, type, pins, part_number="", gender="", notes="", rating_a=None, pitch_mm=None, terminal_pn="", nc=())
    name   unique, no dots or spaces ("J1", "BATT")
    type   a catalog type (table below, case and punctuation forgiving: "jst xh") or your own string
    pins   a count (pins "1".."N"), a list of names (["+", "-"]) or a dict {pin name: function} ({"1": "GND", "2": "5V"})
    rating_a   per-pin current rating; required for an unknown type if its pins should be graded
    nc     pin names declared not connected
    c["+"]  c[1]  c.pin("GND")  -> a pin (by name, 1-based position, or function label)

Wire(gauge, color, length_mm=None, insulation="", notes="")
    gauge  AWG as an int (18) or metric as a string ("0.5mm2")
    color  a code BK BN RD OG YE GN BU VT GY WH PK TQ SR GD, a name ("red"), or a stripe "RD/WH"
    A Wire is a template: connect() copies it, so one Wire may be reused for many connections.

Cable(name, wires=[Wire, ...], shield=False, jacket_color="BK", length_mm=None, part_number="")
    cores without a length take length_mm

Splice(name, kind="crimp")          h.splice("PDB")       a junction of 2+ wires (crimp | solder | terminal)

h.add(connector_or_splice_or_cable_or_net, ...)     returns what it was given; connect() adds unknown connectors itself
h.connector(name, type, pins, **kw)                 shorthand for h.add(Connector(...))
h.net(name, current_A=None, voltage_V=None, max_drop_pct=3, round_trip=True)
h.connect(a, b, wire, net=None, current_A=None, length_mm=None, name=None, label="")  -> Link (has .id "W1", ...)
    a, b   conn["pin"], a Splice, or the string "CONN.PIN" / "SPLICE"
    DIRECTION MATTERS: connect from the source toward the load. Voltage drop is summed along that direction,
    and the diagram puts sources left, loads right.
    net=       the electrical intent this wire is graded against
    current_A= this wire's own current when it differs from the net's (a branch of a trunk)
h.connect_cable(cable, a_pins, b_pins, net=[one net per core], shield_a=pin, shield_b=pin)
    core i runs a_pins[i] -> b_pins[i]; shield_a / shield_b: where the cable shield is terminated at each end
h.nc("J1.3", J2[4])                                 declare pins not connected
h.link("W3")  h.links  h.wire_current(link)         read back what was built (for verify)
```

Nets. `net("VBAT", current_A=44.5, voltage_V=14.8, max_drop_pct=3)`: current_A is the net's MAXIMUM current, the
default for every wire of the net; give a branch its own `current_A=` when it carries less. `round_trip=True` (default)
counts the return conductor: drop = 2*K*I*L/CM; make the return net `round_trip=False` so it is not counted twice.
A splice may only join wires of ONE net, so a trunk and its branches are all on the same net, with
`current_A=` on the branches. Wires without a net are not graded for current or drop (a warning).

## Connector catalog

{{CATALOG}}

An unknown type is allowed (`Connector("X", "ACME-9", pins=4, rating_a=5, pitch_mm=3.5)`); without `rating_a` its pins
are not graded and a warning says so. Crimp-type connectors need one crimp terminal per populated pin (the BOM counts them).

## The checks every build runs

- connectivity (FAIL): every pin connected or declared NC; one wire per pin (wires meet only at a splice); no
  duplicate wires; no dangling splice (fewer than 2 wires); a splice joins one net; every wire has a length.
  (WARN): wires without a net; a net with no wires.
- ampacity (FAIL above the limit, WARN within 20% of it): each wire's current against the chassis-wiring table
  (22 AWG 7 A, 20: 11, 18: 16, 16: 22, 14: 32, 12: 41, 10: 55, 8: 73, 6: 101, 4: 135, 2: 181 A; metric sizes use the nearest AWG),
  and each connector pin's summed current against its rating.
- voltage drop (FAIL above max_drop_pct): V = 2*K*I*L/CM, K = 10.75, L in feet, CM the conductor's circular mils;
  per net, the worst source-to-load path, reported as % of voltage_V.
- consistency: one gauge per net (FAIL if a thinner wire carries more than a thicker one of the net, WARN if the gauge
  changes at the same current); one colour per net (WARN); a shielded cable's shield must be terminated (FAIL).
- power (when an `ARDUINO-UNO` / `ARDUINO-NANO` connector is in the harness): the current leaving the board's `5V` and `3V3` pins
  (the sum of the wires' currents there) against what the pin can supply: Uno and Nano 3V3 50 mA; 5V 500 mA on USB (the Uno's
  polyfuse, the Nano's USB port); the Uno on VIN: its 5 V regulator, 0.8 A or less as it heats up (`Connector(..., powered="vin",
  vin_V=9)`); the Nano on VIN is not published and is reported ungraded. FAIL above, WARN within 20% (the board's own draw shares the
  budget). A servo or a motor never runs from a board pin: give it its own supply and wire that supply's ground to the board's GND.
  Shared ground (FAIL): every connector that takes a signal from a board's D/A pin must reach the board's GND through some wire.
  Name the board's pins as printed on it: `5V`, `3V3`, `GND`, `VIN`, `D0`..`D13`, `A0`..`A5`; give each power net its loads'
  worst-case current (a servo's stall current from its datasheet).
- your own `verify(h, p, c)`: `c.at_most(name, value, limit)`, `c.equal`, `c.between`, `c.at_least`, `c.true`, `c.count`,
  `c.note`. Helpers: `net_stats(h)` ({net: .drop_pct, .drop_V, .current_A, .length_mm, .wires}), `h.link("W1")`,
  `h.wire_current(link)`, `voltage_drop(I, length_mm, Gauge.parse(14))`, `awg_ampacity(14)`.

## Outputs of every build (out/)

`<name>.harness.svg` (the diagram), `<name>.png` (the review image), `<name>.bom.md` / `.bom.csv` (connectors with part
numbers, crimp terminals from connector type x populated pins, wire by gauge and colour with total length + 10% service
loop, splices, labels), `<name>.cutlist.md` / `.csv` (every wire: cut length, strip lengths, terminations each end),
`<name>.wireviz.yaml` (WireViz-compatible, interop only).

## Direct edits and layout

The user edits wires, connectors and nets in the browser editor. Their changes land in the model file as an `EDITS` list
(`set_wire`, `set_connector`, `rename_pin`, `add_pin`, `remove_pin`, `add_wire`, `remove_wire`, `set_net`, `nc`) applied
after `build()`, and diagram positions as a `LAYOUT = {"BATT": [x, y]}` dict (layout hints only). Read them with
`list_edits`, treat them as design intent, and fold them into `build()` when convenient (then clear `EDITS`). A wire is
addressed by its id (`W1`...), assigned in the order `connect()` is called: keep that order stable, or pass `name="W1"`.

## Worked example 1: a point-to-point lead

```python
from eitri.harness import *

PARAMS = dict(length_mm=450, gauge=22)


def build(p):
    h = Harness("Servo extension", "3-wire servo lead, JST-XH to Dupont")
    a = h.add(Connector("J1", "JST-XH", pins={"1": "GND", "2": "5V", "3": "SIG"}, gender="female"))
    b = h.add(Connector("J2", "DUPONT-2.54", pins={"1": "GND", "2": "5V", "3": "SIG"}, gender="male"))
    h.net("5V", current_A=1.5, voltage_V=5)
    h.net("GND", current_A=1.5, voltage_V=5, round_trip=False)        # the return: counted once
    h.net("SIG", current_A=0.02, voltage_V=5)
    h.connect(a["2"], b["2"], Wire(p.gauge, "RD", p.length_mm), net="5V")
    h.connect(a["1"], b["1"], Wire(p.gauge, "BK", p.length_mm), net="GND")
    h.connect(a["3"], b["3"], Wire(p.gauge, "YE", p.length_mm), net="SIG")
    return h


def verify(h, p, c):
    c.at_most("5V drop %", net_stats(h)["5V"].drop_pct, 2.0)
    c.at_least("reach (mm)", min(l.length_mm for l in h.links), 400)
```

## Worked example 2: a trunk with branches through a splice

```python
from eitri.harness import *

PARAMS = dict(trunk_mm=600, pump_mm=400, fan_mm=350, led_mm=250)


def build(p):
    h = Harness("12 V accessories")
    batt = h.add(Connector("BATT", "XT60", pins=["+", "-"]))
    pump = h.add(Connector("PUMP", "MOLEX-MICROFIT-3.0", pins=["+", "-"]))
    fan = h.add(Connector("FAN", "MOLEX-MICROFIT-3.0", pins=["+", "-"]))
    led = h.add(Connector("LED", "JST-XH", pins=["+", "-"]))
    bus_p, bus_n = h.splice("BUS_P"), h.splice("BUS_N")
    h.net("V12", current_A=9, voltage_V=12)                           # the trunk's current: 6 + 2 + 1
    h.net("GND", current_A=9, voltage_V=12, round_trip=False)
    # the supply: battery -> splice -> each load (source toward load); branch wires carry less than the trunk
    h.connect(batt["+"], bus_p, Wire(14, "RD", p.trunk_mm), net="V12")
    h.connect(bus_p, pump["+"], Wire(18, "RD", p.pump_mm), net="V12", current_A=6)
    h.connect(bus_p, fan["+"], Wire(20, "RD", p.fan_mm), net="V12", current_A=2)
    h.connect(bus_p, led["+"], Wire(22, "RD", p.led_mm), net="V12", current_A=1)
    # the return: load -> splice -> battery
    h.connect(pump["-"], bus_n, Wire(18, "BK", p.pump_mm), net="GND", current_A=6)
    h.connect(fan["-"], bus_n, Wire(20, "BK", p.fan_mm), net="GND", current_A=2)
    h.connect(led["-"], bus_n, Wire(22, "BK", p.led_mm), net="GND", current_A=1)
    h.connect(bus_n, batt["-"], Wire(14, "BK", p.trunk_mm), net="GND")
    return h


def verify(h, p, c):
    c.at_most("V12 drop %", net_stats(h)["V12"].drop_pct, 3.0)
```

## Worked example 3: a shielded cable, parameters and configurations

```python
from eitri.harness import *

PARAMS = dict(length_mm=1500)
CONFIGS = {"bench": dict(length_mm=500), "vehicle": dict(length_mm=3000)}


def build(p):
    h = Harness("Pressure sensor cable")
    ecu = h.add(Connector("ECU", "MOLEX-MICROFIT-3.0", pins={"1": "5V", "2": "GND", "3": "SIG", "4": "SHLD"}, gender="male"))
    sns = h.add(Connector("SNS", "JST-PH", pins={"1": "5V", "2": "GND", "3": "SIG"}, gender="female"))
    h.net("5V", current_A=0.05, voltage_V=5)
    h.net("GND", current_A=0.05, voltage_V=5, round_trip=False)
    h.net("SIG", current_A=0.001, voltage_V=5)
    cable = Cable("SENSOR", [Wire(24, "RD"), Wire(24, "BK"), Wire(24, "GN")], shield=True, jacket_color="GY", length_mm=p.length_mm)
    h.connect_cable(cable, [ecu["1"], ecu["2"], ecu["3"]], [sns["1"], sns["2"], sns["3"]], net=["5V", "GND", "SIG"],
                    shield_a=ecu["4"])                                # shield grounded at the ECU end only
    return h


def verify(h, p, c):
    c.at_most("5V drop %", net_stats(h)["5V"].drop_pct, 1.0)
```
