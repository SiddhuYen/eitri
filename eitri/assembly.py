"""Assemblies: joints between parts (rigid, revolute, slider, cylindrical, pin-slot, planar, ball) with
origins, axes and limits, driving them (the jointed part and everything jointed onto it move), motion
links, component groups and a bill of materials. Used by eitri.edits (the viewer's Joint tool and the
agent's direct edits) and by the report."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import build123d as bd
from build123d import Vector

JOINT_DOFS = {"rigid": [], "revolute": ["angle"], "slider": ["slide"], "cylindrical": ["angle", "slide"],
              "pin_slot": ["angle", "slide"], "planar": ["u", "v", "angle"], "ball": ["rx", "ry", "rz"]}
ANGULAR = {"angle", "rx", "ry", "rz"}


def _t(v) -> tuple[float, float, float]:
    return (round(float(v.X), 6), round(float(v.Y), 6), round(float(v.Z), 6))


@dataclass
class Joint:
    name: str
    type: str
    part: str                       # the moving part
    to_part: str                    # the part it is jointed to (fixed side)
    origin: tuple[float, float, float]
    axis: tuple[float, float, float]        # unit: rotation axis / slide direction / planar normal
    x_dir: tuple[float, float, float]       # unit, perpendicular to axis: planar u, pin-slot slide direction
    limits: dict = field(default_factory=dict)   # dof -> [min, max]
    value: dict = field(default_factory=dict)    # dof -> current value (degrees / mm)

    @property
    def dofs(self) -> list[str]:
        return JOINT_DOFS[self.type]

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "part": self.part, "to_part": self.to_part, "origin": list(self.origin),
                "axis": list(self.axis), "x_dir": list(self.x_dir), "limits": dict(self.limits), "value": {d: self.value.get(d, 0.0) for d in self.dofs}, "dofs": self.dofs}


def joint_frame(mov_face: bd.Face, tgt_face: bd.Face, offset: float = 0.0) -> tuple[Vector, Vector, Vector]:
    """The joint origin, axis and in-plane x direction from the mated faces (after alignment):
    flat faces -> the target face's centre and normal; round faces -> the target's axis, at the
    moving face's centre."""
    from .edits import _axis_of, _face_centre, face_frame
    if mov_face.geom_type == bd.GeomType.PLANE and tgt_face.geom_type == bd.GeomType.PLANE:
        n = tgt_face.normal_at().normalized()
        origin = _face_centre(tgt_face) + n * float(offset or 0)
        x = face_frame(n, origin).x_dir
        return origin, n, x
    if mov_face.geom_type == bd.GeomType.CYLINDER and tgt_face.geom_type == bd.GeomType.CYLINDER:
        line_pt, d = _axis_of(tgt_face)
        d = d.normalized()
        c = _face_centre(mov_face)
        origin = line_pt + d * (c - line_pt).dot(d)
        x = face_frame(d, origin).x_dir
        return origin, d, x
    raise ValueError("a joint needs two flat faces or two round faces (a pin in a hole)")


def normalize_limits(jtype: str, limits) -> dict:
    """{"min", "max"} applies to the joint's first degree of freedom; otherwise dof -> [min, max]."""
    if not limits:
        return {}
    dofs = JOINT_DOFS[jtype]
    if isinstance(limits, dict) and ("min" in limits or "max" in limits):
        if not dofs:
            return {}
        return {dofs[0]: [float(limits.get("min", -1e9)), float(limits.get("max", 1e9))]}
    out = {}
    for k, v in dict(limits).items():
        if k in dofs and isinstance(v, (list, tuple)) and len(v) == 2:
            out[k] = [float(v[0]), float(v[1])]
    return out


def clamp_value(joint: Joint, target: dict) -> tuple[dict, list[str]]:
    out, notes = {}, []
    for d, v in target.items():
        if d not in joint.dofs:
            raise ValueError(f"joint {joint.name!r} ({joint.type}) has no '{d}' degree of freedom; it has {joint.dofs or 'none'}")
        v = float(v)
        lim = joint.limits.get(d)
        if lim:
            if v < lim[0]:
                notes.append(f"{d} limited to {lim[0]:g}"); v = lim[0]
            elif v > lim[1]:
                notes.append(f"{d} limited to {lim[1]:g}"); v = lim[1]
        out[d] = v
    return out, notes


def value_dict(joint: Joint, value) -> dict:
    if isinstance(value, dict):
        return {k: float(v) for k, v in value.items()}
    if not joint.dofs:
        raise ValueError(f"joint {joint.name!r} is rigid: nothing to drive")
    return {joint.dofs[0]: float(value)}


class _Motion:
    """A rigid motion built from a joint's delta: applies to shapes, points and directions."""

    def __init__(self, joint: Joint, delta: dict):
        self.o = Vector(*joint.origin); self.n = Vector(*joint.axis).normalized(); self.x = Vector(*joint.x_dir).normalized()
        self.y = self.n.cross(self.x).normalized()
        t = joint.type
        self.steps: list[tuple] = []                         # ("rot", axis_dir, deg) / ("move", vector), applied in order
        if t in ("revolute", "cylindrical", "pin_slot", "planar") and delta.get("angle"):
            self.steps.append(("rot", self.n, float(delta["angle"])))
        if t in ("slider", "cylindrical") and delta.get("slide"):
            self.steps.append(("move", self.n * float(delta["slide"])))
        if t == "pin_slot" and delta.get("slide"):
            self.steps.append(("move", self.x * float(delta["slide"])))
        if t == "planar":
            mv = self.x * float(delta.get("u", 0)) + self.y * float(delta.get("v", 0))
            if mv.length > 1e-12:
                self.steps.append(("move", mv))
        if t == "ball":
            for d, ax in (("rx", self.x), ("ry", self.y), ("rz", self.n)):
                if delta.get(d):
                    self.steps.append(("rot", ax, float(delta[d])))

    @property
    def identity(self) -> bool:
        return not self.steps

    def shape(self, s: bd.Shape) -> bd.Shape:
        for kind, a, *rest in self.steps:
            s = s.rotate(bd.Axis(self.o, a), rest[0]) if kind == "rot" else s.moved(bd.Location((a.X, a.Y, a.Z)))
        return s

    def point(self, p: Vector) -> Vector:
        for kind, a, *rest in self.steps:
            p = (p - self.o).rotate(bd.Axis((0, 0, 0), a), rest[0]) + self.o if kind == "rot" else p + a
        return p

    def direction(self, d: Vector) -> Vector:
        for kind, a, *rest in self.steps:
            if kind == "rot":
                d = d.rotate(bd.Axis((0, 0, 0), a), rest[0])
        return d


def dependents(joints: Sequence[Joint], part: str) -> list[str]:
    """Parts jointed onto `part`, transitively (they move with it)."""
    out: list[str] = []
    stack = [part]
    while stack:
        p = stack.pop()
        for j in joints:
            if j.to_part == p and j.part not in out and j.part != part:
                out.append(j.part)
                stack.append(j.part)
    return out


def drive(joints: Sequence[Joint], joint: Joint, target: dict, shapes: dict[str, bd.Shape]) -> tuple[dict[str, bd.Shape], list[str]]:
    """Move the joint to `target` (absolute values): returns the new shapes of every moved part and
    notes about clamped limits. The joint's own frame stays; the frames of joints carried along move."""
    target, notes = clamp_value(joint, target)
    delta = {d: target[d] - float(joint.value.get(d, 0.0)) for d in target}
    motion = _Motion(joint, delta)
    moved: dict[str, bd.Shape] = {}
    if not motion.identity:
        names = [joint.part] + dependents(joints, joint.part)
        for n in names:
            if n in shapes:
                moved[n] = motion.shape(shapes[n])
        for j in joints:
            if j is not joint and (j.part in names or j.to_part in names) and j.to_part in names:
                j.origin = _t(motion.point(Vector(*j.origin)))
                j.axis = _t(motion.direction(Vector(*j.axis)))
                j.x_dir = _t(motion.direction(Vector(*j.x_dir)))
    joint.value.update(target)
    return moved, notes


# ---------------------------------------------------------------------------- bill of materials

def bom(parts) -> list[dict]:
    """Rows of identical parts (same volume, size and material), numbered for balloons."""
    from .measure import DENSITY
    rows: list[dict] = []
    index: dict[tuple, dict] = {}
    for p in parts:
        shape = p.shape
        try:
            vol = abs(shape.volume)
            bb = shape.bounding_box()
            size = tuple(round(x, 2) for x in (bb.size.X, bb.size.Y, bb.size.Z))
        except Exception:
            vol, size = 0.0, (0, 0, 0)
        mat = getattr(p, "material", None)
        key = (round(vol, 2), size, mat or "")
        row = index.get(key)
        if row is None:
            row = {"item": len(rows) + 1, "name": p.name, "qty": 0, "parts": [], "material": mat or "-", "volume": round(vol, 1),
                   "mass_g": (round(vol / 1000 * DENSITY[mat], 1) if mat in DENSITY else None), "size": list(size), "group": getattr(p, "group", None)}
            index[key] = row
            rows.append(row)
        row["qty"] += 1
        row["parts"].append(p.name)
    for r in rows:
        if r["qty"] > 1:
            stem = _common_stem(r["parts"])
            if stem:
                r["name"] = stem
            if r["mass_g"] is not None:
                r["mass_total_g"] = round(r["mass_g"] * r["qty"], 1)
    return rows


def _common_stem(names: Sequence[str]) -> str:
    import re
    stems = {re.sub(r"[_\-\s]?\d+$", "", n) for n in names}
    return stems.pop() if len(stems) == 1 else ""


def bom_csv(rows: Sequence[dict]) -> str:
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["item", "name", "qty", "material", "volume_mm3", "mass_g_each", "size_mm", "parts", "group"])
    for r in rows:
        w.writerow([r["item"], r["name"], r["qty"], r["material"], r["volume"], "" if r["mass_g"] is None else r["mass_g"],
                    "x".join(f"{s:g}" for s in r["size"]), ";".join(r["parts"]), r.get("group") or ""])
    return buf.getvalue()
