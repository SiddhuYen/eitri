# Designing around a customer's part (reference parts)

A job often starts with "here is our motor / sensor / casting, design the bracket (fixture, guard, jig) around it".
eitri loads that STEP file as a **reference part**: real geometry the design is checked against, which neither the
agent nor a direct edit can change.

## 1. Put the file in the project

```sh
eitri reference add ~/eitri-projects/motor-bracket customer_motor.step --name motor
#   accepted inputs/motor.step (sha256 98f5…) for motor_bracket.py
#   use it in the model:  motor = reference_step("inputs/motor.step", name="motor")
```

`add` copies the file into the project's `inputs/` folder and records its SHA-256 in `eitri.json`. A file you copy
into `inputs/` by hand is recorded the first time a build reads it. Reference files must sit directly in `inputs/`
(the one folder a sandboxed build is given) with a plain name (letters, digits, `_ - .`).

## 2. Use it in the model file

```python
from eitri import *

def build(p):
    motor = reference_step("inputs/motor.step", name="motor", clearance=1.0, contact=["bracket"])
    holes = motor.holes(axis="Z", max_diameter=3.0)          # the tapped holes on the front face
    pilot = max(motor.bosses(axis="Z"), key=lambda b: b.diameter)
    plate = ...                                              # designed against motor.bbox, holes, pilot
    for h in holes:
        plate -= Pos(h.center[0], h.center[1], 0) * Cylinder(iso273_clearance("M3", "medium") / 2, 20)
        motor.mount(h, part="bracket", screw="M3", series="medium", coaxial_tol=0.1)
    return {"bracket": plate}                                # the motor is added to the assembly for you
```

| | |
|---|---|
| `reference_step(path, name=, at=, clearance=, contact=, material=, density=)` | load and place it (`at`: a `Location` such as `Pos(..) * Rot(..)`, or `(x, y, z)`) |
| `.bbox`, `.size`, `.volume` | extents of the placed part (mm) |
| `.holes(...)`, `.bosses(...)`, `.features()` | full-round cylindrical holes / bosses: `.diameter .axis .center .start .end .depth .through .axis_name` |
| `.hole(near=(x, y, z))` | the hole whose axis passes closest to a point |
| `.mass_properties(density=, material=)` | volume, mass, centre of mass, inertia (needs a density: a STEP file carries none you can trust) |
| `.shape` | a copy of the geometry, for booleans (cut a pocket the shape of the part) |
| `.mount(hole, part=, screw=, series=, coaxial_tol=, angle_tol=, dia_tol=)` | declare a screw joint; checked on every build |

The full example is `examples/motor-bracket/` (`eitri build examples/motor-bracket/motor_bracket.py`).

## 3. What every build checks

- **Unchanged file.** The file on disk must have the accepted SHA-256. If it changed, the build fails with
  `reference file inputs/motor.step CHANGED since it was accepted`. Restore it, or, if the customer really sent a
  new revision: `eitri reference accept <project> inputs/motor.step`. `add`, `accept` and `remove` are for a person: like
  `eitri ticket approve` they refuse in an agent's process, ask for a one-time code typed back in the terminal, and sign the
  record (`eitri reference list` shows who accepted each file and whether its record still verifies).
- **Unmodified geometry.** Direct edits that name a reference part (push/pull, move, delete, a boolean that consumes it)
  are refused, and every build proves the part's geometry after the edits is what was loaded.
- **No interference.** No designed part may overlap it (more than 0.001 mm³, the boolean's numerical tolerance).
- **Clearance** (with `clearance=`). Every designed part not listed in `contact=` keeps at least that gap. Without
  `clearance=` the check is "not run" (a warning; a change package needs it run, or waived by a person).
- **Mounts.** For each `mount(...)`: the designed part has a hole coaxial with the reference hole (axis offset and
  angle within your stated tolerances; the smallest coaxial hole is taken as the clearance bore), and its diameter
  is the ISO 273 clearance for the screw and series (fine / medium / coarse) as published by Albany County Fasteners
  (the one ISO-labelled table read; ISO 273 itself is paywalled), within `dia_tol` (default 0.01 mm).
  The table is in `eitri/lib/fasteners.py` with its source.
- **Kept.** Once a model declared a reference, a later build that drops it, or declares fewer mounts on it, fails.
  A person removes one with `eitri reference remove <project> inputs/motor.step`.

The reference also appears in the viewer (grey, translucent, "reference, read-only" in the parts list), the review
image, the STEP/GLB exports and the BOM (as a customer part). It is never put on a 3D-print plate and is not graded
for wall thickness or sharp edges: it is the customer's geometry, not ours.

## 4. The file is untrusted input

Before OpenCASCADE sees it, the file is size-capped (`EITRI_REFERENCE_MAX_MB`, default 20 MB), its entity count is
capped (`EITRI_REFERENCE_MAX_ENTITIES`, default 500 000), and it is pre-scanned: it must be one plain ISO 10303-21 text
file (not compressed, no NUL bytes, no `DOCUMENT_FILE` references to other files, balanced statements in its DATA
section). It is parsed inside the build (the subprocess, warm worker or sandbox that runs the model), its faces are
capped (`EITRI_REFERENCE_MAX_FACES`, default 20 000), and the bytes are hashed again after parsing to make sure they
did not change underneath. A STEP file is data; nothing in it is executed.

## Limits

- Hole detection finds full-round cylindrical faces. Threads modelled as helical surfaces, or holes split into
  partial arcs below 90% coverage, are not found; vendor models usually model a tapped hole as a plain cylinder at
  the tap-drill or the nominal diameter.
- The mount check measures the designed part's hole, not the screw length, thread engagement or the head's seat.
- Accepting a new revision is command-line only; there is no button in the editor yet.
