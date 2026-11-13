"""The arm as a mechanism: yaw, shoulder, elbow on MG996R servos, a 50 g payload at the tip, a pick and a place target in front of it.
The shoulder swings well below horizontal to reach them, so at its extremes the tip can dip below the base plane (the kernel warns).

COUPLING takes the link masses and centres of mass from model.py's bodies and checks the CAD parts for collisions as the arm moves
(eitri_suite.coupling)."""
from eitri.mechanism import *

PARAMS = dict(upper_mm=100.0, fore_mm=100.0, payload_g=50.0)

H, W = 42.9, 19.7                       # the MG996R body (see model.py)
YAW_Z = 8.0 + H                         # the yaw shaft: top of the yaw servo
SHOULDER_Z = YAW_Z + 5.0 + W / 2

COUPLING = dict(
    cad="model.py",
    links={"base": ["base", "yaw_servo"], "turret": ["turret", "shoulder_servo"], "upper": ["upper_arm", "elbow_servo"], "fore": ["forearm"]},
    base_fixing="bolted",               # four M3 bolts through the base (model.py BASE_BOLTS): free-standing it would tip at full reach
)


def build(p):
    m = Mechanism("Robot arm", "yaw, shoulder, elbow on MG996R servos")
    servo = Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=400, travel_deg=180)
    m.link("base", fixed=True)
    m.link("turret", length_mm=SHOULDER_Z - YAW_Z, mass_g=67, direction=(0, 0, 1))
    m.link("upper", length_mm=p.upper_mm, mass_g=67)
    m.link("fore", length_mm=p.fore_mm, mass_g=12)
    m.joint("yaw", "base", "turret", axis=(0, 0, 1), at=(0, 0, YAW_Z), limit_deg=(-90, 90), actuator=servo)
    m.joint("shoulder", "turret", "upper", axis=(0, -1, 0), at=(20, H / 2, SHOULDER_Z), limit_deg=(-30, 80), actuator=servo)
    m.joint("elbow", "upper", "fore", axis=(0, -1, 0), at=(120, H / 2 + 4 + H, SHOULDER_Z), limit_deg=(-10, 120), actuator=servo)
    m.payload(mass_g=p.payload_g)
    m.target("pick", at=(210, -45, 24), tol_mm=5)
    m.target("place", at=(210, 45, 24), tol_mm=5)
    m.require_reach(180)
    m.poses = 16
    return m
