"""A 3-DOF desktop robot arm: yaw base, shoulder, elbow, three MG996R servos on 5 V, an ESP32 and a PCA9685 on the base.

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


def box(x, y, z, sx, sy, sz):
    return Pos(x, y, z) * Box(sx, sy, sz)


def build(p):
    sh_y = H / 2                                            # the shoulder servo's output face, on +Y
    el_y = sh_y + p.plate_t + H                             # the elbow servo's output face, further out on +Y
    base = box(0, 0, BASE_T / 2, 140, 90, BASE_T)
    # four M3 clearance holes (3.4 mm, ISO 273 medium) to bolt the base down: free-standing, the arm at full reach with its 50 g payload
    # would tip it (eitri_suite.stability), so mech.py declares base_fixing="bolted"
    for bx_, by_ in ((-62, -37), (-62, 37), (62, -37), (62, 37)):
        base = base - Pos(bx_, by_, BASE_T / 2) * Cylinder(3.4 / 2, BASE_T * 3)
    yaw_servo = box(0, 0, BASE_T + H / 2, L, W, H)
    turret = box(10, 0, YAW_Z + TURRET_T / 2, 50, 40, TURRET_T) - Pos(-8, 0, YAW_Z + TURRET_T / 2) * Cylinder(p.hole_d / 2, TURRET_T * 3)
    shoulder_servo = box(20, 0, SHOULDER_Z, L, H, W)         # shaft along +Y
    upper_arm = box(20 + p.upper_mm / 2, sh_y + p.plate_t / 2, SHOULDER_Z, p.upper_mm, p.plate_t, p.arm_h)
    elbow_servo = box(20 + p.upper_mm, sh_y + p.plate_t + H / 2, SHOULDER_Z, L, H, W)
    forearm = box(20 + p.upper_mm + p.fore_mm / 2, el_y + p.plate_t / 2, SHOULDER_Z, p.fore_mm, p.plate_t, p.arm_h)
    pwm_board = box(-45, 0, BASE_T + 1.5, 25.4, 62.5, 3.0)   # PCA9685 breakout, lying on the base
    controller = box(50, 0, BASE_T + 6.5, 28.3, 54.4, 13.0)  # ESP32 devkit
    # where the harness lands (an anchor per connector of wiring.py and uno_wiring.py, named as the connector; z: the way the
    # wire leaves; part: what it moves with). The assembly check fails for a connector without one.
    top = BASE_T + 3.0                                       # the PCA9685's top face
    anchor("PCA_I2C", at=(-45, 28, top), z=(0, 0, 1), x=(1, 0, 0), part="pwm_board")
    anchor("PCA_PWR", at=(-45, -28, top), z=(0, 0, 1), x=(1, 0, 0), part="pwm_board")
    for i in range(3):
        anchor(f"PCA_CH{i}", at=(-45, -12 + 6 * i, top), z=(0, 0, 1), x=(1, 0, 0), part="pwm_board")
    anchor("ESP32", at=(50, 0, BASE_T + 13.0), z=(0, 0, 1), x=(0, 1, 0), part="controller")
    anchor("UNO", at=(50, 0, BASE_T + 13.0), z=(0, 0, 1), x=(0, 1, 0), part="controller")       # uno_wiring.py's board, in the same place
    anchor("GND_REF", at=(36, 0, BASE_T), z=(0, 0, 1), part="base")                                # uno_wiring.py's ground split, beside the board
    anchor("PSU", at=(-80, 0, BASE_T), z=(1, 0, 0))
    anchor("F1", at=(-60, 10, BASE_T), z=(0, 0, 1), part="base")                                # the inline fuse holder, beside the supply lead                                             # the supply lead comes in past the base's edge
    anchor("SERVO_YAW", at=(-L / 2, 0, BASE_T + 8), z=(-1, 0, 0), part="yaw_servo")              # a servo's lead leaves the end of its body
    anchor("SERVO_SH", at=(20 - L / 2, 0, SHOULDER_Z), z=(-1, 0, 0), part="shoulder_servo")
    anchor("SERVO_EL", at=(20 + p.upper_mm - L / 2, el_y - p.plate_t - H / 2, SHOULDER_Z), z=(-1, 0, 0), part="elbow_servo")
    for name, servo in (("YAW", "SERVO_YAW"), ("SHOULDER", "SERVO_SH"), ("ELBOW", "SERVO_EL")):  # uno_wiring.py's servo plugs: the same leads
        anchor(name, at={"SERVO_YAW": (-L / 2, 0, BASE_T + 8), "SERVO_SH": (20 - L / 2, 0, SHOULDER_Z), "SERVO_EL": (20 + p.upper_mm - L / 2, el_y - p.plate_t - H / 2, SHOULDER_Z)}[servo],
               z=(-1, 0, 0), part={"SERVO_YAW": "yaw_servo", "SERVO_SH": "shoulder_servo", "SERVO_EL": "elbow_servo"}[servo])
    return {"base": base, "yaw_servo": yaw_servo, "turret": turret, "shoulder_servo": shoulder_servo, "upper_arm": upper_arm,
            "elbow_servo": elbow_servo, "forearm": forearm, "pwm_board": pwm_board, "controller": controller}


MATERIALS = {"base": "pla", "turret": "pla", "upper_arm": "pla", "forearm": "pla"}

# purchased parts that have CAD geometry here: part name -> catalog id (mass and the BOM come from the catalog)
HARDWARE = {"yaw_servo": "MG996R", "shoulder_servo": "MG996R", "elbow_servo": "MG996R", "pwm_board": "PCA9685", "controller": "ESP32-DEVKIT"}
# purchased parts with no geometry: (catalog id, quantity)
PURCHASED = [("625ZZ", 2), ("M3x8-SHCS", 16), ("M3x16-SHCS", 8), ("M3-NUT", 24), ("M3-WASHER", 12), ("PSU-5V-10A", 1)]   # 10 A: three MG996R start together at 7.5 A (eitri_suite.power)

INTEGRATION = dict(
    harness="wiring.py",
    routes=[dict(name="arm servo leads", wires=["SH_GND", "SH_V", "SH_SIG", "EL_GND", "EL_V", "EL_SIG"],
                 waypoints=[[-45, 0, 11], [-8, 0, 30], [-8, 0, YAW_Z + 2.5], [20, 0, SHOULDER_Z], [120, H / 2 + 4 + H / 2, SHOULDER_Z]],
                 through=[dict(part="turret", at=[-8, 0, YAW_Z + TURRET_T / 2], label="turret cable hole")], service_loop_pct=10, clearance_mm=1.0)],
    mating=[dict(connector=c, part="pwm_board", at=[-45 + dx, 0, BASE_T + 3.0], normal=[0, 0, 1], approach_mm=20)
            for c, dx in (("PCA_CH0", -9), ("PCA_CH1", -4), ("PCA_CH2", 1), ("PCA_I2C", 9))]
           + [dict(connector="ESP32", part="controller", at=[50, 0, BASE_T + 13.0], normal=[0, 0, 1], approach_mm=20)],
)
