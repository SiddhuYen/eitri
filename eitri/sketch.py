"""2D sketches the way Fusion draws them: points, lines, arcs, circles, ellipses, splines
and text, construction geometry, constraints and dimensions solved numerically, assembled
into closed profiles (faces, with holes) for extrude/revolve/sweep/loft - or left as open
chains for ribs and thin features. Also the editing operations (trim, extend, split,
offset, mirror, patterns, corner fillet/chamfer, projected edges, SVG import) and a code
emitter so a sketch folds into build123d code.

A sketch is a JSON dict (it lives in a direct edit's "profile" with "type": "sketch"):

    {"type": "sketch",
     "entities": [
        {"id": "l1", "type": "line", "a": [0, 0], "b": [20, 0]},
        {"id": "a1", "type": "arc", "c": [20, 5], "r": 5, "start": -90, "end": 90},
        {"id": "c1", "type": "circle", "c": [10, 5], "r": 2, "construction": false},
        {"id": "e1", "type": "ellipse", "c": [0, 0], "rx": 10, "ry": 5, "angle": 0},
        {"id": "s1", "type": "spline", "points": [[0, 0], [5, 3], [10, 0]], "kind": "fit", "closed": false},
        {"id": "p1", "type": "point", "at": [3, 3]},
        {"id": "t1", "type": "text", "at": [0, 0], "text": "A", "size": 8, "angle": 0}],
     "constraints": [{"type": "coincident", "a": "l1.b", "b": "a1.s"}, {"type": "horizontal", "a": "l1"}, ...],
     "dims": [{"type": "length", "a": "l1", "value": 20}, {"type": "radius", "a": "a1", "value": 5}, ...]}

Point references are "<entity>.<point>": line a/b, arc c/s/e (centre, start, end), circle c,
ellipse c, point at, spline p0..pN. Angles are degrees, counter-clockwise; an arc runs from
start to end counter-clockwise.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Sequence

import numpy as np

import build123d as bd
from build123d import Vector

POINT_FIELDS = {"line": ("a", "b"), "point": ("at",), "circle": ("c",), "arc": ("c",), "ellipse": ("c",), "text": ("at",)}
SCALAR_FIELDS = {"circle": ("r",), "arc": ("r", "start", "end"), "ellipse": ("rx", "ry", "angle")}
CONSTRAINT_TYPES = ("coincident", "point_on", "horizontal", "vertical", "parallel", "perpendicular", "tangent", "equal",
                    "concentric", "midpoint", "symmetric", "collinear", "fix")
DIM_TYPES = ("distance", "distance_h", "distance_v", "length", "radius", "diameter", "angle", "distance_point_line")


# ---------------------------------------------------------------------------- basics

def new_id(sketch: dict, prefix: str) -> str:
    used = {e["id"] for e in sketch.get("entities", [])}
    k = 1
    while f"{prefix}{k}" in used:
        k += 1
    return f"{prefix}{k}"


def entity(sketch: dict, eid: str) -> dict:
    for e in sketch.get("entities", []):
        if e["id"] == eid:
            return e
    raise KeyError(f"no sketch entity {eid!r}")


def arc_point(e: dict, which: str) -> list[float]:
    a = math.radians(float(e["start"] if which == "s" else e["end"]))
    return [e["c"][0] + e["r"] * math.cos(a), e["c"][1] + e["r"] * math.sin(a)]


def point_of(sketch: dict, ref: str) -> list[float]:
    """The (u, v) of a point reference such as "l1.b" or "a1.s"."""
    eid, _, name = ref.partition(".")
    e = entity(sketch, eid)
    if e["type"] == "arc" and name in ("s", "e"):
        return arc_point(e, name)
    if e["type"] == "spline" and name.startswith("p"):
        return list(e["points"][int(name[1:])])
    if name in e and isinstance(e[name], list):
        return list(e[name])
    raise KeyError(f"{ref!r} is not a point of a {e['type']}")


def validate(sketch: dict) -> None:
    ids = set()
    for e in sketch.get("entities", []):
        if e.get("id") in ids:
            raise ValueError(f"duplicate sketch entity id {e['id']!r}")
        ids.add(e.get("id"))
        t = e.get("type")
        if t not in POINT_FIELDS and t != "spline":
            raise ValueError(f"unknown sketch entity type {t!r}")
    for c in sketch.get("constraints", []):
        if c.get("type") not in CONSTRAINT_TYPES:
            raise ValueError(f"unknown constraint {c.get('type')!r}")
    for d in sketch.get("dims", []):
        if d.get("type") not in DIM_TYPES:
            raise ValueError(f"unknown dimension {d.get('type')!r}")


# ---------------------------------------------------------------------------- geometry (build123d)

def entity_edges(e: dict) -> list[bd.Edge]:
    """build123d edges (in the XY plane) for one entity; text and points give none."""
    t = e["type"]
    if t == "line":
        a, b = e["a"], e["b"]
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9:
            return []
        return [bd.Edge.make_line((a[0], a[1], 0), (b[0], b[1], 0))]
    if t == "circle":
        return list(bd.Circle(float(e["r"])).moved(bd.Location((e["c"][0], e["c"][1], 0))).edges())
    if t == "arc":
        s, en = float(e["start"]), float(e["end"])
        while en <= s:
            en += 360.0
        sweep = en - s
        if sweep >= 360 - 1e-9:
            return list(bd.Circle(float(e["r"])).moved(bd.Location((e["c"][0], e["c"][1], 0))).edges())
        return [bd.CenterArc((e["c"][0], e["c"][1]), float(e["r"]), s, sweep).edges()[0]]
    if t == "ellipse":
        rx, ry, ang = float(e["rx"]), float(e["ry"]), float(e.get("angle", 0))
        if e.get("start") is not None and e.get("end") is not None:
            s, en = float(e["start"]), float(e["end"])
            while en <= s:
                en += 360.0
            edge = bd.EllipticalCenterArc((0, 0), rx, ry, s, en).edges()[0]
        else:
            edge = bd.Ellipse(rx, ry).edges()[0]
        return [edge.moved(bd.Location((e["c"][0], e["c"][1], 0), (0, 0, ang)))]
    if t == "spline":
        pts = [(p[0], p[1], 0) for p in e["points"]]
        if len(pts) < 2:
            return []
        if e.get("kind") == "control":
            return [bd.Bezier(*pts).edges()[0]] if len(pts) <= 25 else [bd.Spline(*pts).edges()[0]]
        if e.get("closed") and len(pts) > 2:
            return list(bd.Spline(*pts, periodic=True).edges())
        return [bd.Spline(*pts).edges()[0]]
    return []


def text_faces(e: dict) -> list[bd.Face]:
    style = bd.FontStyle.BOLD if e.get("bold") else bd.FontStyle.REGULAR
    txt = bd.Text(str(e.get("text", "")), font_size=float(e.get("size", 8)), font=e.get("font") or "Arial",
                  font_style=style, align=(bd.Align.CENTER, bd.Align.CENTER))
    placed = bd.Pos(e["at"][0], e["at"][1], 0) * bd.Rot(0, 0, float(e.get("angle", 0))) * txt
    return list(placed.faces())


def _loops(edges: list[bd.Edge]) -> tuple[list[bd.Wire], list[bd.Wire]]:
    """Closed and open wires from a bag of edges."""
    if not edges:
        return [], []
    wires = bd.Wire.combine(edges, tol=1e-3)             # solved coordinates agree to ~1e-6; stitch within 1e-3
    closed = [w for w in wires if w.is_closed]
    open_ = [w for w in wires if not w.is_closed]
    return closed, open_


def profile_faces(sketch: dict) -> tuple[list[bd.Face], list[bd.Wire]]:
    """The faces a sketch encloses (holes subtracted, text included) and its open chains."""
    edges: list[bd.Edge] = []
    faces: list[bd.Face] = []
    for e in sketch.get("entities", []):
        if e.get("construction"):
            continue
        if e["type"] == "text":
            faces += text_faces(e)
        else:
            edges += entity_edges(e)
    closed, open_ = _loops(edges)
    loop_faces = []
    for w in closed:
        try:
            loop_faces.append(bd.Face(w))
        except Exception:
            continue
    # nesting: a loop inside another is a hole of its innermost container
    loop_faces.sort(key=lambda f: -f.area)
    result: list[bd.Face] = []
    assigned = [False] * len(loop_faces)
    for i, f in enumerate(loop_faces):
        if assigned[i]:
            continue
        outer = f
        inner_wires = []
        for j in range(i + 1, len(loop_faces)):
            g = loop_faces[j]
            if assigned[j]:
                continue
            p = g.center()
            if outer.is_inside(p) and not any(h.is_inside(p) for h in inner_wires_faces(inner_wires)):
                inner_wires.append(g.outer_wire())
                assigned[j] = True
        assigned[i] = True
        result.append(bd.Face(outer.outer_wire(), inner_wires) if inner_wires else outer)
    return result + faces, open_


def inner_wires_faces(wires: list[bd.Wire]) -> list[bd.Face]:
    out = []
    for w in wires:
        try:
            out.append(bd.Face(w))
        except Exception:
            pass
    return out


def profile(sketch: dict):
    """The sketch as a build123d Sketch (faces) - what extrude/revolve/loft consume."""
    faces, _ = profile_faces(sketch)
    if not faces:
        raise ValueError("the sketch has no closed profile (close the loop, or check for gaps)")
    sk = faces[0]
    for f in faces[1:]:
        sk = sk + f
    return sk


def open_chains(sketch: dict) -> list[bd.Wire]:
    _, chains = profile_faces(sketch)
    return chains


# ---------------------------------------------------------------------------- the solver

class _Vars:
    """Flat variable vector over the free coordinates of a sketch."""

    def __init__(self, sketch: dict):
        self.index: dict[tuple, int] = {}
        self.x0: list[float] = []
        fixed = {c["a"].split(".")[0] if "." in c.get("a", "") else c.get("a") for c in sketch.get("constraints", []) if c.get("type") == "fix"}
        fixed_points = {c["a"] for c in sketch.get("constraints", []) if c.get("type") == "fix" and "." in c.get("a", "")}
        for e in sketch.get("entities", []):
            if e["id"] in fixed and e["id"] not in {p.split(".")[0] for p in fixed_points}:
                continue
            for name in POINT_FIELDS.get(e["type"], ()):
                if f"{e['id']}.{name}" in fixed_points:
                    continue
                for k in (0, 1):
                    self.index[(e["id"], name, k)] = len(self.x0)
                    self.x0.append(float(e[name][k]))
            if e["type"] == "spline":
                for i, p in enumerate(e["points"]):
                    if f"{e['id']}.p{i}" in fixed_points:
                        continue
                    for k in (0, 1):
                        self.index[(e["id"], f"p{i}", k)] = len(self.x0)
                        self.x0.append(float(p[k]))
            for name in SCALAR_FIELDS.get(e["type"], ()):
                self.index[(e["id"], name, 0)] = len(self.x0)
                self.x0.append(float(e[name]))
        self.sketch = sketch

    def get(self, x, eid: str, name: str, k: int = 0) -> float:
        i = self.index.get((eid, name, k))
        if i is not None:
            return x[i]
        e = entity(self.sketch, eid)
        if name.startswith("p") and e["type"] == "spline":
            return float(e["points"][int(name[1:])][k])
        v = e[name]
        return float(v[k]) if isinstance(v, list) else float(v)

    def point(self, x, ref: str) -> tuple[float, float]:
        eid, _, name = ref.partition(".")
        e = entity(self.sketch, eid)
        if e["type"] == "arc" and name in ("s", "e"):
            c = (self.get(x, eid, "c", 0), self.get(x, eid, "c", 1))
            r = self.get(x, eid, "r")
            a = math.radians(self.get(x, eid, "start" if name == "s" else "end"))
            return (c[0] + r * math.cos(a), c[1] + r * math.sin(a))
        return (self.get(x, eid, name, 0), self.get(x, eid, name, 1))

    def line_dir(self, x, eid: str) -> tuple[float, float]:
        a, b = self.point(x, eid + ".a"), self.point(x, eid + ".b")
        return (b[0] - a[0], b[1] - a[1])

    def write_back(self, x) -> None:
        for (eid, name, k), i in self.index.items():
            e = entity(self.sketch, eid)
            if name.startswith("p") and e["type"] == "spline":
                e["points"][int(name[1:])][k] = round(float(x[i]), 6)
            elif isinstance(e[name], list):
                e[name][k] = round(float(x[i]), 6)
            else:
                e[name] = round(float(x[i]), 6)


def _norm(d):
    return math.hypot(d[0], d[1]) + 1e-12


def _residual_fns(sketch: dict, V: _Vars) -> list[Callable]:
    fns: list[Callable] = []
    is_round = lambda eid: entity(sketch, eid)["type"] in ("circle", "arc")

    def pt_line_dist(x, p, line):
        a = V.point(x, line + ".a")
        d = V.line_dir(x, line)
        return ((p[0] - a[0]) * d[1] - (p[1] - a[1]) * d[0]) / _norm(d)

    for c in sketch.get("constraints", []):
        t = c["type"]
        if t == "coincident":
            fns.append(lambda x, c=c: [V.point(x, c["a"])[0] - V.point(x, c["b"])[0], V.point(x, c["a"])[1] - V.point(x, c["b"])[1]])
        elif t == "point_on":
            if is_round(c["b"]):
                fns.append(lambda x, c=c: [_norm((V.point(x, c["a"])[0] - V.get(x, c["b"], "c", 0), V.point(x, c["a"])[1] - V.get(x, c["b"], "c", 1))) - V.get(x, c["b"], "r")])
            else:
                fns.append(lambda x, c=c: [pt_line_dist(x, V.point(x, c["a"]), c["b"])])
        elif t in ("horizontal", "vertical"):
            k = 1 if t == "horizontal" else 0
            if "." in c["a"]:
                fns.append(lambda x, c=c, k=k: [V.point(x, c["a"])[k] - V.point(x, c["b"])[k]])
            else:
                fns.append(lambda x, c=c, k=k: [V.point(x, c["a"] + ".a")[k] - V.point(x, c["a"] + ".b")[k]])
        elif t == "parallel":
            fns.append(lambda x, c=c: [(lambda d1, d2: (d1[0] * d2[1] - d1[1] * d2[0]) / (_norm(d1) * _norm(d2)))(V.line_dir(x, c["a"]), V.line_dir(x, c["b"]))])
        elif t == "perpendicular":
            fns.append(lambda x, c=c: [(lambda d1, d2: (d1[0] * d2[0] + d1[1] * d2[1]) / (_norm(d1) * _norm(d2)))(V.line_dir(x, c["a"]), V.line_dir(x, c["b"]))])
        elif t == "tangent":
            a, b = c["a"], c["b"]
            if is_round(a) and is_round(b):
                def tang(x, a=a, b=b):
                    ca = (V.get(x, a, "c", 0), V.get(x, a, "c", 1)); cb = (V.get(x, b, "c", 0), V.get(x, b, "c", 1))
                    d = _norm((ca[0] - cb[0], ca[1] - cb[1])); ra, rb = V.get(x, a, "r"), V.get(x, b, "r")
                    ext, inn = d - (ra + rb), d - abs(ra - rb)
                    return [ext if abs(ext) <= abs(inn) else inn]
                fns.append(tang)
            else:
                line, rnd = (a, b) if is_round(b) else (b, a)
                fns.append(lambda x, line=line, rnd=rnd: [abs(pt_line_dist(x, (V.get(x, rnd, "c", 0), V.get(x, rnd, "c", 1)), line)) - V.get(x, rnd, "r")])
        elif t == "equal":
            a, b = c["a"], c["b"]
            if is_round(a) and is_round(b):
                fns.append(lambda x, a=a, b=b: [V.get(x, a, "r") - V.get(x, b, "r")])
            else:
                fns.append(lambda x, a=a, b=b: [_norm(V.line_dir(x, a)) - _norm(V.line_dir(x, b))])
        elif t == "concentric":
            fns.append(lambda x, c=c: [V.get(x, c["a"], "c", 0) - V.get(x, c["b"], "c", 0), V.get(x, c["a"], "c", 1) - V.get(x, c["b"], "c", 1)])
        elif t == "midpoint":
            fns.append(lambda x, c=c: [V.point(x, c["a"])[0] - (V.point(x, c["b"] + ".a")[0] + V.point(x, c["b"] + ".b")[0]) / 2,
                                       V.point(x, c["a"])[1] - (V.point(x, c["b"] + ".a")[1] + V.point(x, c["b"] + ".b")[1]) / 2])
        elif t == "symmetric":
            def sym(x, c=c):
                p, q = V.point(x, c["a"]), V.point(x, c["b"])
                a = V.point(x, c["line"] + ".a"); d = V.line_dir(x, c["line"]); n = _norm(d); u = (d[0] / n, d[1] / n)
                rel = (p[0] - a[0], p[1] - a[1]); along = rel[0] * u[0] + rel[1] * u[1]
                foot = (a[0] + u[0] * along, a[1] + u[1] * along)
                mirror = (2 * foot[0] - p[0], 2 * foot[1] - p[1])
                return [mirror[0] - q[0], mirror[1] - q[1]]
            fns.append(sym)
        elif t == "collinear":
            fns.append(lambda x, c=c: [pt_line_dist(x, V.point(x, c["b"] + ".a"), c["a"]), pt_line_dist(x, V.point(x, c["b"] + ".b"), c["a"])])
        elif t == "fix":
            pass                                             # fixed points are simply not variables
    for d in sketch.get("dims", []):
        if d.get("driven"):
            continue
        t, val = d["type"], float(d["value"])
        if t == "distance":
            fns.append(lambda x, d=d, val=val: [_norm((V.point(x, d["a"])[0] - V.point(x, d["b"])[0], V.point(x, d["a"])[1] - V.point(x, d["b"])[1])) - val])
        elif t in ("distance_h", "distance_v"):
            k = 0 if t == "distance_h" else 1
            fns.append(lambda x, d=d, val=val, k=k: [abs(V.point(x, d["b"])[k] - V.point(x, d["a"])[k]) - val])
        elif t == "length":
            fns.append(lambda x, d=d, val=val: [_norm(V.line_dir(x, d["a"])) - val])
        elif t == "radius":
            fns.append(lambda x, d=d, val=val: [V.get(x, d["a"], "r") - val])
        elif t == "diameter":
            fns.append(lambda x, d=d, val=val: [2 * V.get(x, d["a"], "r") - val])
        elif t == "angle":
            def ang(x, d=d, val=val):
                d1, d2 = V.line_dir(x, d["a"]), V.line_dir(x, d["b"])
                a = math.degrees(math.atan2(d1[0] * d2[1] - d1[1] * d2[0], d1[0] * d2[0] + d1[1] * d2[1]))
                a = abs(a)
                return [min(a, 180 - a) - val if val <= 90 else a - val]
            fns.append(ang)
        elif t == "distance_point_line":
            fns.append(lambda x, d=d, val=val: [abs(pt_line_dist(x, V.point(x, d["a"]), d["b"])) - val])
    return fns


def solve(sketch: dict, max_iter: int = 200) -> dict:
    """Solve the constraints and dimensions in place (least squares from the current
    positions, so entities move as little as possible). Returns a status dict:
    {"ok", "residual", "dof", "conflicts": [...]}."""
    from scipy.optimize import least_squares
    validate(sketch)
    V = _Vars(sketch)
    fns = _residual_fns(sketch, V)
    if not V.x0:
        return {"ok": True, "residual": 0.0, "dof": 0, "conflicts": []}
    if not fns:
        return {"ok": True, "residual": 0.0, "dof": len(V.x0), "conflicts": []}

    def residuals(x):
        out: list[float] = []
        for f in fns:
            out.extend(f(x))
        return np.array(out, dtype=float)

    x0 = np.array(V.x0, dtype=float)
    lam = 1e-4                                            # keeps unconstrained directions where they are (minimum movement)

    def augmented(x):
        return np.concatenate([residuals(x), lam * (x - x0)])

    res = None
    for method in ("trf", "lm"):                         # trust-region first (robust far from the solution), LM as a second try
        cand = least_squares(augmented, x0, method=method, xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=max_iter * max(1, len(x0)))
        if res is None or np.linalg.norm(residuals(cand.x)) < np.linalg.norm(residuals(res.x)):
            res = cand
        if np.linalg.norm(residuals(res.x)) <= 1e-7:
            break
    # stage two: from that minimum-movement neighbourhood, satisfy the constraints exactly
    m, n = len(residuals(res.x)), len(x0)
    try:
        fine = least_squares(residuals, res.x, method="lm" if m >= n else "trf", xtol=1e-14, ftol=1e-14, gtol=1e-14, max_nfev=max_iter * max(1, n))
        if np.linalg.norm(residuals(fine.x)) <= np.linalg.norm(residuals(res.x)):
            res = fine
    except Exception:
        pass
    V.write_back(res.x)
    true_res = residuals(res.x)
    rn = float(np.linalg.norm(true_res)) if len(true_res) else 0.0
    # degrees of freedom left: variables minus the rank of the constraint Jacobian (finite differences)
    try:
        h = 1e-6
        J = np.empty((len(true_res), len(x0)))
        for j in range(len(x0)):
            xp = res.x.copy(); xp[j] += h
            J[:, j] = (residuals(xp) - true_res) / h
        rank = int(np.linalg.matrix_rank(J, tol=1e-6)) if J.size else 0
    except Exception:
        rank = 0
    dof = max(0, len(x0) - rank)
    conflicts = []
    if rn > 1e-5:
        # name the constraints that cannot be met
        k = 0
        names = []
        for c in sketch.get("constraints", []):
            if c["type"] != "fix":
                names.append(c)
        for d in sketch.get("dims", []):
            if not d.get("driven"):
                names.append(d)
        for f, item in zip(fns, names):
            vals = f(res.x)
            if any(abs(v) > 1e-5 for v in vals):
                conflicts.append(item.get("type") + (f" {item.get('a', '')}" if item.get("a") else ""))
            k += 1
    return {"ok": rn <= 1e-5, "residual": rn, "dof": dof, "conflicts": conflicts}


def measure(sketch: dict, dim: dict) -> float:
    """The current value a (driven) dimension would read."""
    V = _Vars(sketch)
    x = np.array(V.x0, dtype=float)
    t = dim["type"]
    if t == "distance":
        p, q = V.point(x, dim["a"]), V.point(x, dim["b"])
        return math.hypot(p[0] - q[0], p[1] - q[1])
    if t in ("distance_h", "distance_v"):
        k = 0 if t == "distance_h" else 1
        return abs(V.point(x, dim["b"])[k] - V.point(x, dim["a"])[k])
    if t == "length":
        return _norm(V.line_dir(x, dim["a"])) - 1e-12
    if t == "radius":
        return V.get(x, dim["a"], "r")
    if t == "diameter":
        return 2 * V.get(x, dim["a"], "r")
    if t == "angle":
        d1, d2 = V.line_dir(x, dim["a"]), V.line_dir(x, dim["b"])
        return abs(math.degrees(math.atan2(d1[0] * d2[1] - d1[1] * d2[0], d1[0] * d2[0] + d1[1] * d2[1])))
    if t == "distance_point_line":
        a = V.point(x, dim["b"] + ".a"); d = V.line_dir(x, dim["b"]); p = V.point(x, dim["a"])
        return abs(((p[0] - a[0]) * d[1] - (p[1] - a[1]) * d[0]) / _norm(d))
    raise ValueError(t)


# ---------------------------------------------------------------------------- editing operations

def add(sketch: dict, ent: dict) -> dict:
    ent = dict(ent)
    ent.setdefault("id", new_id(sketch, ent["type"][0]))
    sketch.setdefault("entities", []).append(ent)
    return ent


def remove(sketch: dict, ids: Sequence[str]) -> None:
    ids = set(ids)
    sketch["entities"] = [e for e in sketch.get("entities", []) if e["id"] not in ids]
    refs = lambda item: [v for v in item.values() if isinstance(v, str)]
    sketch["constraints"] = [c for c in sketch.get("constraints", []) if not any(r.split(".")[0] in ids for r in refs(c) if r.split(".")[0] in {e for e in ids})]
    sketch["dims"] = [d for d in sketch.get("dims", []) if not any(r.split(".")[0] in ids for r in refs(d))]


def move(sketch: dict, ids: Sequence[str], delta: Sequence[float]) -> None:
    du, dv = float(delta[0]), float(delta[1])
    for e in sketch.get("entities", []):
        if e["id"] not in ids:
            continue
        for name in POINT_FIELDS.get(e["type"], ()):
            e[name] = [e[name][0] + du, e[name][1] + dv]
        if e["type"] == "spline":
            e["points"] = [[p[0] + du, p[1] + dv] for p in e["points"]]


def rotate(sketch: dict, ids: Sequence[str], centre: Sequence[float], angle: float) -> None:
    a = math.radians(angle)
    cu, cv = float(centre[0]), float(centre[1])
    rot = lambda p: [cu + (p[0] - cu) * math.cos(a) - (p[1] - cv) * math.sin(a), cv + (p[0] - cu) * math.sin(a) + (p[1] - cv) * math.cos(a)]
    for e in sketch.get("entities", []):
        if e["id"] not in ids:
            continue
        for name in POINT_FIELDS.get(e["type"], ()):
            e[name] = rot(e[name])
        if e["type"] == "spline":
            e["points"] = [rot(p) for p in e["points"]]
        if e["type"] == "arc":
            e["start"] = float(e["start"]) + angle
            e["end"] = float(e["end"]) + angle
        if e["type"] in ("ellipse", "text"):
            e["angle"] = float(e.get("angle", 0)) + angle


def copy(sketch: dict, ids: Sequence[str], delta: Sequence[float] = (0, 0)) -> list[str]:
    import copy as _copy
    out = []
    for e in list(sketch.get("entities", [])):
        if e["id"] in ids:
            n = _copy.deepcopy(e)
            n["id"] = new_id(sketch, e["type"][0])
            sketch["entities"].append(n)
            out.append(n["id"])
    move(sketch, out, delta)
    return out


def mirror(sketch: dict, ids: Sequence[str], a: Sequence[float], b: Sequence[float]) -> list[str]:
    """Mirrored copies of entities across the line through a and b."""
    out = copy(sketch, ids)
    ax, ay = float(a[0]), float(a[1])
    dx, dy = float(b[0]) - ax, float(b[1]) - ay
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    refl = lambda p: (lambda along: [2 * (ax + ux * along) - p[0], 2 * (ay + uy * along) - p[1]])((p[0] - ax) * ux + (p[1] - ay) * uy)
    line_ang = math.degrees(math.atan2(uy, ux))
    for e in sketch["entities"]:
        if e["id"] not in out:
            continue
        for name in POINT_FIELDS.get(e["type"], ()):
            e[name] = refl(e[name])
        if e["type"] == "spline":
            e["points"] = [refl(p) for p in e["points"]]
        if e["type"] == "arc":                              # reflect the angles and swap the ends to keep it counter-clockwise
            s, en = float(e["start"]), float(e["end"])
            e["start"], e["end"] = 2 * line_ang - en, 2 * line_ang - s
        if e["type"] in ("ellipse", "text"):
            e["angle"] = 2 * line_ang - float(e.get("angle", 0))
    return out


def pattern_rect(sketch: dict, ids: Sequence[str], nx: int, ny: int, dx: float, dy: float) -> list[str]:
    out = []
    for j in range(max(1, ny)):
        for i in range(max(1, nx)):
            if i or j:
                out += copy(sketch, ids, (i * dx, j * dy))
    return out


def pattern_circular(sketch: dict, ids: Sequence[str], n: int, centre: Sequence[float], angle: float = 360.0) -> list[str]:
    out = []
    step = angle / n if abs(angle) >= 360 else (angle / (n - 1) if n > 1 else 0)
    for k in range(1, max(1, n)):
        new = copy(sketch, ids)
        rotate(sketch, new, centre, k * step)
        out += new
    return out


def _intersections(sketch: dict, eid: str) -> list[tuple[float, float]]:
    """(u, v) points where an entity meets any other non-construction entity."""
    me = entity_edges(entity(sketch, eid))
    pts: list[tuple[float, float]] = []
    for e in sketch.get("entities", []):
        if e["id"] == eid or e.get("construction") or e["type"] in ("point", "text"):
            continue
        for a in me:
            for b in entity_edges(e):
                try:
                    hits = a.find_intersection_points(b, tolerance=1e-6)
                except Exception:
                    try:
                        hits = [v.to_tuple() if hasattr(v, "to_tuple") else (v.X, v.Y, v.Z) for v in a.intersect(b).vertices()]
                    except Exception:
                        hits = []
                for h in hits:
                    hx, hy = (h.X, h.Y) if hasattr(h, "X") else (h[0], h[1])
                    pts.append((round(float(hx), 6), round(float(hy), 6)))
    return pts


def _param_on(e: dict, p: Sequence[float]) -> float:
    """A parameter along a line (0..1) or an arc/circle (angle in degrees) for a point near it."""
    if e["type"] == "line":
        a, b = e["a"], e["b"]
        d = (b[0] - a[0], b[1] - a[1])
        return ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / (d[0] ** 2 + d[1] ** 2 + 1e-12)
    return math.degrees(math.atan2(p[1] - e["c"][1], p[0] - e["c"][0]))


def split(sketch: dict, eid: str, at: Sequence[float]) -> list[str]:
    """Split a line, arc or circle at a point on it into two entities (a circle becomes one full arc)."""
    e = entity(sketch, eid)
    if e["type"] == "line":
        t = min(max(_param_on(e, at), 0.0), 1.0)
        a, b = e["a"], e["b"]
        m = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
        n = add(sketch, {"type": "line", "a": list(m), "b": list(b), "construction": e.get("construction", False)})
        e["b"] = list(m)
        return [eid, n["id"]]
    if e["type"] == "circle":
        ang = _param_on(e, at)
        e.update({"type": "arc", "start": ang, "end": ang + 360.0})
        return [eid]
    if e["type"] == "arc":
        ang = _param_on(e, at)
        s, en = float(e["start"]), float(e["end"])
        while ang < s:
            ang += 360.0
        if not s < ang < en:
            raise ValueError("the point is not on the arc")
        n = add(sketch, {"type": "arc", "c": list(e["c"]), "r": e["r"], "start": ang, "end": en, "construction": e.get("construction", False)})
        e["end"] = ang
        return [eid, n["id"]]
    raise ValueError(f"cannot split a {e['type']}")


def trim(sketch: dict, eid: str, near: Sequence[float]) -> list[str]:
    """Remove the piece of a line/arc/circle around the point `near`, between the nearest
    intersections with other entities (or its ends)."""
    e = entity(sketch, eid)
    hits = _intersections(sketch, eid)
    if e["type"] == "line":
        t_near = _param_on(e, near)
        ts = sorted(set([0.0, 1.0] + [min(max(_param_on(e, h), 0.0), 1.0) for h in hits]))
        lo = max([t for t in ts if t <= t_near], default=0.0)
        hi = min([t for t in ts if t >= t_near], default=1.0)
        a, b = list(e["a"]), list(e["b"])
        P = lambda t: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
        kept = []
        if lo > 1e-9:
            e["b"] = P(lo)
            kept.append(eid)
        if hi < 1 - 1e-9:
            n = add(sketch, {"type": "line", "a": P(hi), "b": b, "construction": e.get("construction", False)})
            kept.append(n["id"])
        if lo <= 1e-9:
            if hi < 1 - 1e-9:
                e["a"], e["b"] = P(hi), b
                sketch["entities"] = [x for x in sketch["entities"] if x["id"] != kept[-1]] if kept and kept[-1] != eid else sketch["entities"]
                return [eid]
            remove(sketch, [eid])
            return []
        return kept
    if e["type"] in ("circle", "arc"):
        if e["type"] == "circle":
            if len(hits) < 2:
                raise ValueError("a circle needs two crossings to trim")
            e.update({"type": "arc", "start": 0.0, "end": 360.0})
        s, en = float(e["start"]), float(e["end"])
        norm = lambda a: (lambda v: v if v >= s else v + 360.0)(a % 360.0 if e["type"] == "arc" else a)
        a_near = norm(_param_on(e, near))
        angs = sorted(set([s, en] + [norm(_param_on(e, h)) for h in hits if s <= norm(_param_on(e, h)) <= en]))
        lo = max([a for a in angs if a <= a_near], default=s)
        hi = min([a for a in angs if a >= a_near], default=en)
        kept = []
        if lo - s > 1e-6:
            e["end"] = lo
            kept.append(eid)
        if en - hi > 1e-6:
            n = add(sketch, {"type": "arc", "c": list(e["c"]), "r": e["r"], "start": hi, "end": en, "construction": e.get("construction", False)})
            kept.append(n["id"])
            if lo - s <= 1e-6:
                e.update({"start": hi, "end": en})
                sketch["entities"] = [x for x in sketch["entities"] if x["id"] != n["id"]]
                return [eid]
        if not kept:
            remove(sketch, [eid])
        return kept
    raise ValueError(f"cannot trim a {e['type']}")


def extend(sketch: dict, eid: str, end: str = "b") -> bool:
    """Extend a line's end to the next entity it would meet along its direction."""
    e = entity(sketch, eid)
    if e["type"] != "line":
        raise ValueError("only lines can be extended")
    a, b = e["a"], e["b"]
    d = (b[0] - a[0], b[1] - a[1])
    n = math.hypot(*d) or 1.0
    u = (d[0] / n, d[1] / n)
    origin, sign = (b, 1.0) if end == "b" else (a, -1.0)
    probe = {"type": "line", "a": list(origin), "b": [origin[0] + sign * u[0] * 1e4, origin[1] + sign * u[1] * 1e4], "id": "__probe"}
    sketch["entities"].append(probe)
    try:
        hits = _intersections(sketch, "__probe")
    finally:
        sketch["entities"] = [x for x in sketch["entities"] if x["id"] != "__probe"]
    best = None
    for h in hits:
        t = (h[0] - origin[0]) * sign * u[0] + (h[1] - origin[1]) * sign * u[1]
        if t > 1e-6 and (best is None or t < best[0]):
            best = (t, h)
    if best is None:
        return False
    e[end] = [best[1][0], best[1][1]]
    return True


def offset(sketch: dict, ids: Sequence[str], distance: float) -> list[str]:
    """Offset a chain of entities by a distance (positive = outward/left); returns the new ids."""
    edges = []
    for e in sketch.get("entities", []):
        if e["id"] in ids:
            edges += entity_edges(e)
    if not edges:
        raise ValueError("nothing to offset")
    wires = bd.Wire.combine(edges)
    out = []
    for w in wires:
        res = bd.offset(w, amount=float(distance), kind=bd.Kind.ARC, side=bd.Side.LEFT if not w.is_closed else bd.Side.BOTH)
        for ent in entities_from_edges(list(res.edges())):
            out.append(add(sketch, ent)["id"])
    return out


def fillet_corner(sketch: dict, line1: str, line2: str, r: float) -> str:
    """Round the corner where two lines meet: both are trimmed and a tangent arc is inserted."""
    e1, e2 = entity(sketch, line1), entity(sketch, line2)
    if e1["type"] != "line" or e2["type"] != "line":
        raise ValueError("corner fillets need two lines")
    ends = [(k1, k2) for k1 in ("a", "b") for k2 in ("a", "b")]
    k1, k2 = min(ends, key=lambda kk: math.hypot(e1[kk[0]][0] - e2[kk[1]][0], e1[kk[0]][1] - e2[kk[1]][1]))
    P = e1[k1]
    o1, o2 = e1["b" if k1 == "a" else "a"], e2["b" if k2 == "a" else "a"]
    d1 = (o1[0] - P[0], o1[1] - P[1]); d2 = (o2[0] - P[0], o2[1] - P[1])
    n1, n2 = math.hypot(*d1), math.hypot(*d2)
    u1, u2 = (d1[0] / n1, d1[1] / n1), (d2[0] / n2, d2[1] / n2)
    cos_t = max(-0.999999, min(0.999999, u1[0] * u2[0] + u1[1] * u2[1]))
    theta = math.acos(cos_t)
    t = r / math.tan(theta / 2)                              # distance from the corner to the tangent points
    if t > min(n1, n2):
        raise ValueError("the fillet radius is too large for these lines")
    T1 = [P[0] + u1[0] * t, P[1] + u1[1] * t]
    T2 = [P[0] + u2[0] * t, P[1] + u2[1] * t]
    bis = (u1[0] + u2[0], u1[1] + u2[1]); nb = math.hypot(*bis)
    C = [P[0] + bis[0] / nb * r / math.sin(theta / 2), P[1] + bis[1] / nb * r / math.sin(theta / 2)]
    a1, a2 = math.degrees(math.atan2(T1[1] - C[1], T1[0] - C[0])), math.degrees(math.atan2(T2[1] - C[1], T2[0] - C[0]))
    # the arc runs the short way round between the tangent points
    diff = (a2 - a1) % 360.0
    start, end = (a1, a1 + diff) if diff <= 180 else (a2, a2 + (360.0 - diff))
    _length_dims_to_distances(sketch, line1, k1, line2, k2)
    e1[k1] = T1
    e2[k2] = T2
    _drop_coincidence(sketch, f"{line1}.{k1}", f"{line2}.{k2}")
    arc = add(sketch, {"type": "arc", "c": C, "r": float(r), "start": start, "end": end})
    sketch.setdefault("constraints", []).extend([
        {"type": "coincident", "a": f"{line1}.{k1}", "b": f"{arc['id']}.{'s' if start == a1 else 'e'}"},
        {"type": "coincident", "a": f"{line2}.{k2}", "b": f"{arc['id']}.{'e' if start == a1 else 's'}"},
        {"type": "tangent", "a": line1, "b": arc["id"]}, {"type": "tangent", "a": line2, "b": arc["id"]}])
    sketch.setdefault("dims", []).append({"type": "radius", "a": arc["id"], "value": float(r)})   # the fillet's radius drives it
    return arc["id"]


def _drop_coincidence(sketch: dict, p: str, q: str) -> None:
    sketch["constraints"] = [c for c in sketch.get("constraints", []) if not (c.get("type") == "coincident" and {c.get("a"), c.get("b")} == {p, q})]


def _length_dims_to_distances(sketch: dict, line1: str, k1: str, line2: str, k2: str) -> None:
    """A length dimension on a line that is about to be trimmed at a corner becomes the
    distance from its far end to the other line (the same value, unaffected by the trim)."""
    for d in sketch.get("dims", []):
        if d.get("type") != "length":
            continue
        if d.get("a") == line1:
            d.update({"type": "distance_point_line", "a": f"{line1}.{'b' if k1 == 'a' else 'a'}", "b": line2})
        elif d.get("a") == line2:
            d.update({"type": "distance_point_line", "a": f"{line2}.{'b' if k2 == 'a' else 'a'}", "b": line1})


def chamfer_corner(sketch: dict, line1: str, line2: str, d: float) -> str:
    """Cut the corner where two lines meet with a straight line at distance d along each."""
    e1, e2 = entity(sketch, line1), entity(sketch, line2)
    ends = [(k1, k2) for k1 in ("a", "b") for k2 in ("a", "b")]
    k1, k2 = min(ends, key=lambda kk: math.hypot(e1[kk[0]][0] - e2[kk[1]][0], e1[kk[0]][1] - e2[kk[1]][1]))
    P = e1[k1]
    o1, o2 = e1["b" if k1 == "a" else "a"], e2["b" if k2 == "a" else "a"]
    for e, k, o in ((e1, k1, o1), (e2, k2, o2)):
        n = math.hypot(o[0] - P[0], o[1] - P[1])
        if d > n:
            raise ValueError("the chamfer is longer than the line")
        e[k] = [P[0] + (o[0] - P[0]) / n * d, P[1] + (o[1] - P[1]) / n * d]
    _length_dims_to_distances(sketch, line1, k1, line2, k2)
    _drop_coincidence(sketch, f"{line1}.{k1}", f"{line2}.{k2}")
    seg = add(sketch, {"type": "line", "a": list(e1[k1]), "b": list(e2[k2])})
    sketch.setdefault("constraints", []).extend([{"type": "coincident", "a": f"{line1}.{k1}", "b": f"{seg['id']}.a"},
                                                 {"type": "coincident", "a": f"{line2}.{k2}", "b": f"{seg['id']}.b"}])
    sketch.setdefault("dims", []).append({"type": "length", "a": seg["id"], "value": round(math.hypot(e2[k2][0] - e1[k1][0], e2[k2][1] - e1[k1][1]), 6)})
    return seg["id"]


def entities_from_edges(edges: Sequence[bd.Edge], construction: bool = False, samples: int = 12) -> list[dict]:
    """Sketch entities (in XY) from build123d edges: lines, arcs/circles, ellipses, splines."""
    out: list[dict] = []
    for e in edges:
        kind = e.geom_type
        if kind == bd.GeomType.LINE:
            a, b = e.position_at(0), e.position_at(1)
            out.append({"type": "line", "a": [round(a.X, 6), round(a.Y, 6)], "b": [round(b.X, 6), round(b.Y, 6)]})
        elif kind == bd.GeomType.CIRCLE:
            c = e.arc_center
            r = e.radius
            if e.is_closed:
                out.append({"type": "circle", "c": [round(c.X, 6), round(c.Y, 6)], "r": round(r, 6)})
            else:
                a, b = e.position_at(0), e.position_at(1)
                s, en = math.degrees(math.atan2(a.Y - c.Y, a.X - c.X)), math.degrees(math.atan2(b.Y - c.Y, b.X - c.X))
                mid = e.position_at(0.5)
                m = math.degrees(math.atan2(mid.Y - c.Y, mid.X - c.X))
                while en <= s:
                    en += 360.0
                while m < s:
                    m += 360.0
                if not s < m < en:                             # the edge runs clockwise: swap
                    s, en = en - 360.0, s
                    while en <= s:
                        en += 360.0
                out.append({"type": "arc", "c": [round(c.X, 6), round(c.Y, 6)], "r": round(r, 6), "start": round(s, 6), "end": round(en, 6)})
        else:
            pts = [e.position_at(i / samples) for i in range(samples + 1)]
            out.append({"type": "spline", "kind": "fit", "closed": bool(e.is_closed), "points": [[round(p.X, 6), round(p.Y, 6)] for p in (pts[:-1] if e.is_closed else pts)]})
    for ent in out:
        if construction:
            ent["construction"] = True
    return out


def project_edges(sketch: dict, edges: Sequence[bd.Edge], plane: bd.Plane, construction: bool = True) -> list[str]:
    """Project 3D edges onto the sketch plane (orthographically, along its normal) and add
    them as entities: lines stay lines, circles parallel to the plane stay circles/arcs,
    anything else becomes a fit spline through sampled points."""
    ents: list[dict] = []
    for e in edges:
        try:
            local = plane.to_local_coords(e)
        except Exception:
            continue
        kind = local.geom_type
        if kind == bd.GeomType.LINE:
            a, b = local.position_at(0), local.position_at(1)
            if math.hypot(b.X - a.X, b.Y - a.Y) < 1e-9:
                continue
            ents.append({"type": "line", "a": [round(a.X, 6), round(a.Y, 6)], "b": [round(b.X, 6), round(b.Y, 6)]})
            continue
        if kind == bd.GeomType.CIRCLE:
            try:
                axis = local.arc_center, local.normal() if hasattr(local, "normal") else None
            except Exception:
                axis = (local.arc_center, None)
            n = None
            try:
                n = bd.Vector(*local.wrapped.Curve().Axis().Direction()) if False else None
            except Exception:
                n = None
            flat = local.position_at(0).Z, local.position_at(0.25).Z, local.position_at(0.5).Z
            if max(flat) - min(flat) < 1e-6:                    # the circle lies parallel to the plane: exact
                sample = [local.position_at(i / 8) for i in range(9)]
                sample = [bd.Vector(p.X, p.Y, 0) for p in sample]
                c = local.arc_center
                flat_edge = bd.Edge.make_circle(local.radius, bd.Plane(origin=(c.X, c.Y, 0)), start_angle=0, end_angle=360) if local.is_closed else None
                if flat_edge is not None:
                    ents += entities_from_edges([flat_edge])
                    continue
                a, b, m = local.position_at(0), local.position_at(1), local.position_at(0.5)
                deg = lambda q: math.degrees(math.atan2(q.Y - c.Y, q.X - c.X))
                s, en, mid = deg(a), deg(b), deg(m)
                while en <= s:
                    en += 360.0
                while mid < s:
                    mid += 360.0
                if not s < mid < en:
                    s, en = en - 360.0, s
                    while en <= s:
                        en += 360.0
                ents.append({"type": "arc", "c": [round(c.X, 6), round(c.Y, 6)], "r": round(local.radius, 6), "start": round(s, 6), "end": round(en, 6)})
                continue
        k = 16
        pts = [local.position_at(i / k) for i in range(k + 1)]
        ents.append({"type": "spline", "kind": "fit", "closed": bool(local.is_closed), "points": [[round(q.X, 6), round(q.Y, 6)] for q in (pts[:-1] if local.is_closed else pts)]})
    for ent in ents:
        if construction:
            ent["construction"] = True
    return [add(sketch, ent)["id"] for ent in ents]


def import_svg(sketch: dict, path: str, scale: float = 1.0) -> list[str]:
    """Add the curves of an SVG file as sketch entities."""
    shapes = bd.import_svg(path)
    edges = []
    for s in shapes:
        edges += list(s.edges())
    if scale != 1.0:
        edges = [e.scale(scale) for e in edges]
    return [add(sketch, ent)["id"] for ent in entities_from_edges(edges)]


# ---------------------------------------------------------------------------- the old simple profiles

def from_simple(profile: dict) -> dict:
    """Decompose rect/circle/polygon/slot/text profiles into a full sketch with the
    constraints Fusion would add (so the result can be edited entity by entity)."""
    sk: dict = {"type": "sketch", "entities": [], "constraints": [], "dims": []}
    t = profile.get("type")
    if t == "sketch":
        return profile
    if t == "circle":
        c = add(sk, {"type": "circle", "c": list(profile.get("center", [0, 0])), "r": float(profile["r"])})
        sk["dims"].append({"type": "diameter", "a": c["id"], "value": 2 * float(profile["r"])})
    elif t == "rect":
        cx, cy = profile.get("center", [0, 0]); w, h = float(profile["w"]) / 2, float(profile["h"]) / 2
        ang = math.radians(float(profile.get("angle", 0)))
        rot = lambda p: [cx + p[0] * math.cos(ang) - p[1] * math.sin(ang), cy + p[0] * math.sin(ang) + p[1] * math.cos(ang)]
        corners = [rot([-w, -h]), rot([w, -h]), rot([w, h]), rot([-w, h])]
        ids = [add(sk, {"type": "line", "a": corners[i], "b": corners[(i + 1) % 4]})["id"] for i in range(4)]
        for i in range(4):
            sk["constraints"].append({"type": "coincident", "a": f"{ids[i]}.b", "b": f"{ids[(i + 1) % 4]}.a"})
        if abs(float(profile.get("angle", 0))) < 1e-9:
            sk["constraints"] += [{"type": "horizontal", "a": ids[0]}, {"type": "horizontal", "a": ids[2]}, {"type": "vertical", "a": ids[1]}, {"type": "vertical", "a": ids[3]}]
        else:
            sk["constraints"] += [{"type": "perpendicular", "a": ids[0], "b": ids[1]}, {"type": "parallel", "a": ids[0], "b": ids[2]}, {"type": "parallel", "a": ids[1], "b": ids[3]}]
        # dimension the rectangle by the distance between opposite sides (survives corner fillets/chamfers)
        sk["dims"] += [{"type": "distance_point_line", "a": f"{ids[3]}.a", "b": ids[1], "value": float(profile["w"])},
                       {"type": "distance_point_line", "a": f"{ids[0]}.a", "b": ids[2], "value": float(profile["h"])}]
        if profile.get("corner_r"):
            r = float(profile["corner_r"])
            for i in range(4):
                fillet_corner(sk, ids[i], ids[(i + 1) % 4], r)
    elif t == "polygon":
        pts = [list(map(float, p)) for p in profile["points"]]
        ids = [add(sk, {"type": "line", "a": pts[i], "b": pts[(i + 1) % len(pts)]})["id"] for i in range(len(pts))]
        for i in range(len(pts)):
            sk["constraints"].append({"type": "coincident", "a": f"{ids[i]}.b", "b": f"{ids[(i + 1) % len(pts)]}.a"})
    elif t == "slot":
        cx, cy = profile.get("center", [0, 0]); L, w = float(profile["length"]), float(profile["w"])
        ang = math.radians(float(profile.get("angle", 0))); half = (L - w) / 2; r = w / 2
        u = (math.cos(ang), math.sin(ang)); n = (-u[1], u[0])
        c1 = [cx - u[0] * half, cy - u[1] * half]; c2 = [cx + u[0] * half, cy + u[1] * half]
        deg = math.degrees(ang)
        top = add(sk, {"type": "line", "a": [c1[0] + n[0] * r, c1[1] + n[1] * r], "b": [c2[0] + n[0] * r, c2[1] + n[1] * r]})
        bot = add(sk, {"type": "line", "a": [c2[0] - n[0] * r, c2[1] - n[1] * r], "b": [c1[0] - n[0] * r, c1[1] - n[1] * r]})
        a2 = add(sk, {"type": "arc", "c": c2, "r": r, "start": deg - 90, "end": deg + 90})
        a1 = add(sk, {"type": "arc", "c": c1, "r": r, "start": deg + 90, "end": deg + 270})
        sk["constraints"] += [{"type": "coincident", "a": f"{top['id']}.b", "b": f"{a2['id']}.e"}, {"type": "coincident", "a": f"{a2['id']}.s", "b": f"{bot['id']}.a"},
                              {"type": "coincident", "a": f"{bot['id']}.b", "b": f"{a1['id']}.e"}, {"type": "coincident", "a": f"{a1['id']}.s", "b": f"{top['id']}.a"},
                              {"type": "tangent", "a": top["id"], "b": a2["id"]}, {"type": "tangent", "a": bot["id"], "b": a2["id"]},
                              {"type": "tangent", "a": top["id"], "b": a1["id"]}, {"type": "tangent", "a": bot["id"], "b": a1["id"]},
                              {"type": "equal", "a": a1["id"], "b": a2["id"]}]
        sk["dims"] += [{"type": "distance", "a": f"{a1['id']}.c", "b": f"{a2['id']}.c", "value": L - w}, {"type": "diameter", "a": a1["id"], "value": w}]
    elif t == "text":
        add(sk, {"type": "text", "at": list(profile.get("center", [0, 0])), "text": profile.get("text", ""), "size": float(profile.get("size", 8)),
                 "angle": float(profile.get("angle", 0)), "font": profile.get("font") or "Arial", "bold": bool(profile.get("bold"))})
    else:
        raise ValueError(f"unknown profile type {t!r}")
    return sk


# ---------------------------------------------------------------------------- code emitter

def code(sketch: dict, plane_expr: str = "Plane.XY", indent: str = "") -> str:
    """build123d code that rebuilds this sketch's profile (for folding into a model file)."""
    lines = [f"{indent}with BuildSketch({plane_expr}) as sk:"]
    ents = [e for e in sketch.get("entities", []) if not e.get("construction")]
    curve_lines = []
    for e in ents:
        t = e["type"]
        f = lambda v: f"{float(v):g}"
        if t == "line":
            curve_lines.append(f"Line(({f(e['a'][0])}, {f(e['a'][1])}), ({f(e['b'][0])}, {f(e['b'][1])}))")
        elif t == "arc":
            s, en = float(e["start"]), float(e["end"])
            while en <= s:
                en += 360
            curve_lines.append(f"CenterArc(({f(e['c'][0])}, {f(e['c'][1])}), {f(e['r'])}, {f(s)}, {f(en - s)})")
        elif t == "circle":
            curve_lines.append(f"Circle({f(e['r'])}).moved(Location(({f(e['c'][0])}, {f(e['c'][1])}, 0))).edges()[0]")
        elif t == "ellipse":
            if e.get("start") is not None:
                curve_lines.append(f"EllipticalCenterArc((0, 0), {f(e['rx'])}, {f(e['ry'])}, {f(e['start'])}, {f(e['end'])}).moved(Location(({f(e['c'][0])}, {f(e['c'][1])}, 0), (0, 0, {f(e.get('angle', 0))}))).edges()[0]")
            else:
                curve_lines.append(f"Ellipse({f(e['rx'])}, {f(e['ry'])}).moved(Location(({f(e['c'][0])}, {f(e['c'][1])}, 0), (0, 0, {f(e.get('angle', 0))}))).edges()[0]")
        elif t == "spline":
            pts = ", ".join(f"({f(p[0])}, {f(p[1])})" for p in e["points"])
            curve_lines.append((f"Bezier({pts})" if e.get("kind") == "control" else f"Spline({pts}{', periodic=True' if e.get('closed') else ''})"))
    if curve_lines:
        lines.append(f"{indent}    with BuildLine():")
        for c in curve_lines:
            lines.append(f"{indent}        {c}")
        lines.append(f"{indent}    make_face()")
    for e in ents:
        if e["type"] == "text":
            lines.append(f"{indent}    with Locations(({float(e['at'][0]):g}, {float(e['at'][1]):g})):")
            lines.append(f"{indent}        Text({e.get('text', '')!r}, font_size={float(e.get('size', 8)):g}, rotation={float(e.get('angle', 0)):g})")
    lines.append(f"{indent}profile = sk.sketch")
    return "\n".join(lines)


def summary(sketch: dict) -> str:
    ents = sketch.get("entities", [])
    kinds: dict[str, int] = {}
    for e in ents:
        kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    return ", ".join(f"{n} {k}{'s' if n > 1 else ''}" for k, n in kinds.items()) + (f", {len(sketch.get('constraints', []))} constraints" if sketch.get("constraints") else "") + (f", {len(sketch.get('dims', []))} dims" if sketch.get("dims") else "")
