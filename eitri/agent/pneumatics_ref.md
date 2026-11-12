# Pneumatics reference

A pneumatics model file has the same contract as a CAD or harness model: `PARAMS`, `build(p)`, optional `verify(c, p, chk)`,
optional `CONFIGS`. `build(p)` returns a `Circuit`. Start the file with `from eitri.pneumatics import *`. Everything is plain
Python data, deterministic, no file reads.

Every build runs the checks below and returns a report (pass / warn / fail per check) and a review image (the schematic: ISO 1219-
style symbols, supply at the bottom, cylinders at the top, components and tubes failing a check in red). Fix the design until every
check passes and the schematic matches the request. Never delete or loosen a check to make it pass.

## The API

```
c = Circuit("name", description="")

c.supply(name, pressure_bar, flow_l_min=None)                  the source: gauge pressure, capacity in L/min ANR; port OUT
c.frl(name, set_bar, C=None, b=None, lubricator=False)           filter-regulator(-lubricator) unit; ports IN, OUT
c.regulator(name, set_bar, C=None, b=None)                       a bare regulator; ports IN, OUT
c.valve(name, "5/2", actuation="solenoid", returns="spring", C=None, b=None, normally="closed", center="closed")
    classes "2/2", "3/2", "4/2", "5/2", "5/3"; ports P (supply), A, B (work), R, S (exhausts) by ISO 5599; actuation solenoid |
    manual | pilot | lever | pedal | roller; returns spring | solenoid | detent; 3/2 and 2/2 take normally closed | open; 5/3 takes
    center closed | exhaust | pressure. Position 1 of a 5/2 joins P-A and B-S, position 2 joins P-B and A-R.
c.cylinder(name, bore_mm, rod_mm, stroke_mm, acting="double", load_N=0, load_retract_N=None, speed_mm_s=None,
           speed_retract_mm_s=None, cycles_per_min=0, spring_N=None, friction_fraction=0.10, duty="moving")
    ports CAP and ROD (a single-acting cylinder: CAP only, spring return of spring_N); duty sets the load-ratio limit:
    "moving" (the default: the load is moved vertically or horizontally) 0.5, "static" (clamping, low-speed pressing) 0.7,
    "guided" (moved horizontally on its own guide) 1.0 (SMC's bore-size selection guide)
c.flow_control(name, meter="out", C=None, b=None)                a speed controller (throttle-check); ports A (valve side), B (cylinder side)
c.node(name)                                                     a tee: any number of tubes meet at its port N
c.tube(name, from_port, to_port, id_mm=6, length_m=1, fittings=2, C=None)    a run of tube; ports are comp["PORT"] or "NAME.PORT"
c.silencer(valve["R"])                                           a silencer screwed onto an exhaust port;  c.exhaust(port) leaves it open
c.sensor(name, cylinder, "extended"|"retracted", part=None, signal="", terminal="")   a position switch on a cylinder: a PLC input
c.control_voltage(24)                                            the DC control voltage the coils and sensors run on
c.drawing_info(project="", ticket="", revision="", title="", drawn_by="", approved_by="")   the circuit drawing's title block
c.step(name, "C1+", "C2-", until="B2", dwell_s=0)                one step of the machine's sequence, in order: moves ("C1+" extend,
    "C1-" retract), the sensor(s) that end it; a step with no move waits dwell_s (machining, loading). c.start(C1="extended") sets
    a start position (default retracted). Checks: every step ends on a sensor that exists, at the end its move reaches; no step
    commands a state its valves cannot give (a hand valve, one valve in two positions); the cycle returns to its start; the
    ESTIMATED cycle time (valve response + stroke / mean speed + sensor + dwell: a lower bound) against cycles_per_min. A circuit
    with a control voltage or sensors and no sequence gets a "not run" notice. Outputs <name>.sequence.pdf/.svg (the
    displacement-step diagram with the coil signals) and <name>.sequence.md (the step table with times and assumptions).
m = c.manifold("M1", part="SMC SS5Y3-20-04-Q")                    a valve manifold base: ports P (common supply), R, S (common exhausts)
c.mount(valve, m, station=None)                                  mount a valve on the next free station: its P, R, S are the manifold's
    (no tubes or silencers on them; silence the manifold's R and S); it takes the catalogue's valve-on-manifold C, b. Checks: valves
    vs stations (empty stations get blanking plates in the BOM), and the drop through the manifold's supply at the air the cylinders
    on it draw at once (per sequence step when c.step(...) declares one, else all at once). The I/O list names each coil's station.
every component also takes max_bar= and min_bar= (its working pressure range, from its datasheet) and part_number=; a tube max_bar=
valves also take coil_V=, coil_W=, connector=, signal= / signal_12= (the PLC signal names of coils 14 / 12), terminal=,
  C_exhaust= / b_exhaust= (the A/B -> R/S passage when the datasheet gives it apart)
cylinders also take load_mass_kg= (the mass the rod moves), allowable_ke_J=, piston_mass_kg=, speed_min_mm_s= / speed_max_mm_s=, magnet=,
  on_power_loss= / on_air_loss= (what it must do: "hold" | "extend" | "retract" | "release"; graded against its valves)
c.screw_in(port_a, port_b)                                       two parts screwed straight together (a speed controller in a cylinder port): no tube, no fitting
components take connections={"P": "M5", "A": "push-in 6", ...} (catalogue parts fill it): the BOM counts a fitting only where a threaded port needs one
```

## Catalogue parts (part=)

`part="..."` on a cylinder, valve, tube, flow control, silencer or sensor fills its datasheet figures (bore, rod, stroke, pressure
range, port, C/b per passage, coil, cushioning energy, speed range, sensor electrics), each cited in `eitri/pneumatics/catalog.py`.
A figure you also give must agree with the part's. Known part numbers (the stroke is read from the number and checked):

- cylinders: `FESTO DSNU-20-<stroke>-P-A` (ISO 6432, Ø20/8, 1-320 mm, 1-10 bar, G1/8, magnetic, elastic cushioning 0.2 J),
  `SMC CDQ2B32-<stroke>DZ` (compact Ø32/16, strokes 5-50 by 5, 75, 100; 0.5-10 bar; Rc1/8; magnet; bumper 0.29 J; 50-500 mm/s;
  `CQ2B32-...DZ` is the same without a magnet)
- manifolds: `SMC SS5Y3-20-<stations>[00F|00N|00T]-Q` (SY3000, 2-20 stations, P/EA/EB 1/8, valve on base C 0.72 b 0.29, exhaust 0.80
  / 0.36, blanking plate SY3000-26-19A-Q), `SMC SS5Y5-20-<stations>-Q` (SY5000, 1/4, C 1.9 / 2.2); silencer `SMC AN10-01` (R1/8, C 2)
- valves: `SMC SY3120-5LZ-C6` (5/2 single solenoid, spring return, C 0.76 b 0.30 P->A/B, C 0.65 b 0.39 A/B->R/S, 1.5-7 bar,
  24 VDC 0.4 W, L plug), `SMC SY3220-5LZ-C6` (the same, double solenoid: holds its position unpowered, 1-7 bar),
  `SMC SY5120-5LZ-01` (C 1.9 / 2.4, all ports 1/8); drop the Z for the 0.35 W coil without indicator light
- more cylinders: `FESTO DSNU-<16|20|25|32|40|50|63>-<stroke>-P-A`, `SMC C[D]Q2B<12..100>-<stroke>DZ` (standard strokes per bore),
  `FESTO DSBC-<32..100>-<stroke>-PPVA-N3` (ISO 15552, 0.4/0.6-12 bar), `SMC MGPM<20|25|32>-<stroke>Z` (guided, no allowable energy printed)
- filter regulators: `SMC AW20-02BG` (10 bar, regulates 0.5-8.5 bar), `FESTO MS4-LFR-1/4-D6-CRM-AS` (14 bar, regulates 0.3-7 bar):
  the set pressure is checked against the regulation range
- `SMC TU0425` / `TU0805` tube, `SMC AS1201F-M5-04` (C 0.3) / `AS2201F-02-08` (C 1.4) speed controllers for 4 / 8 mm tube
- `SMC TU0604` tube (6 x 4 mm polyurethane, 8 bar), `SMC AS2201F-01-06` speed controller (C 0.7), `SMC AN05-M5` silencer (C 1.0)
- sensors: `SMC D-M9B` (2-wire, 10-28 V, 2.5-40 mA, for CQ2), `FESTO SMT-8M-A-PS-24V-E-0,3-M8D` (PNP, 5-30 V, M8, for DSNU with SMBR-8-20)

Any other part: give its figures by hand from its datasheet (and its part_number) and say where they come from in your reply.

C is the ISO 6358 sonic conductance in L/(s·bar) ANR and b the critical pressure ratio, from the component's datasheet
(a 1/8" solenoid valve is around C 0.5-2, b 0.3: SMC's body-ported SY3000 is C 0.6-0.76; a 1/4" FRL around C 2-4; SMC AN silencers C 1 (M5), 2 (1/8"), 3 (1/4");
SMC AS speed controllers fully open about C 0.3 (M5), 0.7 (1/8"), 1.4 (1/4"), 2.8 (3/8")). Without them a conservative estimate
is used (the smallest of those sizes: valve 0.6, silencer 1.0, flow control 0.3) and the report says so in a warning. Units: bar gauge, N, mm, mm/s, L/min ANR.

## The checks every build runs

- circuit: every cylinder port fed from a supply through a valve in some position and able to exhaust in another (a chamber that
  cannot vent is dead-headed); every valve's P supplied and work ports connected; no tee or inline component with a single connection.
- force per stroke (FAIL below the load, WARN above the duty's load ratio: 0.5 moving, 0.7 static, 1.0 guided): usable force = pressure at the cylinder x area x (1 -
  friction allowance), the pressure being the lowest regulator on the supply path (or the supply's); the load ratio is load / theoretical
  force. A single-acting cylinder retracts by its spring (spring_N), graded against load_retract_N.
- air (FAIL above the supply's capacity, WARN above 80%): free-air consumption per cycle = the swept volumes (both strokes, one for a
  spring return) at p_abs / p_atm, plus the lines between valve and cylinder refilled from atmospheric (V x p_gauge / p_atm, SMC's
  method); x cycles per minute.
- speed per stroke (FAIL below the requirement, WARN within 20%): ISO 6358 flow through the fill path (supply -> regulator -> valve ->
  tube, components in series: 1/C² = sum 1/Cᵢ², b = min bᵢ) against the pressure the load needs, and through the exhaust path to the
  silencer; the achievable speed is the lesser of the two.
- pressure drop per tube of a fill path at the required flow (WARN above 0.3 bar or 5%, FAIL when the flow cannot pass at all).
- pressure ratings (FAIL over): each supply (at its pressure) and regulator (at its set pressure) against the lowest-rated component
  it reaches before the next regulator; the failure names it. Give `max_bar=` from the part's datasheet on any component; the
  defaults are vendor figures: regulator 10 bar (SMC AR20-AR60, 1.0 MPa), FRL 10 (Festo MS4-LFR), valve 7 (SMC SY3000), flow control
  10 (SMC AS), cylinder 10 (Festo DSNU), silencer 10 (Festo U), tee 10 (SMC KQ2). A supply above a valve's 7 bar needs a regulator.
- catalogue figures (when a part= or the same fields are given): the cylinder's force against the force its datasheet prints
  (a FAIL means the bore, rod or part is wrong), minimum operating pressure of cylinders and valves (a pilot-operated valve does
  not shift below it), the piston speed range (FAIL above the maximum, WARN below the minimum), the end-of-stroke kinetic energy
  1/2 m v^2 (load_mass_kg + piston mass) against the cushioning's allowable energy (v is the required speed when a speed controller
  sits on the stroke, else what the open paths pass; not run without load_mass_kg), each coil's voltage against the control voltage,
  and each sensor's cylinder (a magnetic piston, the series it mounts on) and supply voltage.
- fail-safe, per cylinder, on a loss of electrical power and on a loss of air: the state its valves give (5/2 single solenoid
  spring return: its rest position; 5/2 double solenoid: holds its last position on a power cut; 5/3: its centre: closed traps
  the air (a WARNING for "hold": SMC says it may not hold for long), exhaust releases, pressure extends; manual/pilot valves keep
  their position; on a loss of air every chamber joined to the supply or an exhaust loses its force, a spring returns a
  single-acting cylinder) against `on_power_loss=` / `on_air_loss=`. Not declared: "not run" (a warning naming what to declare).
- end-of-stroke energy uses an impact speed of 2 x the mean speed (SMC: "collision speed = 2 x average speed" when the speed comes
  from the stroke time).
- flow data estimated: one warning naming every component or tube sized with an estimate (tubes: effective area from bore and
  equivalent length with 30 diameters per fitting, C = S/5, b = 0.5; valves/regulators/silencers/flow controls: small-component
  defaults; a flow control's free-flow direction passes no more than its throttle fully open, as in SMC's AS tables).
- your own `verify(c, p, chk)`: `chk.at_most(name, value, limit)`, `chk.at_least`, `chk.between`, `chk.equal`, `chk.true`, `chk.count`,
  `chk.note`. Helpers: `cylinder_forces(cyl, p_bar)`, `consumption_per_cycle_l(cyl, p_bar)`, `flow_l_s(C, b, p1_abs, p2_abs)`,
  `series([(C, b), ...])`, `tube_conductance(id_mm, length_m, fittings)`, `find_path(c, cyl["CAP"], "supply")`.

## Outputs of every build (out/)

`<name>.schematic.svg` (the schematic), `<name>.png` (the review image), `<name>.bom.md` / `.bom.csv` (components with part numbers,
tube by bore with a 10% service loop, fittings, sensors), `<name>.cylinders.md` / `.csv` (per stroke: pressure, load, usable force, load
ratio, required and achievable speed, air per minute, status), `<name>.io.md` / `.io.csv` (the I/O list for the PLC programmer: every
solenoid coil a DO and every position sensor a DI, with signal name, function, part, voltage, current, connector and terminal).
`<name>.drawing.pdf` / `.drawing.svg`: the circuit diagram for the customer: a device tag on every component (EN 81346-2 codes as
Festo uses them: -MM cylinder, -QM valve, -BG sensor, -AZ air preparation, -XM tee, -RZ flow control), the sensors on their
cylinders, the coil signals (14 / 12) on the valves, a parts table keyed by tag and a title block (project, ticket, revision, date,
drawn by eitri, approved by: blank).

## Direct edits and layout

The user edits components and tubes in the browser editor and drags components around. Their changes land in the model file as an
`EDITS` list (`set_component`, `set_tube`, `add_tube`, `remove_tube`, `set_vent`) applied after `build()`, and positions as a
`LAYOUT = {"V1": [x, y]}` dict (layout hints only). Read them with `list_edits`, treat them as design intent, and fold them into
`build()` when convenient (then clear `EDITS`). Keep component and tube names stable: the edits address them by name.

## Worked example 1: a single-acting clamp

```python
from eitri.pneumatics import *

PARAMS = dict(p_bar=5.0, bore_mm=25, stroke_mm=25, clamp_N=150, cycles_per_min=12)


def build(p):
    c = Circuit("Clamp", "a spring-return clamp cylinder on a 3/2 valve")
    air = c.supply("AIR", pressure_bar=7.0, flow_l_min=300)
    frl = c.frl("FRL", set_bar=p.p_bar, C=2.0, b=0.35)
    v = c.valve("V1", "3/2", actuation="solenoid", returns="spring", C=0.8, b=0.3)
    cyl = c.cylinder("C1", bore_mm=p.bore_mm, rod_mm=10, stroke_mm=p.stroke_mm, acting="single", load_N=p.clamp_N,
                     spring_N=25, speed_mm_s=100, cycles_per_min=p.cycles_per_min, duty="static",   # clamping: load ratio up to 0.7
                     on_power_loss="retract", on_air_loss="retract")      # the 3/2 vents and the spring opens the clamp
    c.tube("T1", air["OUT"], frl["IN"], id_mm=8, length_m=1.5)
    c.tube("T2", frl["OUT"], v["P"], id_mm=6, length_m=0.8)
    c.tube("T3", v["A"], cyl["CAP"], id_mm=6, length_m=1.0)
    c.silencer(v["R"])
    return c


def verify(c, p, chk):
    f = cylinder_forces(c.get("C1"), p.p_bar)
    chk.at_least("clamp force margin", f["extend_usable_N"] / p.clamp_N, 1.3)
```

## Worked example 2: a double-acting cylinder with speed control

```python
from eitri.pneumatics import *

PARAMS = dict(p_bar=6.0, bore_mm=32, stroke_mm=100, load_N=350, speed=200, cycles_per_min=20)
CONFIGS = {"slow": dict(speed=100), "fast": dict(speed=800)}


def build(p):
    c = Circuit("Pusher", "a double-acting pusher on a 5/2 valve, meter-out speed control")
    air = c.supply("AIR", pressure_bar=7.0, flow_l_min=600)
    frl = c.frl("FRL", set_bar=p.p_bar, C=2.5, b=0.35)
    v = c.valve("V1", "5/2", actuation="solenoid", returns="spring", C=1.2, b=0.3)
    fc1 = c.flow_control("FC1", meter="out", C=0.6, b=0.3)                    # on the rod side: controls the extend stroke
    fc2 = c.flow_control("FC2", meter="out", C=0.6, b=0.3)                    # on the cap side: controls the retract stroke
    cyl = c.cylinder("C1", bore_mm=p.bore_mm, rod_mm=12, stroke_mm=p.stroke_mm, load_N=p.load_N, load_retract_N=100,   # the return stroke only drags the empty pusher back
                     speed_mm_s=p.speed, cycles_per_min=p.cycles_per_min,
                     on_power_loss="extend", on_air_loss="release")       # V1 at rest feeds A -> CAP: it pushes out on a power cut
    c.tube("T1", air["OUT"], frl["IN"], id_mm=8, length_m=2.0)
    c.tube("T2", frl["OUT"], v["P"], id_mm=6, length_m=1.0)
    c.tube("T3", v["A"], fc2["A"], id_mm=6, length_m=1.0)
    c.tube("T4", fc2["B"], cyl["CAP"], id_mm=6, length_m=0.5)
    c.tube("T5", v["B"], fc1["A"], id_mm=6, length_m=1.0)
    c.tube("T6", fc1["B"], cyl["ROD"], id_mm=6, length_m=0.5)
    c.silencer(v["R"])
    c.silencer(v["S"])
    return c


def verify(c, p, chk):
    chk.at_most("air per minute", consumption_per_cycle_l(c.get("C1"), p.p_bar)["per_minute_l"], 100)
```

Reading the pusher report: at 6 bar a Ø32 bore gives 483 N theoretical, 434 N usable (10% allowance), so 350 N is a 0.72 load ratio
(a warning: a moving load wants 0.5 or less, so a Ø40 bore at 6 bar, 0.46, or say duty="guided" if a guide carries the load); the extend speed is limited by whichever of the fill path and the
meter-out exhaust path passes less flow (about 390 mm/s here: the fill path runs through FC2's free-flow direction, which passes no
more than its throttle fully open), and the "fast" configuration (800 mm/s) fails the speed check until
the valve and flow controls grow.
