"""A 3-axis desktop robot arm on an Arduino Uno: yaw base, shoulder, elbow, three MG996R servos on a 5 V 10 A supply (elec.py), the Uno and the
power bus blocks on the base. The reference combined project for the Assembly view: every harness connector of wiring.py has an
anchor here, named as the connector, so the board, the servo plugs, the supply lead and the bus blocks land on the model.

Zero pose: the arm lies out along +X (fully extended, the worst case for torque). The servos are boxes of their catalog size;
their shafts are taken at the middle of the output face (a real MG996R's is offset about 10 mm: ESTIMATE, adjust when printing).
Joint origins are the shaft points. Positive shoulder and elbow angles raise the arm.
"""
from eitri import *

PARAMS = dict(
    upper_mm=100.0,        # shoulder shaft to elbow shaft
    fore_mm=100.0,         # elbow shaft to the tip
    plate_t=4.0,           # thickness of the printed arm plates
    arm_h=24.0,            # height of the printed arm plates
    payload_g=50.0,        # what the tip must hold
    supply_v=5.0,          # servo supply
    hole_d=8.0,            # the cable hole through the turret
)

L, W, H = 40.7, 19.7, 42.9      # MG996R body (eitri.catalog "MG996R" dims_mm)
BASE_T, TURRET_T = 8.0, 5.0
YAW_Z = BASE_T + H              # top of the yaw servo = the yaw shaft
SHOULDER_Z = YAW_Z + TURRET_T + W / 2
UNO = (-56.0, 0.0)              # where the Uno sits on the base (its centre), USB towards -X
STANDOFF_H = 5.0               # the Uno stands on four nylon standoffs, clear of the plate
UNO_TOP = BASE_T + STANDOFF_H + 1.6
UNO_HOLES = [(-20.32, -24.13), (-19.05, 24.13), (31.75, 8.89), (31.75, -19.05)]      # Uno R3 mounting holes from its centre (USB at -X):
# its board file's (13.97, 2.54), (15.24, 50.8), (66.04, 35.56), (66.04, 7.62) mm from the corner (eitri_suite.elec_sources)
BASE_BOLTS = [(-87.0, -42.0), (-87.0, 42.0), (47.0, -42.0), (47.0, 42.0)]   # the base's bolt-down holes, 8 mm in from its corners
BOLT_D = 3.4                    # M3 clearance, ISO 273 medium
TAP_D = 2.5                     # the standoffs are tapped M3: the M3 x 0.5 tap drill (ISO 2306)


def box(x, y, z, sx, sy, sz):
    return Pos(x, y, z) * Box(sx, sy, sz)


def build(p):
    sh_y = H / 2                                            # the shoulder servo's output face, on +Y
    el_y = sh_y + p.plate_t + H                             # the elbow servo's output face, further out on +Y
    base = box(-20, 0, BASE_T / 2, 150, 100, BASE_T)          # the plate ends before the arm's sweep: the elbow swings down past it
    # four M3 clearance holes (3.4 mm, ISO 273 medium) to bolt the base down: free-standing, the arm at full reach with its 50 g payload
    # would tip it (eitri_suite.stability), so the mechanism declares base_fixing="bolted"
    for bx_, by_ in BASE_BOLTS:
        base = base - Pos(bx_, by_, BASE_T / 2) * Cylinder(BOLT_D / 2, BASE_T * 3)
    yaw_servo = box(0, 0, BASE_T + H / 2, L, W, H)
    turret = box(10, 0, YAW_Z + TURRET_T / 2, 50, 40, TURRET_T) - Pos(-8, 0, YAW_Z + TURRET_T / 2) * Cylinder(p.hole_d / 2, TURRET_T * 3)
    shoulder_servo = box(20, 0, SHOULDER_Z, L, H, W)         # shaft along +Y
    upper_arm = box(20 + p.upper_mm / 2, sh_y + p.plate_t / 2, SHOULDER_Z, p.upper_mm, p.plate_t, p.arm_h)
    elbow_servo = box(20 + p.upper_mm, sh_y + p.plate_t + H / 2, SHOULDER_Z, L, H, W)
    forearm = box(20 + p.upper_mm + p.fore_mm / 2, el_y + p.plate_t / 2, SHOULDER_Z, p.fore_mm, p.plate_t, p.arm_h)
    standoffs = None
    for hx, hy in UNO_HOLES:
        s = Pos(UNO[0] + hx, UNO[1] + hy, BASE_T + STANDOFF_H / 2) * (Cylinder(2.5, STANDOFF_H) - Cylinder(TAP_D / 2, STANDOFF_H))
        standoffs = s if standoffs is None else standoffs + s
    controller = box(UNO[0], UNO[1], UNO_TOP - 0.8, 68.6, 53.4, 1.6)      # the Uno's PCB, on its standoffs
    # the harness lands here: an anchor per connector and splice of wiring.py, named as it is there
    anchor("UNO", at=(UNO[0], UNO[1], UNO_TOP), z=(0, 0, 1), x=(1, 0, 0), part="controller")
    anchor("PSU", at=(-95, -40, BASE_T), z=(1, 0, 0))                                        # the supply lead comes in past the base's edge
    anchor("BUS_5V", at=(-25, -40, BASE_T), z=(0, 0, 1), part="base")                         # the two bus blocks, screwed to the base
    anchor("BUS_GND", at=(-25, -30, BASE_T), z=(0, 0, 1), part="base")
    for i, f in enumerate(("F_YAW", "F_SH", "F_EL")):                                          # the fuse block beside the bus (MINI fuses)
        anchor(f, at=(-33 + 8 * i, -48, BASE_T), z=(0, 0, 1), part="base")
    anchor("SERVO_YAW", at=(-L / 2, 0, BASE_T + 8), z=(-1, 0, 0), part="yaw_servo")           # a servo's lead leaves the end of its body
    anchor("SERVO_SH", at=(20 - L / 2, 0, SHOULDER_Z), z=(-1, 0, 0), part="shoulder_servo")
    anchor("SERVO_EL", at=(20 + p.upper_mm - L / 2, el_y - p.plate_t - H / 2, SHOULDER_Z), z=(-1, 0, 0), part="elbow_servo")
    return {"base": base, "yaw_servo": yaw_servo, "turret": turret, "shoulder_servo": shoulder_servo, "upper_arm": upper_arm,
            "elbow_servo": elbow_servo, "forearm": forearm, "standoffs": standoffs, "controller": controller}


def verify(parts, p, c):
    c.count("turret cable hole", holes(parts["turret"], diameter=p.hole_d, through=True), 1)
    c.at_least("arm plate thickness", min_wall(parts["upper_arm"]).thickness, 3.0)


MATERIALS = {"base": "pla", "turret": "pla", "upper_arm": "pla", "forearm": "pla", "controller": "acrylic", "standoffs": "nylon"}
HARDWARE = {"yaw_servo": "MG996R", "shoulder_servo": "MG996R", "elbow_servo": "MG996R"}
PURCHASED = [("625ZZ", 2), ("M3x8-SHCS", 16), ("M3x16-SHCS", 8), ("M3-NUT", 24), ("M3-WASHER", 12), ("PSU-5V-10A", 1)]

INTEGRATION = dict(
    harness="wiring.py",
    routes=[dict(name="arm servo leads", wires=["SH_V", "SH_GND", "SH_SIG", "EL_V", "EL_GND", "EL_SIG"],
                 waypoints=[[-25, -35, 12], [-8, 0, 30], [-8, 0, YAW_Z + 2.5], [20, 0, SHOULDER_Z], [120, H / 2 + 4 + H / 2, SHOULDER_Z]],
                 through=[dict(part="turret", at=[-8, 0, YAW_Z + TURRET_T / 2], label="turret cable hole")], service_loop_pct=10, clearance_mm=1.0)],
    mating=[dict(connector="UNO", part="controller", at=[UNO[0], UNO[1], UNO_TOP], normal=[0, 0, 1], approach_mm=20)],
)
