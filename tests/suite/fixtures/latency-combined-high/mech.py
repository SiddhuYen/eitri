from eitri.mechanism import *

# geometry shared with model.py (joint points sit on the servo output faces there)
PARAMS = dict(
    plate_t=5.0,
    servo_w=19.7,
    servo_h=42.9,
    disc_t=5.0,
    ped_h=6.0,
    upper_mm=100.0,
    fore_mm=110.0,
    palm_len=25.0,
    block=20.0,
    jaw_t=4.0,
    arm_w=20.0,
    micro_h=22.7,
    jaw_ext=8.0,
    payload_g=10.0,
    pick_yaw=40.0,
    place_yaw=-40.0,
    grab_r=110.0,
    grab_z=12.0,
)

COUPLING = dict(
    cad="model.py",
    links={"turret": ["turret", "shoulder_servo"],
           "upper": ["upper_arm", "elbow_servo"],
           "fore": ["forearm", "grip_servo"],
           "finger": ["finger"]},
    print_fill=1.0,
)


def pts(p):
    z_yaw = p.plate_t + p.servo_h
    zs = z_yaw + p.disc_t + p.ped_h + p.servo_w / 2
    ys = p.servo_h / 2
    xt = p.upper_mm + p.fore_mm
    px = xt - 20 - p.palm_len / 2
    py = ys + p.block / 2 + p.jaw_t / 2
    pz = zs + p.arm_w / 2 + p.micro_h
    return z_yaw, zs, ys, xt, (px, py, pz)


def table_point(p, yaw_deg, r, z):
    """World point of the tip for a planar radius r at a yaw angle (the tip runs ys off the arm plane)."""
    _, _, ys, _, _ = pts(p)
    a = math.radians(yaw_deg)
    return (r * math.cos(a) - ys * math.sin(a), r * math.sin(a) + ys * math.cos(a), z)


import math


def build(p):
    z_yaw, zs, ys, xt, piv = pts(p)
    m = Mechanism("Desktop arm", "yaw / shoulder / elbow MG996R, SG90-class gripper, picks a 20 mm block")
    m.mount("table")
    m.link("base", fixed=True)
    m.link("turret", length_mm=zs - z_yaw, mass_g=72.5, direction=(0, 0, 1))      # incl. shoulder MG996R
    m.link("upper", length_mm=p.upper_mm, mass_g=69.3)                              # incl. elbow MG996R
    m.link("fore", length_mm=p.fore_mm, mass_g=38.7)                                # incl. grip servo
    m.link("finger", length_mm=xt + p.jaw_ext - piv[0], mass_g=7)
    mg = Actuator("MG996R", stall_kgcm=9.4, rated_kgcm=4.7, speed_deg_s=315, travel_deg=180)
    micro = Actuator("SG90", stall_kgcm=1.8, speed_deg_s=600, travel_deg=180)
    m.joint("yaw", "base", "turret", axis=(0, 0, 1), at=(0, 0, z_yaw), limit_deg=(-90, 90), actuator=mg)
    m.joint("shoulder", "turret", "upper", axis=(0, -1, 0), at=(0, -ys, zs), limit_deg=(30, 165), actuator=mg, home=90)
    m.joint("elbow", "upper", "fore", axis=(0, -1, 0), at=(p.upper_mm, ys, zs), limit_deg=(-110, 30), actuator=mg, home=-90)
    m.joint("grip", "fore", "finger", axis=(0, 0, 1), at=piv, limit_deg=(-5, 30), actuator=micro)
    m.tip("fore", at=(p.fore_mm, 0, 0))
    m.payload(mass_g=p.payload_g)
    m.target("pick", table_point(p, p.pick_yaw, p.grab_r, p.grab_z))
    m.target("place", table_point(p, p.place_yaw, p.grab_r, p.grab_z))
    m.speed("yaw", deg_s=90)
    m.speed("shoulder", deg_s=90)
    return m


def verify(m, p, c):
    loads = joint_loads(m, {})
    c.at_most("shoulder load at full extension (kg·cm)", loads["shoulder"], 4.7)
    c.at_most("elbow load at full extension (kg·cm)", loads["elbow"], 4.7)
    c.at_least("horizontal reach (mm)", p.upper_mm + p.fore_mm, 200)
