from eitri import *

PARAMS = dict(length=80.0, width=50.0, height=25.0, wall=2.0, lid_t=2.0, lip=1.5, corner_r=4.0, gap=0.3)


def build(p):
    outer = extrude(RectangleRounded(p.length, p.width, p.corner_r), p.height)
    cavity = extrude(RectangleRounded(p.length - 2 * p.wall, p.width - 2 * p.wall, p.corner_r - p.wall), p.height)
    cavity = cavity.moved(Location((0, 0, p.wall)))
    base = outer - cavity
    base.color = srgb("#6C8EBF")

    # lid: plate + lip that drops inside the cavity with a small clearance
    plate = extrude(RectangleRounded(p.length, p.width, p.corner_r), p.lid_t)
    lip_outer = extrude(RectangleRounded(p.length - 2 * p.wall - 2 * p.gap, p.width - 2 * p.wall - 2 * p.gap,
                                         max(p.corner_r - p.wall - p.gap, 0.5)), p.lip)
    lip_inner = extrude(RectangleRounded(p.length - 4 * p.wall - 2 * p.gap, p.width - 4 * p.wall - 2 * p.gap,
                                         max(p.corner_r - 2 * p.wall - p.gap, 0.5)), p.lip)
    lip = (lip_outer - lip_inner).moved(Location((0, 0, -p.lip)))
    lid = plate + lip
    lid = lid.moved(Location((0, 0, p.height)))     # assembled position: sitting on the base
    lid.color = srgb("#D79B5B")
    return {"base": base, "lid": lid}


def verify(parts, p, c):
    c.equal("base length", parts["base"].bounding_box().size.X, p.length)
    c.equal("total height", parts.size.Z, p.height + p.lid_t)
    c.at_least("lid/base clearance", min_distance(parts["lid"], parts["base"]) if overlap_volume(parts["lid"], parts["base"]) == 0 else -1, 0.0)
