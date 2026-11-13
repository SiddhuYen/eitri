from eitri import *

PARAMS = dict(
    plate_x0=-110.0,
    plate_x1=30.0,
    plate_w=110.0,
    plate_t=5.0,
    servo_l=40.7,
    servo_w=19.7,
    servo_h=42.9,
    tower_wall=3.0,
    tower_h=33.0,
    fit=0.4,
    platter_t=4.0,
    arm_t=4.0,
    arm_h=18.0,
    L1=100.0,
    grip_off=45.0,
    block=20.0,
    finger_t=4.0,
    finger_lo=8.0,
    finger_hi=12.0,
    finger_x0=28.0,
    finger_x1=50.0,
    standoff_h=6.0,
    standoff_d=7.0,
    pcb_t=1.6,
)

UNO_L, UNO_W = 68.6, 53.3
UNO_HOLES = [(14.0, 2.5), (15.3, 50.7), (66.1, 7.6), (66.1, 35.5)]

HARDWARE = {"yaw_servo": "MG996R", "shoulder_servo": "MG996R", "elbow_servo": "MG996R",
            "grip_servo": "MG996R", "uno": "ARDUINO-UNO"}
PURCHASED = [("PSU-5V-4A", 1)]
MATERIALS = {"base": "pla", "turret": "pla", "upper_arm": "pla", "forearm": "pla", "jaw": "pla"}


def geom(p):
    g = {}
    g["yaw_top"] = p.plate_t + p.servo_h
    g["zc"] = g["yaw_top"] + p.platter_t + p.servo_w / 2
    g["sy"] = p.servo_h / 2                                  # half the servo length along its shaft
    g["xa"] = p.L1 + p.grip_off                              # grip servo axis x
    g["yc"] = -g["sy"] + p.block / 2                         # grasp centre y (fixed finger face at -sy)
    g["ay"] = g["yc"] + p.block / 2 + p.finger_t / 2         # grip servo axis y
    g["grip_top"] = g["zc"] + p.servo_h / 2
    g["ux"] = p.plate_x0 + 8.0                               # Uno lower-left corner
    g["uy"] = -UNO_W / 2
    return g


def bx(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def ycyl(r, x, y0, y1, z):
    return Pos(x, (y0 + y1) / 2, z) * Rot(90, 0, 0) * Cylinder(r, y1 - y0)


def build(p):
    g = geom(p)
    zc, sy, xa, yc, ay = g["zc"], g["sy"], g["xa"], g["yc"], g["ay"]
    SL, SW, SH = p.servo_l, p.servo_w, p.servo_h
    hw = p.plate_w / 2

    # ---- base: plate + yaw servo tower + Uno standoffs
    plate = bx(p.plate_x0, p.plate_x1, -hw, hw, 0, p.plate_t)
    plate = round_vertical_edges(plate, 8)
    cx, cy = SL / 2 + p.fit, SW / 2 + p.fit
    tower = bx(-cx - p.tower_wall, cx + p.tower_wall, -cy - p.tower_wall, cy + p.tower_wall,
               p.plate_t - 0.5, p.plate_t + p.tower_h)
    tower = tower - bx(-cx, cx, -cy, cy, p.plate_t, p.plate_t + p.tower_h + 1)
    tower = tower - bx(-cx - p.tower_wall - 1, -cx + 0.5, -5, 5, p.plate_t, p.plate_t + 8)   # lead slot
    base = plate + tower
    ux, uy = g["ux"], g["uy"]
    for hx, hy in UNO_HOLES:
        base = base + Pos(ux + hx, uy + hy, p.plate_t - 0.5) * Cylinder(
            p.standoff_d / 2, p.standoff_h + 0.5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    base = base - [Pos(ux + hx, uy + hy, p.plate_t + p.standoff_h) * hole_cutter(tap_drill("M3"), 6)
                   for hx, hy in UNO_HOLES]
    base.color = srgb("#5B6770")

    z_pcb = p.plate_t + p.standoff_h
    uno = bx(ux, ux + UNO_L, uy, uy + UNO_W, z_pcb, z_pcb + p.pcb_t)
    uno.color = srgb("#1F6FB2")

    # ---- servos (simple bodies of the catalog size)
    yaw_servo = bx(-SL / 2, SL / 2, -SW / 2, SW / 2, p.plate_t, g["yaw_top"])
    shoulder_servo = bx(-SL / 2, SL / 2, -sy, sy, zc - SW / 2, zc + SW / 2)
    elbow_servo = bx(p.L1 - SL / 2, p.L1 + SL / 2, -sy, sy, zc - SW / 2, zc + SW / 2)
    grip_servo = bx(xa - SL / 2, xa + SL / 2, ay - SW / 2, ay + SW / 2, zc - SH / 2, g["grip_top"])
    for s in (yaw_servo, shoulder_servo, elbow_servo, grip_servo):
        s.color = srgb("#222222")

    # ---- turret: platter on the yaw horn carrying the shoulder servo
    turret = bx(-SL / 2 - 2, SL / 2 + 2, -sy, sy, g["yaw_top"], g["yaw_top"] + p.platter_t)
    turret = turret + Pos(0, 0, g["yaw_top"]) * Cylinder(10, p.platter_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    turret.color = srgb("#E07B39")

    # ---- upper arm: a bar on the shoulder horn (y = +sy face) carrying the elbow servo
    r = p.arm_h / 2
    y0, y1 = sy, sy + p.arm_t
    upper_arm = bx(0, p.L1 + SL / 2, y0, y1, zc - r, zc + r) + ycyl(r, 0, y0, y1, zc)
    upper_arm.color = srgb("#E07B39")

    # ---- forearm: side bar on the elbow horn (y = -sy face), grip servo cradle, fixed finger
    f0, f1 = -sy - p.arm_t, -sy
    flo, fhi = p.finger_lo, p.finger_hi        # finger extent below / above the forearm axis
    forearm = (bx(p.L1, xa + SL / 2, f0, f1, zc - r, zc + r) + ycyl(r, p.L1, f0, f1, zc)
               + bx(xa - SL / 2, xa + SL / 2, f0, ay - SW / 2, zc - r, zc + r)
               + bx(xa + SL / 2 - 1, xa + p.finger_x1, f0, f1, zc - flo, zc + fhi))
    forearm.color = srgb("#E07B39")

    # ---- jaw: lever on the grip servo horn + moving finger (inner face at yc + block/2)
    j0, j1 = ay - p.finger_t / 2, ay + p.finger_t / 2
    gt = g["grip_top"]
    jaw = (bx(xa - 8, xa + p.finger_x1, j0, j1, gt, gt + 6)
           + bx(xa - 8, xa + 12, j0, ay + 8, gt, gt + 6)            # horn plate (widened to +Y only)
           + bx(xa + p.finger_x0, xa + p.finger_x1, j0, j1, zc - flo, gt + 0.5))
    jaw.color = srgb("#F2A65A")

    # ---- anchors for the harness (named as the harness connectors / splices)
    anchor("UNO", at=(ux + UNO_L / 2, 0, z_pcb + p.pcb_t), z=(0, 0, 1), x=(1, 0, 0), part="uno")
    anchor("PSU", at=(p.plate_x0 + 4, hw - 10, p.plate_t), z=(-1, 0, 0), x=(0, 1, 0), part="base")
    anchor("BUS_5V", at=(-55, hw - 10, p.plate_t), z=(0, 0, 1), part="base")
    anchor("BUS_GND", at=(-55, hw - 20, p.plate_t), z=(0, 0, 1), part="base")
    anchor("SERVO_YAW", at=(-SL / 2, 0, p.plate_t + 6), z=(-1, 0, 0), x=(0, 1, 0), part="yaw_servo")
    anchor("SERVO_SHOULDER", at=(-SL / 2, 0, zc), z=(-1, 0, 0), x=(0, 1, 0), part="shoulder_servo")
    anchor("SERVO_ELBOW", at=(p.L1 - SL / 2, 0, zc), z=(-1, 0, 0), x=(0, 1, 0), part="elbow_servo")
    anchor("SERVO_GRIP", at=(xa - SL / 2, ay + 4, zc - 10), z=(-1, 0, 0), x=(0, 1, 0), part="grip_servo")

    return {"base": base, "uno": uno, "yaw_servo": yaw_servo, "turret": turret,
            "shoulder_servo": shoulder_servo, "upper_arm": upper_arm, "elbow_servo": elbow_servo,
            "forearm": forearm, "grip_servo": grip_servo, "jaw": jaw}


def verify(parts, p, c):
    g = geom(p)
    for n in ("base", "turret", "upper_arm", "forearm", "jaw"):
        c.equal(f"{n} is one solid", len(parts[n].solids()), 1)
    # jaw and fixed finger inner faces are exactly one block apart at the closed pose
    jy = parts["jaw"].bounding_box().min.Y
    fy = parts["forearm"].bounding_box().max.Y
    c.equal("finger gap = block (mm)", jy - (-g["sy"]), p.block, tol=0.05)
    c.at_least("forearm reaches grip servo", fy, g["ay"] - p.servo_w / 2 - 0.01)
    c.equal("shoulder axis height (mm)", g["zc"], p.plate_t + p.servo_h + p.platter_t + p.servo_w / 2)
    c.at_least("thinnest printed wall (mm)", min_wall(parts["forearm"]).thickness, 2.0)
    c.at_most("arm fits a desk (overall length mm)", parts.size.X, 320)
