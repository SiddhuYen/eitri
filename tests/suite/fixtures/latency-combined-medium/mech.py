from eitri.mechanism import *
import math

# geometry mirrors model.py (yaw axis at the origin, arm extended along +X at the zero pose)
PARAMS = dict(
    yaw_top=47.9, zc=61.75, sy=21.45,  # yaw horn height, shoulder/elbow axis height, half servo length
    L1=100.0, L2=89.0,                 # shoulder->elbow, elbow->grasp centre (x)
    grasp_dy=10.0,                     # grasp centre offset in +Y from the elbow horn face
    grip_x=145.0, grip_y=0.55, grip_top=83.2,
    finger_past=6.0, finger_lo=8.0,    # finger end past the grasp centre, finger depth below the axis (model.py)
    payload_g=20.0,                    # 20 mm block (solid PLA ~10 g; 2x margin)
    pick_x=150.0, pick_y=-60.0, place_x=60.0, place_y=130.0,
    grasp_z=14.0,                      # grasp 4 mm above the block centre (block on the table, centre z = 10)
)

COUPLING = dict(
    cad="model.py",
    links={"base": ["base", "yaw_servo"], "turret": ["turret", "shoulder_servo"],
           "upper": ["upper_arm", "elbow_servo"], "fore": ["forearm", "grip_servo"], "jaw": ["jaw"]},
    actuators={"yaw": "yaw_servo", "shoulder": "shoulder_servo", "elbow": "elbow_servo", "grip": "grip_servo"},
    print_fill=0.5,
)


def build(p):
    m = Mechanism("Desk arm", "4x MG996R: yaw, shoulder, elbow, parallel-finger gripper; picks a 20 mm block")
    m.mount("table")
    # MG996R at 5 V: stall interpolated 9.4 (4.8 V) .. 11 (6 V) -> 9.6; rated = catalog 0.5 x stall; 0.18 s/60deg
    def mg996r():
        return Actuator("MG996R", stall_kgcm=9.6, rated_kgcm=4.8, speed_deg_s=325, travel_deg=180, mass_g=55)

    # link masses = the CAD bodies (printed parts at 50 % fill + the servo each one carries)
    m.link("base", fixed=True)
    m.link("turret", length_mm=p.zc - p.yaw_top, mass_g=60, direction=(0, 0, 1))
    m.link("upper", length_mm=p.L1, mass_g=61)
    m.link("fore", length_mm=p.L2, mass_g=65)
    m.link("jaw", length_mm=50, mass_g=3)

    m.joint("yaw", "base", "turret", axis=(0, 0, 1), at=(0, 0, p.yaw_top), limit_deg=(-90, 90), actuator=mg996r())
    m.joint("shoulder", "turret", "upper", axis=(0, -1, 0), at=(0, p.sy, p.zc), limit_deg=(-10, 90), actuator=mg996r())
    m.joint("elbow", "upper", "fore", axis=(0, -1, 0), at=(p.L1, -p.sy, p.zc), limit_deg=(-90, 60), actuator=mg996r())
    m.joint("grip", "fore", "jaw", axis=(0, 0, 1), at=(p.grip_x, p.grip_y, p.grip_top), limit_deg=(-5, 35), actuator=mg996r())

    m.tip("fore", at=(p.L2, p.grasp_dy, 0))
    m.payload(mass_g=p.payload_g)
    m.target("pick", (p.pick_x, p.pick_y, p.grasp_z))
    m.target("place", (p.place_x, p.place_y, p.grasp_z))
    m.speed("yaw", deg_s=90)
    m.speed("shoulder", deg_s=60)
    m.speed("elbow", deg_s=60)
    return m


def finger_low_z(m, p, q):
    """lowest finger corner (z) at pose q: the gripper pitches with shoulder + elbow (no wrist)."""
    tip = m.tip_position(q)
    pitch = math.radians(q.get("shoulder", 0) + q.get("elbow", 0))
    return float(tip[2]) + p.finger_past * math.sin(pitch) - p.finger_lo * math.cos(pitch)


def verify(m, p, c):
    loads = joint_loads(m, {})
    c.at_most("shoulder load fully extended (kgf·cm, <= rated/1.5)", loads["shoulder"], 4.8 / 1.5)
    tip = m.tip_position({})
    c.equal("grasp centre x at home", float(tip[0]), p.L1 + p.L2, tol=0.5)
    c.equal("grasp centre y at home", float(tip[1]), -p.sy + p.grasp_dy, tol=0.5)
    poses = sample_poses(m)
    for name, pt in (("pick", (p.pick_x, p.pick_y, p.grasp_z)), ("place", (p.place_x, p.place_y, p.grasp_z))):
        d, q = solve_ik(m, pt, poses)
        c.note(f"{name} pose", {k: round(v, 1) for k, v in q.items()})
        c.at_least(f"fingers clear the table at {name} (mm)", finger_low_z(m, p, q), 1.0)
