from eitri import *

PARAMS = dict(
    length=74.0, width=45.0, height=28.0, wall=2.0, floor=2.0, lid_t=2.0,
    boss_od=7.0,
    nano_l=43.18, nano_w=17.78, pcb_t=1.6, standoff_h=4.0, nano_x_min=-34.0, nano_y=-3.0,
    nano_hole_dx=40.64, nano_hole_dy=15.24,
    # Adafruit 326 (STEMMA QT) PCB 29.21 x 31.75, M2.5 holes 24.13 x 26.67 apart; glass about 26.7 x 19.3 (ASSUMPTION: centred)
    oled_l=29.21, oled_w=31.75, glass_l=26.7, glass_w=19.3, glass_t=2.0, oled_x=-6.0, win_l=24.0, win_w=13.0,
    oled_hx=12.065, oled_hy=13.335,
    # Adafruit 2652 (STEMMA QT) PCB 25.4 x 18.0, M2.5 holes 20.32 x 12.7 apart; long side along y
    bme_l=25.4, bme_w=18.0, bme_x=23.5, bme_post_h=12.0, bme_hx=10.16, bme_hy=6.35,
    vent_w=1.6, vent_pitch=3.6,
)

HARDWARE = {}
PURCHASED = [("M3x8-SHCS", 4)]

INTEGRATION = dict(harness="wiring.py")


def build(p):
    L, W, H = p.length, p.width, p.height
    zt = H - p.lid_t                       # top of the base walls
    ix, iy = L / 2 - p.wall, W / 2 - p.wall   # inner half sizes

    base = shell_box(L, W, zt, p.wall, floor=p.floor, corner_radius=3.0)
    # corner bosses for the lid screws, tapped M3
    bx, by = ix - p.boss_od / 2 + 0.5, iy - p.boss_od / 2 + 0.5   # into the walls: a boss tangent to a wall leaves a non-manifold edge
    corners = [(sx * bx, sy * by) for sx in (-1, 1) for sy in (-1, 1)]
    for (x, y) in corners:
        base = base + Pos(x, y, p.floor - 0.1) * Cylinder(p.boss_od / 2, zt - p.floor + 0.1, align=(Align.CENTER, Align.CENTER, Align.MIN))
    for (x, y) in corners:
        base = base - Pos(x, y, zt + 0.1) * hole_cutter(tap_drill("M3"), depth=8.1)

    # Nano standoffs (tapped for its screws)
    ncx = p.nano_x_min + p.nano_l / 2
    ncy = p.nano_y
    holes_xy = [(ncx + sx * p.nano_hole_dx / 2, ncy + sy * p.nano_hole_dy / 2) for sx in (-1, 1) for sy in (-1, 1)]
    for (x, y) in holes_xy:
        base = base + Pos(x, y, p.floor - 0.1) * standoff(p.standoff_h + 0.1, "M1.6", od=4.6, kind="tap")
    # BME280 standoffs (tapped M2.5) at the board's holes; the board's long side runs along y
    bme_holes = [(p.bme_x + sy * p.bme_hy, sx * p.bme_hx) for sx in (-1, 1) for sy in (-1, 1)]
    for (x, y) in bme_holes:
        base = base + Pos(x, y, p.floor - 0.1) * standoff(p.bme_post_h + 0.1, "M2.5", od=5.5, kind="tap")

    # USB opening in the -x wall, at the Nano's mini-USB
    pcb_top = p.floor + p.standoff_h + p.pcb_t
    usb_z0 = p.floor + p.standoff_h + 0.4          # starts above the standoffs so it never nicks them
    base = base - Pos(-L / 2 + p.wall / 2, ncy, usb_z0) * Box(p.wall + 4, 12.0, 8.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    # vents in the +x wall, by the sensor
    for i in range(4):
        z = p.floor + 5 + i * p.vent_pitch
        base = base - Pos(L / 2 - p.wall / 2, 0, z) * Box(p.wall + 1, 20.0, p.vent_w)
    base.color = srgb("#E8E8E8")

    # lid
    lid = Pos(0, 0, zt) * rounded_box(L, W, p.lid_t, 3.0)
    for (x, y) in corners:
        lid = lid - Pos(x, y, H + 0.1) * hole_cutter(screw_clearance("M3"), depth=p.lid_t + 1)
    lid = lid - Pos(p.oled_x, 0, zt + p.lid_t / 2) * Box(p.win_l, p.win_w, p.lid_t + 2)
    # OLED bosses under the lid, tapped M2.5, down to the PCB (the glass sits between the PCB and the lid)
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = p.oled_x + sx * p.oled_hx, sy * p.oled_hy
            lid = lid + Pos(x, y, zt - p.glass_t) * Cylinder(2.8, p.glass_t + 0.1, align=(Align.CENTER, Align.CENTER, Align.MIN))
            lid = lid - Pos(x, y, zt - 1.0) * hole_cutter(tap_drill("M2.5"), depth=p.glass_t)
    for i in range(4):
        x = p.bme_x - 1.5 * p.vent_pitch + i * p.vent_pitch
        lid = lid - Pos(x, 0, zt + p.lid_t / 2) * Box(p.vent_w, 14.0, p.lid_t + 2)
    lid.color = srgb("#4A6FA5")

    # stand-ins for the purchased boards
    controller = Pos(ncx, ncy, p.floor + p.standoff_h) * Box(p.nano_l, p.nano_w, p.pcb_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    controller.color = srgb("#1F5FA8")
    oled_z = zt - p.glass_t - p.pcb_t
    oled = (Pos(p.oled_x, 0, oled_z) * Box(p.oled_l, p.oled_w, p.pcb_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
            + Pos(p.oled_x, 0, oled_z + p.pcb_t) * Box(p.glass_l, p.glass_w, p.glass_t, align=(Align.CENTER, Align.CENTER, Align.MIN)))
    oled.color = srgb("#202020")
    bme_z = p.floor + p.bme_post_h
    bme = Pos(p.bme_x, 0, bme_z) * Box(p.bme_w, p.bme_l, p.pcb_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    bme.color = srgb("#7B3FA0")

    # anchors for the harness
    anchor("NANO", at=(ncx, ncy, pcb_top), z=(0, 0, 1), x=(1, 0, 0), part="controller")
    # board anchors at each PCB's centre (eitri's board frame: x along the header row): the OLED's z faces down, its PCB above
    anchor("OLED", at=(p.oled_x, 0, oled_z), z=(0, 0, -1), x=(1, 0, 0), part="oled")
    anchor("BME280", at=(p.bme_x, 0, bme_z + p.pcb_t), z=(0, 0, 1), x=(0, 1, 0), part="bme280")
    for i, name in enumerate(["BUS_3V3", "BUS_GND", "BUS_SDA", "BUS_SCL"]):
        anchor(name, at=(2.0, 9.0 + i * 2.5, p.floor), z=(0, 0, 1), part="base")

    return {"base": base, "lid": lid, "controller": controller, "oled": oled, "bme280": bme}


def verify(parts, p, c):
    c.note("thinnest wall point", tuple(round(v, 1) for v in min_wall(parts["base"]).point))
    c.at_least("thinnest wall (base)", min_wall(parts["base"]).thickness, 1.6)
    c.equal("outer length", parts.size.X, p.length, tol=0.1)
    c.equal("outer width", parts.size.Y, p.width, tol=0.1)
    c.equal("outer height", parts.size.Z, p.height, tol=0.1)
    c.at_least("display window covers the 0.96in active area (21.7 x 10.9 mm): length", p.win_l, 21.7)
    c.at_least("display window covers the active area: width", p.win_w, 10.9)
    c.count("lid screw holes", holes(parts["lid"], diameter=screw_clearance("M3"), through=True), 4)
    c.true("sensor sits apart from the Nano (>= 8 mm gap)", min_distance(parts["bme280"], parts["controller"]) >= 8.0)
