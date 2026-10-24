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
           speed_retract_mm_s=None, cycles_per_min=0, spring_N=None, friction_fraction=0.10)
    ports CAP and ROD (a single-acting cylinder: CAP only, spring return of spring_N)
c.flow_control(name, meter="out", C=None, b=None)                a speed controller (throttle-check); ports A (valve side), B (cylinder side)
c.node(name)                                                     a tee: any number of tubes meet at its port N
c.tube(name, from_port, to_port, id_mm=6, length_m=1, fittings=2, C=None)    a run of tube; ports are comp["PORT"] or "NAME.PORT"
c.silencer(valve["R"])                                           a silencer screwed onto an exhaust port;  c.exhaust(port) leaves it open
```

C is the ISO 6358 sonic conductance in L/(s·bar) ANR and b the critical pressure ratio, from the component's datasheet
(a 1/8" solenoid valve is around C 0.5-1.5, b 0.3; a 1/4" FRL around C 2-4). Without them a conservative estimate is used
and the report says so in a warning. Units: bar gauge, N, mm, mm/s, L/min ANR.

## The checks every build runs

- circuit: every cylinder port fed from a supply through a valve in some position and able to exhaust in another (a chamber that
  cannot vent is dead-headed); every valve's P supplied and work ports connected; no tee or inline component with a single connection.
- force per stroke (FAIL below the load, WARN above a load ratio of 0.7): usable force = pressure at the cylinder x area x (1 -
  friction allowance), the pressure being the lowest regulator on the supply path (or the supply's); the load ratio is load / theoretical
  force. A single-acting cylinder retracts by its spring (spring_N), graded against load_retract_N.
- air (FAIL above the supply's capacity, WARN above 80%): free-air consumption per cycle = the swept volumes (both strokes, one for a
  spring return) plus the dead volume of the lines between valve and cylinder, expanded to atmospheric; x cycles per minute.
- speed per stroke (FAIL below the requirement, WARN within 20%): ISO 6358 flow through the fill path (supply -> regulator -> valve ->
  tube, components in series: 1/C² = sum 1/Cᵢ², b = min bᵢ) against the pressure the load needs, and through the exhaust path to the
  silencer; the achievable speed is the lesser of the two.
- pressure drop per tube of a fill path at the required flow (WARN above 0.3 bar or 5%, FAIL when the flow cannot pass at all).
- flow data estimated: one warning naming every component or tube sized with an estimate (tubes: effective area from bore and
  equivalent length with 30 diameters per fitting, C = S/5, b = 0.5; valves/regulators/silencers: small-component defaults).
- your own `verify(c, p, chk)`: `chk.at_most(name, value, limit)`, `chk.at_least`, `chk.between`, `chk.equal`, `chk.true`, `chk.count`,
  `chk.note`. Helpers: `cylinder_forces(cyl, p_bar)`, `consumption_per_cycle_l(cyl, p_bar)`, `flow_l_s(C, b, p1_abs, p2_abs)`,
  `series([(C, b), ...])`, `tube_conductance(id_mm, length_m, fittings)`, `find_path(c, cyl["CAP"], "supply")`.

## Outputs of every build (out/)

`<name>.schematic.svg` (the schematic), `<name>.png` (the review image), `<name>.bom.md` / `.bom.csv` (components with part numbers,
tube by bore with a 10% service loop, fittings), `<name>.cylinders.md` / `.csv` (per stroke: pressure, load, usable force, load ratio,
required and achievable speed, air per minute, status).

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
                     spring_N=25, speed_mm_s=100, cycles_per_min=p.cycles_per_min)
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
                     speed_mm_s=p.speed, cycles_per_min=p.cycles_per_min)
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
(a warning: use a Ø40 bore, or 7 bar, for a dynamic application); the extend speed is limited by whichever of the fill path and the
meter-out exhaust path passes less flow (about 640 mm/s here), and the "fast" configuration (800 mm/s) fails the speed check until
the valve and flow controls grow.
