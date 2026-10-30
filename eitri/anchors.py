"""Anchors: named points and frames a CAD model declares, written into its build result.

    def build(p):
        board = Pos(50, 0, 10) * Box(68.6, 53.4, 1.6)
        anchor("UNO", at=(50, 0, 10.8), z=(0, 0, 1), x=(1, 0, 0))     # a frame: where the board sits, and how it is turned
        anchor("SERVO_YAW", at=(0, 12, 48), z=(0, 1, 0), part="yaw_servo")   # a point on a part that moves with it
        return {"base": base, "yaw_servo": servo}

An anchor is a name, a point (mm, in the model's coordinates) and optionally a frame: `z` is the direction the thing at the anchor
faces or is approached from, `x` its in-plane direction (both are normalised; an `x` that is not perpendicular to `z` is made so).
`part` names the part the anchor belongs to, so whatever reads the result knows it moves with that part. Names are free text and
case does not matter; declaring one twice is an error.

The core records anchors and shows them (result.json and state.json `anchors`, and the build summary). What they mean is the
reader's business: a host places purchased parts, connectors or probes at them. The core attaches no meaning to a name.
"""
from __future__ import annotations

import math
import threading
from typing import Any

__all__ = ["anchor", "Anchor"]

_local = threading.local()


class AnchorError(ValueError):
    """An anchor was declared wrongly (a build error, with the line that did it)."""


class Anchor:
    __slots__ = ("name", "at", "z", "x", "part")

    def __init__(self, name: str, at, z=None, x=None, part: str | None = None):
        self.name = name
        self.at = at
        self.z = z
        self.x = x
        self.part = part

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "at": list(self.at), "z": list(self.z) if self.z else None, "x": list(self.x) if self.x else None, "part": self.part}

    def __repr__(self) -> str:
        return f"Anchor({self.name!r}, at={tuple(self.at)}" + (f", part={self.part!r}" if self.part else "") + ")"


def _vec(v, what: str, name: str) -> tuple[float, float, float]:
    """Three numbers from a tuple, a list, a build123d Vector / Vertex / Location, or anything with .X .Y .Z or .position."""
    if v is None:
        raise AnchorError(f"anchor {name!r}: {what} is missing")
    pos = getattr(v, "position", None)                      # a build123d Location
    if pos is not None and not hasattr(v, "X"):
        v = pos
    if hasattr(v, "X") and hasattr(v, "Y") and hasattr(v, "Z"):
        v = (v.X, v.Y, v.Z)
    try:
        out = tuple(float(c) for c in v)
    except (TypeError, ValueError):
        raise AnchorError(f"anchor {name!r}: {what} must be three numbers (x, y, z), got {v!r}") from None
    if len(out) != 3:
        raise AnchorError(f"anchor {name!r}: {what} must be three numbers (x, y, z), got {len(out)}")
    if any(math.isnan(c) or math.isinf(c) for c in out):
        raise AnchorError(f"anchor {name!r}: {what} has a NaN or infinite coordinate")
    return out


def _unit(v, what: str, name: str) -> tuple[float, float, float]:
    x, y, z = _vec(v, what, name)
    n = math.sqrt(x * x + y * y + z * z)
    if n < 1e-12:
        raise AnchorError(f"anchor {name!r}: {what} is a zero vector")
    return (x / n, y / n, z / n)


def _frame(at, z, x, name: str):
    """(at, z, x): z and x unit and perpendicular (x is projected off z), or None where not given. A Location gives all three."""
    if z is None and x is None and hasattr(at, "z_axis") and hasattr(at, "x_axis"):     # a build123d Location: a whole frame
        loc = at
        at, z, x = loc.position, loc.z_axis.direction, loc.x_axis.direction
    at = _vec(at, "at", name)
    zu = _unit(z, "z", name) if z is not None else None
    xu = None
    if x is not None:
        xu = _unit(x, "x", name)
        if zu is not None:
            d = xu[0] * zu[0] + xu[1] * zu[1] + xu[2] * zu[2]
            px = (xu[0] - d * zu[0], xu[1] - d * zu[1], xu[2] - d * zu[2])
            n = math.sqrt(sum(c * c for c in px))
            if n < 1e-9:
                raise AnchorError(f"anchor {name!r}: x is parallel to z; give an in-plane direction")
            xu = (px[0] / n, px[1] / n, px[2] / n)
    return at, zu, xu


def anchor(name: str, at, z=None, x=None, part: str | None = None) -> Anchor:
    """Declare a named point (or frame) of the model being built. Call it inside build(p); the build records it.

    name: free text, unique in the model (case does not matter). at: the point, mm (a tuple, a Vector, a Vertex, or a Location,
    which then also gives z and x). z: the direction the anchor faces / is approached from. x: its in-plane direction. part: the
    name of the part it belongs to (one of the names build() returns), so it moves with that part.
    """
    name = str(name or "").strip()
    if not name:
        raise AnchorError("anchor(): a name is required")
    at3, zu, xu = _frame(at, z, x, name)
    a = Anchor(name, at3, zu, xu, str(part).strip() if part else None)
    pending = getattr(_local, "pending", None)
    if pending is not None:                                  # inside a build: collected for the result
        if any(o.name.lower() == name.lower() for o in pending):
            raise AnchorError(f"anchor {name!r} is declared twice")
        pending.append(a)
    return a


def begin() -> None:
    """Start collecting (the model loader calls it before build(p))."""
    _local.pending = []


def take() -> list[Anchor]:
    """The anchors declared since begin(), and stop collecting."""
    out = list(getattr(_local, "pending", None) or [])
    _local.pending = None
    return out


def to_json(parts) -> list[dict[str, Any]]:
    """The anchors of a built Parts as plain data, in the order declared (an empty list when the model declared none)."""
    return [a.to_dict() if isinstance(a, Anchor) else dict(a) for a in (getattr(parts, "anchors", None) or [])]


def describe(anchors: list[dict[str, Any]]) -> str:
    """One line per anchor for the build summary: name, position, and the part it belongs to."""
    if not anchors:
        return ""
    lines = ["Anchors:"]
    for a in anchors:
        at = a.get("at") or [0, 0, 0]
        bits = [f"  {a['name']}: ({at[0]:g}, {at[1]:g}, {at[2]:g})"]
        if a.get("z"):
            bits.append("z (" + ", ".join(f"{c:g}" for c in a["z"]) + ")")
        if a.get("part"):
            bits.append(f"on {a['part']}")
        lines.append(" ".join(bits))
    return "\n".join(lines)
