# Mechanism reference

A mechanism model file has the same contract as a CAD or harness model: `PARAMS`, `build(p)`, optional `verify(m, p, c)`, optional
`CONFIGS`. `build(p)` returns a `Mechanism`. Start the file with `from eitri.mechanism import *`. Everything is plain Python data,
deterministic, no file reads.

Every build runs the checks below and returns a report (pass / warn / fail per check) and a review image (the kinematic diagram:
side and plan views at the home pose, the worst-load pose ghosted, joints failing a check in red, targets as crosses). Fix the
design until every check passes and the diagram matches the request. Never delete or loosen a check to make it pass.

## The API

```
m = Mechanism("name", description="")

m.link(name, length_mm=0, mass_g=0, cg=None, fixed=False, direction=(1, 0, 0))
    a rigid body from the joint that moves it to its tip, `length_mm` along its own x axis; `cg` in its own frame (default
    (length/2, 0, 0)); `direction` = where its x axis points IN WORLD COORDINATES at the zero pose; exactly one link is
    fixed=True (the ground); a link with length 0 is a bracket / turret with no reach of its own

m.joint(name, parent, child, type="revolute", axis=(0, 0, 1), at=None, limit_deg=(lo, hi) | limit_mm=(lo, hi),
        actuator=None, home=0)
    moves `child` relative to `parent`; `at` = the joint's world point at the zero pose (default: the parent's tip);
    `axis` = its world direction at the zero pose; a positive revolute angle is a right-hand rotation about `axis`;
    type "prismatic" slides along `axis` by the joint value in mm; type "fixed" is a rigid attachment (no limits)

Actuator(name, stall_kgcm=None, rated_kgcm=None, stall_N=None, rated_N=None, speed_deg_s=None, speed_mm_s=None,
         gear_ratio=1, efficiency=1, travel_deg=None, travel_mm=None, mass_g=0)
    the motor's own figures (at its shaft); gear_ratio > 1 multiplies torque (x efficiency) and divides speed at the joint;
    rated_* = recommended continuous: give the datasheet's when it has one; else the catalog's (STS3215: 6.5 kg·cm), else
    1/3 of stall, an assumption the checks name (MG996R, SG90 publish stall only); travel = the actuator's own
    range (180 for a hobby servo; None = continuous); mass_g sits on the parent link at the joint

m.payload(mass_g, link=None, at=None)       a point mass the tip carries (default: at the tip)
m.mass(name, link, mass_g, at=None)         an extra point mass on a link (a gripper, a camera), at a point in the link's frame
m.tip(link, at=None)                        the tool point (default: the end of the longest chain's last link)
m.target(name, (x, y, z), tol_mm=2)         a world point the tip must reach
m.speed(joint, deg_s=... | mm_s=...)        a required joint speed
m.require_reach(mm)                         a required tip radius from the first joint
m.poses = 24                                random poses sampled by the checks besides the structured ones
m.mount(on="table")                         where the base is fixed: "table" (default) warns when any part reaches below the
                                            base plane (z < 0); "edge", "ceiling" or "wall" when it is meant to reach below its base
```

Units: mm, grams, degrees, kgf·cm (1 kgf·cm = 0.0980665 N·m), N for prismatic joints; gravity is -Z at 9.80665 m/s².

## The checks every build runs

- structure: the joints form a tree from the ground link (anything else is a failed check, not a crash).
- mass rollup: every link with what it carries (point masses, motors mounted on it), the payload, the total.
- torque / force per joint (FAIL above stall, WARN above rated, PASS below rated): the static holding load at the worst sampled
  pose (home, every joint alone at each limit, the {min, mid, max} grid, random poses from a fixed seed), gravity only. A vertical
  axis has no gravity load and passes with a note; inertial loads are not graded.
- speed per joint (FAIL below the requirement, WARN within 20% of it): the actuator's no-load speed through the gearing.
- joint limits: the declared range must fit the actuator's travel.
- targets: each target reached within its tolerance by the tip, searched inside the joint limits; when it is not, the check says
  whether ignoring the limits would reach it (widen them) or not (lengthen the links or move the target).
- reach envelope: the tip's radius from the first joint over the sampled poses, against `require_reach`.
- your own `verify(m, p, c)`: `c.at_most(name, value, limit)`, `c.equal`, `c.between`, `c.at_least`, `c.true`, `c.count`, `c.note`.
  Helpers: `joint_loads(m, q)` ({joint: kgf·cm or N at pose q}), `sample_poses(m)`, `solve_ik(m, point, sample_poses(m))`
  ((distance mm, pose)), `mass_rollup(m)`, `m.tip_position(q)`, `m.points(q)`.

## Outputs of every build (out/)

`<name>.mechanism.svg` (the diagram), `<name>.png` (the review image), `<name>.joints.md` / `.csv` (per joint: limits, actuator,
required vs stall and rated, margins, speed, moving mass, status; the targets; the reach), `<name>.mass.md` / `.csv` (the mass rollup).

## Direct edits and layout

The user edits joints, links, actuators, the payload and targets in the browser editor. Their changes land in the model file as an
`EDITS` list (`set_joint`, `set_actuator`, `set_link`, `set_payload`, `set_target`, `add_target`, `remove_target`, `set_speed`) applied
after `build()`. Read them with `list_edits`, treat them as design intent, and fold them into `build()` when convenient (then clear
`EDITS`). Keep joint, link and target names stable: the edits address them by name.

## Worked example 1: a 3-DOF arm

```python
from eitri.mechanism import *

PARAMS = dict(upper_mm=120.0, fore_mm=100.0, turret_mm=40.0, payload_g=50.0)


def build(p):
    m = Mechanism("3-DOF arm", "yaw, shoulder, elbow on hobby servos")
    m.mount("edge")                                                                  # clamped at the bench edge: it reaches below its base
    m.link("base", fixed=True)
    m.link("turret", length_mm=p.turret_mm, mass_g=70, direction=(0, 0, 1))      # stands up from the base
    m.link("upper", length_mm=p.upper_mm, mass_g=60)                               # home pose: extended along +X
    m.link("fore", length_mm=p.fore_mm, mass_g=30)
    big = Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=400, travel_deg=180, mass_g=55)
    small = Actuator("MG90S", stall_kgcm=2.2, speed_deg_s=600, travel_deg=180, mass_g=14)
    m.joint("yaw", "base", "turret", axis=(0, 0, 1), at=(0, 0, 0), limit_deg=(-90, 90), actuator=big)
    m.joint("shoulder", "turret", "upper", axis=(0, -1, 0), limit_deg=(-50, 100), actuator=big)    # +angle lifts the arm
    m.joint("elbow", "upper", "fore", axis=(0, -1, 0), limit_deg=(-10, 120), actuator=small)
    m.payload(mass_g=p.payload_g)
    m.target("pick", (150, 60, 20))
    m.target("place", (60, 150, 60))
    m.speed("shoulder", deg_s=120)
    return m


def verify(m, p, c):
    loads = joint_loads(m, {})                                                   # the home pose: extended, horizontal
    c.at_most("shoulder load at full extension (kg·cm)", loads["shoulder"], 4.0)
    c.at_least("reach (mm)", float(abs(m.tip_position({})[0])), p.upper_mm + p.fore_mm - 1)
```

## Worked example 2: a pan-tilt camera mount

```python
from eitri.mechanism import *

PARAMS = dict(camera_g=90.0, tilt_arm_mm=35.0, tilt_speed=180.0)
CONFIGS = {"light": dict(camera_g=30.0), "heavy": dict(camera_g=550.0)}


def build(p):
    m = Mechanism("Pan-tilt", "two micro servos, a camera on the tilt arm")
    m.link("base", fixed=True)
    m.link("yoke", length_mm=30, mass_g=12, direction=(0, 0, 1))
    m.link("cradle", length_mm=p.tilt_arm_mm, mass_g=10)                           # the camera sits at its end
    servo = Actuator("SG90", stall_kgcm=1.8, speed_deg_s=500, travel_deg=180, mass_g=9)
    m.joint("pan", "base", "yoke", axis=(0, 0, 1), at=(0, 0, 0), limit_deg=(-90, 90), actuator=servo)
    m.joint("tilt", "yoke", "cradle", axis=(0, -1, 0), limit_deg=(-30, 60), actuator=servo)
    m.mass("camera", "cradle", p.camera_g, at=(p.tilt_arm_mm, 0, 0))
    m.tip("cradle")
    m.speed("tilt", deg_s=p.tilt_speed)
    return m


def verify(m, p, c):
    c.at_most("tilt load at level (kg·cm)", joint_loads(m, {})["tilt"], 0.9)        # half the SG90's stall, with margin
```

Reading the pan-tilt report: the tilt joint carries the camera at `tilt_arm_mm` from its axis, so its load is
`camera_g x tilt_arm_mm / 1000` kgf·cm at level (90 g x 35 mm = 0.315 kgf·cm), plus the cradle's own mass; the pan axis is vertical
and carries no gravity load. The "heavy" configuration (550 g: 1.94 kgf·cm, above the SG90's 1.8 stall) fails the tilt check: the
fix is a shorter arm or a stronger servo, not a lighter requirement.
