"""Engineering drawings: third-angle front/top/right views plus an isometric, with hidden
lines, overall dimensions, hole callouts and a title block - as SVG (for people) and DXF
(for CAM/other CAD). The equivalent of Fusion's Drawings workspace, kept simple.

    drawing_svg(parts, "bracket") -> str
    drawing_dxf(parts, "bracket", path)
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import build123d as bd
import numpy as np
from build123d import Vector

from .model import Parts

SHEETS = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "letter": (279.4, 215.9)}
SCALES = (10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05)
VIEWS = {                                    # direction the camera sits in, and the sheet's up vector
    "front": ((0, -1, 0), (0, 0, 1)),
    "back": ((0, 1, 0), (0, 0, 1)),
    "top": ((0, 0, 1), (0, 1, 0)),
    "bottom": ((0, 0, -1), (0, 1, 0)),
    "right": ((1, 0, 0), (0, 0, 1)),
    "left": ((-1, 0, 0), (0, 0, 1)),
    "iso": ((1, -1, 0.8), (0, 0, 1)),
}


@dataclass
class View:
    name: str
    visible: list[np.ndarray]            # polylines in view coordinates (mm), y up
    hidden: list[np.ndarray]
    circles: list[tuple[float, float, float]]   # full circles (cx, cy, r) in view coordinates (for DXF)
    bbox: tuple[float, float, float, float]      # xmin, ymin, xmax, ymax
    holes: list[dict] = field(default_factory=list)   # callouts: {x, y, text}
    hatch: list[np.ndarray] = field(default_factory=list)   # section views: 45-degree hatch segments over the cut faces
    section: dict | None = None                        # {"plane": "XZ", "offset": 20, "parent": "top", "label": "A"}
    cu: float = 0.0                                    # view coordinates are relative to the look-at point: its (u, v)
    cv: float = 0.0
    # placement on the sheet (filled by the layout)
    ox: float = 0.0
    oy: float = 0.0
    arcs: list = field(default_factory=list)           # visible arcs (cx, cy, r, start_deg, end_deg) for radius dimensions
    angles: list = field(default_factory=list)         # (vx, vy, dir1_deg, dir2_deg, angle_deg) at sloped corners
    scale_mult: float = 1.0                            # per-view scale on top of the sheet scale (details: 2x by default)
    detail: dict | None = None                         # {"parent", "x", "y", "r", "label"}
    broken: dict | None = None                         # {"x1", "x2", "gap"} in view coordinates (the band removed)
    centerlines: list = field(default_factory=list)    # dash-dot axes of holes seen from the side: (x1, y1, x2, y2)


def _polyline(edge: bd.Edge, n: int = 48) -> np.ndarray:
    if edge.geom_type == bd.GeomType.LINE:
        a, b = edge.position_at(0), edge.position_at(1)
        return np.array([[a.X, a.Y], [b.X, b.Y]])
    k = max(8, min(n, int(edge.length / 0.5) + 4))
    pts = [edge.position_at(i / k) for i in range(k + 1)]
    return np.array([[p.X, p.Y] for p in pts])


SECTIONS = {   # section plane -> the view it is seen in, the half kept, and the parent view that shows the cut line
    "XZ": ("front", "+", "top"), "XY": ("top", "-", "front"), "YZ": ("right", "-", "front"),
}


def parse_view(name: str) -> tuple[str, dict | None]:
    """"front" -> ("front", None); "section:XZ@20" -> ("front", {"plane": "XZ", "offset": 20.0, ...});
    "halfsection:XZ@20|YZ@0" (section one side of the second plane), "detail:front@10,5:8:2" (a circle
    of radius 8 at view point (10, 5), 2x), "aux:1,0,1" (a view from that direction, optional "/ux,uy,uz"),
    "broken:front@30..70" (the band between those view x values removed)."""
    kind = name.split(":", 1)[0]
    if kind not in ("section", "halfsection", "detail", "aux", "broken"):
        return name, None
    spec = name.split(":", 1)[1] if ":" in name else ""
    if kind in ("section", "halfsection"):
        main, _, divider = spec.partition("|")
        plane, _, off = (main or "XZ@0").partition("@")
        plane = plane.upper() or "XZ"
        if plane not in SECTIONS:
            raise ValueError(f"section plane must be XY, XZ or YZ (got {plane!r})")
        view, keep, parent = SECTIONS[plane]
        out = {"plane": plane, "offset": float(off or 0), "keep": keep, "parent": parent, "label": "A"}
        if kind == "halfsection":
            dplane, _, doff = (divider or "").partition("@")
            out["half"] = {"plane": dplane.upper() or {"XZ": "YZ", "XY": "YZ", "YZ": "XZ"}[plane], "offset": float(doff or 0)}
        return view, out
    if kind == "detail":
        parent, _, rest = spec.partition("@")
        bits = rest.split(":")
        x, y = (float(c) for c in (bits[0].split(",") if bits and bits[0] else ("0", "0")))
        r = float(bits[1]) if len(bits) > 1 and bits[1] else 10.0
        k = float(bits[2]) if len(bits) > 2 and bits[2] else 2.0
        return parent or "front", {"detail": {"parent": parent or "front", "x": x, "y": y, "r": r, "scale": k, "label": "B"}}
    if kind == "aux":
        d, _, u = spec.partition("/")
        return name, {"aux": {"dir": [float(c) for c in d.split(",")], "up": [float(c) for c in u.split(",")] if u else None}}
    parent, _, rest = spec.partition("@")
    x1, _, x2 = rest.partition("..") if ".." in rest else rest.partition(",")
    a, b = sorted((float(x1 or 0), float(x2 or 0)))
    return parent or "front", {"broken": {"parent": parent or "front", "x1": a, "x2": b, "gap": 6.0}}


def project(parts: Parts, name: str) -> View:
    """Hidden-line projection of all parts into a named view (coordinates in mm, y up).
    A "section:XZ@20" view cuts the model at that plane first and hatches the cut faces."""
    base, sec = parse_view(name)
    if sec is not None and sec.get("detail"):
        return _detail_view(parts, name, sec["detail"])
    if sec is not None and sec.get("broken"):
        return _broken_view(parts, name, sec["broken"])
    if sec is not None and sec.get("half"):
        return _half_section_view(parts, name, sec)
    if sec is not None and not sec.get("aux"):
        from .render import section_parts
        half = section_parts(parts, plane=sec["plane"], offset=sec["offset"], keep=sec["keep"])
        view = project(half, base)
        view.name = name
        view.section = sec
        view.hatch = _hatch(half, base, sec, (view.cu, view.cv))
        view.holes = []
        return view
    direction, up = view_dir(name)
    compound = parts.compound
    bb = compound.bounding_box()
    centre = bb.center()
    dist = max(bb.diagonal, 1.0) * 5
    d = Vector(*direction).normalized()
    origin = centre + d * dist
    vis, hid = compound.project_to_viewport(viewport_origin=tuple(origin), viewport_up=up, look_at=tuple(centre))
    visible = [_polyline(e) for e in vis]
    hidden = [_polyline(e) for e in hid]
    circles = []
    for e in vis:
        if e.geom_type == bd.GeomType.CIRCLE and e.is_closed:
            c = e.arc_center
            circles.append((c.X, c.Y, e.radius))
    pts = np.vstack(visible + hidden) if (visible or hidden) else np.zeros((1, 2))
    view = View(name, visible, hidden, circles, (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())))
    view.holes = _hole_callouts(parts, name, origin, up, centre) if name in VIEWS else []
    right, up_v = _view_axes(name)
    view.cu, view.cv = centre.dot(right), centre.dot(up_v)
    view.arcs = _arcs_of(vis)
    view.angles = _corner_angles(vis)
    view.centerlines = _side_centerlines(parts, name, centre) if name in VIEWS else []
    return view


def view_dir(name: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """(direction the camera sits in, up vector) of a named or auxiliary ("aux:1,0,1[/0,0,1]") view."""
    if name in VIEWS:
        return VIEWS[name]
    base, spec = parse_view(name)
    if spec and spec.get("aux"):
        d = Vector(*spec["aux"]["dir"]).normalized()
        up = Vector(*spec["aux"]["up"]) if spec["aux"]["up"] else (Vector(0, 0, 1) if abs(d.Z) < 0.95 else Vector(0, 1, 0))
        up = (up - d * up.dot(d)).normalized()
        return (d.X, d.Y, d.Z), (up.X, up.Y, up.Z)
    return VIEWS[base]


def _view_axes(name: str) -> tuple[Vector, Vector]:
    direction, up = view_dir(name)
    d = Vector(*direction).normalized()
    u = Vector(*up).normalized()
    return u.cross(d).normalized(), u                     # (right, up): world -> view coordinates by dot products


def _hatch(half: Parts, base: str, sec: dict, centre_uv=(0.0, 0.0), spacing: float = 2.0, step: float = 0.25) -> list[np.ndarray]:
    """45-degree hatch segments over the faces lying in the section plane (view coordinates)."""
    normal = {"XY": Vector(0, 0, 1), "XZ": Vector(0, 1, 0), "YZ": Vector(1, 0, 0)}[sec["plane"]]
    right, up = _view_axes(base)
    cu, cv = centre_uv
    segs: list[np.ndarray] = []
    for part in half:
        for f in part.shape.faces():
            if f.geom_type != bd.GeomType.PLANE:
                continue
            n = f.normal_at()
            if abs(abs(n.dot(normal)) - 1) > 1e-3 or abs((f.center() - normal * sec["offset"]).dot(normal)) > 1e-3:
                continue
            bb = f.bounding_box()
            lo = Vector(bb.min.X, bb.min.Y, bb.min.Z)
            # 2D frame of the face in view coordinates
            u0, v0 = lo.dot(right) - cu, lo.dot(up) - cv
            w = (Vector(bb.max.X, bb.max.Y, bb.max.Z) - lo)
            uw, vh = abs(w.dot(right)), abs(w.dot(up))
            origin_world = lo
            k = -vh
            while k < uw + vh:
                pts = []
                tpos = 0.0
                run: list[tuple[float, float]] = []
                while tpos <= uw + vh:
                    u, v = k + tpos, tpos                           # a 45-degree line u - v = k
                    inside = 0 <= u <= uw and 0 <= v <= vh
                    if inside:
                        p_world = origin_world + right * u + up * v
                        inside = f.is_inside(p_world, tolerance=1e-3)
                    if inside:
                        run.append((u0 + u, v0 + v))
                    elif run:
                        if len(run) > 1:
                            segs.append(np.array([run[0], run[-1]]))
                        run = []
                    tpos += step
                if len(run) > 1:
                    segs.append(np.array([run[0], run[-1]]))
                k += spacing
    return segs


def _hole_callouts(parts: Parts, name: str, origin: Vector, up, centre: Vector) -> list[dict]:
    """Holes whose axis points at the viewer, as "n x Ø d THRU" labels at their centres."""
    from . import measure
    direction = Vector(*VIEWS[name][0]).normalized()
    if name == "iso":
        return []
    # the view's 2D frame: x = up x direction (right on the sheet), y = up
    u = Vector(*up).normalized()
    right = u.cross(direction).normalized() * -1
    groups: dict[tuple, list[tuple[float, float]]] = {}
    for p in parts:
        try:
            holes = measure.holes(p.shape)
        except Exception:
            continue
        for h in holes:
            ax = h.axis if isinstance(h.axis, Vector) else Vector(*h.axis)
            if abs(abs(ax.dot(direction)) - 1) > 1e-3:
                continue
            c = h.center if isinstance(h.center, Vector) else Vector(*h.center)
            rel = c - centre
            key = (round(h.diameter, 2), None if h.through else round(h.depth, 1))
            groups.setdefault(key, []).append((rel.dot(right), rel.dot(u)))
    out = []
    for (dia, depth), pts in sorted(groups.items()):
        text = f"{len(pts)}x " if len(pts) > 1 else ""
        text += f"Ø{dia:g}" + (" THRU" if depth is None else f" ↓{depth:g}")
        x, y = max(pts, key=lambda q: (q[1], q[0]))          # label the top-most instance
        out.append({"x": x, "y": y, "text": text, "dia": dia, "depth": depth, "points": sorted(pts)})
    return out


def hole_table(views: dict[str, "View"]) -> list[dict]:
    """One row per hole group across the views: letter, size, count and the positions
    (view coordinates, relative to the view's own origin) - the drawing's hole table."""
    rows = []
    letters = iter("ABCDEFGHJKLMNPQRSTUVWXYZ")
    for v in views.values():
        for h in v.holes:
            try:
                letter = next(letters)
            except StopIteration:
                letter = "?"
            h["letter"] = letter
            x0, y0 = v.bbox[0], v.bbox[1]                      # positions from the view's bottom-left datum corner
            rows.append({"letter": letter, "view": v.name, "text": h["text"].split("x ")[-1], "count": len(h["points"]),
                         "points": [(round(px - x0, 3), round(py - y0, 3)) for px, py in h["points"]]})
    return rows


def _fit_scale(views: dict[str, View], sheet_w: float, sheet_h: float, margin: float) -> float:
    """The largest standard scale at which the third-angle layout fits the sheet."""
    gap = 12.0
    f, t, r, i = views.get("front"), views.get("top"), views.get("right"), views.get("iso")
    w = lambda v: (v.bbox[2] - v.bbox[0]) if v else 0.0
    h = lambda v: (v.bbox[3] - v.bbox[1]) if v else 0.0
    need_w = w(f) + (gap + w(r) if r else 0) + (gap + w(i) if i and not r else 0)
    need_h = h(f) + (gap + h(t) if t else 0)
    if i and r:
        need_w = max(need_w, w(f) + gap + max(w(r), w(i)))
        need_h = max(need_h, h(r) + gap + h(i))
    extras = [v for k, v in views.items() if k not in ("front", "top", "right", "iso") and v is not i]
    avail_w, avail_h = sheet_w - 2 * margin - 40, sheet_h - 2 * margin - 36   # room for dimensions and the title block
    for s in SCALES:
        if not (need_w * s + 2 * 14 <= avail_w and need_h * s + 2 * 14 <= avail_h):
            continue
        if extras:                                        # extra views flow in rows under the isometric slot
            iso_x = (w(f) + gap + (w(r) if r else 0) + gap) * s + 18 + 14
            rows = _extra_rows(extras, avail_w - iso_x, s)
            region_h = sum(max(hv for _, _, hv in row) + 12.0 for row in rows)
            region_w = max(sum(wv + 12.0 for _, wv, _ in row) - 12.0 for row in rows)
            total_h = max(need_h * s, (h(i) * s + 12.0 if i else 0) + region_h + (h(r) * s + 12.0 if (r and i) else 0)) + 28
            if iso_x + max(region_w, w(i) * s) > avail_w + 14 or total_h > avail_h:
                continue
        return s
    return SCALES[-1]


def _extra_rows(extras: list[View], avail_w_mm: float, s: float) -> list[list[tuple[View, float, float]]]:
    """Flow extra views left to right in rows of at most avail_w_mm (sheet mm): [(view, w, h)] per row."""
    rows: list[list[tuple[View, float, float]]] = []
    row: list[tuple[View, float, float]] = []
    x = 0.0
    for v in extras:
        es = s * (v.scale_mult or 1.0)
        wv, hv = (v.bbox[2] - v.bbox[0]) * es, (v.bbox[3] - v.bbox[1]) * es
        if row and x + wv > avail_w_mm:
            rows.append(row); row, x = [], 0.0
        row.append((v, wv, hv)); x += wv + 12.0
    if row:
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------- more view kinds

def _clip_polylines(pls: list[np.ndarray], inside) -> list[np.ndarray]:
    """Cut polylines where `inside(x, y)` changes, keeping only the inside runs (boundary crossings
    interpolated by bisection)."""
    out = []
    for pl in pls:
        run: list = []
        prev = None
        for q in pl:
            cur = inside(q[0], q[1])
            if prev is not None:
                pq = prev[0]
                if cur != prev[1]:                               # crossing: find the boundary point
                    a, b = (pq, q) if prev[1] else (q, pq)       # a inside, b outside
                    for _ in range(24):
                        m = (a + b) / 2
                        if inside(m[0], m[1]):
                            a = m
                        else:
                            b = m
                    if prev[1]:
                        run.append(a); out.append(np.array(run)); run = []
                    else:
                        run = [a]
            if cur:
                run.append(np.array(q, dtype=float))
            prev = (np.array(q, dtype=float), cur)
        if len(run) > 1:
            out.append(np.array(run))
    return [r for r in out if len(r) > 1]


def _bbox_of(pls: list[np.ndarray], fallback) -> tuple[float, float, float, float]:
    if not pls:
        return fallback
    pts = np.vstack(pls)
    return (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))


def _detail_view(parts: Parts, name: str, spec: dict) -> View:
    pv = project(parts, spec["parent"])
    cx, cy, r = spec["x"], spec["y"], spec["r"]
    inside = lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    v = View(name, _clip_polylines(pv.visible, inside), _clip_polylines(pv.hidden, inside),
             [c for c in pv.circles if inside(c[0], c[1])], (cx - r, cy - r, cx + r, cy + r))
    v.hatch = _clip_polylines(pv.hatch, inside)
    v.holes = [h for h in pv.holes if inside(h["x"], h["y"])]
    v.cu, v.cv = pv.cu, pv.cv
    v.arcs = [a for a in pv.arcs if inside(a[0], a[1])]
    v.angles = [a for a in pv.angles if inside(a[0], a[1])]
    v.scale_mult = float(spec.get("scale", 2.0))
    v.detail = dict(spec)
    return v


def _broken_view(parts: Parts, name: str, spec: dict) -> View:
    pv = project(parts, spec["parent"])
    x1, x2, gap = spec["x1"], spec["x2"], spec["gap"]
    shift = (x2 - x1) - gap
    def moved(pls):
        left = _clip_polylines(pls, lambda x, y: x < x1)
        right = [pl - np.array([shift, 0.0]) for pl in _clip_polylines(pls, lambda x, y: x > x2)]
        return left + right
    v = View(name, moved(pv.visible), moved(pv.hidden), [(c[0] - shift, c[1], c[2]) if c[0] > x2 else c for c in pv.circles if not (x1 <= c[0] <= x2)], (0, 0, 0, 0))
    v.bbox = _bbox_of(v.visible + v.hidden, pv.bbox)
    v.hatch = moved(pv.hatch)
    v.holes = [dict(h, x=h["x"] - shift) if h["x"] > x2 else h for h in pv.holes if not (x1 <= h["x"] <= x2)]
    v.cu, v.cv = pv.cu, pv.cv
    v.broken = {"x1": x1, "x2": x1 + gap, "gap": gap, "removed": x2 - x1}
    v.centerlines = [(a - shift if a > x2 else a, b, c - shift if c > x2 else c, d) for a, b, c, d in pv.centerlines if not (x1 <= a <= x2 and x1 <= c <= x2)]
    return v


def _half_section_view(parts: Parts, name: str, sec: dict) -> View:
    from .render import section_parts
    base = SECTIONS[sec["plane"]][0]
    full = project(parts, base)
    halfp = section_parts(parts, plane=sec["plane"], offset=sec["offset"], keep=sec["keep"])
    cut = project(halfp, base)
    cut.hatch = _hatch(halfp, base, sec, (full.cu, full.cv))
    right, up = _view_axes(base)
    n = {"XZ": Vector(0, 1, 0), "XY": Vector(0, 0, 1), "YZ": Vector(1, 0, 0)}[sec["half"]["plane"]]
    off = sec["half"]["offset"]
    if abs(n.dot(right)) > 0.5:
        u_div = (n * off).dot(right) - full.cu
        left = lambda x, y: x <= u_div
    else:
        u_div = (n * off).dot(up) - full.cv
        left = lambda x, y: y <= u_div
    right_side = lambda x, y: not left(x, y)
    v = View(name, _clip_polylines(cut.visible, left) + _clip_polylines(full.visible, right_side),
             _clip_polylines(full.hidden, right_side), [c for c in full.circles if right_side(c[0], c[1])], full.bbox)
    v.hatch = _clip_polylines(cut.hatch, left)
    v.holes = [h for h in full.holes if right_side(h["x"], h["y"])]
    v.cu, v.cv = full.cu, full.cv
    v.arcs = [a for a in full.arcs if right_side(a[0], a[1])]
    v.section = dict(sec, label="A", half_div=u_div, half_axis=("x" if abs(n.dot(right)) > 0.5 else "y"))
    v.centerlines = full.centerlines
    return v


def _arcs_of(edges) -> list[tuple[float, float, float, float, float]]:
    out = []
    for e in edges:
        if e.geom_type == bd.GeomType.CIRCLE and not e.is_closed:
            c = e.arc_center
            a, b = e.position_at(0), e.position_at(1)
            out.append((round(c.X, 4), round(c.Y, 4), round(e.radius, 4), math.degrees(math.atan2(a.Y - c.Y, a.X - c.X)), math.degrees(math.atan2(b.Y - c.Y, b.X - c.X))))
    return out


def _corner_angles(edges, limit: int = 2) -> list[tuple[float, float, float, float, float]]:
    """Sloped corners: pairs of visible straight edges meeting at a point at an angle that is not a
    multiple of 90 degrees (chamfers, wedges) - the first few distinct angles."""
    lines = [(e.position_at(0), e.position_at(1)) for e in edges if e.geom_type == bd.GeomType.LINE and e.length > 0.5]
    out, seen = [], set()
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            for pi in lines[i]:
                for pj in lines[j]:
                    if (pi - pj).length > 1e-6:
                        continue
                    oi = lines[i][1] if pi is lines[i][0] else lines[i][0]
                    oj = lines[j][1] if pj is lines[j][0] else lines[j][0]
                    d1 = math.degrees(math.atan2(oi.Y - pi.Y, oi.X - pi.X)); d2 = math.degrees(math.atan2(oj.Y - pj.Y, oj.X - pj.X))
                    ang = abs((d2 - d1 + 180) % 360 - 180)
                    if any(abs(ang - k) < 2 for k in (0, 90, 180)):
                        continue
                    ang = min(ang, 180 - ang)                 # the acute angle, as a chamfer or taper is dimensioned
                    key = round(ang, 1)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((round(pi.X, 4), round(pi.Y, 4), d1, d2, round(ang, 2)))
                    if len(out) >= limit:
                        return out
    return out


def _side_centerlines(parts: Parts, name: str, centre: Vector) -> list[tuple[float, float, float, float]]:
    """Dash-dot axes of holes whose axis lies in the view plane (seen from the side)."""
    from . import measure
    direction = Vector(*view_dir(name)[0]).normalized()
    right, up = _view_axes(name)
    out = []
    for p in parts:
        try:
            holes = measure.holes(p.shape)
        except Exception:
            continue
        for h in holes:
            ax = h.axis if isinstance(h.axis, Vector) else Vector(*h.axis)
            if abs(ax.dot(direction)) > 1e-3:
                continue
            c = h.center if isinstance(h.center, Vector) else Vector(*h.center)
            half = (h.depth if h.depth else 10.0) / 2 + 2.0
            a, b = c - ax * half, c + ax * half
            rel_a, rel_b = a - centre, b - centre
            out.append((rel_a.dot(right), rel_a.dot(up), rel_b.dot(right), rel_b.dot(up)))
    return out[:12]


def layout(views: dict[str, View], sheet: str = "A4", margin: float = 15.0, projection: str = "third") -> tuple[float, float, float]:
    """Place the views: third angle (front bottom-left, top above it, right beside it, iso top-right)
    or first angle (top below the front, the right-side view on the left). Returns (sheet_w, sheet_h,
    scale); each view's ox/oy = sheet position of its bbox origin (mm, y up). Extra views (sections,
    details, auxiliary, broken) go in a row under the isometric slot, each at its own scale."""
    sheet_w, sheet_h = SHEETS.get(sheet, SHEETS["A4"])
    extras = [v for k, v in views.items() if k not in ("front", "top", "right", "iso")]
    if extras and "iso" not in views:
        views = dict(views)
        views["iso"] = extras[0]                           # the first extra view takes the isometric's slot
    s = _fit_scale(views, sheet_w, sheet_h, margin)
    es = lambda v: s * (v.scale_mult or 1.0)
    gap = 12.0 / s
    f, t, r, i = views.get("front"), views.get("top"), views.get("right"), views.get("iso")
    first = projection == "first"
    x0, y0 = margin + 16 + (((r.bbox[2] - r.bbox[0]) * es(r) + gap * s + 18) if (first and r) else 0), margin + 36 + 16
    if first and t:
        y0 += (t.bbox[3] - t.bbox[1]) * es(t) + gap * s + 12
    if f:
        f.ox, f.oy = x0 - f.bbox[0] * es(f), y0 - f.bbox[1] * es(f)
    if t:
        if first:
            base_y = (f.oy + f.bbox[1] * es(f) - gap * s - 12) if f else y0
            t.ox, t.oy = (f.ox if f else x0 - t.bbox[0] * es(t)), base_y - t.bbox[3] * es(t)
        else:
            base_y = (f.bbox[3] * es(f) + f.oy + gap * s) if f else y0
            t.ox, t.oy = (f.ox if f else x0 - t.bbox[0] * es(t)), base_y - t.bbox[1] * es(t)
        if f:
            t.ox = f.ox                                    # aligned with the front view (same x)
    if r:
        if first:
            base_x = (f.ox + f.bbox[0] * es(f) - gap * s - 18) if f else x0
            r.ox, r.oy = base_x - r.bbox[2] * es(r), (f.oy if f else y0 - r.bbox[1] * es(r))
        else:
            base_x = (f.bbox[2] * es(f) + f.ox + gap * s + 18) if f else x0
            r.ox, r.oy = base_x - r.bbox[0] * es(r), (f.oy if f else y0 - r.bbox[1] * es(r))
    if i:
        ix = max((r.bbox[2] * es(r) + r.ox) if r else 0, (f.bbox[2] * es(f) + f.ox) if f else 0) + gap * s + 18
        iy = max((t.bbox[3 if first else 1] * es(t) + t.oy) if t else y0, (r.bbox[3] * es(r) + r.oy + gap * s) if r else y0)
        if first and t and f:
            iy = f.oy + f.bbox[3] * es(f) + gap * s
        if ix + (i.bbox[2] - i.bbox[0]) * es(i) > sheet_w - margin:        # fall back to the top-right corner
            ix = sheet_w - margin - (i.bbox[2] - i.bbox[0]) * es(i)
        i.ox, i.oy = ix - i.bbox[0] * es(i), iy - i.bbox[1] * es(i)
    extra = [v for v in extras if v is not i]
    if extra:                                                              # further views: rows under the iso slot, wrapping
        x_start = (i.ox + i.bbox[0] * es(i)) if i else (margin + 16)
        y_top = (i.oy + i.bbox[1] * es(i) - 12.0) if i else (margin + 36 + 16)
        for row in _extra_rows(extra, sheet_w - margin - x_start, s):
            row_h = max(hv for _, _, hv in row)
            x_next = x_start
            for v, wv, hv in row:
                v.ox, v.oy = x_next - v.bbox[0] * es(v), (y_top - row_h) - v.bbox[1] * es(v)
                x_next += wv + 12.0
            y_top -= row_h + 12.0
    return sheet_w, sheet_h, s


def _svg_path(pl: np.ndarray, ox: float, oy: float, s: float, sheet_h: float) -> str:
    pts = [(ox + x * s, sheet_h - (oy + y * s)) for x, y in pl]
    return "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in pts)


def _dim_h(x1: float, x2: float, y: float, text: str, sheet_h: float, ext_from: float, ansi: bool = False) -> str:
    """A horizontal dimension between x1..x2 at sheet height y (y up), extension lines from ext_from.
    ISO puts the text above the line; ANSI breaks the line and writes the text in the gap."""
    Y = sheet_h - y
    e1 = sheet_h - ext_from
    ext = (f'<line x1="{x1:.2f}" y1="{e1:.2f}" x2="{x1:.2f}" y2="{Y + 2:.2f}"/>'
           f'<line x1="{x2:.2f}" y1="{e1:.2f}" x2="{x2:.2f}" y2="{Y + 2:.2f}"/>')
    if ansi:
        mx = (x1 + x2) / 2; half = max(2.0, 1.1 * len(text))
        return (ext + f'<line x1="{x1:.2f}" y1="{Y:.2f}" x2="{mx - half:.2f}" y2="{Y:.2f}" marker-start="url(#a0)"/>'
                f'<line x1="{mx + half:.2f}" y1="{Y:.2f}" x2="{x2:.2f}" y2="{Y:.2f}" marker-end="url(#a1)"/>'
                f'<text x="{mx:.2f}" y="{Y + 1.1:.2f}" text-anchor="middle">{text}</text>')
    return (ext + f'<line x1="{x1:.2f}" y1="{Y:.2f}" x2="{x2:.2f}" y2="{Y:.2f}" marker-start="url(#a0)" marker-end="url(#a1)"/>'
            f'<text x="{(x1 + x2) / 2:.2f}" y="{Y - 1.2:.2f}" text-anchor="middle">{text}</text>')


def _dim_v(y1: float, y2: float, x: float, text: str, sheet_h: float, ext_from: float, ansi: bool = False) -> str:
    Y1, Y2 = sheet_h - y1, sheet_h - y2
    e1 = ext_from
    ext = (f'<line x1="{e1:.2f}" y1="{Y1:.2f}" x2="{x - 2:.2f}" y2="{Y1:.2f}"/>'
           f'<line x1="{e1:.2f}" y1="{Y2:.2f}" x2="{x - 2:.2f}" y2="{Y2:.2f}"/>')
    if ansi:                                                  # unidirectional: horizontal text in a gap of the line
        my = (Y1 + Y2) / 2; half = 2.2
        return (ext + f'<line x1="{x:.2f}" y1="{Y1:.2f}" x2="{x:.2f}" y2="{my + half:.2f}" marker-start="url(#a0)"/>'
                f'<line x1="{x:.2f}" y1="{my - half:.2f}" x2="{x:.2f}" y2="{Y2:.2f}" marker-end="url(#a1)"/>'
                f'<text x="{x:.2f}" y="{my + 1.1:.2f}" text-anchor="middle">{text}</text>')
    return (ext + f'<line x1="{x:.2f}" y1="{Y1:.2f}" x2="{x:.2f}" y2="{Y2:.2f}" marker-start="url(#a0)" marker-end="url(#a1)"/>'
            f'<text x="{x + 1.2:.2f}" y="{(Y1 + Y2) / 2:.2f}" text-anchor="middle" transform="rotate(-90 {x + 1.2:.2f} {(Y1 + Y2) / 2:.2f})">{text}</text>')


def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _dim_between(p1, p2, kind: str, offset: float, text: str, sheet_h: float, ansi: bool = False) -> str:
    """A linear dimension between two sheet points (mm, y up): horizontal, vertical or aligned, with the
    dimension line `offset` mm away (sign = side) and extension lines from each point."""
    (x1, y1), (x2, y2) = p1, p2
    if kind == "horizontal":
        y = (max(y1, y2) if offset >= 0 else min(y1, y2)) + offset
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        Y = sheet_h - y
        ext = (f'<line x1="{x1:.2f}" y1="{sheet_h - y1:.2f}" x2="{x1:.2f}" y2="{Y + (2 if offset >= 0 else -2):.2f}"/>'
               f'<line x1="{x2:.2f}" y1="{sheet_h - y2:.2f}" x2="{x2:.2f}" y2="{Y + (2 if offset >= 0 else -2):.2f}"/>')
        return ext + _dim_h(lo, hi, y, text, sheet_h, y, ansi).split("/>", 2)[2]
    if kind == "vertical":
        x = (max(x1, x2) if offset >= 0 else min(x1, x2)) + offset
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        ext = (f'<line x1="{x1:.2f}" y1="{sheet_h - y1:.2f}" x2="{x - (2 if offset >= 0 else -2):.2f}" y2="{sheet_h - y1:.2f}"/>'
               f'<line x1="{x2:.2f}" y1="{sheet_h - y2:.2f}" x2="{x - (2 if offset >= 0 else -2):.2f}" y2="{sheet_h - y2:.2f}"/>')
        return ext + _dim_v(lo, hi, x, text, sheet_h, x, ansi).split("/>", 2)[2]
    # aligned: parallel to the segment, offset along its normal
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / L * offset, dx / L * offset
    ax, ay, bx, by = x1 + nx, y1 + ny, x2 + nx, y2 + ny
    ex, ey = nx / (abs(offset) or 1.0) * 2, ny / (abs(offset) or 1.0) * 2                  # extension lines run 2 mm past the dimension line
    ang = -math.degrees(math.atan2(dy, dx))
    if ang > 90 or ang < -90:
        ang += 180
    mx, my = (ax + bx) / 2, sheet_h - (ay + by) / 2
    ty = my - 1.2
    return (f'<line x1="{x1:.2f}" y1="{sheet_h - y1:.2f}" x2="{ax + ex:.2f}" y2="{sheet_h - (ay + ey):.2f}"/>'
            f'<line x1="{x2:.2f}" y1="{sheet_h - y2:.2f}" x2="{bx + ex:.2f}" y2="{sheet_h - (by + ey):.2f}"/>'
            f'<line x1="{ax:.2f}" y1="{sheet_h - ay:.2f}" x2="{bx:.2f}" y2="{sheet_h - by:.2f}" marker-start="url(#a0)" marker-end="url(#a1)"/>'
            f'<text x="{mx:.2f}" y="{ty:.2f}" text-anchor="middle" transform="rotate({ang:.1f} {mx:.2f} {my:.2f})">{text}</text>')


def user_dimensions(vs: dict, dims, s: float, sheet_h: float, ansi: bool) -> list[str]:
    """User-placed dimensions: [{"view", "from": [u, v], "to": [u, v], "kind": horizontal|vertical|aligned
    (default by direction), "offset": mm, "text"}] and groups {"view", "points": [[u, v], ...], "axis":
    "x"|"y", "offset", "baseline": false} (a chain between consecutive points, or every point from the
    first)."""
    out = []
    for d in dims or []:
        v = vs.get(d.get("view", "front"))
        if v is None:
            continue
        vsz = s * (v.scale_mult or 1.0)
        to_sheet = lambda p: (v.ox + float(p[0]) * vsz, v.oy + float(p[1]) * vsz)
        off = float(d.get("offset", 8))
        if d.get("points"):
            pts = [tuple(map(float, p)) for p in d["points"]]
            axis = (d.get("axis") or ("x" if abs(pts[-1][0] - pts[0][0]) >= abs(pts[-1][1] - pts[0][1]) else "y")).lower()
            kind = "horizontal" if axis == "x" else "vertical"
            pairs = [(pts[0], q) for q in pts[1:]] if d.get("baseline") else list(zip(pts, pts[1:]))
            for i, (a, b) in enumerate(pairs):
                step = i * 7.0 * (1 if off >= 0 else -1) if d.get("baseline") else 0.0
                val = abs(b[0] - a[0]) if kind == "horizontal" else abs(b[1] - a[1])
                if val < 1e-9:
                    continue
                out.append(_dim_between(to_sheet(a), to_sheet(b), kind, off + step, _fmt(val), sheet_h, ansi))
            continue
        a, b = tuple(map(float, d["from"])), tuple(map(float, d["to"]))
        kind = (d.get("kind") or ("horizontal" if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else "vertical")).lower()
        val = {"horizontal": abs(b[0] - a[0]), "vertical": abs(b[1] - a[1])}.get(kind, math.hypot(b[0] - a[0], b[1] - a[1]))
        out.append(_dim_between(to_sheet(a), to_sheet(b), kind, off, str(d.get("text") or _fmt(val)), sheet_h, ansi))
    return out


def parts_list(parts: Parts) -> list[dict]:
    """The bill of materials of an assembly: one row per kind of part (identical parts counted),
    numbered as the balloons on the isometric view."""
    from .assembly import bom
    rows = []
    for r in bom(parts):
        rows.append({"item": r["item"], "name": r["name"], "qty": r["qty"], "parts": r["parts"], "material": r["material"], "volume": r["volume"], "mass_g": r["mass_g"]})
    return rows


def balloons(parts: Parts, plist: list[dict], view: "View") -> list[dict]:
    """Balloon positions (view coordinates) for every part of the isometric view: the part's centre
    and the item number of its parts-list row."""
    if view.name != "iso":
        return []
    right, up = _view_axes("iso")
    item_of = {n: r["item"] for r in plist for n in r.get("parts", [])}
    out = []
    for p in parts:
        try:
            c = p.shape.bounding_box().center()
        except Exception:
            continue
        out.append({"part": p.name, "item": item_of.get(p.name, 0), "x": c.dot(right) - view.cu, "y": c.dot(up) - view.cv})
    return out


GDT = {"flatness": "⏤", "straightness": "⏤", "circularity": "○", "cylindricity": "⌭", "profile": "⌓", "parallelism": "∥", "perpendicularity": "⟂",
       "angularity": "∠", "position": "⌖", "concentricity": "◎", "symmetry": "⌯", "runout": "↗", "total_runout": "⌰"}


def drawing_svg(parts: Parts, name: str, views: Sequence[str] = ("front", "top", "right", "iso"), sheet: str = "A4",
                material: str | None = None, mass_g: float | None = None, notes: str | Sequence[str] = "", style: str = "iso",
                projection: str = "third", annotations: Sequence[dict] | None = None, gdt: Sequence[dict] | None = None,
                datums: Sequence[dict] | None = None, scales: dict | None = None, auto_dims: bool = True, title: str | None = None,
                dims: Sequence[dict] | None = None) -> str:
    """An SVG drawing sheet of the parts: the requested views (named, sections, half sections, details,
    auxiliary and broken views), overall dimensions, radius and angle dimensions, centre marks, hole
    callouts, notes, leaders, GD&T frames and datums, balloons for assemblies, and the title block.
    `style` iso|ansi sets the dimension text style, `projection` third|first the view arrangement,
    `scales` {view: factor} a per-view scale."""
    vs = {v: project(parts, v) for v in views if v in VIEWS or ":" in v}
    for k, v in vs.items():
        if scales and k in scales:
            v.scale_mult = float(scales[k])
    sheet_w, sheet_h, s = layout(vs, sheet, projection=projection)
    table = hole_table(vs)
    ansi = style.lower() == "ansi"
    note_lines = [notes] if isinstance(notes, str) and notes else (list(notes) if not isinstance(notes, str) else [])
    annotations = list(annotations or []); gdt = list(gdt or []); datums = list(datums or [])
    bb = parts.bbox
    size = bb.size
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{sheet_w}mm" height="{sheet_h}mm" viewBox="0 0 {sheet_w} {sheet_h}" font-family="Helvetica, Arial, sans-serif" font-size="3">',
           '<defs><marker id="a0" markerWidth="4" markerHeight="4" refX="0.5" refY="2" orient="auto"><path d="M4 0 L0 2 L4 4 z" fill="#222"/></marker>'
           '<marker id="a1" markerWidth="4" markerHeight="4" refX="3.5" refY="2" orient="auto"><path d="M0 0 L4 2 L0 4 z" fill="#222"/></marker></defs>',
           f'<rect x="0" y="0" width="{sheet_w}" height="{sheet_h}" fill="#fff"/>',
           f'<rect x="5" y="5" width="{sheet_w - 10}" height="{sheet_h - 10}" fill="none" stroke="#222" stroke-width="0.5"/>']
    sheet_s = s
    for v in vs.values():
        s = sheet_s * (v.scale_mult or 1.0)
        clip = ""
        if v.detail:
            cx, cy = v.ox + v.detail["x"] * s, sheet_h - (v.oy + v.detail["y"] * s)
            out.append(f'<defs><clipPath id="clip-{_esc(v.name)}"><circle cx="{cx:.2f}" cy="{cy:.2f}" r="{v.detail["r"] * s:.2f}"/></clipPath></defs>')
            out.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{v.detail["r"] * s:.2f}" fill="none" stroke="#222" stroke-width="0.35" stroke-dasharray="3 1.5"/>')
            clip = f' clip-path="url(#clip-{_esc(v.name)})"'
        out.append(f'<g id="view-{v.name}"{clip} fill="none" stroke="#111" stroke-width="0.45" stroke-linecap="round" stroke-linejoin="round">')
        for pl in v.visible:
            out.append(f'<path d="{_svg_path(pl, v.ox, v.oy, s, sheet_h)}"/>')
        out.append('</g>')
        if v.hidden:
            out.append(f'<g{clip} fill="none" stroke="#555" stroke-width="0.25" stroke-dasharray="2.4 1.2">')
            for pl in v.hidden:
                out.append(f'<path d="{_svg_path(pl, v.ox, v.oy, s, sheet_h)}"/>')
            out.append('</g>')
        if v.hatch:
            out.append(f'<g{clip} stroke="#333" stroke-width="0.2">')
            for pl in v.hatch:
                out.append(f'<path d="{_svg_path(pl, v.ox, v.oy, s, sheet_h)}"/>')
            out.append('</g>')
        # centre marks (circles seen end-on) and centrelines (holes seen from the side), dash-dot
        marks = []
        for (ccx, ccy, cr) in v.circles:
            mx, my = v.ox + ccx * s, sheet_h - (v.oy + ccy * s)
            e = cr * s + 2.5
            marks.append(f'<line x1="{mx - e:.2f}" y1="{my:.2f}" x2="{mx + e:.2f}" y2="{my:.2f}"/><line x1="{mx:.2f}" y1="{my - e:.2f}" x2="{mx:.2f}" y2="{my + e:.2f}"/>')
        for (ax, ay, bx, by) in v.centerlines:
            marks.append(f'<line x1="{v.ox + ax * s:.2f}" y1="{sheet_h - (v.oy + ay * s):.2f}" x2="{v.ox + bx * s:.2f}" y2="{sheet_h - (v.oy + by * s):.2f}"/>')
        if marks:
            out.append(f'<g class="centerlines" stroke="#333" stroke-width="0.2" stroke-dasharray="4 1 1 1">' + "".join(marks) + '</g>')
        if v.broken:                                           # break lines closing the removed band
            for bx in (v.broken["x1"], v.broken["x2"]):
                X = v.ox + bx * s; y_lo, y_hi = sheet_h - (v.oy + v.bbox[1] * s) + 2, sheet_h - (v.oy + v.bbox[3] * s) - 2
                zig = " ".join(f"{X + (1.2 if k % 2 else -1.2):.2f} {y_hi + (y_lo - y_hi) * k / 8:.2f}" for k in range(9))
                out.append(f'<polyline class="break" points="{zig}" fill="none" stroke="#111" stroke-width="0.35"/>')
        if auto_dims and v.name != "iso" and not v.name.startswith("aux:"):   # radius and angle dimensions (true size only in orthographic views)
            seen_r = set()
            for (acx, acy, ar, a0, a1) in v.arcs:
                key = round(ar, 2)
                if key in seen_r or len(seen_r) >= 3 or ar < 0.3:
                    continue
                seen_r.add(key)
                mid = math.radians((a0 + a1) / 2 if a1 >= a0 else (a0 + a1 + 360) / 2)
                px, py = v.ox + (acx + ar * math.cos(mid)) * s, sheet_h - (v.oy + (acy + ar * math.sin(mid)) * s)
                tx, ty = px + 6 * math.cos(mid), py - 6 * math.sin(mid)
                out.append(f'<g class="rdim" stroke="#222" stroke-width="0.25" fill="none"><line x1="{px:.2f}" y1="{py:.2f}" x2="{tx:.2f}" y2="{ty:.2f}" marker-start="url(#a0)"/></g>'
                           f'<text x="{tx + (1 if math.cos(mid) >= 0 else -1):.2f}" y="{ty + 1:.2f}" font-size="2.6" fill="#222" text-anchor="{"start" if math.cos(mid) >= 0 else "end"}">R{_fmt(ar)}</text>')
            for (vx, vy, d1, d2, ang) in v.angles:
                px, py = v.ox + vx * s, sheet_h - (v.oy + vy * s)
                r_arc = 7.0
                a1r, a2r = math.radians(d1), math.radians(d2)
                if (d2 - d1 + 360) % 360 > 180:
                    a1r, a2r = a2r, a1r
                x1, y1 = px + r_arc * math.cos(a1r), py - r_arc * math.sin(a1r)
                x2, y2 = px + r_arc * math.cos(a2r), py - r_arc * math.sin(a2r)
                mid = (a1r + a2r) / 2 if a2r > a1r else (a1r + a2r + 2 * math.pi) / 2
                out.append(f'<g class="adim" stroke="#222" stroke-width="0.25" fill="none"><path d="M {x1:.2f} {y1:.2f} A {r_arc:.2f} {r_arc:.2f} 0 0 0 {x2:.2f} {y2:.2f}"/></g>'
                           f'<text x="{px + (r_arc + 2.5) * math.cos(mid):.2f}" y="{py - (r_arc + 2.5) * math.sin(mid) + 1:.2f}" font-size="2.6" fill="#222" text-anchor="middle">{_fmt(ang)}°</text>')
        lx, ly = v.ox + v.bbox[0] * s, sheet_h - (v.oy + v.bbox[1] * s) + 4.5
        scale_txt = f" ({_fmt(s)}:1)" if s >= 1 else f" (1:{_fmt(1 / s)})"
        if v.detail:
            label = f'DETAIL {v.detail["label"]}{scale_txt}'
        elif v.section and v.section.get("half"):
            label = f'HALF SECTION {v.section["label"]}-{v.section["label"]} ({v.section["plane"]} at {_fmt(v.section["offset"])})'
        elif v.section:
            label = f'SECTION {v.section["label"]}-{v.section["label"]} ({v.section["plane"]} at {_fmt(v.section["offset"])})'
        elif v.name.startswith("aux:"):
            label = f'AUXILIARY VIEW {v.name.split(":", 1)[1]}' + (scale_txt if v.scale_mult != 1 else "")
        elif v.broken:
            label = f'{v.broken and v.name.split(":", 1)[1].split("@")[0].upper()} (BROKEN, {_fmt(v.broken["removed"])} mm removed)'
        else:
            label = v.name.upper() + (scale_txt if v.scale_mult != 1 else "")
        out.append(f'<text x="{lx:.2f}" y="{ly:.2f}" fill="#444" font-size="2.8">{label}</text>')
        if v.detail and v.detail["parent"] in vs:            # the detail circle and its letter on the parent view
            pv = vs[v.detail["parent"]]; ps = sheet_s * (pv.scale_mult or 1.0)
            cx, cy, cr = pv.ox + v.detail["x"] * ps, sheet_h - (pv.oy + v.detail["y"] * ps), v.detail["r"] * ps
            out.append(f'<circle class="detail-mark" cx="{cx:.2f}" cy="{cy:.2f}" r="{cr:.2f}" fill="none" stroke="#222" stroke-width="0.35"/>'
                       f'<text x="{cx + cr * 0.75 + 1:.2f}" y="{cy - cr * 0.75:.2f}" font-size="3" fill="#222">{v.detail["label"]}</text>')
        if v.section and v.section["parent"] in vs:        # the cutting-plane line on the parent view
            pv = vs[v.section["parent"]]
            axis = {"XZ": ("y", "top"), "XY": ("z", "front"), "YZ": ("x", "front")}[v.section["plane"]]
            right, up = _view_axes(pv.name)
            n = {"XZ": Vector(0, 1, 0), "XY": Vector(0, 0, 1), "YZ": Vector(1, 0, 0)}[v.section["plane"]]
            off = v.section["offset"]
            if abs(n.dot(up)) > 0.5:                            # horizontal line at v = offset
                y = sheet_h - (pv.oy + ((n * off).dot(up) - pv.cv) * s)
                x1, x2 = pv.ox + pv.bbox[0] * s - 14, pv.ox + pv.bbox[2] * s + 6     # starts left of the dimension lines
                out.append(f'<g stroke="#222" stroke-width="0.5" stroke-dasharray="4 1.5 1 1.5"><line x1="{x1:.2f}" y1="{y:.2f}" x2="{x2:.2f}" y2="{y:.2f}"/></g>'
                           f'<text x="{x1 - 1:.2f}" y="{y - 1.5:.2f}" font-size="2.8" text-anchor="end">{v.section["label"]}</text><text x="{x2 + 1:.2f}" y="{y - 1.5:.2f}" font-size="2.8">{v.section["label"]}</text>')
            else:                                               # vertical line at u = offset
                x = pv.ox + ((n * off).dot(right) - pv.cu) * s
                y1, y2 = sheet_h - (pv.oy + pv.bbox[1] * s) + 6, sheet_h - (pv.oy + pv.bbox[3] * s) - 6
                out.append(f'<g stroke="#222" stroke-width="0.5" stroke-dasharray="4 1.5 1 1.5"><line x1="{x:.2f}" y1="{y1:.2f}" x2="{x:.2f}" y2="{y2:.2f}"/></g>'
                           f'<text x="{x - 1:.2f}" y="{y1 + 3:.2f}" font-size="2.8" text-anchor="middle">{v.section["label"]}</text><text x="{x - 1:.2f}" y="{y2 - 1:.2f}" font-size="2.8" text-anchor="middle">{v.section["label"]}</text>')
        if v.section and v.section.get("half") and v.section.get("half_div") is not None:   # the dividing centreline of a half section
            if v.section["half_axis"] == "x":
                X = v.ox + v.section["half_div"] * s
                out.append(f'<line x1="{X:.2f}" y1="{sheet_h - (v.oy + v.bbox[1] * s) + 3:.2f}" x2="{X:.2f}" y2="{sheet_h - (v.oy + v.bbox[3] * s) - 3:.2f}" stroke="#333" stroke-width="0.2" stroke-dasharray="4 1 1 1"/>')
            else:
                Y = sheet_h - (v.oy + v.section["half_div"] * s)
                out.append(f'<line x1="{v.ox + v.bbox[0] * s - 3:.2f}" y1="{Y:.2f}" x2="{v.ox + v.bbox[2] * s + 3:.2f}" y2="{Y:.2f}" stroke="#333" stroke-width="0.2" stroke-dasharray="4 1 1 1"/>')
        for h in v.holes:
            hx, hy = v.ox + h["x"] * s, sheet_h - (v.oy + h["y"] * s)
            tx, ty = hx + 9, hy - 7
            out.append(f'<g stroke="#1a4d8f" stroke-width="0.3" fill="none"><line x1="{hx:.2f}" y1="{hy:.2f}" x2="{tx - 1:.2f}" y2="{ty + 1:.2f}"/></g>'
                       f'<text x="{tx:.2f}" y="{ty:.2f}" fill="#1a4d8f" font-size="2.8">{h.get("letter", "")}: {h["text"]}</text>')
            for (px, py) in h["points"][1:]:
                qx, qy = v.ox + px * s, sheet_h - (v.oy + py * s)
                out.append(f'<text x="{qx + 2:.2f}" y="{qy - 2:.2f}" fill="#1a4d8f" font-size="2.4">{h.get("letter", "")}</text>')
    # overall dimensions: width under the front view, height beside it, depth beside the top view
    # (a view that carries the user's own dimensions is left to them)
    s = sheet_s
    out.append('<g stroke="#222" stroke-width="0.25" fill="#222">')
    user_views = {str(d.get("view", "front")) for d in (dims or [])}
    f, t, r = (vs.get(k) if k not in user_views else None for k in ("front", "top", "right"))
    if f:
        fs = s * (f.scale_mult or 1.0)
        x1, x2 = f.ox + f.bbox[0] * fs, f.ox + f.bbox[2] * fs
        ybase = f.oy + f.bbox[1] * fs
        out.append(_dim_h(x1, x2, ybase - 9, _fmt(size.X), sheet_h, ybase - 1, ansi))
        y1, y2 = f.oy + f.bbox[1] * fs, f.oy + f.bbox[3] * fs
        out.append(_dim_v(y1, y2, x1 - 9, _fmt(size.Z), sheet_h, x1 - 1, ansi))
    if t:
        ts = s * (t.scale_mult or 1.0)
        y1, y2 = t.oy + t.bbox[1] * ts, t.oy + t.bbox[3] * ts
        x1 = t.ox + t.bbox[0] * ts
        out.append(_dim_v(y1, y2, x1 - 9, _fmt(size.Y), sheet_h, x1 - 1, ansi))
    elif r:
        x1, x2 = r.ox + r.bbox[0] * s, r.ox + r.bbox[2] * s
        ybase = r.oy + r.bbox[1] * s
        out.append(_dim_h(x1, x2, ybase - 9, _fmt(size.Y), sheet_h, ybase - 1))
    out.append('</g>')
    # hole table (above the title block): letter, size, count, positions in the view's coordinates
    tb_w, tb_h = 120.0, 30.0
    tx, ty = sheet_w - 5 - tb_w, sheet_h - 5 - tb_h
    plist = parts_list(parts) if len(parts) > 1 else []
    if plist:                                              # parts list for assemblies, to the left of the title block
        row_h = 4.2
        ph = row_h * (len(plist) + 1) + 2
        px0, py0 = tx - 3 - 100, sheet_h - 5 - ph
        out.append(f'<g font-size="2.4" fill="#222"><rect x="{px0:.1f}" y="{py0:.1f}" width="100" height="{ph:.1f}" fill="none" stroke="#222" stroke-width="0.4"/>')
        out.append(f'<text x="{px0 + 2:.1f}" y="{py0 + 3.4:.1f}" fill="#777" font-size="2">ITEM</text><text x="{px0 + 10:.1f}" y="{py0 + 3.4:.1f}" fill="#777" font-size="2">PART</text><text x="{px0 + 36:.1f}" y="{py0 + 3.4:.1f}" fill="#777" font-size="2">QTY</text><text x="{px0 + 44:.1f}" y="{py0 + 3.4:.1f}" fill="#777" font-size="2">MATERIAL</text>'
                   f'<text x="{px0 + 66:.1f}" y="{py0 + 3.4:.1f}" fill="#777" font-size="2">VOLUME mm³</text><text x="{px0 + 88:.1f}" y="{py0 + 3.4:.1f}" fill="#777" font-size="2">MASS g</text>')
        for i, r in enumerate(plist[:8]):
            y = py0 + 3.4 + row_h * (i + 1)
            out.append(f'<text x="{px0 + 2:.1f}" y="{y:.1f}">{r.get("item", i + 1)}</text><text x="{px0 + 10:.1f}" y="{y:.1f}">{_esc(r["name"])}</text><text x="{px0 + 36:.1f}" y="{y:.1f}">{r.get("qty", 1)}</text><text x="{px0 + 44:.1f}" y="{y:.1f}">{_esc(r["material"])}</text>'
                       f'<text x="{px0 + 66:.1f}" y="{y:.1f}">{_fmt(r["volume"])}</text><text x="{px0 + 88:.1f}" y="{y:.1f}">{_fmt(r["mass_g"]) if r["mass_g"] is not None else "-"}</text>')
        out.append('</g>')
        if "iso" in vs:                                        # balloons on the isometric view
            iv = vs["iso"]; s_iso = s
            for k, b in enumerate(balloons(parts, plist, iv)):
                bx, by = iv.ox + b["x"] * s_iso, sheet_h - (iv.oy + b["y"] * s_iso)
                ang = math.radians(35 + (k % 4) * 25)
                cx, cy = bx + 11 * math.cos(ang), by - 11 * math.sin(ang)
                out.append(f'<g class="balloon" stroke="#1a4d8f" stroke-width="0.3" fill="none"><line x1="{bx:.2f}" y1="{by:.2f}" x2="{cx:.2f}" y2="{cy:.2f}"/><circle cx="{cx:.2f}" cy="{cy:.2f}" r="2.6" fill="#fff"/></g>'
                           f'<text x="{cx:.2f}" y="{cy + 1:.2f}" fill="#1a4d8f" font-size="2.6" text-anchor="middle">{b["item"]}</text>')
    if table:
        row_h = 4.2
        th = row_h * (len(table) + 1) + 2
        ty0 = ty - 3 - th
        out.append(f'<g font-size="2.4" fill="#222"><rect x="{tx}" y="{ty0:.1f}" width="{tb_w}" height="{th:.1f}" fill="none" stroke="#222" stroke-width="0.4"/>')
        out.append(f'<text x="{tx + 2:.1f}" y="{ty0 + 3.4:.1f}" fill="#777" font-size="2">HOLE</text><text x="{tx + 12:.1f}" y="{ty0 + 3.4:.1f}" fill="#777" font-size="2">SIZE</text>'
                   f'<text x="{tx + 40:.1f}" y="{ty0 + 3.4:.1f}" fill="#777" font-size="2">QTY</text><text x="{tx + 50:.1f}" y="{ty0 + 3.4:.1f}" fill="#777" font-size="2">POSITIONS from the bottom-left corner of the view (x, y)</text>')
        for i, r in enumerate(table):
            y = ty0 + 3.4 + row_h * (i + 1)
            pos = "  ".join(f"({_fmt(px)}, {_fmt(py)})" for px, py in r["points"][:6]) + ("…" if len(r["points"]) > 6 else "")
            out.append(f'<text x="{tx + 2:.1f}" y="{y:.1f}">{r["letter"]}</text><text x="{tx + 12:.1f}" y="{y:.1f}">{_esc(r["text"])}</text>'
                       f'<text x="{tx + 40:.1f}" y="{y:.1f}">{r["count"]}</text><text x="{tx + 50:.1f}" y="{y:.1f}">{_esc(r["view"].upper() + ": " + pos)}</text>')
        out.append('</g>')
    # annotations: leaders with text, GD&T feature control frames, datum symbols (view coordinates)
    for a in annotations:
        v = vs.get(a.get("view", "front"))
        if v is None:
            continue
        vsz = s * (v.scale_mult or 1.0)
        px, py = v.ox + float(a["at"][0]) * vsz, sheet_h - (v.oy + float(a["at"][1]) * vsz)
        dx, dy = (float(a.get("offset", [10, 8])[0]), float(a.get("offset", [10, 8])[1]))
        tx_, ty_ = px + dx, py - dy
        out.append(f'<g class="note" stroke="#222" stroke-width="0.25" fill="none"><line x1="{px:.2f}" y1="{py:.2f}" x2="{tx_:.2f}" y2="{ty_:.2f}" marker-start="url(#a0)"/><line x1="{tx_:.2f}" y1="{ty_:.2f}" x2="{tx_ + 3 * (1 if dx >= 0 else -1):.2f}" y2="{ty_:.2f}"/></g>'
                   f'<text x="{tx_ + 3.5 * (1 if dx >= 0 else -1):.2f}" y="{ty_ - 0.8:.2f}" font-size="2.6" fill="#222" text-anchor="{"start" if dx >= 0 else "end"}">{_esc(a.get("text", ""))}</text>')
    if dims:
        out.append('<g class="userdims" fill="#222" stroke="#222" stroke-width="0.25">' + "".join(user_dimensions(vs, dims, s, sheet_h, ansi)) + '</g>')
    for g in gdt:
        v = vs.get(g.get("view", "front"))
        if v is None:
            continue
        vsz = s * (v.scale_mult or 1.0)
        px, py = v.ox + float(g["at"][0]) * vsz, sheet_h - (v.oy + float(g["at"][1]) * vsz)
        cells = [GDT.get(str(g.get("symbol", "flatness")).lower(), "?"), str(g.get("tolerance", "0.1"))] + [str(d) for d in (g.get("datums") or ([g["datum"]] if g.get("datum") else []))]
        widths = [6.0] + [max(6.0, 2.0 + 1.8 * len(c)) for c in cells[1:]]
        fx, fy = px + 8, py - 10
        out.append(f'<g class="gdt" stroke="#222" stroke-width="0.3" fill="none"><line x1="{px:.2f}" y1="{py:.2f}" x2="{fx:.2f}" y2="{fy + 2.5:.2f}" marker-start="url(#a0)"/>')
        x = fx
        for c, w in zip(cells, widths):
            out.append(f'<rect x="{x:.2f}" y="{fy - 2.5:.2f}" width="{w:.2f}" height="5" fill="#fff"/>')
            out.append(f'<text x="{x + w / 2:.2f}" y="{fy + 1.1:.2f}" font-size="2.8" fill="#222" stroke="none" text-anchor="middle">{_esc(c)}</text>')
            x += w
        out.append('</g>')
    for d in datums:
        v = vs.get(d.get("view", "front"))
        if v is None:
            continue
        vsz = s * (v.scale_mult or 1.0)
        px, py = v.ox + float(d["at"][0]) * vsz, sheet_h - (v.oy + float(d["at"][1]) * vsz)
        out.append(f'<g class="datum" stroke="#222" stroke-width="0.3" fill="#222"><path d="M {px:.2f} {py:.2f} L {px - 1.8:.2f} {py + 3:.2f} L {px + 1.8:.2f} {py + 3:.2f} z"/><line x1="{px:.2f}" y1="{py + 3:.2f}" x2="{px:.2f}" y2="{py + 7:.2f}"/>'
                   f'<rect x="{px - 2.6:.2f}" y="{py + 7:.2f}" width="5.2" height="5.2" fill="#fff"/><text x="{px:.2f}" y="{py + 10.9:.2f}" font-size="3" stroke="none" text-anchor="middle">{_esc(d.get("letter", "A"))}</text></g>')
    # notes block (lower left, above the frame)
    if note_lines:
        ny = sheet_h - 9 - 4 * len(note_lines)
        out.append('<g class="notes" font-size="2.6" fill="#222">')
        out.append(f'<text x="8" y="{ny:.1f}" fill="#777" font-size="2">NOTES</text>')
        for k, line in enumerate(note_lines):
            out.append(f'<text x="8" y="{ny + 4 * (k + 1):.1f}">{k + 1}. {_esc(line)}</text>')
        out.append('</g>')
    # title block, with the projection symbol
    date = _dt.date.today().isoformat()
    rows = [("PART", title or name), ("SCALE", f"{_fmt(s)}:1" if s >= 1 else f"1:{_fmt(1 / s)}"), ("SIZE", f"{_fmt(size.X)} x {_fmt(size.Y)} x {_fmt(size.Z)} mm"),
            ("MATERIAL", (material or "-") + (f", {mass_g:g} g" if mass_g else "")), ("DATE", date), ("SHEET", f"{sheet} · {projection} angle · {'ANSI' if ansi else 'ISO'} · mm")]
    out.append(f'<g font-size="2.6" fill="#222"><rect x="{tx}" y="{ty}" width="{tb_w}" height="{tb_h}" fill="none" stroke="#222" stroke-width="0.5"/>')
    for k, (label, val) in enumerate(rows):
        col, row = divmod(k, 3)
        cx, cy = tx + 3 + col * 60, ty + 7 + row * 8
        out.append(f'<text x="{cx:.1f}" y="{cy:.1f}" fill="#777" font-size="2">{label}</text><text x="{cx:.1f}" y="{cy + 3.4:.1f}">{_esc(val)}</text>')
    out.append('</g>')
    out.append(_projection_symbol(tx - 16, ty + tb_h - 2, projection))
    out.append('</svg>')
    return "\n".join(out)


def _projection_symbol(x: float, y_base: float, projection: str) -> str:
    """The truncated-cone symbol: third angle shows the end view (circles) on the side it is seen from."""
    circ = f'<circle cx="{{cx:.2f}}" cy="{y_base - 4:.2f}" r="3.4" fill="none"/><circle cx="{{cx:.2f}}" cy="{y_base - 4:.2f}" r="1.6" fill="none"/>'
    trap = f'<path d="M {{x0:.2f}} {y_base - 5.6:.2f} L {{x1:.2f}} {y_base - 7.4:.2f} L {{x1:.2f}} {y_base - 0.6:.2f} L {{x0:.2f}} {y_base - 2.4:.2f} z" fill="none"/>'
    if projection == "first":
        body = trap.format(x0=x, x1=x + 7) + circ.format(cx=x + 12.5)
    else:
        body = circ.format(cx=x + 3.5) + trap.format(x0=x + 8, x1=x + 15)
    return f'<g class="projection-{projection}" stroke="#222" stroke-width="0.35">{body}</g>'


def drawing_pdf(svg_text: str, path: str | Path, sheet: str = "A4") -> Path:
    """Render an SVG sheet to PDF with headless Chromium (Playwright)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:                                   # pragma: no cover
        raise RuntimeError("PDF output needs Playwright with Chromium (pip install playwright; playwright install chromium)") from exc
    sheet_w, sheet_h = SHEETS.get(sheet, SHEETS["A4"])
    html = f'<!doctype html><html><head><meta charset="utf-8"><style>@page {{ size: {sheet_w}mm {sheet_h}mm; margin: 0 }} html, body {{ margin: 0; padding: 0 }} svg {{ display: block }}</style></head><body>{svg_text}</body></html>'
    path = Path(path)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html)
        page.pdf(path=str(path), width=f"{sheet_w}mm", height=f"{sheet_h}mm", print_background=True, prefer_css_page_size=True)
        browser.close()
    return path


def drawing_png(svg_text: str, path: str | Path, sheet: str = "A4", width_px: int = 1800) -> Path:
    """Rasterize an SVG sheet to PNG with headless Chromium (a preview the agent can look at)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:                                   # pragma: no cover
        raise RuntimeError("PNG previews need Playwright with Chromium (pip install playwright; playwright install chromium)") from exc
    sheet_w, sheet_h = SHEETS.get(sheet, SHEETS["A4"])
    height_px = int(round(width_px * sheet_h / sheet_w))
    svg = svg_text.replace(f'width="{sheet_w}mm" height="{sheet_h}mm"', f'width="{width_px}px" height="{height_px}px"', 1)
    html = f'<!doctype html><html><head><meta charset="utf-8"><style>html, body {{ margin: 0; padding: 0; background: #fff }} svg {{ display: block }}</style></head><body>{svg}</body></html>'
    path = Path(path)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width_px, "height": height_px})
        page.set_content(html)
        page.screenshot(path=str(path), clip={"x": 0, "y": 0, "width": width_px, "height": height_px})
        browser.close()
    return path


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def drawing_dxf(parts: Parts, name: str, path: str | Path, views: Sequence[str] = ("front", "top", "right"), sheet: str = "A4") -> Path:
    """The same views as a DXF (model units = mm at 1:1, views spread out on the XY plane),
    with VISIBLE, HIDDEN (dashed), DIM and TEXT layers."""
    import ezdxf
    vs = {v: project(parts, v) for v in views if v in VIEWS or ":" in v}
    sheet_w, sheet_h, s = layout(vs, sheet)
    doc = ezdxf.new("R2010", setup=True)
    doc.layers.add("VISIBLE", color=7)
    doc.layers.add("HIDDEN", color=8, linetype="DASHED")
    doc.layers.add("TEXT", color=5)
    doc.layers.add("HATCH", color=3)
    doc.layers.add("CENTER", color=1, linetype="DASHDOT") if "DASHDOT" in doc.linetypes else doc.layers.add("CENTER", color=1)
    msp = doc.modelspace()
    for v in vs.values():
        ox, oy = v.ox / s, v.oy / s                          # keep 1:1 model units, use the layout only for spacing
        for cx, cy, r in v.circles:
            msp.add_circle((ox + cx, oy + cy), r, dxfattribs={"layer": "VISIBLE"})
            e = r + 2.5
            msp.add_line((ox + cx - e, oy + cy), (ox + cx + e, oy + cy), dxfattribs={"layer": "CENTER"})
            msp.add_line((ox + cx, oy + cy - e), (ox + cx, oy + cy + e), dxfattribs={"layer": "CENTER"})
        for (ax, ay, bx, by) in v.centerlines:
            msp.add_line((ox + ax, oy + ay), (ox + bx, oy + by), dxfattribs={"layer": "CENTER"})
        for pl in v.visible:
            if len(pl) == 2:
                msp.add_line((ox + pl[0][0], oy + pl[0][1]), (ox + pl[1][0], oy + pl[1][1]), dxfattribs={"layer": "VISIBLE"})
            else:
                msp.add_lwpolyline([(ox + x, oy + y) for x, y in pl], dxfattribs={"layer": "VISIBLE"})
        for pl in v.hidden:
            msp.add_lwpolyline([(ox + x, oy + y) for x, y in pl], dxfattribs={"layer": "HIDDEN"})
        for pl in v.hatch:
            msp.add_line((ox + pl[0][0], oy + pl[0][1]), (ox + pl[1][0], oy + pl[1][1]), dxfattribs={"layer": "HATCH"})
        msp.add_text(v.name.upper(), dxfattribs={"layer": "TEXT", "height": 3}).set_placement((ox + v.bbox[0], oy + v.bbox[1] - 6))
        for h in v.holes:
            msp.add_text(h["text"], dxfattribs={"layer": "TEXT", "height": 2.5}).set_placement((ox + h["x"] + 3, oy + h["y"] + 3))
    bb = parts.bbox
    msp.add_text(f"{name}  {_fmt(bb.size.X)} x {_fmt(bb.size.Y)} x {_fmt(bb.size.Z)} mm  (third angle, mm)", dxfattribs={"layer": "TEXT", "height": 3}).set_placement((0, -20))
    path = Path(path)
    doc.saveas(str(path))
    return path
