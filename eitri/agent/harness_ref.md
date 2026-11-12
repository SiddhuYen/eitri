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
h.source(pin, voltage_V, max_current_A=None, short_circuit_A=None, kind="dc", part="")   a DC power source (its + pin)
    kind       psu | battery | board | dc;  voltage_V the highest it reaches (a battery at full charge)
    max_current_A    the most it pushes into a fault: a supply's current limit or the top of its overload-protection range
    short_circuit_A  its prospective short-circuit current, from its datasheet (runs the interrupting-rating check)
    Leave both None when the datasheet gives neither: the source then counts as unlimited (protection required).
    DC up to 60 V only: ac=True or a higher voltage is refused (mains wiring is out of scope).
Fuse(name, type, rating_A)    a protective device in a wire: two terminals, LINE (source side) and LOAD
    Fuse("F1", "ATO", 5)  Fuse("F2", "MINI", 3)  Fuse("F3", "MF-R110")   catalog parts (table below): their figures are fixed
    Fuse("F4", "FUSE", 3, voltage_rating_V=, interrupt_A=, continuous_fraction=, part_number=)   your own part, its datasheet's figures
    Breaker("CB1", 10, voltage_rating_V=, interrupt_A=, continuous_fraction=, part_number=)       a DC breaker
    wire it in line: h.connect(src["+"], f["LINE"], ...), h.connect(f["LOAD"], <the branch>, ...)
Harness(name, description="", ambient_C=None)   ambient_C: the air around the fuses (default 40 C, said in the report)
h.net(..., signal=True)    a net driven by a logic or sensor output, not a supply: graded against the devices declared at its ends
h.mcu_pin(pin, board="uno"|"nano")           that pin is a microcontroller board's I/O pin (20 mA, Arduino's pinout); a connector of
                                             type ARDUINO-UNO / ARDUINO-NANO declares its own pins
h.plc_input(pin, iec_type=1|2|3, part="")    a PLC digital input of IEC 61131-2 type 1, 2 or 3 (24 V DC): ON 15.2-30 V (type 1) or
                                             11.2-30 V (types 2, 3) at up to 15 / 30 / 15 mA; a 2-wire sensor must leak under
                                             0.4 / 1.9 / 1.3 mA (graph-read from TI's ISO1228 data sheet, Figure 8-1)
h.sensor_output(pin, part="Festo SMT-8M-A-PS-24V-E-0,3-M8D" | "SMC D-M9B")   a catalogue sensor's switching output (its data
                                             sheet's output current, drop, leakage); another sensor: max_output_A=, wires=3|2,
                                             drop_V=, leakage_A= from its data sheet
h.link("W3")  h.links  h.wire_current(link)         read back what was built (for verify)
```

Nets. `net("VBAT", current_A=44.5, voltage_V=14.8, max_drop_pct=3)`: current_A is the net's MAXIMUM current, the
default for every wire of the net; give a branch its own `current_A=` when it carries less. `round_trip=True` (default)
counts the return conductor: drop = 2*K*I*L/CM; make the return net `round_trip=False` so it is not counted twice.
A splice may only join wires of ONE net, so a trunk and its branches are all on the same net, with
`current_A=` on the branches. Wires without a net are not graded for current or drop (a warning). A branch or a cable's return core that carries less than the whole net needs its own `current_A` (`connect(..., current_A=)`,
or `links[i].current_A = ...` on what `connect_cable` returns); without it the wire is graded at the whole net's current, and a thin
return core then fails "one gauge per net" (the message names the wire).

## Connector catalog

{{CATALOG}}

An unknown type is allowed (`Connector("X", "ACME-9", pins=4, rating_a=5, pitch_mm=3.5)`); without `rating_a` its pins
are not graded and a warning says so. Industrial field wiring: `M8-3`, `M8-4`, `M12-4`, `M12-5` (A-coded; the connector end of a
moulded cordset, so model the cordset as a `Cable` ending in that connector; Pepperl+Fuchs data sheets) and `WAGO-2002-1201` (a 2.5 mm²
push-in DIN-rail feed-through terminal: one pin per terminal block, the harness lands on one of its two points; its wire range
is graded in mm²). These carry a DC voltage rating: "connector voltage" checks the nets on them against it. Crimp-type connectors need one crimp terminal per populated pin (the BOM counts them).
Ratings are continuous. Between an XT60's or XT90's continuous and its short-time rating the check warns (peaks under a
minute only); past it, it fails. Spade terminals are rated by the wire crimped in (22 AWG 3 A ... 12 AWG 20 A, 10 AWG 24 A), and
so is Micro-Fit 3.0, which also derates with the number of powered circuits in the housing (Molex PS-43045, wire-to-wire:
18 AWG 7 A at 2 circuits, 6 A at 6, 5.5 A at 12; 20 AWG 6.5 / 5 / 4.5 A; 22 AWG 5.5 / 4 / 3.5 A; 24 AWG 5 / 4 / 3 A; 26-30 AWG
4 A or less): a thicker wire or fewer powered pins raises the rating, and the BOM names the terminal for each wire's size.

## Protective devices (fuses, PTCs, breakers)

{{FUSES}}

Every branch fed from a declared source that can deliver more than its thinnest wire's ampacity needs a protective device
before it reaches a load. Put one fuse right at the source (within 175 mm) for the whole rail when one rating fits both the
thinnest wire and the steady load; otherwise a main fuse at the source plus a branch fuse per thinner branch. The fuse protects
the wire, not the load: choose the rating from the wire (at most its ampacity) and check the load fits under it.
Opening times, for judging a load's inrush (Littelfuse datasheets): ATO and MINI hold 110 % for at least 100 h and open in
0.75-600 s at 135 %, 0.15-5 s at 200 %; the 5x20 time-lag 477 holds 150 % for 60 min (30 min from 8 A) and opens in
0.75-80 s at 275 %. A PTC holds its hold current for 30 min and trips at twice it (Bourns MF-R). Every figure's source is
in `eitri/harness/fuses.py` (`FUSE_SOURCES`).

## The checks every build runs

- connectivity (FAIL): every pin connected or declared NC; one wire per pin (wires meet only at a splice); no
  duplicate wires; no dangling splice (fewer than 2 wires); a splice joins one net; every wire has a length.
  (WARN): wires without a net; a net with no wires.
- ampacity (FAIL above the limit, WARN within 20% of it): each wire's current against the chassis-wiring table
  (22 AWG 7 A, 20: 11, 18: 16, 16: 22, 14: 32, 12: 41, 10: 55, 8: 73, 6: 101, 4: 135, 2: 181 A; metric sizes use the nearest AWG),
  and each connector pin's summed current against its rating.
- terminal fit (FAIL): every wire crimped into a connector is within the sizes that connector family's contacts take
  (JST XH AWG 22-30, JST PH 24-32, Dupont 2.54 22-32, Micro-Fit 3.0 18-30): a 22 AWG wire does not go into a PH contact.
- voltage drop (FAIL above max_drop_pct): V = 2*K*I*L/CM, K = 12.9 (copper at 75 C, NEC Chapter 9 Table 8), L in feet, CM the conductor's circular mils;
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
- protection (DC up to 60 V; starts from every `h.source(...)`):
  - every branch from a source: FAIL if a wire thinner than what the source can deliver reaches a load with no device between.
  - rating within the wire: a device's rating (a PTC: its trip current, 2 x hold) at most the ampacity of the thinnest wire
    it protects (FAIL above).
  - continuous load: the steady current through it at most the maker's continuous fraction of the rating, rerated for
    `ambient_C` (FAIL above: it would open on a normal load).
  - voltage rating: its DC rating at least the system voltage (FAIL). Interrupting rating: the source's `short_circuit_A` at
    most the device's interrupting rating (a PTC: Imax) (FAIL).
  - at the source end: at most 175 mm of unprotected wire from the source to the device (ABYC E-11), unless every wire on that
    lead is rated for all the source can deliver; a device fed through another device passes.
  - a supply net no declared source reaches, a device on no branch from a source, and any figure left unset (a source's
    short-circuit current, your own part's voltage or interrupting rating) are WARN "not run: ...", listed under
    "Not run" in the report. They never pass: give the figure.
  - signal nets (`signal=True`): the net's current at most the lowest limit of the devices declared at its ends (an MCU pin
    20 mA, a PLC input's type maximum, a sensor's output rating) (FAIL above: it is a supply). A PLC input also gets "signal
    net N input <pin>": the net voltage (less a 2-wire sensor's drop) within the type's ON range, and a 2-wire sensor's OFF
    leakage within the type's OFF current (FAIL). No device declared at either end: "not run" (declare one; never guess).
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
    h.source(a["2"], 5, max_current_A=3, kind="psu", part="receiver BEC, 3 A current limit (its datasheet)")   # 3 A < 22 AWG's 7 A: no fuse needed
    h.net("5V", current_A=1.5, voltage_V=5)
    h.net("GND", current_A=1.5, voltage_V=5, round_trip=False)        # the return: counted once
    h.net("SIG", current_A=0.02, voltage_V=5, signal=True)            # the servo pulse from an Arduino Uno pin wired to J1.3
    h.connect(a["2"], b["2"], Wire(p.gauge, "RD", p.length_mm), net="5V")
    h.connect(a["1"], b["1"], Wire(p.gauge, "BK", p.length_mm), net="GND")
    h.connect(a["3"], b["3"], Wire(p.gauge, "YE", p.length_mm), net="SIG")
    h.mcu_pin(a["3"], board="uno")                                    # the device at the signal's end: its 20 mA limit grades SIG
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
    h = Harness("12 V accessories", ambient_C=40)
    batt = h.add(Connector("BATT", "XT60", pins=["+", "-"]))
    pump = h.add(Connector("PUMP", "XT30", pins=["+", "-"]))          # 6 A: over 80 % of a 2-circuit Micro-Fit's 7 A (18 AWG, Molex)
    fan = h.add(Connector("FAN", "MOLEX-MICROFIT-3.0", pins=["+", "-"]))
    led = h.add(Connector("LED", "JST-XH", pins=["+", "-"]))
    bus_p, bus_n = h.splice("BUS_P"), h.splice("BUS_N")
    # the battery: 14.6 V at full charge; short_circuit_A from YOUR battery's datasheet (400 A here is an example figure)
    h.source(batt["+"], 14.6, short_circuit_A=400, kind="battery")
    main = h.add(Fuse("F1", "ATO", 15))                               # protects the 14 AWG trunk; 9 A steady
    f_pump, f_fan, f_led = h.add(Fuse("F2", "ATO", 10), Fuse("F3", "MINI", 5), Fuse("F4", "MINI", 3))
    h.net("V12", current_A=9, voltage_V=14.6)                         # the trunk's current: 6 + 2 + 1
    h.net("GND", current_A=9, voltage_V=14.6, round_trip=False)
    # the supply: battery -> main fuse (at the battery) -> splice -> a fuse per branch -> each load; source toward load
    h.connect(batt["+"], main["LINE"], Wire(14, "RD", 80), net="V12")
    h.connect(main["LOAD"], bus_p, Wire(14, "RD", p.trunk_mm), net="V12")
    for f, load, gauge, mm, amps in ((f_pump, pump, 18, p.pump_mm, 6), (f_fan, fan, 20, p.fan_mm, 2), (f_led, led, 22, p.led_mm, 1)):
        h.connect(bus_p, f["LINE"], Wire(14, "RD", 50), net="V12")       # the fuse block's feed: short, trunk gauge (F1 protects it)
        h.connect(f["LOAD"], load["+"], Wire(gauge, "RD", mm), net="V12", current_A=amps)
    # the return: load -> splice -> battery (unfused: the fuses sit in the + side)
    h.connect(pump["-"], bus_n, Wire(18, "BK", p.pump_mm), net="GND", current_A=6)
    h.connect(fan["-"], bus_n, Wire(20, "BK", p.fan_mm), net="GND", current_A=2)
    h.connect(led["-"], bus_n, Wire(22, "BK", p.led_mm), net="GND", current_A=1)
    h.connect(bus_n, batt["-"], Wire(14, "BK", p.trunk_mm + 80), net="GND")
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
    h.source(ecu["1"], 5, max_current_A=0.2, kind="board", part="ECU sensor supply, 200 mA limit (its datasheet)")
    h.net("5V", current_A=0.05, voltage_V=5)
    h.net("GND", current_A=0.05, voltage_V=5, round_trip=False)
    h.net("SIG", current_A=0.001, voltage_V=5, signal=True)            # into the controller's analog pin (an Arduino Nano A0)
    h.mcu_pin(ecu["3"], board="nano")
    cable = Cable("SENSOR", [Wire(24, "RD"), Wire(24, "BK"), Wire(24, "GN")], shield=True, jacket_color="GY", length_mm=p.length_mm)
    h.connect_cable(cable, [ecu["1"], ecu["2"], ecu["3"]], [sns["1"], sns["2"], sns["3"]], net=["5V", "GND", "SIG"],
                    shield_a=ecu["4"])                                # shield grounded at the ECU end only
    return h


def verify(h, p, c):
    c.at_most("5V drop %", net_stats(h)["5V"].drop_pct, 1.0)
```
