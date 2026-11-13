"""The desk arm as a mechanism: yaw, shoulder, elbow, wrist, and the gripper's sliding jaw; a bottle cap picked up and set down.

Home is the arm folded up with the gripper pointing down (shoulder 45, elbow -45, wrist -90): not the zero pose. COUPLING takes the
link masses from model.py's bodies, checks them for collisions as the arm moves, and carries the cap (payload) through the cycle."""
from eitri.mechanism import *

PARAMS = dict(upper_mm=90.0, fore_mm=80.0, payload_g=5.0)

H, W = 42.9, 19.7
SH = 22.7
YAW_Z = 6.0 + H
SHOULDER_Z = YAW_Z + 5.0 + W / 2
SH_Y = H / 2
EL_Y = SH_Y + 4 + H
WR_Y = EL_Y + 4 + SH

COUPLING = dict(
    cad="model.py",
    links={"base": ["base", "enclosure", "standoffs", "controller", "yaw_servo"], "turret": ["turret", "shoulder_servo"],
           "upper": ["upper_arm", "elbow_servo"], "fore": ["forearm", "wrist_servo"],
           "grip": ["gripper_base", "gripper_servo", "finger_fixed"], "jaw": ["finger_jaw"]},
    payload=dict(name="bottle cap", shape="cylinder", size_mm=[30, 12], at="pick", fingers=["finger_fixed", "finger_jaw"]),
)


def build(p):
    m = Mechanism("Desk arm", "yaw, shoulder, elbow, wrist on servos; a two-finger gripper")
    big = Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=400, travel_deg=180)
    small = Actuator("SG90", stall_kgcm=1.8, speed_deg_s=600, travel_deg=180)
    m.link("base", fixed=True)
    m.link("turret", length_mm=SHOULDER_Z - YAW_Z, mass_g=92, direction=(0, 0, 1))   # the MG996R at 80 g (TowerPro's higher figure)
    m.link("upper", length_mm=p.upper_mm, mass_g=91)
    m.link("fore", length_mm=p.fore_mm, mass_g=16)
    m.link("grip", length_mm=50, mass_g=15)
    m.link("jaw", length_mm=26, mass_g=2)
    x_el, x_wr = 20 + p.upper_mm, 20 + p.upper_mm + p.fore_mm
    m.joint("yaw", "base", "turret", axis=(0, 0, 1), at=(0, 0, YAW_Z), limit_deg=(-90, 90), actuator=big)
    m.joint("shoulder", "turret", "upper", axis=(0, -1, 0), at=(20, SH_Y, SHOULDER_Z), limit_deg=(-30, 90), home=45, actuator=big)
    m.joint("elbow", "upper", "fore", axis=(0, -1, 0), at=(x_el, EL_Y, SHOULDER_Z), limit_deg=(-120, 30), home=-45, actuator=big)
    m.joint("wrist", "fore", "grip", axis=(0, -1, 0), at=(x_wr, WR_Y, SHOULDER_Z), limit_deg=(-120, 30), home=-90, actuator=small)
    m.joint("jaw", "grip", "jaw", type="prismatic", axis=(0, 0, -1), at=(x_wr + 24, WR_Y + 9, SHOULDER_Z - 18), limit_mm=(0, 20), actuator=Actuator("SG90 jaw", stall_N=8, speed_mm_s=60, travel_mm=20))
    m.tip("grip", at=(38, 9, 0))                         # between the fingers, on the cap's centre when closed
    m.payload(mass_g=p.payload_g)
    m.target("pick", at=(189.8, -5.8, 6), tol_mm=3)
    m.target("place", at=(126.4, 141.6, 6), tol_mm=3)
    m.require_reach(150)
    m.poses = 16
    return m
