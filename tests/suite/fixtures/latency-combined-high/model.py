from eitri import *

PARAMS = dict(
    plate_t=5.0,
    base_x0=-160.0,
    base_x1=50.0,
    base_w=140.0,
    base_r=8.0,
    servo_l=40.7,
    servo_w=19.7,
    servo_h=42.9,
    micro_l=22.5,
    micro_w=12.0,
    micro_h=22.7,
    tower_wall=2.4,
    tower_clear=0.4,
    disc_r=21.0,
    disc_t=5.0,
    ped_h=6.0,
    upper_len=100.0,
    fore_len=110.0,
    arm_t=5.0,
    arm_w=20.0,
    block=20.0,
    jaw_t=4.0,
    jaw_ext=8.0,
    palm_len=25.0,
    uno_x=-95.0,
    uno_l=68.6,
    uno_w=53.4,
    uno_t=1.6,
    standoff_h=6.0,
)

HARDWARE = {"yaw_servo": "MG996R", "shoulder_servo": "MG996R", "elbow_servo": "MG996R", "uno": "ARDUINO-UNO"}
PURCHASED = [("PSU-5V-4A", 1), ("M3x8-SHCS", 4)]
MATERIALS = {"base": "pla", "turret": "pla", "upper_arm": "pla", "forearm": "pla", "finger": "pla", "grip_servo": "pla"}
INTEGRATION = dict(harness="wiring.py")

UNO_HOLES = [(13.97, 2.54), (15.24, 50.8), (66.04, 7.62), (66.04, 35.56)]


def geom(p):
    g = dict()
    g["z_yaw"] = p.plate_t + p.servo_h
    g["zs"] = g["z_yaw"] + p.disc_t + p.ped_h + p.servo_w / 2
    g["ys"] = p.servo_h / 2
    g["xt"] = p.upper_len + p.fore_len
    g["yt"] = g["ys"]
    g["px"] = g["xt"] - 20 - p.palm_len / 2
    g["py"] = g["yt"] + p.block / 2 + p.jaw_t / 2
    g["pz"] = g["zs"] + p.arm_w / 2 + p.micro_h
    return g


def box(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def ycyl(r, x, y0, y1, z):
    return Pos(x, (y0 + y1) / 2, z) * Rot(90, 0, 0) * Cylinder(r, y1 - y0)


def build(p):
    g = geom(p)
    zs, ys, xt, yt = g["zs"], g["ys"], g["xt"], g["yt"]
    out = {}

    # ---- base: plate + yaw-servo tower + Uno standoffs
    plate = Pos((p.base_x0 + p.base_x1) / 2, 0, 0) * extrude(
        RectangleRounded(p.base_x1 - p.base_x0, p.base_w, p.base_r), p.plate_t)
    ti_x = p.servo_l + 2 * p.tower_clear
    ti_y = p.servo_w + 2 * p.tower_clear
    th = p.servo_h - 4
    tower = box(-ti_x / 2 - p.tower_wall, ti_x / 2 + p.tower_wall, -ti_y / 2 - p.tower_wall,
                ti_y / 2 + p.tower_wall, p.plate_t - 0.01, p.plate_t + th)
    tower = tower - box(-ti_x / 2, ti_x / 2, -ti_y / 2, ti_y / 2, p.plate_t, p.plate_t + th + 1)
    tower = tower - box(-ti_x / 2 - p.tower_wall - 1, -ti_x / 2 + 0.5, -5, 5, p.plate_t, p.plate_t + 14)  # lead slot
    base = plate + tower
    ux0, uy0 = p.uno_x - p.uno_l / 2, -p.uno_w / 2
    for hx, hy in UNO_HOLES:
        so = Pos(ux0 + hx, uy0 + hy, p.plate_t - 0.01) * Cylinder(3.0, p.standoff_h + 0.01, align=(Align.CENTER, Align.CENTER, Align.MIN))
        base = base + so
    for hx, hy in UNO_HOLES:
        base = base - Pos(ux0 + hx, uy0 + hy, p.plate_t + 1.0) * Cylinder(tap_drill("M3") / 2, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))
    base.color = srgb("#3A6EA5")
    out["base"] = base

    # ---- purchased bodies on the base
    yaw_servo = box(-p.servo_l / 2, p.servo_l / 2, -p.servo_w / 2, p.servo_w / 2, p.plate_t, g["z_yaw"])
    yaw_servo.color = srgb("#222222")
    out["yaw_servo"] = yaw_servo

    uz0 = p.plate_t + p.standoff_h
    uno = box(ux0, ux0 + p.uno_l, uy0, uy0 + p.uno_w, uz0, uz0 + p.uno_t)
    uno.color = srgb("#0B7A75")
    out["uno"] = uno

    tb = box(-54, -39, -15, 15, p.plate_t, p.plate_t + 12)      # behind the yaw tower, clear of the arm's swing
    tb.color = srgb("#2E8B57")
    out["terminal_block"] = tb
    jack = box(-157, -143, 45.5, 54.5, p.plate_t, p.plate_t + 11)
    jack.color = srgb("#111111")
    out["dc_jack"] = jack

    # ---- turret (on the yaw horn) carrying the shoulder servo
    turret = Pos(0, 0, g["z_yaw"]) * Cylinder(p.disc_r, p.disc_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    turret = turret + box(-15, 15, -20, 20, g["z_yaw"] + p.disc_t - 0.01, g["z_yaw"] + p.disc_t + p.ped_h)
    turret.color = srgb("#F2A541")
    out["turret"] = turret

    shoulder_servo = box(-p.servo_l / 2, p.servo_l / 2, -ys, ys, zs - p.servo_w / 2, zs + p.servo_w / 2)
    shoulder_servo.color = srgb("#222222")
    out["shoulder_servo"] = shoulder_servo

    # ---- upper arm: a slotted plate on the -Y face of the shoulder servo
    uy = (-ys - p.arm_t, -ys)
    upper = box(0, p.upper_len, uy[0], uy[1], zs - p.arm_w / 2, zs + p.arm_w / 2)
    upper = upper + ycyl(p.arm_w / 2, 0, uy[0], uy[1], zs) + ycyl(p.arm_w / 2, p.upper_len, uy[0], uy[1], zs)
    upper.color = srgb("#F2A541")
    out["upper_arm"] = upper

    elbow_servo = box(p.upper_len - p.servo_l / 2, p.upper_len + p.servo_l / 2, -ys, ys, zs - p.servo_w / 2, zs + p.servo_w / 2)
    elbow_servo.color = srgb("#222222")
    out["elbow_servo"] = elbow_servo

    # ---- forearm: plate on the +Y face of the elbow servo, palm and fixed jaw
    fy = (ys, ys + p.arm_t)
    palm_x0 = xt - 20 - p.palm_len
    fore = box(p.upper_len, palm_x0 + 0.01, fy[0], fy[1], zs - p.arm_w / 2, zs + p.arm_w / 2)
    fore = fore + ycyl(p.arm_w / 2, p.upper_len, fy[0], fy[1], zs)
    fore = fore + box(palm_x0, xt - 20, yt - 16, yt + 16, zs - p.arm_w / 2, zs + p.arm_w / 2)
    fore = fore + box(xt - 20 - 0.01, xt + p.jaw_ext, yt - p.block / 2 - p.jaw_t, yt - p.block / 2, zs - 8, zs + 8)
    fore.color = srgb("#F2A541")
    out["forearm"] = fore

    grip_servo = box(g["px"] - p.micro_l / 2, g["px"] + p.micro_l / 2, g["py"] - p.micro_w / 2, g["py"] + p.micro_w / 2,
                     zs + p.arm_w / 2, g["pz"])
    grip_servo.color = srgb("#1F4E9A")
    out["grip_servo"] = grip_servo

    # ---- finger: swings about the grip servo's shaft (vertical at the zero pose)
    fy0, fy1 = yt + p.block / 2, yt + p.block / 2 + p.jaw_t
    finger = box(g["px"] - 5, xt + p.jaw_ext, fy0, fy1, g["pz"], g["pz"] + 4)
    finger = finger + Pos(g["px"], g["py"], g["pz"]) * Cylinder(6, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
    finger = finger + box(xt - 19, xt + p.jaw_ext, fy0, fy1, zs - 8, g["pz"] + 0.01)
    finger.color = srgb("#E4572E")
    out["finger"] = finger

    # ---- anchors for the harness
    anchor("UNO", at=(p.uno_x, 0, uz0 + p.uno_t), z=(0, 0, 1), x=(1, 0, 0), part="uno")
    anchor("PSU", at=(-157, 50, p.plate_t + 5.5), z=(-1, 0, 0), x=(0, 1, 0), part="dc_jack")
    anchor("BUS_5V", at=(-46.5, -7, p.plate_t + 12), z=(0, 0, 1), x=(0, 1, 0), part="terminal_block")
    anchor("BUS_GND", at=(-46.5, 7, p.plate_t + 12), z=(0, 0, 1), x=(0, 1, 0), part="terminal_block")
    anchor("SERVO_YAW", at=(-p.servo_l / 2, 0, p.plate_t + 8), z=(-1, 0, 0), x=(0, 1, 0), part="yaw_servo")
    anchor("SERVO_SHOULDER", at=(-p.servo_l / 2, 0, zs), z=(-1, 0, 0), x=(0, 1, 0), part="shoulder_servo")
    anchor("SERVO_ELBOW", at=(p.upper_len - p.servo_l / 2, 0, zs), z=(-1, 0, 0), x=(0, 1, 0), part="elbow_servo")
    anchor("SERVO_GRIP", at=(g["px"] - p.micro_l / 2, g["py"], zs + p.arm_w / 2 + p.micro_h / 2), z=(-1, 0, 0), x=(0, 1, 0), part="grip_servo")
    return out


def verify(parts, p, c):
    g = geom(p)
    c.equal("shoulder axis height", g["zs"], 68.75, tol=0.01)
    # jaw gap equals the block
    fore, finger = parts["forearm"], parts["finger"]
    jaw_zone = box(g["xt"] - 15, g["xt"] + p.jaw_ext - 1, -100, 100, g["zs"] - 7, g["zs"] + 7)
    gap = (finger & jaw_zone).bounding_box().min.Y - (fore & jaw_zone).bounding_box().max.Y
    c.equal("jaw gap = block (mm)", gap, p.block, tol=0.05)
    c.at_least("base plate footprint X (mm)", p.base_x1 - p.base_x0, 200)
    c.true("Uno on standoffs, not on the plate", parts["uno"].bounding_box().min.Z > p.plate_t + 1)
    c.at_least("tower wall (mm)", p.tower_wall, 2.0)
