"""Sheet metal: a folded solid built from a base flange, edge flanges, hems, tabs and cuts, with the flat
pattern tracked alongside (bend allowance from the K-factor) so it can be unfolded, drawn and exported as
DXF with bend lines. Rules: thickness t, inner bend radius r, K-factor k."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import build123d as bd
from build123d import Vector


def _v2(a, b) -> tuple[float, float]:
    return (float(a), float(b))


@dataclass
class Panel:
    """A flat region of the sheet: its top face plane (material lies below it, along -z) and the
    rigid map from the plane's (u, v) to the flat pattern's (x, y)."""
    plane: bd.Plane
    flat_origin: tuple[float, float]
    flat_x: tuple[float, float]
    flat_y: tuple[float, float]

    def local2(self, p: Vector) -> tuple[float, float]:
        q = self.plane.to_local_coords(Vector(p))
        return (q.X, q.Y)

    def local_z(self, p: Vector) -> float:
        return self.plane.to_local_coords(Vector(p)).Z

    def dir3(self, v2) -> Vector:
        return self.plane.x_dir * v2[0] + self.plane.y_dir * v2[1]

    def flat2(self, p2) -> tuple[float, float]:
        return (self.flat_origin[0] + self.flat_x[0] * p2[0] + self.flat_y[0] * p2[1],
                self.flat_origin[1] + self.flat_x[1] * p2[0] + self.flat_y[1] * p2[1])

    def flat_dir(self, v2) -> tuple[float, float]:
        return (self.flat_x[0] * v2[0] + self.flat_y[0] * v2[1], self.flat_x[1] * v2[0] + self.flat_y[1] * v2[1])


@dataclass
class SheetState:
    thickness: float
    radius: float
    k: float
    base_frame: bd.Plane
    flat: bd.Face                          # the flat pattern (XY, base sketch coordinates)
    panels: list[Panel] = field(default_factory=list)
    bends: list[dict] = field(default_factory=list)   # {"a": (x, y), "b": (x, y), "angle": deg, "radius": r} in flat coordinates

    def to_dict(self) -> dict:
        return {"thickness": self.thickness, "radius": self.radius, "k": self.k, "flat_area": round(self.flat.area, 3),
                "bends": [{"a": list(b["a"]), "b": list(b["b"]), "angle": b["angle"], "radius": b["radius"]} for b in self.bends]}


def bend_allowance(angle_deg: float, radius: float, thickness: float, k: float) -> float:
    return math.radians(abs(angle_deg)) * (radius + k * thickness)


def _fuse_flat(flat: bd.Face, other, cut: bool = False) -> bd.Face:
    out = (flat - other) if cut else (flat + other)
    faces = [f for f in out.faces() if f.area > 1e-9]
    if len(faces) == 1:
        return faces[0]
    if not faces:
        raise ValueError("the flat pattern vanished")
    # several faces: keep them as one face when they touch; otherwise the largest (a detached tab is an error)
    merged = faces[0]
    for f in faces[1:]:
        merged = merged + f
    fs = merged.faces()
    return max(fs, key=lambda f: f.area) if fs else faces[0]


# ---------------------------------------------------------------------------- base flange

def base_flange(frame: bd.Plane, face2d: bd.Face, thickness: float, radius: float | None = None, k: float = 0.44) -> tuple[bd.Shape, SheetState]:
    """A flat sheet: the closed profile extruded by the thickness along the sketch normal
    (the sketch plane is the bottom face)."""
    t = float(thickness)
    if t <= 0:
        raise ValueError("the sheet thickness must be positive")
    solid = bd.extrude(frame * face2d, t)
    top = frame.offset(t)
    state = SheetState(t, float(radius) if radius else t, float(k), frame, face2d,
                       [Panel(top, (0.0, 0.0), (1.0, 0.0), (0.0, 1.0))])
    return solid, state


def find_panel(state: SheetState, p: Vector) -> tuple[Panel, bool]:
    """The panel whose top (True) or bottom (False) face holds the point."""
    t = state.thickness
    best = None
    for panel in state.panels:
        z = panel.local_z(p)
        for on_top, target in ((True, 0.0), (False, -t)):
            d = abs(z - target)
            if d < 1e-3 and (best is None or d < best[0]):
                best = (d, panel, on_top)
    if best is None:
        raise ValueError("that edge is not on a face of the sheet (pick an edge of a flat sheet face)")
    return best[1], best[2]


def _flange_profile(t: float, r: float, angle: float, height: float, eps: float) -> bd.Face:
    """The bend + leg cross-section in profile coordinates: x outward from the sheet's edge, y the bend
    direction; the sheet ends at x = 0 with its bent face at y = 0 and the other face at y = -t.
    `eps` extends the profile into the sheet so the fuse overlaps."""
    a = math.radians(angle)
    P = lambda rho, th: (rho * math.sin(th), r - rho * math.cos(th))
    q1, q2 = P(r, a), P(r + t, a)
    d = (math.cos(a), math.sin(a))
    e1, e2 = (q1[0] + height * d[0], q1[1] + height * d[1]), (q2[0] + height * d[0], q2[1] + height * d[1])
    m1, m2 = P(r, a / 2), P(r + t, a / 2)
    bend_in = bd.Edge.make_three_point_arc((0, 0, 0), (*m1, 0), (*q1, 0)) if angle > 1e-6 else bd.Edge.make_line((0, 0, 0), (*q1, 0))
    bend_out = bd.Edge.make_three_point_arc((*q2, 0), (*m2, 0), (0, -t, 0)) if angle > 1e-6 else bd.Edge.make_line((*q2, 0), (0, -t, 0))
    if height <= 1e-9:                       # a bare bend: no leg edges
        leg = [bd.Edge.make_line((*q1, 0), (*q2, 0))]
    else:
        leg = [bd.Edge.make_line((*q1, 0), (*e1, 0)), bd.Edge.make_line((*e1, 0), (*e2, 0)), bd.Edge.make_line((*e2, 0), (*q2, 0))]
    edges = [bd.Edge.make_line((-eps, 0, 0), (0, 0, 0)), bend_in, *leg, bend_out,
             bd.Edge.make_line((0, -t, 0), (-eps, -t, 0)), bd.Edge.make_line((-eps, -t, 0), (-eps, 0, 0))]
    return bd.make_face(bd.Wire(edges))


def flange(state: SheetState, solid: bd.Shape, edge: bd.Edge, height: float, angle: float = 90.0, radius: float | None = None,
           inset: float = 0.0, k: float | None = None) -> tuple[bd.Shape, SheetState, dict]:
    """An edge flange: a bend of `angle` about the picked sheet edge (bending towards the face the edge
    belongs to) and a straight leg of `height`; `inset` shortens the flange at both ends (relief)."""
    from .edits import robust_boolean
    t = state.thickness
    r = float(radius) if radius else state.radius
    kf = float(k) if k is not None else state.k
    height = float(height); angle = float(angle); inset = float(inset or 0)
    if edge.geom_type != bd.GeomType.LINE:
        raise ValueError("flanges go on straight edges")
    mid = edge.position_at(0.5)
    panel, on_top = find_panel(state, mid)
    A3, B3 = edge.position_at(0), edge.position_at(1)
    A2, B2 = panel.local2(A3), panel.local2(B3)
    L = math.hypot(B2[0] - A2[0], B2[1] - A2[1])
    if L - 2 * inset <= 1e-6:
        raise ValueError("the inset leaves no flange")
    X2 = ((B2[0] - A2[0]) / L, (B2[1] - A2[1]) / L)
    Y2 = (X2[1], -X2[0])                                            # outward candidate (right of the edge)
    m2 = ((A2[0] + B2[0]) / 2, (A2[1] + B2[1]) / 2)
    probe = panel.flat2((m2[0] + Y2[0] * 0.25 * t, m2[1] + Y2[1] * 0.25 * t))
    if state.flat.is_inside((probe[0], probe[1], 0), tolerance=1e-6):         # that side is material: the edge runs the other way
        A2, B2, A3, B3 = B2, A2, B3, A3
        X2 = (-X2[0], -X2[1]); Y2 = (X2[1], -X2[0])
    if inset:
        A2 = (A2[0] + X2[0] * inset, A2[1] + X2[1] * inset); B2 = (B2[0] - X2[0] * inset, B2[1] - X2[1] * inset)
        A3 = A3 + panel.dir3(X2) * inset; B3 = B3 - panel.dir3(X2) * inset
        L -= 2 * inset
    X3, Y3 = panel.dir3(X2).normalized(), panel.dir3(Y2).normalized()
    up = panel.plane.z_dir
    Zb = up if on_top else -up                                      # the bend direction
    if on_top:
        prof = bd.Plane(origin=B3, x_dir=Y3, z_dir=-X3)             # y_dir = up: extrude from B back to A
    else:
        prof = bd.Plane(origin=A3, x_dir=Y3, z_dir=X3)              # y_dir = -up
    make = lambda eps: bd.extrude(prof * _flange_profile(t, r, angle, height, eps), L)
    new = robust_boolean(solid, make, "add", "the flange")
    # the flat pattern: a rectangle beyond the edge, bend allowance + leg
    ba = bend_allowance(angle, r, t, kf)
    Af, Bf = panel.flat2(A2), panel.flat2(B2)
    of = panel.flat_dir(Y2)
    reach = ba + height
    rect = bd.make_face(bd.Wire.make_polygon([(Af[0], Af[1], 0), (Bf[0], Bf[1], 0), (Bf[0] + of[0] * reach, Bf[1] + of[1] * reach, 0), (Af[0] + of[0] * reach, Af[1] + of[1] * reach, 0)], close=True))
    state.flat = _fuse_flat(state.flat, rect)
    state.bends.append({"a": (Af[0] + of[0] * ba / 2, Af[1] + of[1] * ba / 2), "b": (Bf[0] + of[0] * ba / 2, Bf[1] + of[1] * ba / 2), "angle": angle, "radius": r})
    # the leg becomes a panel: its outer (bent-towards) face continues the picked face
    a = math.radians(angle)
    q1 = (r * math.sin(a), r - r * math.cos(a)); q2 = ((r + t) * math.sin(a), r - (r + t) * math.cos(a))
    d3 = (Y3 * math.cos(a) + Zb * math.sin(a)).normalized()
    away = (Y3 * (q1[0] - q2[0]) + Zb * (q1[1] - q2[1])).normalized()
    if height > 1e-9:
        Q1A = A3 + Y3 * q1[0] + Zb * q1[1]
        leg = bd.Plane(origin=Q1A, x_dir=X3, z_dir=away)
        flat_x = ((Bf[0] - Af[0]) / L, (Bf[1] - Af[1]) / L)
        origin_f = (Af[0] + of[0] * ba, Af[1] + of[1] * ba)
        if (leg.y_dir - d3).length > 1e-6:                          # keep (u, v) = (along the edge, up the leg)
            Q1B = B3 + Y3 * q1[0] + Zb * q1[1]
            leg = bd.Plane(origin=Q1B, x_dir=-X3, z_dir=away)
            flat_x = (-flat_x[0], -flat_x[1]); origin_f = (Bf[0] + of[0] * ba, Bf[1] + of[1] * ba)
        state.panels.append(Panel(leg, origin_f, flat_x, of))
    info = {"length": round(L, 3), "allowance": round(ba, 3), "angle": angle, "radius": r, "height": height, "up": on_top}
    if height > 1e-9:                                               # the leg's far edges (for chained bends)
        P1A = A3 + Y3 * q1[0] + Zb * q1[1]; P1B = B3 + Y3 * q1[0] + Zb * q1[1]
        Q2A = A3 + Y3 * q2[0] + Zb * q2[1]; Q2B = B3 + Y3 * q2[0] + Zb * q2[1]
        fi = (P1A + P1B) * 0.5 + d3 * height; fo = (Q2A + Q2B) * 0.5 + d3 * height
        info["far_inner"] = (fi.X, fi.Y, fi.Z); info["far_outer"] = (fo.X, fo.Y, fo.Z)
    return new, state, info


def _edge_near(solid: bd.Shape, pt, tol: float = 1e-3) -> bd.Edge:
    P = Vector(*pt)
    best = None
    for e in solid.edges():
        if e.geom_type != bd.GeomType.LINE:
            continue
        d = (e.position_at(0.5) - P).length
        if best is None or d < best[0]:
            best = (d, e)
    if best is None or best[0] > tol:
        raise ValueError("could not find the next bend edge on the sheet")
    return best[1]


def contour_flange(state: SheetState, solid: bd.Shape, edge: bd.Edge, legs: Sequence[Sequence[float]], radius: float | None = None,
                   inset: float = 0.0, k: float | None = None) -> tuple[bd.Shape, SheetState, dict]:
    """A chain of bends from one edge, like a profile swept along it: `legs` = [(length, angle), ...] where
    each angle is the signed turn at the start of that leg (the first relative to the sheet, later ones
    relative to the previous leg, as a polyline turning left or right). A positive first angle bends
    towards the face the picked edge is on, a negative one towards the other face. A leg of length 0
    is a bare bend."""
    legs = [(float(l), float(a)) for l, a in legs]
    if not legs:
        raise ValueError("a contour flange needs at least one leg")
    t = state.thickness
    first_len, first_ang = legs[0]
    if first_ang < 0:                                               # bend towards the other face: its edge is t away
        mid = edge.position_at(0.5)
        panel, on_top = find_panel(state, mid)
        other = mid - panel.plane.z_dir * (t if on_top else -t)
        edge = _edge_near(solid, (other.X, other.Y, other.Z), tol=max(1e-3, t * 1e-3))
    shape, st, info = flange(state, solid, edge, first_len, abs(first_ang), radius, inset, k)
    infos = [info]
    prev_sign = 1 if first_ang >= 0 else -1
    for length, ang in legs[1:]:
        if "far_inner" not in info:
            raise ValueError("a bend after a leg of length 0 has no edge to bend")
        sign = 1 if ang >= 0 else -1
        pt = info["far_inner"] if sign == prev_sign else info["far_outer"]
        nxt = _edge_near(shape, pt, tol=max(1e-3, t * 1e-3))
        shape, st, info = flange(st, shape, nxt, length, abs(ang), radius, inset, k)
        infos.append(info)
        prev_sign = sign
    summary = {"legs": [{"length": i["height"], "angle": a, "allowance": i["allowance"]} for i, (_, a) in zip(infos, legs)],
               "unfolded": round(sum(i["height"] + i["allowance"] for i in infos), 3), "bends": len(infos)}
    return shape, st, summary


def hem(state: SheetState, solid: bd.Shape, edge: bd.Edge, length: float, gap: float | None = None, inset: float = 0.0) -> tuple[bd.Shape, SheetState, dict]:
    """A closed hem: a 180 degree bend with inner radius gap/2 and a return of `length`."""
    r = (float(gap) / 2) if gap else state.radius
    return flange(state, solid, edge, length, 180.0, r, inset)


# ---------------------------------------------------------------------------- tabs, cuts, flat pattern

def _map_profile_to_flat(state: SheetState, frame: bd.Plane, face2d: bd.Face) -> bd.Face:
    """A profile sketched on a sheet face, carried into the flat pattern."""
    panel, _on_top = find_panel(state, frame.origin)
    # two points of the sketch frame -> panel (u, v) -> flat (x, y): a rigid 2D map
    o3, x3 = frame.origin, frame.origin + frame.x_dir
    o2, x2 = panel.local2(o3), panel.local2(x3)
    of, xf = panel.flat2(o2), panel.flat2(x2)
    ang = math.degrees(math.atan2(xf[1] - of[1], xf[0] - of[0]))
    # does the frame's y map to +90 or -90 degrees from x in the flat (a mirrored frame)?
    y3 = frame.origin + frame.y_dir
    yf = panel.flat2(panel.local2(y3))
    cross = (xf[0] - of[0]) * (yf[1] - of[1]) - (xf[1] - of[1]) * (yf[0] - of[0])
    f = face2d
    if cross < 0:
        f = bd.mirror(f, about=bd.Plane.XZ).faces()[0]
    f = bd.Rot(0, 0, ang) * f
    return f.moved(bd.Location((of[0], of[1], 0)))


def tab(state: SheetState, solid: bd.Shape, frame: bd.Plane, face2d: bd.Face) -> tuple[bd.Shape, SheetState]:
    """Material added in the plane of a sheet face: the profile extruded by the thickness, flush."""
    from .edits import robust_boolean
    t = state.thickness
    panel, on_top = find_panel(state, frame.origin)
    into = -1.0 if on_top else 1.0           # from the top face the sheet lies below; from the bottom, above
    # the tab fills the sheet's thickness, flush with both faces; the part of it inside the sheet is absorbed
    new = robust_boolean(solid, lambda eps: bd.extrude(frame * face2d, into * t), "add", "the tab")
    state.flat = _fuse_flat(state.flat, _map_profile_to_flat(state, frame, face2d))
    return new, state


def sheet_cut(state: SheetState, solid: bd.Shape, frame: bd.Plane, face2d: bd.Face) -> tuple[bd.Shape, SheetState]:
    """A through cut of the profile, carried into the flat pattern."""
    from .edits import robust_boolean
    t = state.thickness
    panel, on_top = find_panel(state, frame.origin)
    into = -1.0 if on_top else 1.0
    new = robust_boolean(solid, lambda eps: bd.extrude(frame.offset(-into * eps) * face2d, into * (t + 2 * eps)), "cut", "the sheet cut")
    state.flat = _fuse_flat(state.flat, _map_profile_to_flat(state, frame, face2d), cut=True)
    return new, state


def flat_pattern(state: SheetState, at: bd.Plane | None = None) -> tuple[bd.Shape, list[dict]]:
    """The flat pattern as a solid (the flat face extruded by the thickness) in the given plane
    (default: the base sketch frame), with the bend lines in world coordinates."""
    pl = at if at is not None else state.base_frame
    solid = bd.extrude(pl * state.flat, state.thickness)
    lines = []
    for b in state.bends:
        a = pl.origin + pl.x_dir * b["a"][0] + pl.y_dir * b["a"][1] + pl.z_dir * state.thickness
        c = pl.origin + pl.x_dir * b["b"][0] + pl.y_dir * b["b"][1] + pl.z_dir * state.thickness
        lines.append({"a": [round(a.X, 4), round(a.Y, 4), round(a.Z, 4)], "b": [round(c.X, 4), round(c.Y, 4), round(c.Z, 4)], "angle": b["angle"], "radius": b["radius"]})
    return solid, lines


def flat_dxf(state: SheetState, path) -> str:
    """The flat pattern outline (OUTLINE layer) and bend lines (BEND layer, dashed) as DXF."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.layers.add("OUTLINE", color=7)
    doc.layers.add("BEND", color=1, linetype="DASHED") if "DASHED" in doc.linetypes else doc.layers.add("BEND", color=1)
    msp = doc.modelspace()
    for w in [state.flat.outer_wire()] + list(state.flat.inner_wires()):
        for e in w.edges():
            if e.geom_type == bd.GeomType.LINE:
                a, b = e.position_at(0), e.position_at(1)
                msp.add_line((a.X, a.Y), (b.X, b.Y), dxfattribs={"layer": "OUTLINE"})
            elif e.geom_type == bd.GeomType.CIRCLE and e.is_closed:
                c = e.arc_center
                msp.add_circle((c.X, c.Y), e.radius, dxfattribs={"layer": "OUTLINE"})
            else:
                n = max(8, int(e.length / 0.5) + 4)
                pts = [(p.X, p.Y) for p in (e.position_at(i / n) for i in range(n + 1))]
                msp.add_lwpolyline(pts, dxfattribs={"layer": "OUTLINE"})
    for b in state.bends:
        msp.add_line(b["a"], b["b"], dxfattribs={"layer": "BEND"})
        mx, my = (b["a"][0] + b["b"][0]) / 2, (b["a"][1] + b["b"][1]) / 2
        msp.add_text(f"BEND {b['angle']:g} R{b['radius']:g}", dxfattribs={"layer": "BEND", "height": 2.0}).set_placement((mx, my + 1))
    doc.saveas(str(path))
    return str(path)
