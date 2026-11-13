# Cross-domain reference (combined projects)

A combined project holds model files of several domains side by side (a CAD model, a harness, a mechanism, a pneumatic circuit, a
firmware project), each starting with its own import line. Use `use_model` to switch between them or add one. Each file builds and
is graded on its own by its domain's checks; the declarations below add the checks BETWEEN files. They are module-level dicts, plain
data, and they cannot be deleted to make a check go away (a build that declares less than an earlier one fails "suite: declared
checks are kept").

## INTEGRATION (in the CAD model): CAD <-> harness

```python
INTEGRATION = dict(
    harness="wiring.py",                                  # the harness model file (default: the only one)
    routes=[dict(name="servo leads", wires=["SH_SIG", "SH_V"],                    # harness wire ids
                 waypoints=[[x, y, z], ...],                                       # the path through the CAD model, mm
                 through=[dict(part="turret", at=[x, y, z], label="turret cable hole")],   # a pass-through hole, by a point on its axis
                 service_loop_pct=10, clearance_mm=1.0)],
    mating=[dict(connector="PCA_CH0", part="pwm_board", at=[x, y, z], normal=[0, 0, 1], approach_mm=20)],
)
```

- route fit: each pass-through hole is measured in the CAD model; it must be at least the routed bundle's diameter plus the clearance,
  and the wires as long as the route plus the service loop (wire outside diameters and bundle fill are ESTIMATES).
- mating face: a connector mated to a CAD-mounted component needs a free corridor of `approach_mm` in front of the component's face.

## COUPLING (in the mechanism model): mechanism <-> CAD

```python
COUPLING = dict(
    cad="arm.py",                                          # the CAD model file (default: the only one)
    links={"turret": ["turret", "shoulder_servo"], "upper": ["upper_arm", "elbow_servo"], "fore": ["forearm"]},   # link -> the CAD parts bolted to it
    actuators={"elbow": "elbow_servo"},                    # optional: joint -> the CAD body of its actuator (else found by name or position)
    print_fill=1.0,                                        # fraction of a printed part's solid mass (default 1.0)
)
```

- every actuator at its joint: a joint's Actuator is a body in the CAD model (a box of the catalog size is fine), named after the
  joint (`elbow_servo` drives `elbow`) or named in `actuators`, else the nearest servo or motor body. A revolute actuator's output
  face is centred on the joint's `at` point (within 2 mm) with the joint `axis` normal to that face (within 5°): the shaft is the
  axis. A prismatic actuator sits within 30 mm of its stroke axis. The body rides on the link it is listed under (one of the joint's
  two links), else on the joint's parent link, and the Assembly view moves it with that link. "every actuator is at the joint it
  drives" fails naming the body, the joint and the distance or angle; a servo body that drives no joint, or a joint whose actuator
  has no body, warns.
- link masses: each coupled link's mass and centre of mass come from the CAD bodies (volume x density from MATERIALS, or the catalog
  mass of a part named in the CAD file's HARDWARE), overriding the Link's declared mass_g and cg. The check says which source each
  link used. An actuator body counts on its link unless the Actuator declares `mass_g` (then the Actuator carries it; never both).
- torque: the mechanism kernel's torque grading re-run with those masses (checks named "... (masses from CAD)").
- self-collision: the CAD parts are posed through the joints and checked for interference.
- every body attached: each CAD body rides on the link COUPLING lists it under or its actuator binding gives it (a body under no
  link is fixed to the base); on each link the bodies must touch one another (within 0.5 mm) back to the joint that moves the link,
  or, on the base, to the table, and that holds at the zero pose, at home and at every SEQUENCE step. The harness connectors,
  splices and the board count too: each sits on the part its anchor names (`part=`), within 0.5 mm. A floating body fails, named
  with the pose and its distance from the nearest attachment point; so does one listed under a link the mechanism does not have.
- no interference at rest: no two bodies overlap at the zero pose (a board sunk into its floor, two bodies in one place), except
  bodies on the two links of one joint (a horn seated in its arm).
- the payload: `m.payload(mass_g)` is the mass the torque checks carry; nothing is drawn for it. Model the object ONLY when the
  person asks to see or model it (a bottle cap, a block): then declare it, with the bodies allowed to touch it, and it rests at the
  pick target, rides in the end-effector frame from the step that grabs it to the step that releases it, and must touch nothing but
  the fingers on the way (the plan: the firmware SEQUENCE, else the IK targets):

```python
COUPLING = dict(cad="model.py", links={...},
                payload=dict(name="bottle cap", shape="cylinder", size_mm=[30, 12], at="pick", fingers=["finger_l", "finger_r"]))
```

  `shape` "cylinder" ([diameter, height], axis vertical) or "box" ([x, y, z]); `at` a target name or a point; `fingers` default to
  the end-effector link's bodies. Declare the tool point with `m.tip(link, at=...)` between the fingers, on the object's centre when
  they close. Name SEQUENCE steps so the grab and the release read: a step named grab, pick, take or close (not above / over / lift)
  grabs on arrival; one named release, place, drop, put or open (not above / over) releases. Close the gripper in its own step,
  after the step that lowers onto the object.

- the base does not tip over (eitri_suite.stability; the mechanism and the CAD builds both run it): say how the base is fixed with
  `base_fixing="bolted"` (through holes in the base for the bolts: a bolted base with no vertical through hole warns), `"clamped"`
  or `"free"` in this COUPLING (`m.mount("wall"|"ceiling"|"edge")` also counts as fastened). Free-standing (declared, or nothing
  declared and no through hole in the base), the combined centre of mass of every CAD body (catalog or MATERIALS masses), the
  actuators and the payload, in every pose of a grid over the joint limits plus home and every SEQUENCE step, must stay inside the
  base's footprint (its underside at the lowest level) by h x tan 10 deg (h: its height). A base with through holes and nothing
  declared is "not run": declare it. Fix a failure in the design: a wider or heavier base, less reach or payload, or bolt it down
  (holes in the base, then `base_fixing="bolted"`).

## COUPLING (in the firmware model): firmware <-> harness

```python
COUPLING = dict(harness="uno_wiring.py", pins={"YAW": "UNO.D9", "SHOULDER": "UNO.D10"})     # firmware pin net -> harness connector pin
```

Each mapped harness pin must exist, be wired, and be the pin the firmware drives (a board connector's pins are named like the board's:
D9, A4). Firmware pins not mapped are a warning.

## Catalog, BOM and package

`parts_catalog(query)` looks up purchased parts (servos, boards, supplies, fasteners). Name a CAD part's purchased part in the CAD
model's `HARDWARE = {part: catalog id}` and list parts with no geometry in `PURCHASED = [(catalog id, qty)]`; `bom` rolls printed
parts, purchased hardware and the harness's wire and terminals into one list. `build_package` writes everything needed to print,
wire and assemble the design, but only when every check passes.

## Anchors (in the CAD model): where the harness lands

Inside `build(p)`, declare an anchor for every harness connector and splice, **named exactly as the connector** (case does not
matter), with `z` the direction the wire leaves and `part` the part it moves with:

```python
anchor("UNO", at=(50, 0, 21), z=(0, 0, 1), x=(1, 0, 0), part="base")         # the board: x along its long side, USB at -x
anchor("SERVO_YAW", at=(-20, 0, 16), z=(-1, 0, 0), part="yaw_servo")         # a servo's lead leaves the end of its body
anchor("PSU", at=(-80, 0, 8), z=(1, 0, 0))                                   # the supply lead, past the base's edge
```

- "every harness connector has a CAD anchor" fails naming the connectors without one; the Assembly view shows them unplaced.
- A board connector (pins named like the board's: D9, GND, 5V) is drawn as the board, each pin at its real position (a CAD body of
  the board's size at its anchor is its stand-in: the view draws the board once, in its place); any other
  connector is a housing with its pins along the anchor's `x` at the connector's pitch; a splice is a point.
- Put a board on standoffs (four short cylinders under its mounting holes) and its anchor on its top face: a board lying flat on
  a floor or a plate warns ("rests on its mounts"), one sunk into it fails ("bodies do not interfere at rest").
- Every wire is routed from pin to pin: along a declared route's waypoints, or a free spline over the parts in its way. "wire cut
  lengths cover their 3D paths" fails when a cut length is shorter than the path.

## SEQUENCE (in the firmware model): the pick-and-place cycle

```python
COUPLING = dict(harness="wiring.py", pins={"YAW": "UNO.D9"}, mechanism="mech.py", joints={"YAW": "yaw", "SHOULDER": "shoulder"})
SEQUENCE = [("home", dict(YAW=90, SHOULDER=90)), ("pick", dict(YAW=60, SHOULDER=135)), ("place", dict(YAW=120, SHOULDER=135)), ("home", dict(YAW=90, SHOULDER=90))]
```

The servo angles the sketch writes, step by step; `joints` maps each servo to a mechanism joint (joint angle = servo angle - 90;
`servo_zero={"YAW": 90}` and `servo_sign={"YAW": -1}` change that). "the sequence stays within the servos and the joints" fails
for an angle outside 0-180 or a joint pushed past its limit. Every firmware pin must have a wire on the board connector ("every
firmware pin is wired in the harness").

"power: the supply covers the actuators that move together" (eitri_suite.power): the servos whose angle changes in a SEQUENCE step
move together, and at power-up every servo moves at once; each at its catalog stall current (MG996R 2.5 A, SG90 0.5 A, or
`servo(current_A=)` when higher), plus the board's supply-pin limit when the harness wires the supply to its 5V/VIN, against the
supply the CAD file buys (PURCHASED, a catalog power supply). A sketch that moves one joint at a time (start-up included) says so
with `motion="sequential"` in this COUPLING. No SEQUENCE: every actuator at once. Fix a failure with a bigger supply, or firmware
that moves fewer at once (and declares it).

## COUPLING (in the electronics model): electronics <-> harness, firmware, mechanism, CAD

Only when the combined workspace offers an electronics file (elec.py, `from eitri.electronics import *`). Build it last: its checks
read every other file.

```python
COUPLING = dict(
    harness="wiring.py", firmware="fw.py", mechanism="mech.py", cad="model.py",     # default: the project's only file of that kind
    connectors={"UNO": "UNO", "PSU": "PSU", "SERVO_YAW": "SERVO_YAW"},  # harness connector -> circuit part (default: the same name)
    actuators={"yaw": "SERVO_YAW"},                  # mechanism joint -> its servo/motor (default: through the firmware's servo pins)
    mounts={"UNO": dict(anchor="UNO", stand_in="controller", carrier=["standoffs"], screw="M3", clearance_mm=1.0)},
)
```

Name the circuit's parts as the harness connectors: each harness pin is then the circuit pin of the same name (UNO.D9, PSU.+,
SERVO_YAW.V+). What is checked (each number with its source; a check missing an input says "not run: <why>", which is not a pass):
- pins: every pin of a mapped connector is a pin of its part; the pins one harness net joins are on one circuit net; the harness
  pins the circuit puts on one net are joined by the harness; the firmware's board and pins against the circuit (wired, output vs
  input, the internal pull-up, a servo pin reaching a servo's SIG, the servo's power where the firmware's `power=` says).
- harness power: each harness wire's current from the circuit (a servo at its catalog stall current, all at once), against its
  gauge's ampacity and the connector pins' rating; the voltage left at each load after the supply and return wires' drop, against
  its minimum supply voltage (MG996R 4.8 V); the supply against what leaves it through the harness and against the BOM's supply
  (give the circuit's supply `part_number=` the catalog id the CAD file's PURCHASED buys). A harness that declares less current
  than the circuit draws warns.
- actuators: every actuated mechanism joint has its servo or motor in the circuit (the same model); their stall current, all
  stalled at once, against what carries it (the supply, a MOSFET's ID, a relay's contacts; a PCA9685's V+ has no published rating).
- dissipation: a regulator or MOSFET carrying actuator current, at the stall current, against its junction limit.
- mounting: a board with a published outline (Uno, Nano, Adafruit PCA9685) sits at its anchor (the Assembly frame: the PCB's top
  face, x along the long side, USB at -x); every mounting hole needs a CAD hole in a carrier part that the mount's screw passes
  with it (standoffs tapped M3: a Ø2.5 bore); the outline must not overlap any body but its stand-in; and the nearest other body
  must be at least `clearance_mm` away.
