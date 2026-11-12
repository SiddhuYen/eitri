"""A bracket designed around a customer's motor (a reference part).

inputs/example_motor.step stands in for the customer's file: a made-up stepper-like motor (42 x 42 mm body, Ø22 pilot,
Ø5 shaft, four Ø2.5 holes on a 31 mm square on the front face, axis +Z). It is not a vendor model; it is
tests/fixtures/reference/test_motor.step, made by tests/fixtures/reference/make_fixtures.py.

The motor is read-only: its SHA-256 is recorded on the first build, and the build fails if the file changes until a
person runs `eitri reference accept <project> inputs/example_motor.step`. The bracket is designed against the holes
eitri finds on the motor, and every build checks that each bracket hole is coaxial with its motor hole and is the
ISO 273 medium clearance for M3, that nothing overlaps the motor, and that the base plate keeps 1 mm away from it.
"""
from eitri import *

PARAMS = dict(plate_t=5.0, pilot_clearance=1.0, foot_len=35.0, foot_t=5.0, base_t=6.0, screw="M3", series="medium")


def build(p):
    motor = reference_step("inputs/example_motor.step", name="motor", clearance=1.0, contact=["bracket"])
    bb = motor.bbox
    face_z = bb.min.Z + 40.0                                   # the motor's front face (body is 40 mm long)
    face_holes = [h for h in motor.holes(axis="Z") if h.diameter < 3.0]
    pilot = max(motor.bosses(axis="Z"), key=lambda b: b.diameter)

    half = bb.size.X / 2 + 4.0                                 # plate overhangs the body by 4 mm a side
    bottom = bb.min.Y - 9.0                                    # the foot sits 9 mm below the motor body
    top = bb.max.Y + 4.0
    plate = Pos(0, (top + bottom) / 2, face_z + p.plate_t / 2) * Box(2 * half, top - bottom, p.plate_t)
    plate -= Pos(pilot.center[0], pilot.center[1], face_z) * Cylinder(pilot.radius + p.pilot_clearance, 4 * p.plate_t)
    for h in face_holes:
        plate -= Pos(h.center[0], h.center[1], face_z) * Cylinder(iso273_clearance(p.screw, p.series) / 2, 4 * p.plate_t)
    foot = Pos(0, bottom + p.foot_t / 2, face_z + p.foot_len / 2) * Box(2 * half, p.foot_t, p.foot_len)
    for x in (-half + 8, half - 8):
        foot -= Pos(x, bottom + p.foot_t / 2, face_z + p.foot_len - 10) * (Rot(90, 0, 0) * Cylinder(iso273_clearance("M4", "medium") / 2, 3 * p.foot_t))
    bracket = plate + foot
    base = Pos(0, bottom - p.base_t / 2, face_z - 5) * Box(2 * half + 30, p.base_t, p.foot_len + 70)

    for h in face_holes:                                       # declared mounting pairs: checked on every build
        motor.mount(h, part="bracket", screw=p.screw, series=p.series, coaxial_tol=0.1)
    return {"bracket": bracket, "base": base}


def verify(parts, p, c):
    c.at_least("plate thickness", p.plate_t, 4.0)
    c.count("one bracket solid", parts["bracket"].solids(), 1)
