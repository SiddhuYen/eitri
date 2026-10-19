"""2.5-D CAM for a part held top-up on a 3-axis mill: facing, outside/inside contours with tabs, pockets,
drilling and V-bit chamfers from the part's geometry; a tool library with feeds and speeds; G-code for
GRBL / LinuxCNC; a time estimate; toolpath previews; and 2-D cut files (laser / waterjet / plasma)
with a kerf offset."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import build123d as bd
from build123d import Vector

TOOLS = {                          # diameters mm, rpm, feeds mm/min (aluminium baseline), stepdown as a fraction of d
    "endmill_3": {"type": "endmill", "d": 3.0, "flutes": 2, "rpm": 15000, "feed": 600, "plunge": 200, "stepdown": 0.5},
    "endmill_6": {"type": "endmill", "d": 6.0, "flutes": 2, "rpm": 12000, "feed": 900, "plunge": 300, "stepdown": 0.5},
    "endmill_10": {"type": "endmill", "d": 10.0, "flutes": 3, "rpm": 9000, "feed": 1200, "plunge": 300, "stepdown": 0.5},
    "ball_6": {"type": "ballmill", "d": 6.0, "flutes": 2, "rpm": 12000, "feed": 800, "plunge": 250, "stepdown": 0.3},
    "drill_3": {"type": "drill", "d": 3.0, "flutes": 2, "rpm": 6000, "feed": 150, "plunge": 150, "stepdown": 1.0},
    "drill_5": {"type": "drill", "d": 5.0, "flutes": 2, "rpm": 4500, "feed": 150, "plunge": 150, "stepdown": 1.0},
    "drill_6.5": {"type": "drill", "d": 6.5, "flutes": 2, "rpm": 4000, "feed": 140, "plunge": 140, "stepdown": 1.0},
    "vbit_90": {"type": "vbit", "d": 8.0, "angle": 90, "flutes": 2, "rpm": 15000, "feed": 700, "plunge": 250, "stepdown": 1.0},
    "facemill_20": {"type": "endmill", "d": 20.0, "flutes": 4, "rpm": 6000, "feed": 1500, "plunge": 200, "stepdown": 0.2},
}
MATERIAL_FEED = {"aluminum": 1.0, "aluminium": 1.0, "brass": 0.9, "copper": 0.7, "steel": 0.35, "stainless": 0.25, "titanium": 0.2,
                 "pla": 1.6, "petg": 1.3, "abs": 1.5, "nylon": 1.2, "acrylic": 1.1, "wood": 1.8, "resin": 1.0}
RAPID_FEED = 5000.0


@dataclass
class Move:
    x: float
    y: float
    z: float
    kind: str = "cut"              # rapid | cut | plunge
    feed: float | None = None


@dataclass
class Toolpath:
    op: str
    tool: str
    moves: list[Move] = field(default_factory=list)
    notes: str = ""

    def lengths(self) -> tuple[float, float]:
        cut = rapid = 0.0
        for a, b in zip(self.moves, self.moves[1:]):
            d = math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
            if b.kind == "rapid":
                rapid += d
            else:
                cut += d
        return cut, rapid

    def seconds(self) -> float:
        t = 0.0
        for a, b in zip(self.moves, self.moves[1:]):
            d = math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
            f = RAPID_FEED if b.kind == "rapid" else (b.feed or 500.0)
            t += d / f * 60.0
        return t


@dataclass
class Job:
    part: str
    stock: tuple[float, float, float, float, float, float]
    safe_z: float
    toolpaths: list[Toolpath] = field(default_factory=list)
    post: str = "grbl"
    material: str | None = None

    @property
    def seconds(self) -> float:
        return sum(tp.seconds() for tp in self.toolpaths)

    def summary(self) -> str:
        lines = [f"{self.part}: stock {self.stock[3] - self.stock[0]:g} x {self.stock[4] - self.stock[1]:g} x {self.stock[5] - self.stock[2]:g} mm, "
                 f"{len(self.toolpaths)} operation(s), ~{self.seconds / 60:.1f} min ({self.post})"]
        for tp in self.toolpaths:
            cut, rapid = tp.lengths()
            lines.append(f"  {tp.op} with {tp.tool}: {len(tp.moves)} moves, cut {cut:.0f} mm, ~{tp.seconds() / 60:.1f} min" + (f" - {tp.notes}" if tp.notes else ""))
        return "\n".join(lines)


# ---------------------------------------------------------------------------- geometry helpers

def slice_faces(shape: bd.Shape, z: float) -> list[bd.Face]:
    try:
        return [f for f in bd.section(shape, section_by=bd.Plane.XY.offset(z), height=0.0).faces() if f.area > 1e-6]
    except Exception:
        return []


def safe_offset(wire: bd.Wire, distance: float) -> bd.Wire | None:
    try:
        o = wire.offset_2d(distance, kind=bd.Kind.ARC)
    except Exception:
        return None
    if not o.is_closed or o.length < 1e-6:
        return None
    return o


def wire_points(wire: bd.Wire, step: float = 0.5) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for e in wire.edges():
        k = 1 if e.geom_type == bd.GeomType.LINE else max(8, int(e.length / step))
        for i in range(k):
            p = e.position_at(i / k)
            pts.append((round(p.X, 4), round(p.Y, 4)))
    first = wire.position_at(0)
    pts.append(pts[0] if wire.is_closed else (round(wire.position_at(1).X, 4), round(wire.position_at(1).Y, 4)))
    return pts


def _feeds(tool: dict, material: str | None) -> tuple[float, float]:
    k = MATERIAL_FEED.get((material or "aluminum").lower(), 1.0)
    return tool["feed"] * k, tool["plunge"] * k


def _tool(name: str) -> dict:
    if name not in TOOLS:
        raise ValueError(f"unknown tool {name!r}; tools: {', '.join(TOOLS)}")
    return dict(TOOLS[name], name=name)


# ---------------------------------------------------------------------------- operations

def _depth_passes(z_top: float, z_bottom: float, stepdown: float) -> list[float]:
    n = max(1, math.ceil((z_top - z_bottom) / stepdown - 1e-9))
    return [round(z_top - (z_top - z_bottom) * (i + 1) / n, 4) for i in range(n)]


def contour(shape: bd.Shape, tool: dict, material: str | None = None, side: str = "outside", depth: float | None = None,
            stepdown: float | None = None, tabs: int = 0, tab_width: float = 6.0, tab_height: float = 2.0, safe_z: float = 5.0,
            stock_to_leave: float = 0.0) -> Toolpath:
    """Follow the part's outline (outside) or the inside of its through-openings, in depth passes, with
    optional holding tabs on the last pass."""
    bb = shape.bounding_box()
    z_top, z_bottom = bb.max.Z, (bb.max.Z - depth) if depth else bb.min.Z
    r = tool["d"] / 2 + stock_to_leave
    feed, plunge = _feeds(tool, material)
    sd = stepdown or tool["d"] * tool["stepdown"]
    tp = Toolpath("contour " + side, tool["name"])
    passes = _depth_passes(z_top, z_bottom, sd)
    for pi, z in enumerate(passes):
        faces = slice_faces(shape, min(z + 1e-3, z_top - 1e-3))
        wires = []
        for f in faces:
            if side == "outside":
                o = safe_offset(f.outer_wire(), r)
                if o is not None:
                    wires.append(o)
            else:
                for iw in f.inner_wires():
                    o = safe_offset(iw, -r)
                    if o is not None:
                        wires.append(o)
        if not wires:
            tp.notes = "nothing to cut at this depth (tool too large for the opening?)" if side != "outside" else tp.notes
            continue
        last = pi == len(passes) - 1
        for w in wires:
            pts = wire_points(w)
            total = w.length
            tab_spans: list[tuple[float, float]] = []
            if last and tabs > 0 and z_bottom <= bb.min.Z + 1e-6:
                for k in range(tabs):
                    c = (k + 0.5) * total / tabs
                    tab_spans.append((c - tab_width / 2, c + tab_width / 2))
            tp.moves.append(Move(pts[0][0], pts[0][1], z_top + safe_z, "rapid"))
            tp.moves.append(Move(pts[0][0], pts[0][1], z, "plunge", plunge))
            s = 0.0
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                seg = math.hypot(x1 - x0, y1 - y0)
                if tab_spans:
                    # split the segment at tab boundaries, raising the cutter over tabs
                    a, b = s, s + seg
                    cuts = sorted({a, b} | {t for span in tab_spans for t in span if a < t < b})
                    for u0, u1 in zip(cuts, cuts[1:]):
                        mid = (u0 + u1) / 2
                        on_tab = any(lo <= mid <= hi for lo, hi in tab_spans)
                        tt = (u1 - a) / seg if seg else 1.0
                        px, py = x0 + (x1 - x0) * tt, y0 + (y1 - y0) * tt
                        zz = z + tab_height if on_tab else z
                        if abs(tp.moves[-1].z - zz) > 1e-9:
                            tp.moves.append(Move(tp.moves[-1].x, tp.moves[-1].y, zz, "cut", feed))
                        tp.moves.append(Move(round(px, 4), round(py, 4), zz, "cut", feed))
                else:
                    tp.moves.append(Move(x1, y1, z, "cut", feed))
                s += seg
            tp.moves.append(Move(tp.moves[-1].x, tp.moves[-1].y, z_top + safe_z, "rapid"))
    if tabs and tp.moves:
        tp.notes = (tp.notes + "; " if tp.notes else "") + f"{tabs} tab(s) {tab_width:g} x {tab_height:g} mm on the last pass"
    return tp


def pocket_regions(shape: bd.Shape, min_area: float = 1.0) -> list[tuple[bd.Wire, float, float]]:
    """Closed pockets open to the top: (wire at the rim, z_top, z_floor)."""
    bb = shape.bounding_box()
    z_top = bb.max.Z
    out = []
    top_faces = slice_faces(shape, z_top - 1e-3)
    bottom_faces = slice_faces(shape, bb.min.Z + 1e-3)
    through_centres = [w.bounding_box().center() for f in bottom_faces for w in f.inner_wires()]
    from .measure import point_inside                       # cached per solid: is_inside builds a classifier per call
    solids = list(shape.solids()) or [shape]
    for f in top_faces:
        for w in f.inner_wires():
            c = w.bounding_box().center()                   # the opening's centre (Wire.center() is a point on the wire)
            if any(math.hypot(c.X - tc.X, c.Y - tc.Y) < 1e-3 for tc in through_centres):
                continue                                    # a through hole, not a pocket
            # find the floor: step down until the centre is in material
            z = z_top
            floor = bb.min.Z
            step = max((z_top - bb.min.Z) / 200, 0.05)
            while z > bb.min.Z:
                z -= step
                if any(point_inside(s, (c.X, c.Y, z), tol=1e-4) for s in solids):
                    floor = z + step
                    break
            out.append((w, z_top, round(floor, 4)))
    return out


def pocket(shape: bd.Shape, tool: dict, material: str | None = None, stepdown: float | None = None, stepover: float = 0.4,
           safe_z: float = 5.0, region: bd.Wire | None = None, depth: float | None = None) -> Toolpath:
    """Clear pockets with inward offset rings, in depth passes."""
    bb = shape.bounding_box()
    r = tool["d"] / 2
    feed, plunge = _feeds(tool, material)
    sd = stepdown or tool["d"] * tool["stepdown"]
    tp = Toolpath("pocket", tool["name"])
    regions = [(region, bb.max.Z, bb.max.Z - (depth or (bb.max.Z - bb.min.Z)))] if region is not None else pocket_regions(shape)
    if not regions:
        tp.notes = "no pockets found"
        return tp
    for w, z_top, z_floor in regions:
        rings = []
        k = 0
        while True:
            o = safe_offset(w, -(r + stepover * tool["d"] * k))
            if o is None:
                break
            rings.append(wire_points(o))
            k += 1
        if not rings:
            tp.notes = (tp.notes + "; " if tp.notes else "") + "a pocket is too narrow for this tool"
            continue
        for z in _depth_passes(z_top, z_floor, sd):
            for pts in reversed(rings):                     # innermost first, finishing at the wall
                tp.moves.append(Move(pts[0][0], pts[0][1], z_top + safe_z, "rapid"))
                tp.moves.append(Move(pts[0][0], pts[0][1], z, "plunge", plunge))
                for x, y in pts[1:]:
                    tp.moves.append(Move(x, y, z, "cut", feed))
            tp.moves.append(Move(tp.moves[-1].x, tp.moves[-1].y, z_top + safe_z, "rapid"))
    return tp


def drill(shape: bd.Shape, tool: dict, material: str | None = None, peck: float | None = None, safe_z: float = 5.0,
          diameter: float | None = None, tolerance: float = 0.3) -> Toolpath:
    """Peck-drill the vertical holes (all of them, or those of a diameter)."""
    from . import measure
    feed, plunge = _feeds(tool, material)
    tp = Toolpath("drill", tool["name"])
    holes = [h for h in measure.holes(shape) if abs(abs((h.axis if isinstance(h.axis, Vector) else Vector(*h.axis)).Z) - 1) < 1e-3]
    if diameter is not None:
        holes = [h for h in holes if abs(h.diameter - diameter) <= tolerance]
    if not holes:
        tp.notes = "no vertical holes" + (f" of Ø{diameter:g}" if diameter else "")
        return tp
    pk = peck or tool["d"] * 1.5
    mismatched = [h for h in holes if abs(h.diameter - tool["d"]) > tolerance]
    if mismatched:
        tp.notes = f"{len(mismatched)} hole(s) are not Ø{tool['d']:g} (drilling them with this tool anyway)"
    for h in holes:
        c = h.center if isinstance(h.center, Vector) else Vector(*h.center)
        top = c.Z + h.depth / 2
        bottom = top - h.depth - (0.3 * tool["d"] if h.through else 0.0)
        tp.moves.append(Move(round(c.X, 4), round(c.Y, 4), top + safe_z, "rapid"))
        z = top
        while z > bottom + 1e-9:
            z = max(bottom, z - pk)
            tp.moves.append(Move(round(c.X, 4), round(c.Y, 4), round(z, 4), "plunge", plunge))
            tp.moves.append(Move(round(c.X, 4), round(c.Y, 4), top + 1.0, "rapid"))
        tp.moves.append(Move(round(c.X, 4), round(c.Y, 4), top + safe_z, "rapid"))
    tp.notes = (tp.notes + "; " if tp.notes else "") + f"{len(holes)} hole(s), peck {pk:g} mm"
    return tp


def face(shape: bd.Shape, tool: dict, material: str | None = None, depth: float = 0.5, stepover: float = 0.6, safe_z: float = 5.0) -> Toolpath:
    """Skim the top of the stock: a raster over the bounding box at the top less `depth`."""
    bb = shape.bounding_box()
    feed, plunge = _feeds(tool, material)
    r = tool["d"] / 2
    tp = Toolpath("face", tool["name"])
    z = bb.max.Z - depth
    y, k = bb.min.Y - r + 0.5, 0
    xs = (bb.min.X - r - 1, bb.max.X + r + 1)
    tp.moves.append(Move(xs[0], y, bb.max.Z + safe_z, "rapid"))
    tp.moves.append(Move(xs[0], y, z, "plunge", plunge))
    while y <= bb.max.Y + r - 0.5 + 1e-9:
        a, b = (xs[0], xs[1]) if k % 2 == 0 else (xs[1], xs[0])
        tp.moves.append(Move(b, y, z, "cut", feed))
        y += stepover * tool["d"]; k += 1
        if y <= bb.max.Y + r - 0.5 + 1e-9:
            tp.moves.append(Move(b, y, z, "cut", feed))
    tp.moves.append(Move(tp.moves[-1].x, tp.moves[-1].y, bb.max.Z + safe_z, "rapid"))
    tp.notes = f"{depth:g} mm off the top"
    return tp


def chamfer(shape: bd.Shape, tool: dict, material: str | None = None, size: float = 1.0, safe_z: float = 5.0) -> Toolpath:
    """A V-bit chamfer along the top outline: the tip runs `size` below the top, offset so the flank
    cuts `size` into the edge."""
    if tool.get("type") != "vbit":
        raise ValueError("chamfering needs a V-bit (vbit_90)")
    bb = shape.bounding_box()
    feed, plunge = _feeds(tool, material)
    half = math.radians(tool.get("angle", 90) / 2)
    off = size * math.tan(half)
    tp = Toolpath("chamfer", tool["name"])
    z = bb.max.Z - size
    for f in slice_faces(shape, bb.max.Z - 1e-3):
        for w, sign in [(f.outer_wire(), 1.0)] + [(iw, -1.0) for iw in f.inner_wires()]:
            o = safe_offset(w, sign * off)
            if o is None:
                continue
            pts = wire_points(o)
            tp.moves.append(Move(pts[0][0], pts[0][1], bb.max.Z + safe_z, "rapid"))
            tp.moves.append(Move(pts[0][0], pts[0][1], z, "plunge", plunge))
            for x, y in pts[1:]:
                tp.moves.append(Move(x, y, z, "cut", feed))
            tp.moves.append(Move(tp.moves[-1].x, tp.moves[-1].y, bb.max.Z + safe_z, "rapid"))
    tp.notes = f"{size:g} mm at {tool.get('angle', 90)} deg"
    return tp


# ---------------------------------------------------------------------------- jobs and posts

OPS = ("face", "contour", "pocket", "drill", "chamfer")


def plan(shape: bd.Shape, name: str, ops: Sequence[dict | str], post: str = "grbl", material: str | None = None, safe_z: float = 5.0,
         default_tool: str = "endmill_6") -> Job:
    """A job from op specs: "face" | {"op": "contour", "tool": "endmill_6", "tabs": 3, ...}; keys per op:
    face(depth, stepover), contour(side, depth, stepdown, tabs, tab_width, tab_height, stock_to_leave),
    pocket(stepdown, stepover), drill(peck, diameter), chamfer(size)."""
    bb = shape.bounding_box()
    job = Job(name, (bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z), safe_z, post=post, material=material)
    for spec in ops:
        spec = {"op": spec} if isinstance(spec, str) else dict(spec)
        op = spec.pop("op", "contour")
        if op not in OPS:
            raise ValueError(f"unknown operation {op!r}; operations: {', '.join(OPS)}")
        tname = spec.pop("tool", None) or ("vbit_90" if op == "chamfer" else "drill_5" if op == "drill" else "facemill_20" if op == "face" else default_tool)
        tool = _tool(tname)
        fn = {"face": face, "contour": contour, "pocket": pocket, "drill": drill, "chamfer": chamfer}[op]
        job.toolpaths.append(fn(shape, tool, material, safe_z=safe_z, **spec))
    return job


def gcode(job: Job) -> str:
    post = (job.post or "grbl").lower()
    lines = [f"; eitri 2.5-D job: {job.part} ({post}), {len(job.toolpaths)} operation(s), ~{job.seconds / 60:.1f} min",
             f"; stock X {job.stock[0]:g}..{job.stock[3]:g} Y {job.stock[1]:g}..{job.stock[4]:g} Z {job.stock[2]:g}..{job.stock[5]:g} mm; work zero at the part origin, Z0 at Z={job.stock[2]:g}"]
    if post == "linuxcnc":
        lines = ["%"] + lines + ["G21 G90 G17 G40 G49 G64 P0.01"]
    else:
        lines.append("G21 G90 G17")
    lines.append(f"G0 Z{job.stock[5] + job.safe_z:.3f}")
    cur_tool = None
    for i, tp in enumerate(job.toolpaths, start=1):
        tool = TOOLS[tp.tool]
        if tp.tool != cur_tool:
            if cur_tool is not None:
                lines.append("M5")
                lines.append(f"G0 Z{job.stock[5] + job.safe_z + 20:.3f}")
            lines.append(f"; tool change: {tp.tool} (Ø{tool['d']:g} {tool['type']})")
            lines.append(f"T{list(TOOLS).index(tp.tool) + 1} M6" if post == "linuxcnc" else f"M0 ; load {tp.tool}")
            lines.append(f"S{tool['rpm']} M3")
            lines.append("G4 P2")
            cur_tool = tp.tool
        lines.append(f"; op {i}: {tp.op}" + (f" - {tp.notes}" if tp.notes else ""))
        last_feed = None
        for m in tp.moves:
            if m.kind == "rapid":
                lines.append(f"G0 X{m.x:.3f} Y{m.y:.3f} Z{m.z:.3f}")
            else:
                f = m.feed or 500.0
                lines.append(f"G1 X{m.x:.3f} Y{m.y:.3f} Z{m.z:.3f}" + (f" F{f:.0f}" if f != last_feed else ""))
                last_feed = f
    lines += ["M5", f"G0 Z{job.stock[5] + job.safe_z + 20:.3f}", "M2" if post == "linuxcnc" else "M30"]
    if post == "linuxcnc":
        lines.append("%")
    return "\n".join(lines) + "\n"


def toolpath_json(job: Job) -> dict:
    paths = []
    for tp in job.toolpaths:
        pts = [[m.x, m.y, m.z, 0 if m.kind == "rapid" else 1] for m in tp.moves]
        paths.append({"op": tp.op, "tool": tp.tool, "seconds": round(tp.seconds(), 1), "points": pts, "notes": tp.notes})
    return {"part": job.part, "stock": list(job.stock), "seconds": round(job.seconds, 1), "post": job.post, "paths": paths, "summary": job.summary()}


def preview_png(job: Job, shape: bd.Shape, path: str | Path, size: tuple[int, int] = (900, 700)) -> str:
    """A top-view plot: the part outline in grey, cuts coloured per operation, rapids dashed."""
    from PIL import Image, ImageDraw
    w, h = size
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    x0, y0, _, x1, y1, _ = job.stock
    pad = 30
    s = min((w - 2 * pad) / max(x1 - x0, 1e-6), (h - 2 * pad) / max(y1 - y0, 1e-6))
    def P(x, y):
        return (pad + (x - x0) * s, h - pad - (y - y0) * s)
    for f in slice_faces(shape, job.stock[5] - 1e-3):
        for wire in [f.outer_wire()] + list(f.inner_wires()):
            pts = [P(x, y) for x, y in wire_points(wire)]
            d.line(pts, fill=(140, 140, 140), width=2)
    colours = [(220, 50, 50), (40, 120, 220), (30, 160, 80), (200, 120, 20), (150, 60, 180)]
    for i, tp in enumerate(job.toolpaths):
        col = colours[i % len(colours)]
        for a, b in zip(tp.moves, tp.moves[1:]):
            if b.kind == "rapid":
                pa, pb = P(a.x, a.y), P(b.x, b.y)
                n = max(1, int(math.dist(pa, pb) / 8))
                for k in range(0, n, 2):
                    t0, t1 = k / n, min(1.0, (k + 1) / n)
                    d.line([(pa[0] + (pb[0] - pa[0]) * t0, pa[1] + (pb[1] - pa[1]) * t0), (pa[0] + (pb[0] - pa[0]) * t1, pa[1] + (pb[1] - pa[1]) * t1)], fill=(170, 170, 230), width=1)
            else:
                d.line([P(a.x, a.y), P(b.x, b.y)], fill=col, width=1)
        d.text((pad, 8 + 14 * i), f"{tp.op} ({tp.tool}) ~{tp.seconds() / 60:.1f} min", fill=col)
    img.save(str(path))
    return str(path)


# ---------------------------------------------------------------------------- 2-D cut files

def cut_outlines(shape: bd.Shape, kerf: float = 0.0, z: float | None = None) -> tuple[list[bd.Wire], list[bd.Wire]]:
    """The outer and inner outlines of a flat part at mid-thickness, offset by half the kerf
    (outers outward, inners inward) so the cut part comes out to size."""
    bb = shape.bounding_box()
    zz = z if z is not None else (bb.min.Z + bb.max.Z) / 2
    outers, inners = [], []
    for f in slice_faces(shape, zz):
        o = safe_offset(f.outer_wire(), kerf / 2) if kerf else f.outer_wire()
        if o is not None:
            outers.append(o)
        for iw in f.inner_wires():
            o2 = safe_offset(iw, -kerf / 2) if kerf else iw
            if o2 is not None:
                inners.append(o2)
    return outers, inners


def cut_file(shape: bd.Shape, path: str | Path, kerf: float = 0.0, fmt: str = "dxf", z: float | None = None) -> str:
    """Write the cut outlines as DXF (layers CUT_OUTER / CUT_INNER) or SVG (mm units)."""
    outers, inners = cut_outlines(shape, kerf, z)
    if not outers:
        raise ValueError("no outline to cut at that height")
    path = Path(path)
    if fmt == "svg":
        bb = shape.bounding_box()
        pad = 5.0
        w, h = bb.size.X + 2 * pad + kerf, bb.size.Y + 2 * pad + kerf
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.2f}mm" height="{h:.2f}mm" viewBox="{bb.min.X - pad - kerf / 2:.3f} {-(bb.max.Y + pad + kerf / 2):.3f} {w:.3f} {h:.3f}" fill="none" stroke="#000" stroke-width="0.1">']
        for wires, colour in ((outers, "#000"), (inners, "#00f")):
            for wire in wires:
                pts = wire_points(wire, 0.2)
                parts.append(f'<path stroke="{colour}" d="M ' + " L ".join(f"{x:.3f} {-y:.3f}" for x, y in pts) + ' Z"/>')
        parts.append("</svg>")
        path.write_text("\n".join(parts), encoding="utf-8")
        return str(path)
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.layers.add("CUT_OUTER", color=7)
    doc.layers.add("CUT_INNER", color=5)
    msp = doc.modelspace()
    for wires, layer in ((outers, "CUT_OUTER"), (inners, "CUT_INNER")):
        for wire in wires:
            for e in wire.edges():
                if e.geom_type == bd.GeomType.LINE:
                    a, b = e.position_at(0), e.position_at(1)
                    msp.add_line((a.X, a.Y), (b.X, b.Y), dxfattribs={"layer": layer})
                elif e.geom_type == bd.GeomType.CIRCLE and e.is_closed:
                    c = e.arc_center
                    msp.add_circle((c.X, c.Y), e.radius, dxfattribs={"layer": layer})
                else:
                    n = max(8, int(e.length / 0.3) + 4)
                    msp.add_lwpolyline([(p.X, p.Y) for p in (e.position_at(i / n) for i in range(n + 1))], dxfattribs={"layer": layer})
    doc.saveas(str(path))
    return str(path)
