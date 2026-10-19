from eitri import *

PARAMS = dict(width=60.0, depth=40.0, height=30.0, thickness=4.0, hole_d=5.5, fillet=3.0)


def build(p):
    # L-bracket: base plate on XY, vertical plate along the back (+Y side), two holes in each leg
    base = Box(p.width, p.depth, p.thickness, align=(Align.CENTER, Align.MIN, Align.MIN))
    wall = Box(p.width, p.thickness, p.height, align=(Align.CENTER, Align.MAX, Align.MIN))
    wall = wall.moved(Location((0, p.depth, 0)))
    body = base + wall
    # fillet the inside corner between the legs for strength
    inner = body.edges().filter_by(Axis.X).group_by(Axis.Z)[1]
    inner = [e for e in inner if abs(e.center().Y - (p.depth - p.thickness)) < 1e-6]
    if inner:
        body = fillet(inner, p.fillet)
    # holes: 2 in base (through Z), 2 in wall (through Y)
    for x in (-p.width / 4, p.width / 4):
        body -= Pos(x, p.depth / 2 - p.thickness / 2, 0) * Cylinder(p.hole_d / 2, 3 * p.thickness)
        body -= Pos(x, p.depth, p.height * 0.65) * Rot(90, 0, 0) * Cylinder(p.hole_d / 2, 3 * p.thickness)
    return body


def verify(parts, p, c):
    c.equal("overall width", parts.size.X, p.width)
    c.equal("overall depth", parts.size.Y, p.depth)
    c.equal("overall height", parts.size.Z, p.height)
    c.count("Ø5.5 holes", holes(parts[0], diameter=p.hole_d), 4)
    c.true("all holes through", all(h.through for h in holes(parts[0])))
