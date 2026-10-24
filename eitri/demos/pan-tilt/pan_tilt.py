"""Pan-tilt camera mount: two SG90 micro servos, a 90 g camera on a 35 mm tilt arm.

The tilt joint carries the camera at `tilt_arm_mm` from its axis (0.315 kgf·cm at level for 90 g), the pan axis is vertical and
carries no gravity load. CONFIGS try a lighter and a heavier camera: the heavy one (550 g, 1.94 kgf·cm against the SG90's 1.8 stall)
fails the tilt torque check.
"""
from eitri.mechanism import *

PARAMS = dict(camera_g=90.0, tilt_arm_mm=35.0, tilt_speed=180.0, yoke_mm=30.0)
CONFIGS = {"light": dict(camera_g=30.0), "heavy": dict(camera_g=550.0)}


def build(p):
    m = Mechanism("Pan-tilt", "two micro servos, a camera on the tilt arm")
    m.link("base", fixed=True)
    m.link("yoke", length_mm=p.yoke_mm, mass_g=12, direction=(0, 0, 1))
    m.link("cradle", length_mm=p.tilt_arm_mm, mass_g=10)
    servo = Actuator("SG90", stall_kgcm=1.8, speed_deg_s=500, travel_deg=180, mass_g=9)
    m.joint("pan", "base", "yoke", axis=(0, 0, 1), at=(0, 0, 0), limit_deg=(-90, 90), actuator=servo)
    m.joint("tilt", "yoke", "cradle", axis=(0, -1, 0), limit_deg=(-30, 60), actuator=servo)
    m.mass("camera", "cradle", p.camera_g, at=(p.tilt_arm_mm, 0, 0))
    m.tip("cradle")
    m.target("ahead", (p.tilt_arm_mm, 0, p.yoke_mm))
    m.speed("tilt", deg_s=p.tilt_speed)
    return m


def verify(m, p, c):
    c.at_most("tilt load at level (kg·cm)", joint_loads(m, {})["tilt"], 0.9)
