"""A small desk robot arm that picks up a bottle cap and moves it: yaw, shoulder, elbow and wrist on servos, a two-finger gripper
(one fixed finger, one sliding jaw on an SG90), an Arduino Uno in a clear enclosure on a clear base plate.

Zero pose: the arm lies out along +X (fully extended), the gripper pointing along +X with its jaw closed. Every servo shaft is taken
at the middle of its output face (ESTIMATE: a real horn is offset a few mm). The Uno sits on four 5 mm standoffs inside the
enclosure, never on its floor: the board's underside clears the floor by the standoff height.
"""
from eitri import *

PARAMS = dict(
    upper_mm=90.0,         # shoulder shaft to elbow shaft
    fore_mm=80.0,          # elbow shaft to wrist shaft
    plate_t=4.0,           # printed arm plates
    arm_h=24.0,
    cap_d=30.0,            # the bottle cap the gripper closes on
    standoff_h=5.0,        # the Uno's standoffs (M3 nylon)
)

L, W, H = 40.7, 19.7, 42.9          # MG996R body (catalog dims_mm)
SL, SW, SH = 22.8, 12.2, 22.7       # SG90 body: long side, thickness, shaft direction
BASE_T, TURRET_T = 6.0, 5.0
YAW_Z = BASE_T + H
SHOULDER_Z = YAW_Z + TURRET_T + W / 2
ENC = (-70.0, 0.0)                  # the enclosure's centre on the plate
ENC_IN, ENC_WALL, ENC_FLOOR, ENC_H = (76.0, 60.0), 2.0, 2.0, 26.0
UNO_HOLES = [(-27.94 + 1.27 * 0, -24.13), (-26.67, 24.13), (24.13, 8.89), (24.13, -19.05)]     # Uno R3 holes, from its centre (mm)
CLEAR = (0.85, 0.92, 1.0, 0.35)     # clear acrylic
CLEAR_BASE = (0.80, 0.88, 0.95, 0.45)


def box(x, y, z, sx, sy, sz):
    return Pos(x, y, z) * Box(sx, sy, sz)


def build(p):
    sh_y = H / 2
    el_y = sh_y + p.plate_t + H
    wr_y = el_y + p.plate_t + SH                                    # the wrist servo's output face
    x_el, x_wr = 20 + p.upper_mm, 20 + p.upper_mm + p.fore_mm
    base = box(-30, 0, BASE_T / 2, 180, 140, BASE_T)
    base.color = CLEAR_BASE
    floor_top = BASE_T + ENC_FLOOR
    outer = box(ENC[0], ENC[1], BASE_T + ENC_H / 2, ENC_IN[0] + 2 * ENC_WALL, ENC_IN[1] + 2 * ENC_WALL, ENC_H)
    inner = box(ENC[0], ENC[1], floor_top + ENC_H / 2, ENC_IN[0], ENC_IN[1], ENC_H)
    enclosure = outer - inner
    enclosure.color = CLEAR
    standoffs = None
    for hx, hy in UNO_HOLES:
        s = Pos(ENC[0] + hx, ENC[1] + hy, floor_top + p.standoff_h / 2) * Cylinder(2.5, p.standoff_h)
        standoffs = s if standoffs is None else standoffs + s
    board_z = floor_top + p.standoff_h                              # the Uno's underside: on the standoffs, clear of the floor
    controller = box(ENC[0], ENC[1], board_z + 0.8, 68.58, 53.34, 1.6)
    yaw_servo = box(0, 0, BASE_T + H / 2, L, W, H)
    turret = box(10, 0, YAW_Z + TURRET_T / 2, 50, 40, TURRET_T)
    shoulder_servo = box(20, 0, SHOULDER_Z, L, H, W)
    upper_arm = box(20 + p.upper_mm / 2, sh_y + p.plate_t / 2, SHOULDER_Z, p.upper_mm, p.plate_t, p.arm_h)
    elbow_servo = box(x_el, sh_y + p.plate_t + H / 2, SHOULDER_Z, L, H, W)
    forearm = box(x_el + p.fore_mm / 2, el_y + p.plate_t / 2, SHOULDER_Z, p.fore_mm, p.plate_t, p.arm_h)
    wrist_servo = box(x_wr, el_y + p.plate_t + SH / 2, SHOULDER_Z, SL, SH, SW)
    gripper_base = box(x_wr + 9, wr_y + 2, SHOULDER_Z, 30, 4, 44)          # x 184..214 on the wrist's face
    gripper_servo = box(x_wr + 7.4, wr_y + 4 + SW / 2, SHOULDER_Z, SL, SW, SH)
    r = p.cap_d / 2
    finger_fixed = box(x_wr + 24 + 13, wr_y + 9, SHOULDER_Z + r + 3, 26, 10, 6)   # x 214..240, inner face on the cap
    finger_jaw = box(x_wr + 24 + 13, wr_y + 9, SHOULDER_Z - r - 3, 26, 10, 6)
    # where the harness lands: an anchor per connector and splice of wiring.py
    anchor("UNO", at=(ENC[0], ENC[1], board_z + 1.6), z=(0, 0, 1), x=(1, 0, 0), part="controller")
    anchor("PSU", at=(-120, -50, BASE_T), z=(1, 0, 0))
    anchor("BUS_5V", at=(-20, -55, BASE_T), z=(0, 0, 1), part="base")
    anchor("BUS_GND", at=(-20, -45, BASE_T), z=(0, 0, 1), part="base")
    for i, f in enumerate(("F_YAW", "F_SH", "F_EL", "F_WR", "F_GRIP")):                        # the fuse block beside the bus (MINI fuses)
        anchor(f, at=(-36 + 8 * i, -63, BASE_T), z=(0, 0, 1), part="base")
    anchor("SERVO_YAW", at=(-L / 2, 0, BASE_T + 8), z=(-1, 0, 0), part="yaw_servo")
    anchor("SERVO_SH", at=(20 - L / 2, 0, SHOULDER_Z), z=(-1, 0, 0), part="shoulder_servo")
    anchor("SERVO_EL", at=(x_el - L / 2, el_y - H / 2, SHOULDER_Z), z=(-1, 0, 0), part="elbow_servo")
    anchor("SERVO_WR", at=(x_wr - SL / 2, el_y + p.plate_t + SH / 2, SHOULDER_Z), z=(-1, 0, 0), part="wrist_servo")
    anchor("SERVO_GRIP", at=(x_wr + 7.4 - SL / 2, wr_y + 4 + SW / 2, SHOULDER_Z), z=(-1, 0, 0), part="gripper_servo")
    return {"base": base, "enclosure": enclosure, "standoffs": standoffs, "controller": controller, "yaw_servo": yaw_servo, "turret": turret,
            "shoulder_servo": shoulder_servo, "upper_arm": upper_arm, "elbow_servo": elbow_servo, "forearm": forearm, "wrist_servo": wrist_servo,
            "gripper_base": gripper_base, "gripper_servo": gripper_servo, "finger_fixed": finger_fixed, "finger_jaw": finger_jaw}


MATERIALS = {"base": "acrylic", "enclosure": "acrylic", "standoffs": "nylon", "controller": "acrylic", "turret": "pla", "upper_arm": "pla",
             "forearm": "pla", "gripper_base": "pla", "finger_fixed": "pla", "finger_jaw": "pla", "wrist_servo": "abs", "gripper_servo": "abs"}
HARDWARE = {"yaw_servo": "MG996R", "shoulder_servo": "MG996R", "elbow_servo": "MG996R"}
# the supply: three MG996R (2.5 A stall) and two SG90 (2.0 A stall, the core 0.8.0 catalogue's conservative figure) start together at
# power-up and on the way to "above pick": 11.5 A (eitri_suite.power). 14 A was 82 %; 18 A leaves 36 % headroom.
PURCHASED = [("M3x8-SHCS", 16), ("M3-NUT", 16), ("PSU-5V-18A", 1)]

INTEGRATION = dict(
    harness="wiring.py",
    mating=[dict(connector="UNO", part="controller", at=[ENC[0], ENC[1], BASE_T + ENC_FLOOR + 5.0 + 1.6], normal=[0, 0, 1], approach_mm=12)],
)
