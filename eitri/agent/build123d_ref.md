# build123d quick reference (verified against the installed version)

Model files use `from eitri import *`, which gives every build123d name below
(also as `bd.`), the feature library and the measurement helpers. Units are mm,
Z is up. Prefer the **algebra API** shown here: geometry are values, `+ - &`
are booleans, `Pos/Rot/Plane * shape` place things.

## Solids

```python
b = Box(40, 20, 10)                       # centred on the origin by default
b = Box(40, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))   # bottom on Z=0
c = Cylinder(5, 30)                       # radius 5, height 30, centred (Z from -15 to 15)
c = Cylinder(5, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))   # base on Z=0
k = Cone(10, 4, 20)                       # bottom radius, top radius, height
s = Sphere(8)
t = Torus(20, 3)                          # major radius, minor radius
assert abs(b.volume - 8000) < 1e-6
```

Alignment is `(x, y, z)` with `Align.MIN / CENTER / MAX`: MIN puts the shape's
minimum at the origin. Cylinders/cones are along Z; rotate them to point elsewhere.

## Placing and moving

```python
part = Pos(10, 0, 5) * Box(10, 10, 10)            # translate
part = Rot(90, 0, 0) * Cylinder(3, 20)            # rotate about X: cylinder now along Y
part = Pos(0, 0, 20) * Rot(0, 90, 0) * Cylinder(3, 20)   # rotate first, then translate
part = Location((5, 5, 0), (0, 0, 45)) * Box(10, 10, 2)  # one Location: translation + XYZ angles
part = Plane.XZ * Cylinder(3, 20)                 # place in a plane (axis now along the plane's Z = -Y)
moved = part.moved(Location((1, 2, 3)))           # same as Pos(1,2,3) * part
rotated = Box(10, 10, 2).rotate(Axis.Z, 30)       # rotate about an axis in place
```

Planes: `Plane.XY` (normal +Z), `Plane.XZ` (normal -Y), `Plane.YZ` (normal +X),
plus `Plane.front/back/left/right/top/bottom`, `Plane(origin=(0,0,10), z_dir=(0,0,1))`,
`Plane(face)` (a plane on a planar face, normal outward) and `plane.offset(d)`.

## Booleans

```python
plate = Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
holes = [Pos(x, y, 0) * Cylinder(2.75, 20) for x in (-20, 20) for y in (-12, 12)]
plate = plate - holes                            # subtract a list in one go
boss = Pos(0, 0, 6) * Cylinder(8, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
plate = plate + boss                             # fuse
core = plate & Box(30, 30, 30)                   # intersect
assert len(plate.solids()) == 1
```

Cutters must extend past the faces they cut (make them longer than the wall, never
exactly flush). When a boolean must succeed, keep tools simple and avoid coincident
faces where possible (overlap slightly instead).

## Sketches -> solids

```python
profile = RectangleRounded(60, 40, 5)           # 2D sketch on Plane.XY, centred
body = extrude(profile, 8)                      # +Z by 8
tube = extrude(Circle(10) - Circle(8), 50)      # 2D booleans work too
ring = revolve(Pos(20, 0, 0) * Circle(3), Axis.Z)            # revolve a section around Z
ring2 = revolve(Plane.XZ * Pos(20, 0, 0) * Circle(3), Axis.Z)  # section drawn in XZ: a torus
cone = loft([Circle(10), Pos(0, 0, 20) * Circle(4)])          # loft between sections
vase = loft([Circle(15), Pos(0, 0, 30) * Circle(22), Pos(0, 0, 60) * Circle(10)])
hexagon = extrude(RegularPolygon(8, 6), 5)                     # radius 8 (circumscribed), 6 sides
slot = extrude(SlotOverall(20, 6), 5)                          # 20 long overall, 6 wide
tri = extrude(Polygon((0, 0), (30, 0), (0, 20), align=None), 4)  # arbitrary polygon (points in order)
tapered = extrude(Rectangle(20, 20), 10, taper=5)              # 5 deg draft (positive = inward)
drawn = extrude(Plane.XZ * Rectangle(20, 10), 5)               # sketch on another plane
assert abs(tube.volume - 3.141592653589793 * (100 - 64) * 50) < 1
```

2D objects: `Rectangle(w, h)`, `RectangleRounded(w, h, r)`, `Circle(r)`,
`Ellipse(rx, ry)`, `RegularPolygon(radius, n)`, `Polygon(*points)`,
`SlotOverall(length, width)`, `SlotCenterToCenter(centre_dist, width)`,
`Trapezoid(w, h, left_angle)`, `Triangle(a=..., b=..., c=...)`, `Text("abc", font_size=8)`.
All are centred at the origin unless `align=` says otherwise. `extrude(..., both=True)`
extrudes symmetrically; `extrude(sketch, amount, dir=(0,0,-1))` goes the other way.

## Free-form outlines

```python
pts = [(0, 0), (40, 0), (40, 10), (25, 10), (15, 25), (0, 25)]
outline = Polyline(*pts, close=True)            # a closed wire of straight edges
face = make_face(outline)
part = extrude(face, 5)
curve = Spline((0, 0), (10, 8), (25, 4), (40, 10))   # smooth curve through points
arc = ThreePointArc((0, 0), (10, 5), (20, 0))
line = Line((0, 0), (20, 0))
wire = line + ThreePointArc((20, 0), (25, 5), (20, 10)) + Line((20, 10), (0, 10)) + Line((0, 10), (0, 0))
pad = extrude(make_face(wire), 3)
assert abs(part.volume - face.area * 5) < 1e-6
```

Build an outline from `Line`, `Polyline`, `Spline`, `ThreePointArc`, `RadiusArc`,
`TangentArc`, `Bezier`, joined with `+`, closed, then `make_face`. For sweeps:
`sweep(section_sketch, path=path_wire)`; a helix path is `Helix(pitch, height, radius)`.

## Selecting edges and faces

```python
box = Box(40, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
top = box.faces().sort_by(Axis.Z)[-1]                  # highest face
bottom = box.faces().sort_by(Axis.Z)[0]
vertical_edges = box.edges().filter_by(Axis.Z)         # edges parallel to Z
top_edges = box.faces().sort_by(Axis.Z)[-1].edges()    # the top face's outline
rounded = fillet(vertical_edges, 3)                    # fillet returns a NEW shape
chamfered = chamfer(box.edges().group_by(Axis.Z)[-1], 1.0)   # group_by: edges at the same Z, highest group
circles = rounded.edges().filter_by(GeomType.CIRCLE)
front_face = box.faces().sort_by(Axis.Y)[0]            # face with the lowest Y (normal -Y)
z_faces = box.faces().filter_by(Axis.Z)                # faces whose normal is parallel to Z (top and bottom)
long_edges = box.edges().filter_by(Axis.X).sort_by(SortBy.LENGTH)[-1]
assert len(vertical_edges) == 4 and abs(top.center().Z - 10) < 1e-9
```

`filter_by(Axis)` keeps edges parallel to the axis (faces with normal parallel to
it); `sort_by(Axis)` orders by position along it; `group_by(Axis)` groups equal
positions (index `[-1]` = highest group); `filter_by(GeomType.LINE | CIRCLE | PLANE | CYLINDER)`
by geometry; `filter_by(lambda e: e.length > 5)` by predicate. Select fillet edges
from the *current* solid right before filleting (edges held from an earlier shape
do not belong to the new one).

## Shells, mirrors, patterns, splits

```python
box = Box(60, 40, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))
open_box = offset(box, amount=-2, openings=box.faces().sort_by(Axis.Z)[-1])   # 2 mm walls, open top
half = Pos(0, 0, 0) * Box(30, 20, 5, align=(Align.MIN, Align.CENTER, Align.MIN))
whole = half + mirror(half, about=Plane.YZ)                                    # mirror across the YZ plane
pins = [loc * Cylinder(2, 10) for loc in PolarLocations(20, 6)]                # 6 around a 20 mm radius
grid = [loc * Cylinder(1.5, 10) for loc in GridLocations(10, 10, 3, 2)]        # 3 x 2 grid, 10 mm pitch
lower = split(box, bisect_by=Plane.XY.offset(15), keep=Keep.BOTTOM)           # keep below Z=15
assert abs(open_box.volume - (60*40*30 - 56*36*28)) < 1e-6 and abs(whole.volume - 6000) < 1e-6
```

## Rounding corners robustly

3D fillets on thin or complex solids fail often; rounding the 2D outline first
almost never does. Prefer, in this order:

```python
outline = Polyline((0, 0), (40, 0), (40, 10), (25, 10), (15, 25), (0, 25), close=True)
face = make_face(outline)
rounded = fillet(face.vertices(), 2)              # 2D fillet of every corner of a sketch face
plate = extrude(rounded, 4)
some = fillet([v for v in face.vertices() if v.X > 30], 4)   # only some corners (of the sharp face)
clip = extrude(RectangleRounded(20, 12, 3) - Circle(3), 5)   # rounded rectangle minus a hole
body = Box(30, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
body, applied = try_fillet(body, body.edges().filter_by(Axis.Z), 8, fallbacks=(4, 2))  # 3D with fallbacks
assert applied == 8 and len(some.vertices()) > 6
```

A 3D fillet fails when the radius is bigger than an adjacent face, when the
selected edges meet tangent faces (e.g. the seam of a cylinder), or after a
boolean left tiny faces. Fillet before cutting small features when possible, keep
the radius under half the smallest adjacent wall, and use `try_fillet` so a
failure degrades to a smaller radius instead of a crash. Never spend more than
two attempts on a cosmetic fillet: round the sketch instead.

## Text

```python
label = extrude(Text("A1", font_size=8), 1)                       # raised text 1 mm tall, centred
plate = Box(30, 15, 3, align=(Align.CENTER, Align.CENTER, Align.MIN))
embossed = plate + Pos(0, 0, 3) * label                          # on top of the plate
debossed = plate - Pos(0, 0, 2.4) * extrude(Text("A1", font_size=8), 1)   # 0.6 mm deep
assert embossed.volume > plate.volume > debossed.volume
```

## Measuring in code

```python
part = Box(40, 20, 10) - Cylinder(3, 20)
bb = part.bounding_box()                      # bb.min, bb.max, bb.size (Vectors with .X .Y .Z)
v = part.volume
faces, edges = part.faces(), part.edges()
c = part.center()                             # centre of mass
ok = part.is_valid
n = len(part.solids())
assert abs(bb.size.X - 40) < 1e-9 and n == 1
```

## Builder API (also fine)

```python
with BuildPart() as bp:
    Box(40, 20, 10)
    with Locations((15, 0, 5), (-15, 0, 5)):
        Cylinder(3, 20, mode=Mode.SUBTRACT)
    fillet(bp.edges().filter_by(Axis.Z), 2)
part = bp.part
assert len(part.faces()) > 6
```

## Pitfalls

- `Box` is centred by default and `Cylinder` is centred on its axis *and* along
  Z: use `align=(Align.CENTER, Align.CENTER, Align.MIN)` to sit on Z=0.
- `Rot(a, b, c)` rotates about X, then Y, then Z, about the origin: rotate before
  translating (`Pos(...) * Rot(...) * shape`).
- Fillets fail when the radius is larger than an adjacent face/wall or on tangent
  chains: fillet fewer edges, use a smaller radius, or fillet before the feature
  that makes it impossible. Never silently drop a requested fillet; say so.
- A boolean that returns extra solids or slivers means tools touched faces
  exactly: extend the tool through the wall (e.g. height + 2 mm).
- `extrude` of a sketch on `Plane.XY` goes +Z. For a sketch on another plane it
  goes along that plane's normal (`Plane.XZ` normal is -Y).
- Don't guess dimensions you can compute: derive from parameters.
- `Vector` has no `to_tuple()` in this version: use `tuple(v)` or `v.X, v.Y, v.Z`.
  A `BoundBox` has `.min`, `.max`, `.size`, `.center()` (a method) and `.diagonal`.
- `shape.center()` is the centre of mass; `face.normal_at()` with no argument is
  the normal at the face centre; `edge.length`, `face.area`, `solid.volume`.
