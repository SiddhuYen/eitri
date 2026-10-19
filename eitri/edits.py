"""Direct edits: Fusion-style operations applied on top of `build()`.

A model file may carry an `EDITS` list. Each edit names a part and selects
geometry *geometrically* (a plane by normal + point, a cylinder by axis +
radius, an edge by type + midpoint + length) rather than by index, so it keeps
resolving after parameters change. The kernel applies the edits after
`build()` and before `verify()`/the report, and the agent can later fold them
into the parametric code.

Edit shapes (dicts, JSON-serialisable; any edit may carry "suppressed": true):

    {"op": "push_pull", "part": "base", "face": <face-sel> | "faces": [...], "distance": 3.0}
    {"op": "sketch_extrude", "part": "base", "face": <plane-sel>, "origin": [..], "normal": [..],
         "profile": <profile>, "distance": 5, "mode": "add"|"cut", "through": false,
         "new_part": null, "pattern": <pattern>, "mirror": "u"|"v"|"uv", "offset": 0}
    {"op": "revolve", ... sketch fields ..., "axis": "u"|"v"|{"p1": [u,v], "p2": [u,v]}, "angle": 360}
    {"op": "loft", ... sketch fields ..., "sections": [{"profile": .., "offset": 0}, {"profile": .., "offset": 20}]}
    {"op": "hole", "part": "base", "face": <plane-or-cylinder-sel>, "origin": [..], "normal": [..], "at": [u, v],
         "size": "M3", "head": "counterbore"|"countersink"|null, "fit": "normal", "tapped": false,
         "depth": null, "d": 5.0, "pattern": <pattern>, "mirror": ...}
    {"op": "hole_diameter", "part": "base", "face": <cylinder-sel>, "diameter": 5.5}
    {"op": "move_hole", "part": "base", "face": <cylinder-sel>, "delta": [dx, dy, dz]}
    {"op": "fillet",    "part": "base", "edges": [<edge-sel>, ...], "radius": 2.0}
    {"op": "chamfer",   "part": "base", "edges": [...], "size": 1.0}
    {"op": "shell",     "part": "box", "faces": [<face-sel>, ...], "thickness": 1.6}
    {"op": "thread",    "part": "shaft", "face": <cylinder-sel>, "size": "M6", "length": null, "modeled": false}
    {"op": "primitive", "kind": "box"|"cylinder"|"sphere"|"torus"|"cone", "size": {...}, "mode": "new"|"add"|"cut",
         "part": "base", "new_part": "peg", "at": [x, y, z] | ("face": <plane-sel>, "origin", "normal", "at": [u, v])}
    {"op": "combine",   "part": "base", "tool": "peg", "mode": "join"|"cut"|"intersect", "keep_tool": false}
    {"op": "split",     "part": "base", "plane": <plane-spec>, "offset": 0, "keep": "both"|"top"|"bottom", "new_part": "base_2"}
    {"op": "mirror",    "part": "left", "plane": <plane-spec>, "new_part": "right" | null (joins)}
    {"op": "pattern",   "part": "peg", "type": "rect", "nx": 3, "ny": 2, "dx": 10, "dy": 8, "join": false}
    {"op": "pattern",   "part": "peg", "type": "circular", "n": 6, "center": [0,0,0], "axis": [0,0,1], "angle": 360}
    {"op": "scale",     "part": "base", "factor": 1.5 | [sx, sy, sz], "about": [x, y, z] | null}
    {"op": "align",     "part": "lid", "face": <sel on lid>, "to_part": "base", "to_face": <sel on base>,
         "offset": 0, "flip": false, "center": true}
    {"op": "move",      "part": "lid", "delta": [0, 0, 5]}
    {"op": "rotate",    "part": "lid", "axis": [0, 0, 1], "angle": 90, "about": [x, y, z]}
    {"op": "delete_part", "part": "scrap"}
    {"op": "color",     "part": "lid", "color": "#D79B5B"}
    {"op": "material",  "part": "lid", "material": "steel", "color": "#B0B0B0"}
    {"op": "move_vertices", "part": "box", "vertices": [<vertex-sel>], "edges": [<edge-sel>], "faces": [<face-sel>],
         "delta": [dx, dy, dz]}                       grab corners/edges and drag them; faces around them follow
    {"op": "move_face", "part": "box", "face": <face-sel>, "delta": [dx, dy, dz],
         "rotate": {"axis": [..], "angle": deg, "about": [x, y, z]}}   free move/tilt; neighbours extend to meet it
    {"op": "delete_faces", "part": "box", "faces": [<face-sel>, ...]}  remove a feature (fillet, hole, boss) and heal
A vertex selector is {"kind": "vertex", "pos": [x, y, z]}.

Profiles live in the sketch frame of the face (see `face_frame`):
    {"type": "rect", "center": [u, v], "w": 20, "h": 10, "angle": 0, "corner_r": 0}
    {"type": "circle", "center": [u, v], "r": 5}
    {"type": "slot", "center": [u, v], "length": 20, "w": 6, "angle": 0}
    {"type": "polygon", "points": [[u, v], ...]}
    {"type": "text", "text": "ABC", "size": 8, "center": [u, v], "angle": 0, "font": "Arial", "bold": false}
A pattern is {"type": "rect", "nx", "ny", "dx", "dy"} or {"type": "circular", "n", "center": [u, v], "angle": 360}.
A plane spec is "XY" | "XZ" | "YZ" (+ "through": "origin"|"center") or {"origin": [..], "normal": [..]}.

Selectors are what `selector_for_face` / `selector_for_edge` produce from the
viewer's topology.json entries.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Sequence

import build123d as bd
from build123d import Vector


def _xyz(v) -> tuple[float, float, float]:
    return (float(v.X), float(v.Y), float(v.Z))

from .model import Part, Parts

_BOTTOM = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)


# ---------------------------------------------------------------------------- selectors

def selector_for_face(info: dict) -> dict:
    """A geometric selector from a topology.json face entry."""
    sel = {"kind": "face", "type": info.get("type"), "center": info.get("center"), "area": info.get("area")}
    if info.get("normal"):
        sel["normal"] = info["normal"]
    if info.get("axis"):
        sel["axis"] = info["axis"]
        sel["axis_point"] = info.get("axis_point")
    if info.get("radius") is not None:
        sel["radius"] = info["radius"]
    if info.get("concave") is not None:
        sel["concave"] = info["concave"]
    return sel


def selector_for_vertex(info: dict) -> dict:
    return {"kind": "vertex", "pos": info.get("pos")}


def resolve_vertices(shape: bd.Shape, sels: Sequence[dict], tol: float = 0.5) -> list[Vector]:
    from .direct import nearest_vertex
    out: list[Vector] = []
    for sel in sels:
        v = nearest_vertex(shape, sel["pos"], tol) if sel.get("pos") else None
        if v is not None and all((v - o).length > 1e-6 for o in out):
            out.append(v)
    return out


def selector_for_edge(info: dict) -> dict:
    return {"kind": "edge", "type": info.get("type"), "mid": info.get("mid"), "length": info.get("length"),
            "start": info.get("start"), "end": info.get("end")}


def _vec(x) -> Vector:
    return x if isinstance(x, Vector) else Vector(*x)


def _plane_distance(face: bd.Face, point: Vector) -> float:
    n = face.normal_at()
    return abs((point - face.center()).dot(n))


def resolve_face(shape: bd.Shape, sel: dict, tol: float = 0.5) -> bd.Face | None:
    """The face of `shape` that best matches a selector (None if nothing is close)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    want = sel.get("type")
    center = _vec(sel["center"]) if sel.get("center") else None
    best, best_score = None, None
    for f in shape.faces():
        kind = f.geom_type.name.lower()
        if want and kind != want:
            continue
        score = 0.0
        try:
            if kind == "plane" and sel.get("normal"):
                n = f.normal_at()
                score += n.get_angle(_vec(sel["normal"])) * 0.1          # degrees -> 0.1 per degree
                if center is not None:
                    score += _plane_distance(f, center)                 # out-of-plane distance
                    score += 0.05 * (center - f.center()).length         # mild preference for the same place
            elif kind in ("cylinder", "cone") and sel.get("axis"):
                surf = BRepAdaptor_Surface(f.wrapped)
                ax = surf.Cylinder().Axis() if kind == "cylinder" else surf.Cone().Axis()
                d = Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z())
                loc = Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
                a = _vec(sel["axis"])
                score += min(d.get_angle(a), d.get_angle(a * -1)) * 0.1
                if sel.get("axis_point"):
                    p = _vec(sel["axis_point"])
                    off = p - loc
                    off = off - d * off.dot(d)
                    score += off.length
                if kind == "cylinder" and sel.get("radius") is not None:
                    score += abs(surf.Cylinder().Radius() - float(sel["radius"])) * 2
                if center is not None:
                    score += 0.02 * (center - f.center()).length
            else:
                if center is not None:
                    score += (center - f.center()).length
            if sel.get("area"):
                score += 0.001 * abs(f.area - float(sel["area"]))
        except Exception:
            continue
        if best_score is None or score < best_score:
            best, best_score = f, score
    if best is None:
        return None
    if best_score is not None and best_score > max(tol * 4, 2.0) and center is not None:
        # nothing close to where the user clicked
        if (center - best.center()).length > 10 * max(tol, 1.0) and _plane_distance(best, center) > tol:
            return None
    return best


def resolve_faces(shape: bd.Shape, sels: Sequence[dict]) -> list[bd.Face]:
    out = []
    for sel in sels:
        f = resolve_face(shape, sel)
        if f is not None and all(not f.is_same(o) for o in out):
            out.append(f)
    return out


def resolve_edges(shape: bd.Shape, sels: Sequence[dict], tol: float = 0.5) -> list[bd.Edge]:
    out = []
    edges = list(shape.edges())
    for sel in sels:
        mid = _vec(sel["mid"]) if sel.get("mid") else None
        want = sel.get("type")
        best, best_score = None, None
        for e in edges:
            kind = e.geom_type.name.lower()
            if want and kind != want:
                continue
            try:
                score = (e.position_at(0.5) - mid).length if mid is not None else 0.0
                if sel.get("length"):
                    score += 0.1 * abs(e.length - float(sel["length"]))
            except Exception:
                continue
            if best_score is None or score < best_score:
                best, best_score = e, score
        if best is not None and (best_score is None or best_score < max(tol * 10, 5.0)):
            out.append(best)
    return out


# ---------------------------------------------------------------------------- frames & profiles

def face_frame(normal: Vector | Sequence[float], origin: Vector | Sequence[float]) -> bd.Plane:
    """The deterministic 2D frame used for sketching on a flat face.

    x runs along world X projected onto the face (world Y when the face is nearly
    perpendicular to X); y = normal x x. The viewer computes the same frame from
    topology.json, so (u, v) coordinates mean the same thing on both sides.
    """
    n = _vec(normal).normalized()
    ref = Vector(1, 0, 0) if abs(n.X) < 0.9 else Vector(0, 1, 0)
    x = (ref - n * ref.dot(n)).normalized()
    return bd.Plane(origin=_vec(origin), x_dir=x, z_dir=n)


def _profile_sketch(profile: dict):
    kind = profile.get("type")
    if kind == "sketch":                                   # a full constrained sketch (eitri.sketch)
        from . import sketch as S
        sk = profile
        if sk.get("constraints") or sk.get("dims"):
            st = S.solve(sk)
            if not st["ok"]:
                raise ValueError("the sketch's constraints conflict: " + ", ".join(st["conflicts"][:4]))
        return S.profile(sk)
    cx, cy = (float(c) for c in profile.get("center", (0, 0)))
    place = bd.Pos(cx, cy, 0) * bd.Rot(0, 0, float(profile.get("angle", 0)))
    if kind == "rect":
        if profile.get("corner_r"):
            return place * bd.RectangleRounded(float(profile["w"]), float(profile["h"]), float(profile["corner_r"]))
        return place * bd.Rectangle(float(profile["w"]), float(profile["h"]))
    if kind == "circle":
        return bd.Pos(cx, cy, 0) * bd.Circle(float(profile["r"]))
    if kind == "polygon":
        pts = [tuple(float(c) for c in p) for p in profile["points"]]
        if len(pts) < 3:
            raise ValueError("a polygon needs at least 3 points")
        return bd.Polygon(*pts, align=None)
    if kind == "slot":
        return place * bd.SlotOverall(float(profile["length"]), float(profile["w"]))
    if kind == "text":
        txt = str(profile.get("text", "")).strip()
        if not txt:
            raise ValueError("empty text")
        style = bd.FontStyle.BOLD if profile.get("bold") else bd.FontStyle.REGULAR
        return place * bd.Text(txt, font_size=float(profile.get("size", 8)), font=profile.get("font") or "Arial",
                               font_style=style, align=(bd.Align.CENTER, bd.Align.CENTER))
    raise ValueError(f"unknown profile type {kind!r}")


def _upright(sk):
    """Faces of a 2D sketch with their normals along +Z (mirroring flips them)."""
    faces = []
    for f in sk.faces():
        try:
            if f.normal_at().Z < 0:
                f = bd.Face(f.wrapped.Reversed())
        except Exception:
            pass
        faces.append(f)
    return faces


def pattern_steps(pattern: dict | None) -> list[tuple[float, float, float, float, float]]:
    """(du, dv, rotation_deg, cx, cy) for every instance of a sketch-plane pattern (the original first)."""
    if not pattern:
        return [(0.0, 0.0, 0.0, 0.0, 0.0)]
    kind = pattern.get("type", "rect")
    if kind == "rect":
        nx, ny = max(1, int(pattern.get("nx", 1))), max(1, int(pattern.get("ny", 1)))
        dx, dy = float(pattern.get("dx", 0)), float(pattern.get("dy", 0))
        return [(i * dx, j * dy, 0.0, 0.0, 0.0) for j in range(ny) for i in range(nx)]
    if kind == "circular":
        n = max(1, int(pattern.get("n", 1)))
        total = float(pattern.get("angle", 360))
        cx, cy = (float(c) for c in pattern.get("center", (0, 0)))
        step = total / n if abs(total) >= 360 else (total / (n - 1) if n > 1 else 0.0)
        return [(0.0, 0.0, k * step, cx, cy) for k in range(n)]
    raise ValueError(f"unknown pattern type {kind!r}")


def _instances_2d(profile: dict, pattern: dict | None = None, mirror: str | None = None):
    """A Sketch with every patterned/mirrored copy of a profile (sketch-plane coordinates)."""
    items = [_profile_sketch(profile)]
    if mirror in ("u", "uv"):
        items += [bd.mirror(s, about=bd.Plane.XZ) for s in items]      # v -> -v
    if mirror in ("v", "uv"):
        items += [bd.mirror(s, about=bd.Plane.YZ) for s in items]      # u -> -u
    out = []
    for s in items:
        for du, dv, ang, cx, cy in pattern_steps(pattern):
            t = s
            if ang:
                t = bd.Pos(cx, cy, 0) * bd.Rot(0, 0, ang) * bd.Pos(-cx, -cy, 0) * t
            if du or dv:
                t = bd.Pos(du, dv, 0) * t
            out.append(t)
    faces = [f for s in out for f in _upright(s)]
    sketch = faces[0]                      # Face + Face fuses into a Sketch (or one Face when they overlap)
    for f in faces[1:]:
        sketch = sketch + f
    return sketch


def _points_2d(at: Sequence[float], pattern: dict | None, mirror: str | None) -> list[tuple[float, float]]:
    pts = [(float(at[0]), float(at[1]))]
    if mirror in ("u", "uv"):
        pts += [(u, -v) for u, v in pts]
    if mirror in ("v", "uv"):
        pts += [(-u, v) for u, v in pts]
    out = []
    for u, v in pts:
        for du, dv, ang, cx, cy in pattern_steps(pattern):
            a = math.radians(ang)
            ru, rv = cx + (u - cx) * math.cos(a) - (v - cy) * math.sin(a), cy + (u - cx) * math.sin(a) + (v - cy) * math.cos(a)
            out.append((round(ru + du, 6), round(rv + dv, 6)))
    return out


def _frame_for(face: bd.Face, edit: dict) -> bd.Plane:
    """The sketch frame of an edit: its stored world origin/normal (from the viewer),
    with the origin projected onto the resolved face so the frame follows the face
    if the face itself was pushed; falls back to the face's own geometry.
    On a cylinder the frame sits on the surface at the stored point, normal radial."""
    if face.geom_type == bd.GeomType.CYLINDER:
        r, d, start, length, _full = _cylinder_extent(face)
        p = _vec(edit["origin"]) if edit.get("origin") else face.center()
        on_axis = start + d * (p - start).dot(d)
        radial = (p - on_axis)
        if radial.length < 1e-6:
            radial = _vec(edit["normal"]) if edit.get("normal") else face.normal_at()
        radial = radial.normalized()
        n = _vec(edit["normal"]).normalized() if edit.get("normal") else (radial * (-1 if _is_concave(face) else 1))
        return face_frame(n, on_axis + radial * r)
    n = _vec(edit["normal"]).normalized() if edit.get("normal") else face.normal_at()
    face_n = face.normal_at()
    anchor = _vec(edit["origin"]) if edit.get("origin") else face.center()
    anchor = anchor - face_n * (anchor - face.center()).dot(face_n)     # onto the face plane
    frame = face_frame(n, anchor)
    if edit.get("offset"):
        frame = frame.offset(float(edit["offset"]))
    return frame


def _local(frame: bd.Plane, u: float, v: float, w: float = 0.0) -> Vector:
    return frame.origin + frame.x_dir * u + frame.y_dir * v + frame.z_dir * w


def _extrude_region(edit: dict):
    """The 2D object an extrude works on: the profile instances, or the thin walls of a `thin` extrude
    (a wall just inside each closed profile, a band centred on each open chain)."""
    thin = float(edit.get("thin") or 0)
    if thin <= 0:
        return _instances_2d(edit["profile"], edit.get("pattern"), edit.get("mirror"))
    from . import sketch as S
    from . import solidops as solid
    pr = edit["profile"]
    sk = pr if pr.get("type") == "sketch" else S.from_simple(pr)
    if sk.get("constraints") or sk.get("dims"):
        st = S.solve(sk)
        if not st["ok"]:
            raise ValueError("the sketch's constraints conflict: " + ", ".join(st["conflicts"][:4]))
    faces, chains = S.profile_faces(sk)
    return solid.sketch_of(solid.thin_region(faces, chains, thin))


def _extent_of(edit: dict, mode: str) -> str:
    ext = edit.get("extent") or ("through" if edit.get("through") and mode == "cut" else "distance")
    if ext not in ("distance", "through", "to_next", "to_face"):
        raise ValueError(f"unknown extent {ext!r}: distance, through, to_next or to_face")
    if ext == "through" and mode != "cut":
        raise ValueError("'through all' only cuts; use a distance or 'to next' to add material")
    return ext


def _sketch_extrude(shape: bd.Shape, face: bd.Face | None, profile: dict, distance: float, mode: str, through: bool,
                    edit: dict | None = None, frame: bd.Plane | None = None) -> bd.Shape:
    from . import solidops as solid
    edit = dict(edit or {})
    edit.setdefault("profile", profile)
    frame = frame if frame is not None else _frame_for(face, edit)
    region = _extrude_region(edit)
    taper = float(edit.get("taper") or 0)
    sym = bool(edit.get("symmetric"))
    sgn = -1.0 if edit.get("flip") else 1.0
    extent = _extent_of(edit, mode)
    L = shape.bounding_box().diagonal * 2
    d = abs(float(distance or 0))
    if extent == "through":
        d = L
    elif extent in ("to_next", "to_face"):
        if sym or taper:
            raise ValueError("'to next' / 'to face' extents are plain one-sided extrusions (no taper, not symmetric)")
        target = None
        if extent == "to_face":
            target = resolve_face(shape, edit.get("to_face") or {})
            if target is None:
                raise ValueError("the face to extrude to no longer exists")
            if target.geom_type == bd.GeomType.PLANE:
                d = (target.center() - frame.origin).dot(frame.z_dir * sgn * (1.0 if mode == "add" else -1.0))
                if d <= 1e-6:
                    raise ValueError("that face is not in front of the sketch")
                extent = "distance"
        if extent != "distance":
            piece = solid.to_next_piece(shape, frame, region, mode, L, flip=bool(edit.get("flip")), must_touch=target)
            return checked_boolean(shape, (shape + piece) if mode == "add" else (shape - piece), mode, "extrude to the next face")
    if d <= 0:
        raise ValueError("the extrude distance must be positive")
    if mode == "cut":
        if sym:
            return robust_boolean(shape, lambda eps: bd.extrude(frame * region, d + eps, both=True, taper=taper), "cut", "cut this sketch")
        # start just outside the face, cut inward
        return robust_boolean(shape, lambda eps: bd.extrude(frame.offset(sgn * eps) * region, -sgn * (d + eps), taper=taper), "cut", "cut this sketch")
    if sym:
        return robust_boolean(shape, lambda eps: bd.extrude(frame * region, d, both=True, taper=taper), "add", "extrude this sketch")
    # start just inside, add outward; the overlap slab under the face is removed where it overhangs
    return robust_boolean(shape, lambda eps: bd.extrude(frame.offset(-sgn * eps) * region, sgn * (d + eps), taper=taper), "add", "extrude this sketch",
                          skirt=lambda eps: bd.extrude(frame.offset(-sgn * eps) * region, sgn * eps))


def _extrude_new(frame: bd.Plane, edit: dict) -> bd.Shape:
    """A free-standing extrusion (a new part): symmetric / taper / thin / flip honoured."""
    region = _extrude_region(edit)
    d = abs(float(edit.get("distance") or 0))
    if d <= 0:
        raise ValueError("the extrude distance must be positive")
    sgn = -1.0 if edit.get("flip") else 1.0
    return bd.extrude(frame * region, sgn * d, both=bool(edit.get("symmetric")), taper=float(edit.get("taper") or 0))


def _revolve_axis(frame: bd.Plane, edit: dict, axis_shape: bd.Shape | None = None) -> tuple[Vector, Vector]:
    """Two world points of a revolve axis spec (in the sketch plane)."""
    axis = edit.get("axis", "v")
    if axis == "u":
        a, b = _local(frame, 0.0, 0.0), _local(frame, 1.0, 0.0)
    elif axis == "v":
        a, b = _local(frame, 0.0, 0.0), _local(frame, 0.0, 1.0)
    elif axis in ("x", "y", "z", "X", "Y", "Z"):                       # a model axis through the origin
        a = Vector(0, 0, 0)
        b = {"x": Vector(1, 0, 0), "y": Vector(0, 1, 0), "z": Vector(0, 0, 1)}[str(axis).lower()]
    elif isinstance(axis, dict) and axis.get("edge"):                 # a straight model edge
        if axis_shape is None:
            raise ValueError("the axis edge's part is missing")
        edges = resolve_edges(axis_shape, [axis["edge"]])
        if not edges or edges[0].geom_type != bd.GeomType.LINE:
            raise ValueError("the revolve axis edge must be a straight edge that still exists")
        a, b = edges[0].position_at(0), edges[0].position_at(1)
    else:
        a = _local(frame, *(float(c) for c in axis["p1"]))
        b = _local(frame, *(float(c) for c in axis["p2"]))
    if (b - a).length < 1e-6:
        raise ValueError("the revolve axis needs two distinct points")
    for pt in (a, b):
        if abs((pt - frame.origin).dot(frame.z_dir)) > 1e-3:
            raise ValueError("the revolve axis must lie in the sketch plane")
    return a, b


def _revolve_solid(frame: bd.Plane, edit: dict, axis_shape: bd.Shape | None = None):
    sketch = _instances_2d(edit["profile"], edit.get("pattern"), edit.get("mirror"))
    a, b = _revolve_axis(frame, edit, axis_shape)
    solid = bd.revolve(frame * sketch, axis=bd.Axis(a, b - a), revolution_arc=float(edit.get("angle", 360)))
    if not solid.is_valid or not solid.solids():
        raise ValueError("the profile did not revolve to a valid solid (it must stay on one side of the axis)")
    return solid


def _loft_solid(frame: bd.Plane, edit: dict):
    sections = edit.get("sections") or []
    if len(sections) < 2:
        raise ValueError("a loft needs at least two sections")
    faces = []
    for sec in sections:
        pr = sec.get("profile") or edit.get("profile")
        if not pr:
            raise ValueError("a loft section needs a profile (or the feature a base profile)")
        sk = _instances_2d(pr, None, None)
        if sec.get("scale") not in (None, "", 1, 1.0):
            sk = sk.scale(float(sec["scale"]))
        if sec.get("rotate"):
            sk = bd.Rot(0, 0, float(sec["rotate"])) * sk
        plane = frame.offset(float(sec.get("offset", 0)))
        fs = (plane * sk).faces()
        if len(fs) != 1:
            raise ValueError("each loft section must be a single closed profile")
        faces.append(fs[0])
    solid = bd.loft(faces, ruled=bool(edit.get("ruled")))
    if not solid.is_valid or not solid.solids():
        raise ValueError("the sections did not loft to a valid solid")
    return solid


def _hole_tool(shape: bd.Shape, face: bd.Face, at: Sequence[float], edit: dict) -> bd.Shape:
    from .lib.fasteners import counterbore_dims, countersink_dims, screw_clearance, tap_drill
    from .lib.features import hole_cutter
    frame = _frame_for(face, edit)
    size = edit.get("size")
    if size:
        d = tap_drill(size) if edit.get("tapped") else screw_clearance(size, edit.get("fit", "normal"))
        head = edit.get("head")
        cb = counterbore_dims(size) if head == "counterbore" else None
        cs = countersink_dims(size) if head == "countersink" else None
    else:
        d = float(edit["d"])
        cb = tuple(edit["counterbore"]) if edit.get("counterbore") else None
        cs = tuple(edit["countersink"]) if edit.get("countersink") else None
    depth = float(edit["depth"]) if edit.get("depth") else shape.bounding_box().diagonal * 2
    cutter = hole_cutter(d, depth, counterbore=cb, countersink=cs)
    for u, v in _points_2d(at, edit.get("pattern"), edit.get("mirror")):
        shape = checked_boolean(shape, shape - (frame * bd.Pos(u, v, 0) * cutter), "cut", "hole")
    return shape


def _cylinder_extent(face: bd.Face):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    surf = BRepAdaptor_Surface(face.wrapped)
    cyl = surf.Cylinder()
    ax = cyl.Axis()
    d = Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()).normalized()
    loc = Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face.wrapped)
    lo, hi = sorted((vmin, vmax))
    full = (umax - umin) > 2 * math.pi * 0.95
    return cyl.Radius(), d, loc + d * lo, hi - lo, full


def _cylinder_radius(shape: bd.Shape, face: bd.Face, new_r: float, concave: bool) -> bd.Shape:
    r, d, start, length, full = _cylinder_extent(face)
    if not full:
        raise ValueError("only full-round cylinders (holes, bosses) can be pushed/pulled; use the chat for fillets and partial rounds")
    if new_r <= 0.05:
        raise ValueError("radius would vanish")
    centre = start + d * (length / 2)
    frame = bd.Plane(origin=(centre.X, centre.Y, centre.Z), z_dir=(d.X, d.Y, d.Z))
    if concave:
        filler = frame * bd.Cylinder(r + 0.001, length)
        cutter = frame * bd.Cylinder(new_r, length + 0.2)
        return (shape + filler) - cutter
    if new_r > r:
        return shape + frame * bd.Cylinder(new_r, length)
    ring = frame * bd.Cylinder(r + 0.001, length + 0.2) - frame * bd.Cylinder(new_r, length + 0.4)
    return shape - ring


def _sphere_radius(shape: bd.Shape, face: bd.Face, delta: float) -> bd.Shape:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    sph = BRepAdaptor_Surface(face.wrapped).Sphere()
    r = sph.Radius()
    c = sph.Location()
    centre = Vector(c.X(), c.Y(), c.Z())
    pt = face.position_at(0.5, 0.5)
    convex = face.normal_at(pt).dot(pt - centre) > 0
    new_r = r + delta if convex else r - delta
    if new_r <= 0.05:
        raise ValueError("radius would vanish")
    ball = bd.Pos(centre.X, centre.Y, centre.Z) * bd.Sphere(new_r)
    if convex:
        return shape + ball if new_r > r else _shrink_sphere(shape, centre, r, new_r)
    filler = bd.Pos(centre.X, centre.Y, centre.Z) * bd.Sphere(r + 0.001)
    return (shape + filler) - ball


def _shrink_sphere(shape, centre, r, new_r):
    shell = bd.Pos(centre.X, centre.Y, centre.Z) * bd.Sphere(r + 0.001) - bd.Pos(centre.X, centre.Y, centre.Z) * bd.Sphere(new_r)
    return shape - shell


def _move_hole(shape: bd.Shape, face: bd.Face, delta: Sequence[float]) -> bd.Shape:
    r, d, start, length, full = _cylinder_extent(face)
    if not full:
        raise ValueError("not a full-round hole")
    centre = start + d * (length / 2)
    frame = bd.Plane(origin=(centre.X, centre.Y, centre.Z), z_dir=(d.X, d.Y, d.Z))
    dx, dy, dz = (float(c) for c in delta)
    dv = Vector(dx, dy, dz)
    dv = dv - d * dv.dot(d)                              # only across the axis
    filler = frame * bd.Cylinder(r + 0.001, length)
    cutter = bd.Pos(dv.X, dv.Y, dv.Z) * (frame * bd.Cylinder(r, length + 0.2))
    return (shape + filler) - cutter


def _is_concave(face: bd.Face) -> bool:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
    ax = cyl.Axis()
    d = Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z())
    loc = Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
    pt = face.position_at(0.5, 0.5)
    radial = pt - loc
    radial = radial - d * radial.dot(d)
    return face.normal_at(pt).dot(radial) < 0


# ---------------------------------------------------------------------------- planes, primitives, transforms

_NAMED_PLANES = {"XY": Vector(0, 0, 1), "XZ": Vector(0, 1, 0), "YZ": Vector(1, 0, 0)}


def plane_spec(spec, shape: bd.Shape | None = None, offset: float = 0.0, through: str = "origin") -> bd.Plane:
    """A world plane from "XY"/"XZ"/"YZ" (through the origin or the part's centre),
    or {"origin": [..], "normal": [..]} (a face's plane, as the viewer sends it)."""
    if isinstance(spec, dict):
        n = _vec(spec["normal"]).normalized()
        o = _vec(spec.get("origin") or spec.get("center") or (0, 0, 0))
    else:
        key = str(spec).upper()
        if key not in _NAMED_PLANES:
            raise ValueError(f"unknown plane {spec!r}: use XY, XZ, YZ or {{origin, normal}}")
        n = _NAMED_PLANES[key]
        o = shape.bounding_box().center() if (through == "center" and shape is not None) else Vector(0, 0, 0)
    return bd.Plane(origin=o + n * float(offset or 0), z_dir=n)


def primitive_solid(kind: str, size: dict) -> bd.Shape:
    """A primitive sitting on z=0, centred on the z axis."""
    g = lambda k, d=None: float(size[k]) if k in size and size[k] not in (None, "") else (float(d) if d is not None else None)
    if kind == "box":
        return bd.Box(g("w", 20), g("d", 20), g("h", 10), align=_BOTTOM)
    if kind == "cylinder":
        return bd.Cylinder(g("r", 5), g("h", 10), align=_BOTTOM)
    if kind == "sphere":
        r = g("r", 5)
        return bd.Pos(0, 0, r) * bd.Sphere(r)
    if kind == "torus":
        R, r = g("R", 10), g("r", 2)
        return bd.Pos(0, 0, r) * bd.Torus(R, r)
    if kind == "cone":
        return bd.Cone(g("r1", 5), g("r2", 0), g("h", 10), align=_BOTTOM)
    if kind == "hexagon":
        return bd.extrude(bd.RegularPolygon(g("af", 10) / math.sqrt(3), 6, major_radius=True), g("h", 10))
    from . import solidops as solid
    if kind == "wedge":
        return solid.wedge_solid(g("w", 20), g("d", 20), g("h", 10), g("top_w"), g("top_h", 0) or 0.0)
    if kind == "coil":
        return solid.coil_solid(g("R", 8), g("r", 1), g("pitch", 4), g("h", 20), bool(size.get("lefthand")))
    raise ValueError(f"unknown primitive {kind!r}: box, cylinder, sphere, torus, cone, hexagon, wedge, coil")


def _place_primitive(solid: bd.Shape, edit: dict, host: bd.Shape | None) -> bd.Shape:
    rot = edit.get("rotation")
    if rot:
        solid = bd.Rot(*[float(c) for c in rot]) * solid
    if edit.get("face") and host is not None:
        face = resolve_face(host, edit["face"])
        if face is None:
            raise ValueError("the face to place on no longer exists")
        frame = _frame_for(face, edit)
        u, v = (float(c) for c in (edit.get("at") or (0, 0))[:2])
        return frame * bd.Pos(u, v, 0) * solid
    x, y, z = (float(c) for c in (edit.get("at") or (0, 0, 0)))
    return bd.Pos(x, y, z) * solid


def rotation_between(a: Vector, b: Vector) -> tuple[Vector, float]:
    """(axis, angle in degrees) of the rotation taking direction a onto direction b."""
    a, b = a.normalized(), b.normalized()
    c = a.cross(b)
    d = max(-1.0, min(1.0, a.dot(b)))
    if c.length < 1e-9:
        if d > 0:
            return Vector(0, 0, 1), 0.0
        ref = Vector(1, 0, 0) if abs(a.X) < 0.9 else Vector(0, 1, 0)
        return a.cross(ref).normalized(), 180.0
    return c.normalized(), math.degrees(math.acos(d))


def _axis_of(face: bd.Face) -> tuple[Vector, Vector]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    ax = BRepAdaptor_Surface(face.wrapped).Cylinder().Axis()
    d = Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()).normalized()
    return Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z()), d


def _face_centre(face: bd.Face) -> Vector:
    """Where a user means by "the centre of the face": the centre of its outline for a
    flat face (the centroid drifts towards whatever is not cut away), else its centroid."""
    if face.geom_type == bd.GeomType.PLANE:
        n = face.normal_at()
        c = face.bounding_box().center()
        return c - n * (c - face.center()).dot(n)
    if face.geom_type == bd.GeomType.CYLINDER:          # on the axis, halfway along the face
        loc, d = _axis_of(face)
        c = face.center()
        return loc + d * (c - loc).dot(d)
    return face.center()


def _align(moving: bd.Shape, mov_face: bd.Face, tgt_face: bd.Face, offset: float, flip: bool, center: bool, return_transform: bool = False):
    """Rigidly move a part so one of its faces mates with a face of another part."""
    c_a = _face_centre(mov_face)
    if mov_face.geom_type == bd.GeomType.PLANE and tgt_face.geom_type == bd.GeomType.PLANE:
        n_a, n_b = mov_face.normal_at(), tgt_face.normal_at()
        axis, ang = rotation_between(n_a, n_b if flip else n_b * -1)
        c_b = _face_centre(tgt_face)
        target = (c_b if center else c_a - n_b * (c_a - c_b).dot(n_b)) + n_b * offset
        what = "face to face" + (f", offset {offset:g} mm" if offset else "")
    elif mov_face.geom_type == bd.GeomType.CYLINDER and tgt_face.geom_type == bd.GeomType.CYLINDER:
        _la, d_a = _axis_of(mov_face)
        l_b, d_b = _axis_of(tgt_face)
        axis, ang = rotation_between(d_a, d_b * -1 if flip else d_b)
        c_b = _face_centre(tgt_face)
        target = (c_b if center else l_b + d_b * (c_a - l_b).dot(d_b)) + d_b * offset
        what = "concentric" + (f", offset {offset:g} mm along the axis" if offset else "")
    else:
        raise ValueError("align needs two flat faces (mate) or two round faces (concentric)")
    def xf(s: bd.Shape) -> bd.Shape:
        s = s.rotate(bd.Axis(c_a, axis), ang) if ang > 1e-6 else s
        t = target - c_a
        return s.moved(bd.Location((t.X, t.Y, t.Z))) if t.length > 1e-9 else s
    shape = xf(moving)
    if return_transform:
        return shape, what, xf
    return shape, what


def _thread(shape: bd.Shape, face: bd.Face, edit: dict) -> tuple[bd.Shape, str]:
    from .lib.fasteners import screw_pitch
    r, d, start, length, full = _cylinder_extent(face)
    if not full:
        raise ValueError("threads need a full-round cylindrical face")
    internal = _is_concave(face)
    size = edit.get("size")
    if size:
        major = float(str(size).upper().lstrip("M"))
        pitch = float(edit.get("pitch") or screw_pitch(size))
    else:
        major = float(edit.get("major") or 2 * r)
        pitch = float(edit["pitch"])
    L = min(float(edit["length"]) if edit.get("length") else length, length)
    label = f"M{major:g}x{pitch:g} {'internal' if internal else 'external'} thread, {L:g} mm"
    if not edit.get("modeled"):
        return shape, "cosmetic " + label + " (not modelled; the report lists it)"
    from bd_warehouse.thread import IsoThread
    minor = major - 1.0825 * pitch
    frame = bd.Plane(origin=(start.X, start.Y, start.Z), z_dir=(d.X, d.Y, d.Z))
    if internal:
        shape = _cylinder_radius(shape, face, major / 2, True)
        th = IsoThread(major_diameter=major, pitch=pitch, length=L, external=False, end_finishes=("fade", "fade"))
    else:
        shape = _cylinder_radius(shape, face, minor / 2, False)
        th = IsoThread(major_diameter=major, pitch=pitch, length=L, external=True, end_finishes=("fade", "fade"))
    out = shape + (frame * th)
    if not out.is_valid:
        raise ValueError("the modelled thread produced invalid geometry")
    return out, "modelled " + label


def _part_pattern_locations(part_shape: bd.Shape, edit: dict, path_shape: bd.Shape | None = None) -> list:
    kind = edit.get("type", "rect")
    locs = []
    if kind == "path":
        from . import solidops as solid
        edges = resolve_edges(path_shape if path_shape is not None else part_shape, edit.get("edges") or [])
        if not edges:
            raise ValueError("none of the path edges exist any more")
        wires = bd.Wire.combine(edges)
        if len(wires) != 1:
            raise ValueError("the path edges must connect end to end")
        return solid.path_locations(wires[0], int(edit.get("n", 2)), edit.get("spacing"), bool(edit.get("align", True)))
    if kind == "rect":
        nx, ny = max(1, int(edit.get("nx", 1))), max(1, int(edit.get("ny", 1)))
        d1 = _vec(edit.get("dir1") or (1, 0, 0)).normalized() * float(edit.get("dx", 0))
        d2 = _vec(edit.get("dir2") or (0, 1, 0)).normalized() * float(edit.get("dy", 0))
        for j in range(ny):
            for i in range(nx):
                if i or j:
                    v = d1 * i + d2 * j
                    locs.append(bd.Location((v.X, v.Y, v.Z)))
        return locs
    if kind == "circular":
        n = max(1, int(edit.get("n", 1)))
        total = float(edit.get("angle", 360))
        step = total / n if abs(total) >= 360 else (total / (n - 1) if n > 1 else 0.0)
        axis = bd.Axis(_vec(edit.get("center") or (0, 0, 0)), _vec(edit.get("axis") or (0, 0, 1)))
        return [("rot", axis, k * step) for k in range(1, n)]
    raise ValueError(f"unknown pattern type {kind!r}")


def _transformed(shape: bd.Shape, loc) -> bd.Shape:
    if isinstance(loc, tuple) and loc[0] == "path":
        moved = shape.moved(bd.Location(_xyz(loc[1])))
        return moved.rotate(loc[2], loc[3]) if loc[2] is not None and abs(loc[3]) > 1e-9 else moved
    if isinstance(loc, tuple):
        return shape.rotate(loc[1], loc[2])
    return shape.moved(loc)


def merge_edit(prev: dict, new: dict) -> dict | None:
    """Combine a new edit into the previous one when it continues the same gesture."""
    if prev.get("op") != new.get("op") or prev.get("part") != new.get("part") or prev.get("suppressed") or new.get("suppressed"):
        return None
    op = new["op"]
    same_face = prev.get("face") == new.get("face") and prev.get("faces") == new.get("faces")
    if op == "push_pull" and same_face:
        return {**prev, "distance": round(float(prev["distance"]) + float(new["distance"]), 4)}
    if op == "hole_diameter" and same_face:
        return {**prev, "diameter": new["diameter"]}
    if op == "fillet_radius" and same_face:
        return {**prev, "radius": new["radius"]}
    if op == "chamfer_size" and same_face:
        return {**prev, "size": new["size"]}
    if op == "move_hole" and same_face:
        return {**prev, "delta": [round(a + b, 4) for a, b in zip(prev["delta"], new["delta"])]}
    if op == "move":
        return {**prev, "delta": [round(a + b, 4) for a, b in zip(prev["delta"], new["delta"])]}
    if op == "move_vertices" and all(prev.get(k) == new.get(k) for k in ("vertices", "edges", "faces")):
        return {**prev, "delta": [round(a + b, 4) for a, b in zip(prev["delta"], new["delta"])]}
    if op == "move_face" and same_face and not prev.get("rotate") and not new.get("rotate"):
        return {**prev, "delta": [round(a + b, 4) for a, b in zip(prev.get("delta") or [0, 0, 0], new.get("delta") or [0, 0, 0])]}
    if op == "color":
        return {**prev, **{k: v for k, v in new.items() if k in ("color", "opacity")}}
    if op == "material":
        return {**prev, **{k: v for k, v in new.items() if k in ("material", "color")}}
    if op == "scale" and not isinstance(prev.get("factor"), list) and not isinstance(new.get("factor"), list) and prev.get("about") == new.get("about"):
        return {**prev, "factor": round(float(prev["factor"]) * float(new["factor"]), 6)}
    return None


# ---------------------------------------------------------------------------- operations

@dataclass
class EditNote:
    op: str
    part: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"[{'applied' if self.ok else 'FAILED'}] {self.op} on {self.part}: {self.detail}"


def _push_pull(shape: bd.Shape, face: bd.Face, distance: float) -> bd.Shape:
    """Move a planar face along its normal: extrude it and fuse (+) or cut (-)."""
    n = face.normal_at()

    def tool(eps: float) -> bd.Shape:
        if distance >= 0:
            t = bd.extrude(face, amount=distance + eps)
            return t.moved(bd.Location((-eps * n.X, -eps * n.Y, -eps * n.Z)))
        t = bd.extrude(face, amount=-distance + eps, dir=(-n.X, -n.Y, -n.Z))
        return t.moved(bd.Location((eps * n.X, eps * n.Y, eps * n.Z)))

    def skirt(eps: float) -> bd.Shape:             # the overlap slab just under the face
        return bd.extrude(face, amount=eps, dir=(-n.X, -n.Y, -n.Z))

    return robust_boolean(shape, tool, "add" if distance >= 0 else "cut", "move this face", skirt=skirt if distance >= 0 else None)


def robust_boolean(shape: bd.Shape, make_tool, mode: str, label: str, skirt=None) -> bd.Shape:
    """shape (+|-) make_tool(eps), trying tool overlaps of 0.01, 0 and 0.1 mm until the
    result is a valid solid whose volume makes sense. OCCT occasionally answers a fuse
    or cut against a nearly coincident face with just the tool (or nothing); this
    turns that into a refused edit instead of a silently destroyed part."""
    v0 = abs(shape.volume)
    last: Exception | None = None
    # a small overlap makes OCCT merge coaxial/coplanar faces (a pushed hole stays one face);
    # where the added material overhangs the body, the overlap skirt is cut away again below
    for eps in (0.01, 0.0, 0.1):
        try:
            t = make_tool(eps)
            r = (shape + t) if mode == "add" else (shape - t)
        except Exception as exc:                   # pragma: no cover - kernel exceptions vary
            last = exc
            continue
        if not r.is_valid or not r.solids():
            continue
        v, vt = abs(r.volume), abs(t.volume)
        if mode == "add" and v0 - 1e-3 <= v <= v0 + vt + 1e-3:
            if eps > 0 and skirt is not None:
                try:
                    out = skirt(eps) - shape           # the sliver of overlap outside the original body
                    if out.solids() and abs(out.volume) > 1e-9:
                        r2 = r - out
                        if r2.is_valid and r2.solids():
                            r = r2
                except Exception:
                    pass
            return r
        if mode == "cut" and v0 - vt - 1e-3 <= v <= v0 + 1e-3:
            return r
    raise ValueError(f"the kernel could not {label} cleanly" + (f" ({type(last).__name__}: {last})" if last else "; try a slightly different distance"))


def checked_boolean(before: bd.Shape, result: bd.Shape, mode: str, what: str = "result") -> bd.Shape:
    """A boolean result that must be a valid solid and must not lose (add) or gain (cut) material."""
    result = _valid_solid(result, what)
    v0, v = abs(before.volume), abs(result.volume)
    if (mode == "add" and v < v0 - 1e-3) or (mode == "cut" and v > v0 + 1e-3):
        raise ValueError(f"the kernel lost track of the part while making the {what}; try a slightly different size or position")
    return result


def _push_pull_any(shape: bd.Shape, face: bd.Face, dist: float, sel: dict) -> tuple[bd.Shape, str]:
    if face.geom_type == bd.GeomType.PLANE:
        return _push_pull(shape, face, dist), f"face moved {dist:+g} mm"
    if face.geom_type == bd.GeomType.CYLINDER:
        r, d, start, length, full = _cylinder_extent(face)
        concave = bool(sel.get("concave")) if "concave" in sel else _is_concave(face)
        if not full:                                        # a fillet: + (material added) tightens a round-over, loosens an inside corner
            from .direct import fillet_radius
            new_r = (r + dist) if concave else (r - dist)
            return fillet_radius(shape, face, new_r), f"fillet radius {r:g} -> {new_r:g}"
        new_r = (r - dist) if concave else (r + dist)       # along the outward normal: + adds material
        return _cylinder_radius(shape, face, new_r, concave), f"{'hole' if concave else 'boss'} radius {r:g} -> {new_r:g}"
    if face.geom_type == bd.GeomType.SPHERE:
        return _sphere_radius(shape, face, dist), f"sphere offset {dist:+g} mm"
    raise ValueError(f"push/pull is not available on a {face.geom_type.name.lower()} face; describe the change in the chat instead")


def _hole_diameter(shape: bd.Shape, face: bd.Face, new_d: float) -> bd.Shape:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    surf = BRepAdaptor_Surface(face.wrapped)
    cyl = surf.Cylinder()
    r_old = cyl.Radius()
    ax = cyl.Axis()
    d = Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()).normalized()
    loc = Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
    _, _, vmin, vmax = BRepTools.UVBounds_s(face.wrapped)
    lo, hi = sorted((vmin, vmax))
    length = hi - lo
    start = loc + d * lo
    # fill the old hole over its extent, then cut the new one (slightly longer to clear both ends)
    centre = start + d * (length / 2)
    rot = bd.Plane(origin=(centre.X, centre.Y, centre.Z), z_dir=(d.X, d.Y, d.Z))
    filler = rot * bd.Cylinder(r_old + 0.001, length)
    cutter = rot * bd.Cylinder(new_d / 2, length + 0.2)
    return (shape + filler) - cutter


def _valid_solid(shape: bd.Shape, what: str = "result") -> bd.Shape:
    if not shape.is_valid or len(shape.solids()) == 0:
        raise ValueError(f"{what} is not a valid solid")
    return shape


def _valid_sheet(shape: bd.Shape, what: str = "result") -> bd.Shape:
    if not shape.is_valid or len(shape.faces()) == 0:
        raise ValueError(f"{what} is not a valid surface")
    return shape


def _trim_tool(edit: dict, current: dict, shape: bd.Shape):
    """The tool of a surface trim/split: a plane spec, a face of a part, or a closed sketch profile
    (extruded through everything) with a `keep` side."""
    by = edit.get("by")
    if by is None:
        raise ValueError("say what to trim by: a plane, a face or a sketch profile")
    if isinstance(by, dict) and by.get("face"):
        host = current.get(by.get("part") or edit.get("part"))
        if host is None:
            raise ValueError("the trimming face's part is missing")
        f = resolve_face(host.shape, by["face"])
        if f is None:
            raise ValueError("the trimming face no longer exists")
        return f, "face"
    if isinstance(by, dict) and by.get("profile"):
        frame = face_frame(by.get("normal") or (0, 0, 1), by.get("origin") or (0, 0, 0))
        region = _instances_2d(by["profile"], None, None)
        L = shape.bounding_box().diagonal * 2 + 10
        return bd.extrude(frame.offset(-L) * region, 2 * L), "profile"
    return plane_spec(by, shape, float(edit.get("offset") or 0)), "plane"


def _with(part: Part, shape: bd.Shape | None = None, color: str | None = None, material: str | None = None, name: str | None = None,
          opacity: float | None = None, face_colors: dict | None = None) -> Part:
    return Part(name or part.name, shape if shape is not None else part.shape, color or part.color,
                material if material is not None else getattr(part, "material", None),
                float(opacity) if opacity is not None else float(getattr(part, "opacity", 1.0) or 1.0),
                getattr(part, "group", None), face_colors if face_colors is not None else getattr(part, "face_colors", None))


def _extrude_face_along(region, n: Vector, start: float, amount: float):
    """Extrude a located planar region along n, starting `start` along n (an overlap for booleans)."""
    moved = region.moved(bd.Location(_xyz(n * start)))
    return bd.extrude(moved, amount, dir=n)


def _fmt_extrude_opts(edit: dict) -> str:
    bits = []
    if edit.get("thin"):
        bits.append(f"thin {float(edit['thin']):g} mm")
    if edit.get("symmetric"):
        bits.append("symmetric")
    if edit.get("taper"):
        bits.append(f"taper {float(edit['taper']):g} deg")
    if edit.get("flip"):
        bits.append("flipped")
    return f" ({', '.join(bits)})" if bits else ""


def _fmt_extent(edit: dict, mode: str) -> str:
    ext = edit.get("extent") or ("through" if edit.get("through") and mode == "cut" else "distance")
    if ext == "through":
        return "through"
    if ext == "to_next":
        return "to the next face"
    if ext == "to_face":
        return "to a face"
    return f"{abs(float(edit.get('distance') or 0)):g} mm"


def _fmt_profile(pr: dict) -> str:
    kind = pr.get("type")
    if kind == "sketch":
        from . import sketch as S
        return "sketch (" + S.summary(pr) + ")"
    if kind == "rect":
        return f"{pr.get('w')}x{pr.get('h')} rectangle"
    if kind == "circle":
        return f"Ø{2 * float(pr.get('r', 0)):g} circle"
    if kind == "polygon":
        return f"{len(pr.get('points', []))}-point polygon"
    if kind == "slot":
        return f"{pr.get('length')}x{pr.get('w')} slot"
    if kind == "text":
        return f"text \"{pr.get('text')}\""
    return "sketch"


def _fmt_pattern(edit: dict) -> str:
    s = ""
    p = edit.get("pattern")
    if p:
        s += f" x{len(pattern_steps(p))} {p.get('type')} pattern"
    if edit.get("mirror"):
        s += f" mirrored ({edit['mirror']})"
    return s


def apply_edits(parts: Parts, edits: Sequence[dict]) -> tuple[Parts, list[EditNote]]:
    """Apply EDITS in order; a failing edit is skipped and reported, never fatal.
    Exactly one note per edit, in order."""
    notes: list[EditNote] = []
    current = {p.name: _with(p) for p in parts}
    order = [p.name for p in parts]
    from .assembly import Joint
    joints: list[Joint] = []
    links: list[dict] = []
    sheets: dict = {}                          # part name -> sheetmetal.SheetState
    forms: dict = {}                           # part name -> {"cage": form.Cage, "levels": int}
    flats: dict = {}                           # flat-pattern part name -> {"source": part, "bends": [...]}
    appearances: dict = {}                     # part name -> [(face selectors, colour)], resolved on the finished part

    def add_part(part: Part) -> None:
        current[part.name] = part
        if part.name not in order:
            order.append(part.name)

    def face_of(part: Part, sel: dict, kinds=(bd.GeomType.PLANE,), what: str = "face") -> bd.Face:
        face = resolve_face(part.shape, sel)
        if face is None or (kinds and face.geom_type not in kinds):
            raise ValueError(f"the selected {what} no longer exists" + ("" if not kinds else f" (need a {'/'.join(k.name.lower() for k in kinds)} face)"))
        return face

    def followed_by_form_edit(i: int, part_name) -> bool:
        """The next applied edit is another cage edit of the same form part."""
        for nxt in edits[i + 1:]:
            if nxt.get("suppressed"):
                continue
            return nxt.get("op") == "form_edit" and nxt.get("part") == part_name
        return False

    for idx, edit in enumerate(edits):
        op = edit.get("op")
        name = edit.get("part")
        if edit.get("suppressed"):
            notes.append(EditNote(str(op), str(name), True, "suppressed (not applied)"))
            continue
        creates = (op == "delete_part" or (op == "primitive" and edit.get("mode", "new") == "new")
                   or (op in ("sketch_extrude", "revolve", "loft", "sweep") and edit.get("new_part") and not edit.get("face"))
                   or (op == "surface" and not edit.get("face")) or op in ("stitch", "motion_link")
                   or (op == "base_flange" and not edit.get("face")) or op == "form"
                   or (op in ("move", "rotate", "group", "ungroup") and (edit.get("parts") or edit.get("group")))
                   or (op == "drive_joint")
                   or (op == "insert" and name not in current))
        if not creates and name not in current:
            notes.append(EditNote(str(op), str(name), False, "no such part"))
            continue
        try:
            if op == "push_pull":
                part = current[name]
                sels = edit.get("faces") or [edit["face"]]
                dist = float(edit["distance"])
                shape, details = part.shape, []
                for sel in sels:
                    face = resolve_face(shape, sel)
                    if face is None:
                        raise ValueError("the selected face no longer exists")
                    shape, detail = _push_pull_any(shape, face, dist, sel)
                    details.append(detail)
                current[name] = _with(part, _valid_solid(shape))
                notes.append(EditNote(op, name, True, "; ".join(details) if len(details) > 1 else details[0]))
            elif op in ("sketch_extrude", "revolve", "loft"):
                part = current.get(name)
                mode = edit.get("mode", "add")
                new_part = edit.get("new_part")
                if edit.get("face"):
                    face = face_of(part, edit["face"], what="sketch face")
                    frame = _frame_for(face, edit)
                else:                                           # a sketch on a construction plane (the ground, say)
                    if not edit.get("normal"):
                        raise ValueError("a sketch without a face needs its plane: origin and normal")
                    face = None
                    frame = face_frame(edit["normal"], edit.get("origin") or (0, 0, 0))
                    if edit.get("offset"):
                        frame = frame.offset(float(edit["offset"]))
                    if not new_part and part is None:
                        raise ValueError("nothing to add to: make it a new part")
                if op == "sketch_extrude":
                    what = f"{_fmt_profile(edit['profile'])}{_fmt_pattern(edit)}{_fmt_extrude_opts(edit)}"
                    if new_part:
                        solid = _extrude_new(frame, edit)
                        add_part(Part(new_part, _valid_solid(solid, "the extrusion"), edit.get("color") or "#7FA86B"))
                        notes.append(EditNote(op, new_part, True, f"new part from a {what}, {float(edit['distance']):g} mm"))
                        continue
                    new = _sketch_extrude(part.shape, face, edit["profile"], float(edit.get("distance") or 0), mode, bool(edit.get("through")), edit, frame=frame)
                    detail = f"{mode} {what} {_fmt_extent(edit, mode)}"
                else:
                    axis_shape = None
                    if op == "revolve" and isinstance(edit.get("axis"), dict) and edit["axis"].get("edge"):
                        ap = current.get(edit["axis"].get("part") or name)
                        axis_shape = ap.shape if ap is not None else None
                    solid = _revolve_solid(frame, edit, axis_shape) if op == "revolve" else _loft_solid(frame, edit)
                    what = (f"{_fmt_profile(edit['profile'])} revolved {float(edit.get('angle', 360)):g} deg" if op == "revolve"
                            else f"loft of {len(edit.get('sections', []))} sections")
                    if new_part:
                        add_part(Part(new_part, solid, edit.get("color") or "#7FA86B"))
                        notes.append(EditNote(op, new_part, True, f"new part: {what}"))
                        continue
                    new = checked_boolean(part.shape, (part.shape - solid) if mode == "cut" else (part.shape + solid), "cut" if mode == "cut" else "add", what)
                    detail = f"{mode} {what}"
                current[name] = _with(part, _valid_solid(new))
                notes.append(EditNote(op, name, True, detail))
            elif op == "hole":
                part = current[name]
                face = face_of(part, edit["face"], kinds=(bd.GeomType.PLANE, bd.GeomType.CYLINDER), what="face for the hole")
                new = _hole_tool(part.shape, face, edit["at"], edit)
                current[name] = _with(part, _valid_solid(new))
                kind = (edit.get("size") or "Ø" + str(edit.get("d"))) + (" tapped" if edit.get("tapped") else "") + (" " + edit["head"] if edit.get("head") else "")
                notes.append(EditNote(op, name, True, f"hole {kind} at ({float(edit['at'][0]):g}, {float(edit['at'][1]):g}){_fmt_pattern(edit)}" + (f" on a {face.geom_type.name.lower()}" if face.geom_type != bd.GeomType.PLANE else "")))
            elif op == "move_hole":
                part = current[name]
                face = face_of(part, edit["face"], kinds=(bd.GeomType.CYLINDER,), what="hole")
                new = _move_hole(part.shape, face, edit["delta"])
                if not new.is_valid or len(new.solids()) != len(part.shape.solids()):
                    raise ValueError("result is not a valid solid")
                current[name] = _with(part, new)
                notes.append(EditNote(op, name, True, f"hole moved by {tuple(round(float(c), 2) for c in edit['delta'])}"))
            elif op in ("fillet", "chamfer"):
                part = current[name]
                edges = resolve_edges(part.shape, edit.get("edges", []))
                if not edges:
                    raise ValueError("none of the selected edges exist any more")
                if op == "fillet":
                    r_end = edit.get("radius_end")
                    if r_end not in (None, "") and abs(float(r_end) - float(edit["radius"])) > 1e-9:
                        from . import solidops as _solid
                        new = _solid.variable_fillet(part.shape, edges, float(edit["radius"]), float(r_end))
                        detail = f"r={float(edit['radius']):g}->{float(r_end):g} on {len(edges)} edge(s)"
                    else:
                        new = bd.fillet(edges, float(edit["radius"]))
                        detail = f"r={float(edit['radius']):g} on {len(edges)} edge(s)"
                else:
                    size2, angle = edit.get("size2"), edit.get("angle")
                    if size2 not in (None, "") or angle not in (None, ""):
                        from . import solidops as _solid
                        ref = resolve_face(part.shape, edit["reference"]) if edit.get("reference") else _solid.reference_face(part.shape, edges)
                        if ref is None:
                            raise ValueError("the chamfer's reference face no longer exists")
                        new = bd.chamfer(edges, float(edit["size"]), length2=float(size2) if size2 not in (None, "") else None,
                                         angle=float(angle) if angle not in (None, "") else None, reference=ref)
                        detail = (f"{float(edit['size']):g} x {float(size2):g} mm" if size2 not in (None, "") else f"{float(edit['size']):g} mm at {float(angle):g} deg") + f" on {len(edges)} edge(s)"
                    else:
                        new = bd.chamfer(edges, float(edit["size"]))
                        detail = f"{float(edit['size']):g} mm on {len(edges)} edge(s)"
                if not new.is_valid:
                    raise ValueError("kernel produced invalid geometry")
                current[name] = _with(part, new)
                notes.append(EditNote(op, name, True, detail))
            elif op == "hole_diameter":
                part = current[name]
                face = face_of(part, edit["face"], kinds=(bd.GeomType.CYLINDER,), what="hole")
                new = _hole_diameter(part.shape, face, float(edit["diameter"]))
                if not new.is_valid or len(new.solids()) != len(part.shape.solids()):
                    raise ValueError("result is not a valid solid")
                current[name] = _with(part, new)
                notes.append(EditNote(op, name, True, f"now Ø{float(edit['diameter']):g}"))
            elif op == "shell":
                part = current[name]
                openings = resolve_faces(part.shape, edit.get("faces") or [])
                if edit.get("faces") and not openings:
                    raise ValueError("none of the opening faces exist any more")
                t = float(edit["thickness"])
                if t <= 0:
                    raise ValueError("wall thickness must be positive")
                if openings:
                    new = bd.offset(part.shape, amount=-t, openings=openings)
                else:                                   # closed hollow body: outer minus the inner offset
                    new = part.shape - bd.offset(part.shape, amount=-t)
                new = _valid_solid(new, "the shell")
                if abs(new.volume) >= abs(part.shape.volume):
                    raise ValueError("shelling did not remove material (walls thicker than the part?)")
                current[name] = _with(part, new)
                notes.append(EditNote(op, name, True, f"walls {t:g} mm, {len(openings)} opening(s)"))
            elif op == "thread":
                part = current[name]
                face = face_of(part, edit["face"], kinds=(bd.GeomType.CYLINDER,), what="cylindrical face")
                new, detail = _thread(part.shape, face, edit)
                current[name] = _with(part, _valid_solid(new))
                notes.append(EditNote(op, name, True, detail))
            elif op == "primitive":
                kind = edit.get("kind", "box")
                mode = edit.get("mode", "new")
                host = current[name].shape if (name in current and edit.get("face")) else None
                solid = _place_primitive(primitive_solid(kind, edit.get("size") or {}), edit, host)
                if mode == "new":
                    pname = edit.get("new_part") or (name if name and name not in current else kind)
                    k = 2
                    base = pname
                    while pname in current:
                        pname = f"{base}_{k}"
                        k += 1
                    add_part(Part(pname, _valid_solid(solid, "the primitive"), edit.get("color") or "#7FA86B"))
                    notes.append(EditNote(op, pname, True, f"new {kind}"))
                else:
                    part = current[name]
                    new = checked_boolean(part.shape, (part.shape - solid) if mode == "cut" else (part.shape + solid), "cut" if mode == "cut" else "add", kind)
                    current[name] = _with(part, new)
                    notes.append(EditNote(op, name, True, f"{kind} {'cut from' if mode == 'cut' else 'joined to'} {name}"))
            elif op == "combine":
                part = current[name]
                tool_name = edit.get("tool")
                if tool_name not in current:
                    raise ValueError(f"no such tool part {tool_name!r}")
                tool = current[tool_name].shape
                mode = edit.get("mode", "join")
                if mode == "join":
                    new = checked_boolean(part.shape, part.shape + tool, "add", "combination")
                elif mode == "cut":
                    new = checked_boolean(part.shape, part.shape - tool, "cut", "combination")
                elif mode == "intersect":
                    new = checked_boolean(part.shape, part.shape & tool, "cut", "intersection")
                else:
                    raise ValueError("mode must be join, cut or intersect")
                current[name] = _with(part, new)
                if not edit.get("keep_tool") and tool_name != name:
                    del current[tool_name]
                    order.remove(tool_name)
                notes.append(EditNote(op, name, True, f"{mode} {tool_name}" + (" (kept)" if edit.get("keep_tool") else "")))
            elif op == "split":
                part = current[name]
                plane = plane_spec(edit.get("plane", "XY"), part.shape, float(edit.get("offset", 0)), edit.get("through", "origin"))
                keep = edit.get("keep", "both")
                top = bd.split(part.shape, bisect_by=plane, keep=bd.Keep.TOP)
                bottom = bd.split(part.shape, bisect_by=plane, keep=bd.Keep.BOTTOM)
                halves = {"top": top, "bottom": bottom}
                for k, h in halves.items():
                    if h is None or not h.solids():
                        halves[k] = None
                if keep in ("top", "bottom"):
                    if halves[keep] is None:
                        raise ValueError(f"nothing on the {keep} side of the plane")
                    current[name] = _with(part, halves[keep])
                    notes.append(EditNote(op, name, True, f"kept the {keep} side"))
                else:
                    if halves["top"] is None or halves["bottom"] is None:
                        raise ValueError("the plane does not cut the part")
                    current[name] = _with(part, halves["top"])
                    other = edit.get("new_part") or f"{name}_2"
                    add_part(Part(other, halves["bottom"], edit.get("color") or part.color))
                    notes.append(EditNote(op, name, True, f"split; the other side is part {other}"))
            elif op == "mirror":
                part = current[name]
                plane = plane_spec(edit.get("plane", "YZ"), part.shape, float(edit.get("offset", 0)), edit.get("through", "origin"))
                mirrored = bd.mirror(part.shape, about=plane)
                if edit.get("new_part"):
                    add_part(Part(edit["new_part"], _valid_solid(mirrored, "the mirror"), edit.get("color") or part.color))
                    notes.append(EditNote(op, name, True, f"mirrored into new part {edit['new_part']}"))
                else:
                    current[name] = _with(part, _valid_solid(part.shape + mirrored))
                    notes.append(EditNote(op, name, True, "mirrored and joined"))
            elif op == "pattern":
                part = current[name]
                path_part = current.get(edit.get("path_part") or name) if edit.get("type") == "path" else None
                locs = _part_pattern_locations(part.shape, edit, path_part.shape if path_part is not None else None)
                copies = [_transformed(part.shape, loc) for loc in locs]
                if edit.get("join", False):
                    new = part.shape
                    for c in copies:
                        new = new + c
                    current[name] = _with(part, _valid_solid(new))
                else:
                    for k, c in enumerate(copies, start=2):
                        add_part(Part(f"{name}_{k}", c, part.color, getattr(part, "material", None)))
                notes.append(EditNote(op, name, True, f"{len(copies) + 1} instances, {edit.get('type', 'rect')}" + (" joined" if edit.get("join") else "")))
            elif op == "rib":
                from . import sketch as S
                from . import solidops as _solid
                part = current[name]
                if edit.get("face"):
                    face = face_of(part, edit["face"], what="sketch face")
                    frame = _frame_for(face, edit)
                else:
                    if not edit.get("normal"):
                        raise ValueError("a rib without a face needs its sketch plane: origin and normal")
                    frame = face_frame(edit["normal"], edit.get("origin") or (0, 0, 0))
                pr = edit["profile"]
                if pr.get("type") == "line":
                    pts = [tuple(float(c) for c in q) for q in pr["points"]]
                    chains = [bd.Wire([bd.Edge.make_line((*a, 0), (*b, 0)) for a, b in zip(pts, pts[1:])])]
                else:
                    sk = pr if pr.get("type") == "sketch" else S.from_simple(pr)
                    if sk.get("constraints") or sk.get("dims"):
                        st = S.solve(sk)
                        if not st["ok"]:
                            raise ValueError("the sketch's constraints conflict: " + ", ".join(st["conflicts"][:4]))
                    chains = S.open_chains(sk)
                if len(chains) != 1:
                    raise ValueError("a rib needs exactly one open chain in its sketch")
                dirs = {"u": (1, 0), "-u": (-1, 0), "v": (0, 1), "-v": (0, -1)}
                direction = edit.get("direction", "-v")
                d2 = dirs.get(direction) if isinstance(direction, str) else tuple(float(c) for c in direction)
                if not d2:
                    raise ValueError("rib direction: u, -u, v, -v or [du, dv]")
                L = part.shape.bounding_box().diagonal * 2
                piece = _solid.rib_piece(part.shape, frame, chains[0], d2, float(edit.get("thickness", 2)), edit.get("distance"), L)
                new = checked_boolean(part.shape, part.shape + piece, "add", "the rib")
                current[name] = _with(part, _valid_solid(new))
                notes.append(EditNote(op, name, True, f"rib {float(edit.get('thickness', 2)):g} mm thick" + (f", {float(edit['distance']):g} mm" if edit.get("distance") else " to the next face")))
            elif op == "boss":
                from .lib import features as F
                part = current[name]
                face = face_of(part, edit["face"], what="face")
                frame = _frame_for(face, edit)
                u, v = (float(c) for c in (edit.get("at") or (0, 0))[:2])
                height = float(edit.get("height", 6))
                kind = edit.get("kind", "plain")
                if kind == "plain":
                    d_out, hole_d, hole_depth = float(edit.get("d", 8)), edit.get("hole_d"), edit.get("hole_depth")
                else:
                    from .lib.fasteners import heat_set_insert_dims, screw_clearance, tap_drill
                    size = edit.get("size", "M3")
                    if kind == "insert":
                        hole_d, hole_depth = heat_set_insert_dims(size)
                    elif kind == "tap":
                        hole_d, hole_depth = tap_drill(size), min(height - 1, 4 * screw_clearance(size))
                    elif kind == "clearance":
                        hole_d, hole_depth = screw_clearance(size), height + 2
                    else:
                        raise ValueError("boss kind: plain, insert, tap or clearance")
                    d_out = float(edit.get("d") or (hole_d + 2 * max(1.6, hole_d * 0.35)))
                    if edit.get("hole_depth") not in (None, ""):
                        hole_depth = float(edit["hole_depth"])
                make = lambda eps: frame * bd.Pos(u, v, -eps) * bd.Cylinder(d_out / 2, height + eps, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
                new = robust_boolean(part.shape, make, "add", "the boss")
                if hole_d:
                    depth = float(hole_depth) if hole_depth not in (None, "") else height
                    cutter = lambda eps: frame * bd.Pos(u, v, height + eps) * bd.Cylinder(float(hole_d) / 2, depth + eps, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MAX))
                    new = robust_boolean(new, cutter, "cut", "the boss hole")
                current[name] = _with(part, _valid_solid(new))
                notes.append(EditNote(op, name, True, f"{kind} boss Ø{d_out:g} x {height:g}" + (f" with Ø{float(hole_d):g} hole" if hole_d else "")))
            elif op == "lip":
                from . import solidops as _solid
                part = current[name]
                face = face_of(part, edit["face"], what="rim face")
                mode = edit.get("mode", "lip")
                band = _solid.rim_band(face, float(edit.get("width", 1)), edit.get("side", "inner"))
                region = _solid.sketch_of(band)
                n = face.normal_at()
                h = float(edit.get("height", 1))
                if mode == "lip":
                    make = lambda eps: _extrude_face_along(region, n, -eps, h + eps)
                    new = robust_boolean(part.shape, make, "add", "the lip")
                else:
                    make = lambda eps: _extrude_face_along(region, n, eps, -(h + eps))
                    new = robust_boolean(part.shape, make, "cut", "the groove")
                current[name] = _with(part, _valid_solid(new))
                notes.append(EditNote(op, name, True, f"{mode} {float(edit.get('width', 1)):g} wide x {h:g} high on the {edit.get('side', 'inner')} edge"))
            elif op == "surface":
                from . import surfaces as SF
                kind = edit.get("kind", "extrude")
                host = current.get(name) if name else None
                if edit.get("face") and host is not None:
                    face = face_of(host, edit["face"], what="sketch face")
                    frame = _frame_for(face, edit)
                else:
                    if not edit.get("normal"):
                        raise ValueError("a surface without a face needs its sketch plane: origin and normal")
                    frame = face_frame(edit["normal"], edit.get("origin") or (0, 0, 0))
                    if edit.get("offset"):
                        frame = frame.offset(float(edit["offset"]))
                if kind == "planar":
                    shape = SF.planar_surface(frame, edit["profile"])
                    what = f"planar {_fmt_profile(edit['profile'])}"
                elif kind == "extrude":
                    shape = SF.extrude_surface(frame, edit["profile"], float(edit.get("distance") or 10), bool(edit.get("symmetric")), bool(edit.get("flip")))
                    what = f"{_fmt_profile(edit['profile'])} extruded {float(edit.get('distance') or 10):g} mm"
                elif kind == "revolve":
                    axis_shape = None
                    if isinstance(edit.get("axis"), dict) and edit["axis"].get("edge"):
                        ap = current.get(edit["axis"].get("part") or name)
                        axis_shape = ap.shape if ap is not None else None
                    a, b = _revolve_axis(frame, edit, axis_shape)
                    shape = SF.revolve_surface(frame, edit["profile"], a, b, float(edit.get("angle", 360)))
                    what = f"{_fmt_profile(edit['profile'])} revolved {float(edit.get('angle', 360)):g} deg"
                elif kind == "sweep":
                    path_part = current.get(edit.get("path_part") or name)
                    if path_part is None:
                        raise ValueError("no part holds the path edges")
                    edges = resolve_edges(path_part.shape, edit.get("path") or [])
                    wires = bd.Wire.combine(edges) if edges else []
                    if len(wires) != 1:
                        raise ValueError("the path edges must exist and connect end to end")
                    shape = SF.sweep_surface(frame, edit["profile"], wires[0])
                    what = f"{_fmt_profile(edit['profile'])} swept along {len(edges)} edge(s)"
                elif kind == "loft":
                    shape = SF.loft_surface(frame, edit.get("sections") or [], edit.get("profile"), bool(edit.get("ruled")))
                    what = f"loft of {len(edit.get('sections') or [])} sections"
                else:
                    raise ValueError("surface kind: planar, extrude, revolve, sweep or loft")
                new_name = edit.get("new_part") or "surface"
                base, k = new_name, 2
                while new_name in current:
                    new_name = f"{base}_{k}"
                    k += 1
                add_part(Part(new_name, _valid_sheet(shape, "the surface"), edit.get("color") or "#9BB7D4"))
                notes.append(EditNote(op, new_name, True, f"surface: {what} ({len(shape.faces())} face(s), {shape.area:.1f} mm²)"))
            elif op in ("surface_offset", "surface_extend", "surface_trim", "surface_split", "thicken", "to_solid", "patch", "stitch"):
                from . import surfaces as SF
                if op == "stitch":
                    names = edit.get("parts") or ([name] if name else [])
                    missing = [n for n in names if n not in current]
                    if missing or len(names) < 1:
                        raise ValueError(f"parts to stitch not found: {missing or names}")
                    faces = [f for n in names for f in current[n].shape.faces()]
                    new = SF.sew(faces, float(edit.get("tolerance") or 1e-3), solid_if_closed=True)
                    first = current[names[0]]
                    current[names[0]] = _with(first, new)
                    for n in names[1:]:
                        del current[n]
                        order.remove(n)
                    closed = bool(new.solids())
                    notes.append(EditNote(op, names[0], True, f"stitched {len(names)} part(s), {len(faces)} faces -> {'a closed solid' if closed else str(len(new.faces())) + ' faces, ' + str(len(SF.free_edges(new))) + ' free edge(s)'}"))
                    continue
                part = current[name]
                shape = part.shape
                if op == "surface_offset":
                    new = SF.offset_surface(shape, float(edit["distance"]))
                    current[name] = _with(part, _valid_sheet(new)); detail = f"offset {float(edit['distance']):+g} mm"
                elif op == "surface_extend":
                    edges = resolve_edges(shape, edit.get("edges") or []) if edit.get("edges") else None
                    if edit.get("edges") and not edges:
                        raise ValueError("none of the edges to extend exist any more")
                    new = SF.extend_surface(shape, float(edit.get("distance", 5)), edges, edit.get("mode", "natural"))
                    current[name] = _with(part, _valid_sheet(new)); detail = f"extended {float(edit.get('distance', 5)):g} mm ({edit.get('mode', 'natural')}, {len(edges) if edges else 'all'} edge(s))"
                elif op in ("surface_trim", "surface_split"):
                    tool, tkind = _trim_tool(edit, current, shape)
                    if op == "surface_trim":
                        keep = edit.get("keep") or ("inside" if tkind == "profile" else "top")
                        new = SF.trim_surface(shape, tool, keep)
                        current[name] = _with(part, _valid_sheet(new)); detail = f"trimmed by a {tkind}, kept {keep}: {len(new.faces())} face(s)"
                    else:
                        a, b = SF.split_surface(shape, tool)
                        current[name] = _with(part, _valid_sheet(a))
                        other = edit.get("new_part") or f"{name}_2"
                        k, base = 2, other
                        while other in current:
                            other = f"{base}_{k}"; k += 1
                        add_part(Part(other, _valid_sheet(b), part.color, getattr(part, "material", None)))
                        detail = f"split by a {tkind} into {name} and {other}"
                elif op == "thicken":
                    new = SF.thicken_surface(shape, float(edit.get("thickness", 2)), bool(edit.get("both")))
                    current[name] = _with(part, _valid_solid(new, "the thickened body")); detail = f"thickened {float(edit.get('thickness', 2)):g} mm{' both sides' if edit.get('both') else ''}: now a solid of {abs(new.volume):.1f} mm³"
                elif op == "to_solid":
                    new = SF.to_solid(shape, float(edit.get("tolerance") or 1e-3))
                    current[name] = _with(part, _valid_solid(new, "the solid")); detail = f"closed surface -> solid of {abs(new.volume):.1f} mm³"
                else:                                       # patch
                    edges = resolve_edges(shape, edit.get("edges") or [])
                    if not edges:
                        raise ValueError("none of the opening's edges exist any more")
                    face = SF.patch(edges, edit.get("continuity", "G0"), shape)
                    if SF.is_sheet(shape) and not edit.get("new_part"):
                        new = SF.sew(list(shape.faces()) + [face], solid_if_closed=bool(edit.get("close", True)))
                        current[name] = _with(part, new if new.solids() else _valid_sheet(new))
                        detail = f"patched {len(edges)} edge(s) ({edit.get('continuity', 'G0')}): {'now a closed solid' if new.solids() else str(len(SF.free_edges(new))) + ' free edge(s) left'}"
                    else:
                        pname = edit.get("new_part") or "patch"
                        k, base = 2, pname
                        while pname in current:
                            pname = f"{base}_{k}"; k += 1
                        add_part(Part(pname, face, edit.get("color") or "#9BB7D4"))
                        detail = f"new surface {pname} over {len(edges)} edge(s)"
                        name = pname
                notes.append(EditNote(op, name, True, detail))
            elif op == "base_flange":
                from . import sheetmetal as SM
                host = current.get(name) if name else None
                if edit.get("face") and host is not None:
                    face = face_of(host, edit["face"], what="sketch face")
                    frame = _frame_for(face, edit)
                else:
                    if not edit.get("normal"):
                        raise ValueError("a base flange without a face needs its sketch plane: origin and normal")
                    frame = face_frame(edit["normal"], edit.get("origin") or (0, 0, 0))
                    if edit.get("offset"):
                        frame = frame.offset(float(edit["offset"]))
                region = _instances_2d(edit["profile"], None, None)
                faces = region.faces()
                if len(faces) != 1:
                    raise ValueError("a base flange needs one closed profile")
                solid, st = SM.base_flange(frame, faces[0], float(edit.get("thickness", 2)), edit.get("radius"), float(edit.get("k", 0.44)))
                new_name = edit.get("new_part") or "sheet"
                base, k2 = new_name, 2
                while new_name in current:
                    new_name = f"{base}_{k2}"; k2 += 1
                add_part(Part(new_name, _valid_solid(solid, "the sheet"), edit.get("color") or "#B0BEC5", edit.get("material") or "steel"))
                sheets[new_name] = st
                notes.append(EditNote(op, new_name, True, f"sheet {_fmt_profile(edit['profile'])}, t = {st.thickness:g} mm, bend radius {st.radius:g}, K {st.k:g}"))
            elif op in ("flange", "hem", "contour_flange", "tab", "sheet_cut", "flat_pattern", "sheet_rules"):
                from . import sheetmetal as SM
                part = current[name]
                st = sheets.get(name)
                if st is None:
                    raise ValueError(f"{name} is not a sheet-metal part (start with base_flange)")
                if op == "sheet_rules":
                    if edit.get("radius") not in (None, ""):
                        st.radius = float(edit["radius"])
                    if edit.get("k") not in (None, ""):
                        st.k = float(edit["k"])
                    notes.append(EditNote(op, name, True, f"bend radius {st.radius:g}, K {st.k:g}"))
                    continue
                if op in ("flange", "hem", "contour_flange"):
                    edges = resolve_edges(part.shape, edit.get("edges") or ([edit["edge"]] if edit.get("edge") else []))
                    if not edges:
                        raise ValueError("none of the selected edges exist any more")
                    shape = part.shape
                    details = []
                    for e in edges:
                        if op == "contour_flange":
                            legs = [(float(l[0]), float(l[1])) for l in (edit.get("legs") or [])]
                            shape, st, info = SM.contour_flange(st, shape, e, legs, edit.get("radius"), float(edit.get("inset", 0) or 0), edit.get("k"))
                            details.append(", ".join(f"{l['length']:g} mm at {l['angle']:g} deg" for l in info["legs"]) + f"; unfolded {info['unfolded']:g} mm")
                        elif op == "flange":
                            shape, st, info = SM.flange(st, shape, e, float(edit.get("height", 10)), float(edit.get("angle", 90)), edit.get("radius"), float(edit.get("inset", 0) or 0), edit.get("k"))
                            details.append(f"{info['height']:g} mm at {info['angle']:g} deg{' down' if not info['up'] else ''}, allowance {info['allowance']:g}")
                        else:
                            shape, st, info = SM.hem(st, shape, e, float(edit.get("length", 8)), edit.get("gap"), float(edit.get("inset", 0) or 0))
                            details.append(f"hem {info['height']:g} mm, allowance {info['allowance']:g}")
                    current[name] = _with(part, _valid_solid(shape))
                    notes.append(EditNote(op, name, True, f"{len(edges)} edge(s): " + "; ".join(details)))
                elif op in ("tab", "sheet_cut"):
                    face = face_of(part, edit["face"], what="sheet face")
                    frame = _frame_for(face, edit)
                    region = _instances_2d(edit["profile"], edit.get("pattern"), edit.get("mirror"))
                    faces = region.faces()
                    if not faces:
                        raise ValueError("the profile has no closed region")
                    shape = part.shape
                    for f2 in faces:
                        shape, st = SM.tab(st, shape, frame, f2) if op == "tab" else SM.sheet_cut(st, shape, frame, f2)
                    current[name] = _with(part, _valid_solid(shape))
                    notes.append(EditNote(op, name, True, f"{'tab' if op == 'tab' else 'cut'} {_fmt_profile(edit['profile'])}; flat pattern {st.flat.area:.1f} mm²"))
                else:                                       # flat_pattern: a part laid out next to the folded one
                    at = None
                    if edit.get("at") or edit.get("beside", True):
                        base = st.base_frame
                        if edit.get("at"):
                            at = bd.Plane(origin=_vec(edit["at"]), x_dir=base.x_dir, z_dir=base.z_dir)
                        else:
                            bb = part.shape.bounding_box(); fb = st.flat.bounding_box()
                            shift = (bb.max.X - bb.min.X) + (fb.max.X - fb.min.X) * 0 + 15 + max(0.0, -fb.min.X)
                            at = bd.Plane(origin=base.origin + Vector(bb.max.X - base.origin.X + 15 - fb.min.X, 0, 0), x_dir=base.x_dir, z_dir=base.z_dir)
                    solid, lines = SM.flat_pattern(st, at)
                    fname = edit.get("new_part") or f"{name}_flat"
                    if fname in current and fname not in flats:
                        raise ValueError(f"a part named {fname!r} already exists")
                    add_part(Part(fname, _valid_solid(solid, "the flat pattern"), "#CFD8DC", getattr(part, "material", None), 0.85))
                    flats[fname] = {"source": name, "bends": lines, "thickness": st.thickness, "area": round(st.flat.area, 3)}
                    fb = st.flat.bounding_box()
                    notes.append(EditNote(op, fname, True, f"flat pattern {fb.max.X - fb.min.X:.1f} x {fb.max.Y - fb.min.Y:.1f} mm, {st.flat.area:.1f} mm², {len(lines)} bend line(s)"))
            elif op == "form":
                from . import form as FM
                kind = edit.get("kind", "box")
                if edit.get("cage"):
                    cage = FM.Cage.from_dict(edit["cage"])
                else:
                    if kind not in FM.PRIMITIVES:
                        raise ValueError(f"form primitive {kind!r}: " + ", ".join(FM.PRIMITIVES))
                    args = {k: v for k, v in (edit.get("size") or {}).items() if v not in (None, "")}
                    if edit.get("at"):
                        args["center"] = tuple(float(c) for c in edit["at"])
                    cage = FM.PRIMITIVES[kind](**args)
                if edit.get("symmetry"):
                    cage.symmetry = edit["symmetry"]
                levels = int(edit.get("levels", 2))
                solid = FM.to_solid(cage, levels)
                new_name = edit.get("new_part") or "form"
                base, k2 = new_name, 2
                while new_name in current:
                    new_name = f"{base}_{k2}"; k2 += 1
                add_part(Part(new_name, _valid_solid(solid, "the form"), edit.get("color") or "#C9A0DC"))
                forms[new_name] = {"cage": cage, "levels": levels}
                notes.append(EditNote(op, new_name, True, FM.cage_summary(cage, levels)))
            elif op == "form_edit":
                from . import form as FM
                part = current[name]
                st = forms.get(name)
                if st is None:
                    raise ValueError(f"{name} is not a form body (start with a form op)")
                cage, levels = st["cage"], st["levels"]
                action = edit.get("action", "move_vertices")
                if action == "move_vertices":
                    FM.move_vertices(cage, edit.get("vertices") or [], edit.get("delta") or (0, 0, 0)); detail = f"moved {len(edit.get('vertices') or [])} cage vertex/vertices by {edit.get('delta')}"
                elif action == "move_face":
                    FM.move_face(cage, int(edit["face"]), edit.get("delta"), edit.get("along_normal")); detail = f"moved cage face {edit['face']}"
                elif action == "extrude_face":
                    ids = FM.extrude_face(cage, int(edit["face"]), float(edit.get("distance", 10)), float(edit.get("scale", 1.0))); detail = f"extruded cage face {edit['face']} by {float(edit.get('distance', 10)):g} mm (new vertices {ids})"
                elif action == "insert_loop":
                    n = FM.insert_loop(cage, edit["edge"], float(edit.get("t", 0.5))); detail = f"inserted an edge loop across {n} edge(s)"
                elif action == "crease":
                    FM.crease(cage, edit.get("edges") or [], bool(edit.get("on", True))); detail = f"{'creased' if edit.get('on', True) else 'uncreased'} {len(edit.get('edges') or [])} edge(s)"
                elif action == "symmetry":
                    cage.symmetry = edit.get("axis") or None; FM.apply_symmetry(cage); detail = f"symmetry {cage.symmetry or 'off'}"
                elif action == "levels":
                    levels = int(edit.get("levels", 2)); st["levels"] = levels; detail = f"{levels} subdivision level(s)"
                else:
                    raise ValueError("form_edit action: move_vertices, move_face, extrude_face, insert_loop, crease, symmetry or levels")
                if not followed_by_form_edit(idx, name):          # a run of cage edits makes the smooth body once, at its end
                    solid = FM.to_solid(cage, levels)
                    current[name] = _with(part, _valid_solid(solid, "the form"))
                notes.append(EditNote(op, name, True, detail + "; " + FM.cage_summary(cage, levels)))
            elif op == "copy_part":
                part = current[name]
                new_name = edit.get("new_part") or f"{name}_copy"
                if new_name in current:
                    raise ValueError(f"a part named {new_name!r} already exists")
                delta = tuple(float(c) for c in (edit.get("delta") or (0, 0, 0)))
                add_part(Part(new_name, part.shape.moved(bd.Location(delta)), part.color, getattr(part, "material", None)))
                notes.append(EditNote(op, new_name, True, f"copy of {name}" + (f" moved by {delta}" if any(delta) else "")))
            elif op == "repair":
                from . import solidops as _solid
                part = current[name]
                fixed, report = _solid.repair(part.shape)
                current[name] = _with(part, fixed)
                notes.append(EditNote(op, name, True, report))
            elif op == "scale":
                part = current[name]
                f = edit.get("factor", 1)
                factor = tuple(float(c) for c in f) if isinstance(f, (list, tuple)) else float(f)
                about = _vec(edit["about"]) if edit.get("about") else part.shape.bounding_box().center()
                new = part.shape.scale(factor, about=about)
                current[name] = _with(part, _valid_solid(new))
                notes.append(EditNote(op, name, True, f"x{factor}" if not isinstance(factor, tuple) else f"x({', '.join(f'{c:g}' for c in factor)})"))
            elif op == "align":
                part = current[name]
                tgt_name = edit.get("to_part")
                if tgt_name not in current:
                    raise ValueError(f"no such part {tgt_name!r}")
                mov_face = face_of(part, edit["face"], kinds=(), what="face")
                tgt_face = face_of(current[tgt_name], edit["to_face"], kinds=(), what="target face")
                new, what = _align(part.shape, mov_face, tgt_face, float(edit.get("offset", 0)), bool(edit.get("flip")), edit.get("center", True))
                current[name] = _with(part, new)
                notes.append(EditNote(op, name, True, f"{what} with {tgt_name}"))
            elif op == "move_vertices":
                from . import direct
                part = current[name]
                pts = resolve_vertices(part.shape, edit.get("vertices") or [])
                for e in resolve_edges(part.shape, edit.get("edges") or []):
                    pts += [e.position_at(0), e.position_at(1)]
                for sel in edit.get("faces") or []:
                    f = resolve_face(part.shape, sel)
                    if f is not None:
                        pts += [Vector(v.X, v.Y, v.Z) for v in f.vertices()]
                if not pts:
                    raise ValueError("none of the selected corners exist any more")
                new = direct.move_vertices(part.shape, pts, edit["delta"])
                current[name] = _with(part, _valid_solid(new))
                dx, dy, dz = (float(v) for v in edit["delta"])
                notes.append(EditNote(op, name, True, f"{len(pts)} corner(s) moved by ({dx:g}, {dy:g}, {dz:g})"))
            elif op == "move_face":
                from . import direct
                part = current[name]
                face = face_of(part, edit["face"], kinds=(), what="face")
                new = direct.move_face(part.shape, face, edit.get("delta") or (0, 0, 0), edit.get("rotate"))
                current[name] = _with(part, _valid_solid(new))
                dx, dy, dz = (float(v) for v in (edit.get("delta") or (0, 0, 0)))
                rot = edit.get("rotate") or {}
                notes.append(EditNote(op, name, True, f"{face.geom_type.name.lower()} face moved by ({dx:g}, {dy:g}, {dz:g})" + (f", tilted {float(rot.get('angle', 0)):g} deg" if rot.get("angle") else "")))
            elif op == "chamfer_size":
                from . import direct
                part = current[name]
                face = face_of(part, edit["face"], kinds=(bd.GeomType.PLANE,), what="chamfer face")
                new = direct.chamfer_size(part.shape, face, float(edit["size"]))
                current[name] = _with(part, _valid_solid(new))
                notes.append(EditNote(op, name, True, f"chamfer now {float(edit['size']):g} mm"))
            elif op == "sweep":
                from . import direct
                part = current.get(name)
                new_part = edit.get("new_part")
                if edit.get("face"):
                    face = face_of(part, edit["face"], what="sketch face")
                    frame = _frame_for(face, edit)
                else:
                    if not edit.get("normal"):
                        raise ValueError("a sweep without a face needs its sketch plane: origin and normal")
                    frame = face_frame(edit["normal"], edit.get("origin") or (0, 0, 0))
                path_part = current.get(edit.get("path_part") or name) or part
                if path_part is None:
                    raise ValueError("no part holds the path edges")
                edges = resolve_edges(path_part.shape, edit.get("path") or [])
                if not edges:
                    raise ValueError("none of the path edges exist any more")
                solid = direct.sweep_profile(frame * _profile_sketch(edit["profile"]), edges)
                what = f"{_fmt_profile(edit['profile'])} swept along {len(edges)} edge(s)"
                if new_part or part is None:
                    add_part(Part(new_part or "sweep", solid, edit.get("color") or "#7FA86B"))
                    notes.append(EditNote(op, new_part or "sweep", True, "new part: " + what))
                else:
                    mode = edit.get("mode", "add")
                    new = checked_boolean(part.shape, (part.shape - solid) if mode == "cut" else (part.shape + solid), "cut" if mode == "cut" else "add", what)
                    current[name] = _with(part, new)
                    notes.append(EditNote(op, name, True, f"{mode} {what}"))
            elif op == "insert":
                from .lib.parts import bearing, hex_nut, screw, washer
                kind = edit.get("kind", "screw")
                if kind in ("step", "model", "mesh", "iges"):   # a component from a STEP / IGES / mesh file or another eitri model
                    from .model import load_model, load_step_file
                    from pathlib import Path as _P
                    path = str(edit.get("path") or "")
                    if not path or not _P(path).exists():
                        raise ValueError(f"no such file: {path!r}")
                    comp = load_step_file(path) if kind == "step" else load_model(path, {"decimate": str(edit["decimate"])} if edit.get("decimate") else None).build()
                    at = _vec(edit.get("at") or (0, 0, 0))
                    rot = edit.get("rotation")
                    stem = str(edit.get("name") or name or _P(path).stem)
                    gname = edit.get("group") or (stem if len(comp) > 1 else None)
                    added = []
                    for q in comp:
                        shp = q.shape
                        if rot:
                            shp = bd.Rot(*[float(c) for c in rot]) * shp
                        shp = shp.moved(bd.Location((at.X, at.Y, at.Z)))
                        pname = stem if len(comp) == 1 else f"{stem}_{q.name}"
                        base, k = pname, 2
                        while pname in current:
                            pname = f"{base}_{k}"; k += 1
                        add_part(Part(pname, shp, edit.get("color") or q.color or "#9AA0A6", getattr(q, "material", None), 1.0, gname))
                        added.append(pname)
                    notes.append(EditNote(op, added[0], True, f"{kind} {_P(path).name}: {len(added)} part(s) placed at ({at.X:g}, {at.Y:g}, {at.Z:g})" + (f" as group {gname!r}" if gname else "")))
                    continue
                size = str(edit.get("size") or ("608" if kind == "bearing" else "M3"))
                if kind == "screw":
                    length = float(edit.get("length") or 10)
                    shape = screw(size, length, edit.get("head") or "socket_head")
                    bb = shape.bounding_box()
                    head_h = max((bb.max.Z - bb.min.Z) - length, 0.0)
                    shape = shape.moved(bd.Location((0, 0, head_h)))   # the underside of the head sits on the surface
                    src = Vector(0, 0, -1)                      # shank down: it goes into the hole
                elif kind == "nut":
                    shape = hex_nut(size)
                    src = Vector(0, 0, -1)                      # sits on z=0: its base goes against the part
                elif kind == "washer":
                    shape = washer(size)
                    src = Vector(0, 0, -1)
                elif kind == "bearing":
                    shape = bearing(size)
                    src = Vector(0, 0, 1)                       # stands on z=0: it goes into the bore
                else:
                    raise ValueError("kind must be screw, nut, washer or bearing")
                at = _vec(edit.get("at") or (0, 0, 0))
                axis = _vec(edit.get("axis") or (0, 0, -1)).normalized()
                if edit.get("flip"):
                    axis = axis * -1
                ax, ang = rotation_between(src, axis)
                if ang > 1e-6:
                    shape = shape.rotate(bd.Axis((0, 0, 0), ax), ang)
                shape = shape.moved(bd.Location((at.X, at.Y, at.Z)))
                pname = name if (name and name not in current) else f"{kind}_{size}"
                base, k = pname, 2
                while pname in current:
                    pname = f"{base}_{k}"
                    k += 1
                add_part(Part(pname, _valid_solid(shape, "the inserted part"), edit.get("color") or "#9AA0A6", "steel"))
                notes.append(EditNote(op, pname, True, f"{kind} {size}{(' x ' + str(edit['length'])) if kind == 'screw' and edit.get('length') else ''} placed"))
            elif op == "fillet_radius":
                from . import direct
                part = current[name]
                face = face_of(part, edit["face"], kinds=(bd.GeomType.CYLINDER,), what="fillet face")
                new = direct.fillet_radius(part.shape, face, float(edit["radius"]))
                current[name] = _with(part, _valid_solid(new))
                notes.append(EditNote(op, name, True, f"fillet now r={float(edit['radius']):g}"))
            elif op == "split_face":
                from . import direct
                part = current[name]
                face = face_of(part, edit["face"], kinds=(), what="face")
                plane = plane_spec(edit.get("plane", "XY"), part.shape, float(edit.get("offset", 0)), edit.get("through", "origin"))
                new = direct.split_face(part.shape, face, plane)
                current[name] = _with(part, _valid_solid(new))
                notes.append(EditNote(op, name, True, "face split in two"))
            elif op == "separate":
                part = current[name]
                lumps = list(part.shape.solids())
                if len(lumps) < 2:
                    raise ValueError("this part is a single solid already")
                current[name] = _with(part, lumps[0])
                for k, lump in enumerate(lumps[1:], start=2):
                    add_part(Part(f"{name}_{k}", lump, part.color, getattr(part, "material", None)))
                notes.append(EditNote(op, name, True, f"{len(lumps)} solids -> parts {name}, " + ", ".join(f"{name}_{k}" for k in range(2, len(lumps) + 1))))
            elif op == "delete_faces":
                from . import direct
                part = current[name]
                faces = resolve_faces(part.shape, edit.get("faces") or [])
                if not faces:
                    raise ValueError("none of the selected faces exist any more")
                new = direct.delete_faces(part.shape, faces)
                current[name] = _with(part, _valid_solid(new))
                notes.append(EditNote(op, name, True, f"{len(faces)} face(s) removed and healed"))
            elif op in ("move", "rotate"):
                targets = [n for n in (edit.get("parts") or []) if n in current] if edit.get("parts") else \
                          [n for n in order if n in current and current[n].group == edit["group"]] if edit.get("group") else [name]
                if not targets:
                    raise ValueError("no such part(s) to move")
                if op == "move":
                    dx, dy, dz = (float(v) for v in edit["delta"])
                    for n in targets:
                        current[n] = _with(current[n], current[n].shape.moved(bd.Location((dx, dy, dz))))
                    detail = f"by ({dx:g}, {dy:g}, {dz:g})"
                else:
                    cen = _vec(edit["about"]) if edit.get("about") else None
                    for n in targets:
                        about = cen if cen is not None else current[n].shape.bounding_box().center()
                        current[n] = _with(current[n], current[n].shape.rotate(bd.Axis(about, _vec(edit["axis"])), float(edit["angle"])))
                    detail = f"{float(edit['angle']):g} deg"
                if len(targets) > 1:
                    detail += f" ({len(targets)} parts: {', '.join(targets)})"
                notes.append(EditNote(op, name or targets[0], True, detail))
            elif op == "joint":
                from . import assembly as A
                jtype = edit.get("type", "rigid")
                if jtype not in A.JOINT_DOFS:
                    raise ValueError(f"joint type {jtype!r}: " + ", ".join(A.JOINT_DOFS))
                part = current[name]
                tgt_name = edit.get("to_part")
                if tgt_name not in current:
                    raise ValueError(f"no such part {tgt_name!r}")
                if tgt_name == name:
                    raise ValueError("a joint needs two different parts")
                mov_face = face_of(part, edit["face"], kinds=(), what="face")
                tgt_face = face_of(current[tgt_name], edit["to_face"], kinds=(), what="target face")
                what = "as built"
                if not edit.get("as_built"):
                    new, what, xf = _align(part.shape, mov_face, tgt_face, float(edit.get("offset", 0)), bool(edit.get("flip")), edit.get("center", True), return_transform=True)
                    current[name] = _with(part, new)
                    mov_face = xf(mov_face)                 # the mated face, where it is now
                    xf_pt = lambda q: (lambda v: Vector(v.X, v.Y, v.Z))(xf(bd.Vertex(q.X, q.Y, q.Z)))
                    for dep in A.dependents(joints, name):  # parts jointed onto it ride along, frames included
                        if dep in current:
                            current[dep] = _with(current[dep], xf(current[dep].shape))
                    carried = set(A.dependents(joints, name))
                    for j in joints:
                        if j.part in carried:
                            o = xf_pt(Vector(*j.origin)); z0 = xf_pt(Vector(0, 0, 0))
                            j.origin = A._t(o); j.axis = A._t(xf_pt(Vector(*j.axis)) - z0); j.x_dir = A._t(xf_pt(Vector(*j.x_dir)) - z0)
                origin, axis, x_dir = A.joint_frame(mov_face, tgt_face, float(edit.get("offset", 0)) if not edit.get("as_built") else 0.0)
                jname = edit.get("name") or f"{name}-{tgt_name}"
                k, base = 2, jname
                while any(j.name == jname for j in joints):
                    jname = f"{base}_{k}"; k += 1
                joints = [j for j in joints if not (j.part == name and j.to_part == tgt_name)] + [A.Joint(jname, jtype, name, tgt_name, A._t(origin), A._t(axis), A._t(x_dir), A.normalize_limits(jtype, edit.get("limits")))]
                notes.append(EditNote(op, name, True, f"{jtype} joint {jname!r} with {tgt_name} ({what})" + (f", limits {joints[-1].limits}" if joints[-1].limits else "")))
            elif op == "drive_joint":
                from . import assembly as A
                key = edit.get("joint")
                joint = next((j for j in joints if j.name == key), None)
                if joint is None and isinstance(key, int) and 1 <= key <= len(joints):
                    joint = joints[key - 1]
                if joint is None:
                    raise ValueError(f"no joint {key!r}; joints: {[j.name for j in joints] or 'none'}")
                target = A.value_dict(joint, edit.get("value", 0))
                prev = dict(joint.value)
                moved, lim_notes = A.drive(joints, joint, target, {n: current[n].shape for n in current})
                for n, s in moved.items():
                    current[n] = _with(current[n], s)
                detail = f"{joint.name} -> " + ", ".join(f"{d} {v:g}{'°' if d in A.ANGULAR else ' mm'}" for d, v in joint.value.items() if d in joint.dofs)
                for ln in links:                                 # linked joints follow by the same step, scaled
                    if joint.name not in ln["joints"]:
                        continue
                    other = next((j for j in joints if j.name in ln["joints"] and j is not joint), None)
                    if other is None or not other.dofs or not joint.dofs:
                        continue
                    d0 = joint.dofs[0]; d1 = d0 if d0 in other.dofs else other.dofs[0]       # the same kind of motion when the other joint has it
                    step = float(joint.value.get(d0, 0.0)) - float(prev.get(d0, 0.0))
                    if abs(step) < 1e-12:
                        continue
                    m2, _ = A.drive(joints, other, {d1: float(other.value.get(d1, 0.0)) + step * float(ln.get("ratio", 1.0))}, {n: current[n].shape for n in current})
                    for n, s in m2.items():
                        current[n] = _with(current[n], s)
                    detail += f"; {other.name} -> {d1} {other.value[d1]:g}"
                if lim_notes:
                    detail += " (" + "; ".join(lim_notes) + ")"
                notes.append(EditNote(op, joint.part, True, detail))
            elif op == "motion_link":
                js = edit.get("joints") or []
                if len(js) != 2 or any(not any(j.name == n for j in joints) for n in js):
                    raise ValueError("a motion link needs two existing joints")
                links.append({"joints": list(js), "ratio": float(edit.get("ratio", 1.0))})
                notes.append(EditNote(op, js[0], True, f"{js[0]} drives {js[1]} x{float(edit.get('ratio', 1.0)):g}"))
            elif op in ("group", "ungroup"):
                members = [n for n in (edit.get("parts") or ([name] if name else [])) if n in current]
                if not members:
                    raise ValueError("no such parts to group")
                gname = None if op == "ungroup" else str(edit.get("name") or edit.get("group") or "group")
                for n in members:
                    pt = current[n]
                    current[n] = Part(pt.name, pt.shape, pt.color, getattr(pt, "material", None), getattr(pt, "opacity", 1.0), gname)
                notes.append(EditNote(op, members[0], True, (f"group {gname!r}: " if gname else "ungrouped: ") + ", ".join(members)))
            elif op == "rename":
                new_name = str(edit.get("name") or "").strip()
                if not new_name:
                    raise ValueError("a new name is needed")
                if new_name != name and new_name in current:
                    raise ValueError(f"a part named {new_name!r} already exists")
                part = current.pop(name)
                order[order.index(name)] = new_name
                current[new_name] = _with(part, name=new_name)
                notes.append(EditNote(op, new_name, True, f"renamed from {name}"))
            elif op == "delete_part":
                if name in current:
                    del current[name]
                    order.remove(name)
                notes.append(EditNote(op, name, True, "removed"))
            elif op == "color":
                part = current[name]
                op_val = edit.get("opacity")
                current[name] = _with(part, color=str(edit["color"]) if edit.get("color") else None, opacity=max(0.05, min(1.0, float(op_val))) if op_val is not None else None)
                notes.append(EditNote(op, name, True, (str(edit.get("color")) if edit.get("color") else "") + (f" opacity {float(op_val):g}" if op_val is not None else "")))
            elif op == "appearance":
                part = current[name]
                color = str(edit.get("color") or "").strip()
                if edit.get("clear"):
                    appearances[name] = []
                    current[name] = _with(part, face_colors={})
                    notes.append(EditNote(op, name, True, "face colours cleared"))
                    continue
                if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                    raise ValueError("color must be #rrggbb")
                sels = list(edit.get("faces") or ([edit["face"]] if edit.get("face") else []))
                found = resolve_faces(part.shape, sels)
                if not found:
                    raise ValueError("none of the selected faces exist any more")
                appearances.setdefault(name, []).append((sels, color))
                notes.append(EditNote(op, name, True, f"{len(found)} face(s) coloured {color}"))
            elif op == "material":
                from .measure import DENSITY
                part = current[name]
                mat = str(edit.get("material") or "").lower()
                if mat and mat not in DENSITY:
                    raise ValueError(f"unknown material {mat!r}; known: {', '.join(DENSITY)}")
                current[name] = _with(part, color=edit.get("color"), material=mat or None)
                notes.append(EditNote(op, name, True, (mat or "none") + (f", colour {edit['color']}" if edit.get("color") else "")))
            else:
                notes.append(EditNote(str(op), name, False, "unknown operation"))
        except ValueError as exc:                # the edit code's own explanations, written for people
            notes.append(EditNote(str(op), str(name), False, str(exc)))
        except Exception as exc:
            notes.append(EditNote(str(op), str(name), False, f"{type(exc).__name__}: {exc}"))
    for pname, entries in appearances.items():                 # face colours: resolved on the finished part
        if pname not in current or not entries:
            continue
        part = current[pname]
        all_faces = part.shape.faces()
        fmap = dict(getattr(part, "face_colors", None) or {})
        for sels, color in entries:
            for f in resolve_faces(part.shape, sels):
                for i, g in enumerate(all_faces):
                    if g.is_same(f) or ((g.center() - f.center()).length < 1e-6 and abs(g.area - f.area) < 1e-6):
                        fmap[i] = color
                        break
        current[pname] = _with(part, face_colors=fmap)
    out = Parts([current[n] for n in order])
    out.joints = [j.to_dict() for j in joints]
    out.sheets = {n: st.to_dict() for n, st in sheets.items() if n in current}
    out.sheet_states = {n: st for n, st in sheets.items() if n in current}
    out.forms = {n: {"cage": st["cage"].to_dict(), "levels": st["levels"]} for n, st in forms.items() if n in current}
    out.flats = {n: f for n, f in flats.items() if n in current}
    return out, notes


OPS = ("push_pull", "sketch_extrude", "revolve", "loft", "hole", "move_hole", "fillet", "chamfer", "hole_diameter", "shell",
       "thread", "primitive", "combine", "split", "mirror", "pattern", "scale", "align", "move", "rotate", "delete_part",
       "color", "material", "move_vertices", "move_face", "delete_faces", "fillet_radius", "split_face", "separate",
       "chamfer_size", "sweep", "insert", "rename", "rib", "boss", "lip", "copy_part", "repair",
       "surface", "surface_offset", "surface_extend", "surface_trim", "surface_split", "stitch", "patch", "thicken", "to_solid",
       "joint", "drive_joint", "motion_link", "group", "ungroup",
       "base_flange", "flange", "hem", "contour_flange", "tab", "sheet_cut", "flat_pattern", "sheet_rules", "form", "form_edit", "appearance")


def describe(edit: dict) -> str:
    op = edit.get("op")
    sup = " (suppressed)" if edit.get("suppressed") else ""
    try:
        return _describe(edit, op) + sup
    except Exception:
        return str(edit) + sup


def _describe(edit: dict, op) -> str:
    part = edit.get("part")
    if op == "push_pull":
        n = len(edit.get("faces") or [])
        kind = (edit.get("face") or {}).get("type", "face") if not n else f"{n} faces"
        return f"push/pull {part} {kind} {float(edit.get('distance', 0)):+g} mm"
    if op == "sketch_extrude":
        what = _fmt_profile(edit.get("profile", {})) + _fmt_pattern(edit) + _fmt_extrude_opts(edit)
        if edit.get("new_part"):
            return f"new part {edit['new_part']}: {what} extruded {float(edit.get('distance') or 0):g} mm"
        return f"{'cut' if edit.get('mode') == 'cut' else 'extrude'} {what} {_fmt_extent(edit, edit.get('mode', 'add'))} on {part}"
    if op == "revolve":
        return f"{'cut' if edit.get('mode') == 'cut' else 'revolve'} {_fmt_profile(edit.get('profile', {}))} {float(edit.get('angle', 360)):g}° on {edit.get('new_part') or part}"
    if op == "loft":
        return f"{'cut' if edit.get('mode') == 'cut' else 'loft'} {len(edit.get('sections', []))} sections on {edit.get('new_part') or part}"
    if op == "hole":
        kind = (edit.get("size") or "Ø" + str(edit.get("d"))) + (" tapped" if edit.get("tapped") else "") + (" " + edit["head"] if edit.get("head") else "")
        return f"hole {kind} in {part}{_fmt_pattern(edit)}"
    if op == "move_hole":
        return f"move hole in {part} by {tuple(edit.get('delta', ()))}"
    if op == "fillet":
        r2 = edit.get("radius_end")
        return f"fillet r{float(edit.get('radius', 0)):g}{'->' + format(float(r2), 'g') if r2 not in (None, '') else ''} on {len(edit.get('edges', []))} edge(s) of {part}"
    if op == "chamfer":
        extra = f" x {float(edit['size2']):g}" if edit.get("size2") not in (None, "") else (f" at {float(edit['angle']):g}°" if edit.get("angle") not in (None, "") else "")
        return f"chamfer {float(edit.get('size', 0)):g}{extra} mm on {len(edit.get('edges', []))} edge(s) of {part}"
    if op == "rib":
        return f"rib {float(edit.get('thickness', 2)):g} mm on {part}" + (f", {float(edit['distance']):g} mm" if edit.get("distance") else " to the next face")
    if op == "boss":
        return f"{edit.get('kind', 'plain')} boss{(' ' + str(edit['size'])) if edit.get('size') else ''} on {part} at {edit.get('at')}"
    if op == "lip":
        return f"{edit.get('mode', 'lip')} {float(edit.get('width', 1)):g} x {float(edit.get('height', 1)):g} mm on {part}"
    if op == "copy_part":
        return f"copy {part} -> {edit.get('new_part') or part + '_copy'}"
    if op == "surface":
        k = edit.get("kind", "extrude")
        return f"surface {edit.get('new_part') or 'surface'}: {k} {_fmt_profile(edit.get('profile', {}))}" + (f" {float(edit.get('distance') or 10):g} mm" if k == "extrude" else f" {float(edit.get('angle', 360)):g}°" if k == "revolve" else "")
    if op == "surface_offset":
        return f"offset surface {part} by {float(edit.get('distance', 0)):+g} mm"
    if op == "surface_extend":
        return f"extend surface {part} by {float(edit.get('distance', 5)):g} mm ({edit.get('mode', 'natural')})"
    if op == "surface_trim":
        by = edit.get("by"); kind = "a face" if isinstance(by, dict) and by.get("face") else "a sketch" if isinstance(by, dict) and by.get("profile") else str(by)
        return f"trim surface {part} by {kind}, keep {edit.get('keep', 'top')}"
    if op == "surface_split":
        return f"split surface {part} -> {edit.get('new_part') or part + '_2'}"
    if op == "stitch":
        return f"stitch {', '.join(edit.get('parts') or [str(part)])}"
    if op == "patch":
        return f"patch {len(edit.get('edges', []))} edge(s) of {part} ({edit.get('continuity', 'G0')})"
    if op == "thicken":
        return f"thicken surface {part} by {float(edit.get('thickness', 2)):g} mm{' both sides' if edit.get('both') else ''}"
    if op == "to_solid":
        return f"surface {part} -> solid"
    if op == "joint":
        return f"{edit.get('type', 'rigid')} joint {edit.get('name') or ''}: {part} to {edit.get('to_part')}" + (" (as built)" if edit.get("as_built") else "") + (f", limits {edit['limits']}" if edit.get("limits") else "")
    if op == "drive_joint":
        v = edit.get("value", 0)
        return f"drive joint {edit.get('joint')} to " + (", ".join(f"{k} {float(x):g}" for k, x in v.items()) if isinstance(v, dict) else f"{float(v):g}")
    if op == "motion_link":
        js = edit.get("joints") or ["?", "?"]
        return f"motion link {js[0]} -> {js[1]} x{float(edit.get('ratio', 1)):g}"
    if op == "group":
        return f"group {edit.get('name') or edit.get('group')}: {', '.join(edit.get('parts') or [str(part)])}"
    if op == "ungroup":
        return f"ungroup {', '.join(edit.get('parts') or [str(part)])}"
    if op == "base_flange":
        return f"sheet {edit.get('new_part') or 'sheet'}: {_fmt_profile(edit.get('profile', {}))} t{float(edit.get('thickness', 2)):g}"
    if op == "flange":
        n = len(edit.get("edges") or ([1] if edit.get("edge") else []))
        return f"flange {float(edit.get('height', 10)):g} mm at {float(edit.get('angle', 90)):g}° on {n} edge(s) of {part}"
    if op == "contour_flange":
        legs = edit.get("legs") or []
        return f"contour flange " + ", ".join(f"{float(l[0]):g}@{float(l[1]):g}°" for l in legs) + f" on {len(edit.get('edges') or ([1] if edit.get('edge') else []))} edge(s) of {part}"
    if op == "hem":
        return f"hem {float(edit.get('length', 8)):g} mm on {len(edit.get('edges') or ([1] if edit.get('edge') else []))} edge(s) of {part}"
    if op == "tab":
        return f"tab {_fmt_profile(edit.get('profile', {}))} on {part}"
    if op == "sheet_cut":
        return f"sheet cut {_fmt_profile(edit.get('profile', {}))} in {part}"
    if op == "flat_pattern":
        return f"flat pattern of {part}" + (f" -> {edit['new_part']}" if edit.get("new_part") else "")
    if op == "form":
        return f"form {edit.get('new_part') or 'form'}: {edit.get('kind', 'box')} cage, {edit.get('levels', 2)} level(s)" + (f", symmetric {edit['symmetry']}" if edit.get("symmetry") else "")
    if op == "form_edit":
        a = edit.get("action", "move_vertices")
        return f"form {part}: {a.replace('_', ' ')}" + (f" {edit['face']}" if edit.get("face") is not None and a in ("move_face", "extrude_face") else "") + (f" to {edit['levels']}" if a == "levels" and edit.get("levels") is not None else "") + (f" {edit.get('axis') or 'off'}" if a == "symmetry" else "")
    if op == "sheet_rules":
        return f"sheet rules of {part}: " + ", ".join(f"{k} {edit[k]}" for k in ("radius", "k") if edit.get(k) not in (None, ""))
    if op == "repair":
        return f"repair {part}"
    if op == "hole_diameter":
        return f"hole in {part} to Ø{float(edit.get('diameter', 0)):g}"
    if op == "shell":
        return f"shell {part} {float(edit.get('thickness', 0)):g} mm walls, {len(edit.get('faces') or [])} opening(s)"
    if op == "thread":
        return f"{'modelled' if edit.get('modeled') else 'cosmetic'} thread {edit.get('size') or ''} on {part}"
    if op == "primitive":
        mode = edit.get("mode", "new")
        return f"{edit.get('kind', 'box')} {'as new part ' + str(edit.get('new_part') or edit.get('kind')) if mode == 'new' else (mode + ' ' + str(part))}"
    if op == "combine":
        return f"combine: {edit.get('mode', 'join')} {edit.get('tool')} {'into' if edit.get('mode', 'join') == 'join' else 'with'} {part}"
    if op == "split":
        pl = edit.get("plane", "XY")
        return f"split {part} by {pl if isinstance(pl, str) else 'a face plane'}{(' at ' + str(edit['offset'])) if edit.get('offset') else ''}, keep {edit.get('keep', 'both')}"
    if op == "mirror":
        pl = edit.get("plane", "YZ")
        return f"mirror {part} about {pl if isinstance(pl, str) else 'a face plane'}" + (f" -> {edit['new_part']}" if edit.get("new_part") else " (joined)")
    if op == "pattern":
        if edit.get("type") == "path":
            return f"pattern of {part} along {len(edit.get('edges', []))} edge(s): {edit.get('n')} copies"
        if edit.get("type") == "circular":
            return f"circular pattern of {part}: {edit.get('n')} over {float(edit.get('angle', 360)):g}°"
        return f"rectangular pattern of {part}: {edit.get('nx', 1)} x {edit.get('ny', 1)} at {edit.get('dx', 0)} x {edit.get('dy', 0)} mm"
    if op == "scale":
        return f"scale {part} x{edit.get('factor')}"
    if op == "align":
        return f"align {part} to {edit.get('to_part')}" + (f" offset {edit['offset']}" if edit.get("offset") else "") + (" (flipped)" if edit.get("flip") else "")
    if op == "move":
        return f"move {part} by {tuple(edit.get('delta', ()))}"
    if op == "move_vertices":
        n = len(edit.get("vertices") or []); ne = len(edit.get("edges") or []); nf = len(edit.get("faces") or [])
        what = ", ".join(s for s in [f"{n} corner(s)" if n else "", f"{ne} edge(s)" if ne else "", f"{nf} face(s)" if nf else ""] if s) or "corners"
        return f"drag {what} of {part} by {tuple(edit.get('delta', ()))}"
    if op == "move_face":
        rot = edit.get("rotate") or {}
        return f"move {(edit.get('face') or {}).get('type', 'face')} face of {part} by {tuple(edit.get('delta') or (0, 0, 0))}" + (f", tilt {rot.get('angle')}°" if rot.get("angle") else "")
    if op == "delete_faces":
        return f"delete {len(edit.get('faces') or [])} face(s) of {part} (remove feature)"
    if op == "fillet_radius":
        return f"fillet on {part} to r{float(edit.get('radius', 0)):g}"
    if op == "chamfer_size":
        return f"chamfer on {part} to {float(edit.get('size', 0)):g} mm"
    if op == "sweep":
        return f"{'cut' if edit.get('mode') == 'cut' else 'sweep'} {_fmt_profile(edit.get('profile', {}))} along {len(edit.get('path') or [])} edge(s) on {edit.get('new_part') or part}"
    if op == "insert":
        return f"insert {edit.get('kind', 'screw')} {edit.get('size', '')}{(' x ' + str(edit['length'])) if edit.get('length') else ''} as {part}"
    if op == "split_face":
        pl = edit.get("plane", "XY")
        return f"split a face of {part} by {pl if isinstance(pl, str) else 'a plane'}{(' at ' + str(edit['offset'])) if edit.get('offset') else ''}"
    if op == "separate":
        return f"separate {part} into one part per solid"
    if op == "rotate":
        return f"rotate {part} {edit.get('angle')} deg"
    if op == "delete_part":
        return f"delete {part}"
    if op == "rename":
        return f"rename {part} to {edit.get('name')}"
    if op == "color":
        return f"colour {part} {edit.get('color') or ''}" + (f" opacity {edit['opacity']}" if edit.get("opacity") is not None else "")
    if op == "appearance":
        if edit.get("clear"):
            return f"clear face colours of {part}"
        return f"colour {len(edit.get('faces') or ([1] if edit.get('face') else []))} face(s) of {part} {edit.get('color', '')}"
    if op == "material":
        return f"material of {part}: {edit.get('material')}" + (f", colour {edit['color']}" if edit.get("color") else "")
    return str(edit)
