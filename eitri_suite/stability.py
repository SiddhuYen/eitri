"""Base tipping: does a machine standing on its base stay standing through its whole range of motion?

Only a project that holds both a mechanism file (the joints, their limits, the payload) and a CAD model (the base's footprint and
every body's mass) can answer it, so it is a suite check. It runs in the mechanism file's build and in the CAD file's build (either
change can tip the machine), and is category "integration" (the MCP results mark it [cross-domain]).

WHAT IT DECIDES FIRST: is the base free-standing?
  COUPLING base_fixing in the mechanism file:  "bolted" | "clamped" (fastened: the check passes and says that is its basis; a "bolted"
                                               base with no vertical through hole in its bodies is a warning) | "free" (stands on its
                                               own: the check runs).
  m.mount("wall" | "ceiling" | "edge")         fastened by necessity: passes, with that basis.
  nothing declared, mount "table" (default):   a base with no vertical through hole (nothing could bolt it down) is taken as
                                               free-standing and the check runs, saying so; a base WITH through holes may be bolted:
                                               "not run" (a warning) with the reason and the free-standing result for information.

WHAT IT COMPUTES (free-standing):
  support polygon   the convex hull, in plan, of the downward-facing flat faces at the base bodies' lowest level (the bodies COUPLING
                    links lists under the mechanism's fixed link). Gravity is -Z of the CAD model (the mechanism's base plane is z = 0).
  masses            every CAD body: a purchased part named in HARDWARE at its suite-catalog mass (eitri_suite.catalog, unverified
                    figures, said so), else its MATERIALS density (core eitri.measure.DENSITY) x its CAD volume x COUPLING print_fill;
                    its centre of mass from the CAD solid. An actuator with no CAD body: the Actuator's mass_g, else its catalog mass, at
                    its joint. A link with no CAD body: its declared mass_g at its declared cg. Every declared point mass (the payload)
                    where the mechanism puts it, carried in EVERY pose (conservative). Bodies listed under no link count as fixed to
                    the base. PURCHASED items that are not CAD bodies (a wall supply, screws) are left out and listed.
  poses             a grid over every moving joint's declared limits (endpoints included), home, the zero pose and every firmware
                    SEQUENCE step. Poses that would put the arm into the table are NOT removed (conservative). Static only: the
                    accelerations of a move are not modelled.
  margin            the combined centre of mass, projected along gravity, must stay inside the support polygon by at least
                    h x tan(10 deg), h = its height above the support plane: the machine must not tip when its support is tilted
                    10 deg in any direction. Basis: the 10 deg tilt criterion of IEC 62368-1 clause 8.6 (stability of equipment),
                    used here as a design margin for any free-standing machine; ASSUMPTION: that standard is written for audio/video
                    and ICT equipment, and its normative text was not re-read here (its stability test tilts the equipment 10 deg).
"""
from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np

from eitri.harness.checks import HCheck

NAME = "stability: the base does not tip over"
CAT = "integration"
TILT_DEG = 10.0
TILT_BASIS = ("IEC 62368-1 cl. 8.6 stability: no tipping at a 10 deg tilt, used as a design margin "
              "(ASSUMPTION: written for AV/ICT equipment; normative text not re-read)")
FASTENED = ("bolted", "clamped")
FREE = ("free", "free-standing", "freestanding")
MAX_POSES = 3000                # the limits grid: 7 values per joint for 4 joints, 5 for 5 (endpoints always in)


def _check(severity: str, measured: str, expected: str = "", refs=()) -> HCheck:
    return HCheck(name=NAME, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category=CAT,
                  refs=list(dict.fromkeys(str(r) for r in refs if r)))


def not_run(why: str, refs=()) -> HCheck:
    c = _check("warn", f"not run: {why}", "a declared or provable base fixing, and masses for every moving body", refs)
    c.not_run = [why.split(":")[0][:120]]                    # status not_run (core eitri.checks)
    return c


# ---------------------------------------------------------------------------- geometry

def hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Convex hull, counter-clockwise (Andrew's monotone chain)."""
    pts = sorted(set((round(x, 6), round(y, 6)) for x, y in points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def inside_distance(poly: list[tuple[float, float]], x: float, y: float) -> float:
    """Signed distance from (x, y) to the edges of a convex CCW polygon: positive inside (the nearest edge), negative outside."""
    n = len(poly)
    if n < 3:
        return -math.inf
    best = math.inf
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        L = math.hypot(ex, ey) or 1e-12
        best = min(best, (ex * (y - ay) - ey * (x - ax)) / L)        # left of a CCW edge = inside
    if best >= 0:
        return best
    # outside: the true distance to the polygon (to the nearest segment), negative
    d = math.inf
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * ex + (y - ay) * ey) / ((ex * ex + ey * ey) or 1e-12)))
        d = min(d, math.hypot(x - (ax + t * ex), y - (ay + t * ey)))
    return -d


def _edge_points(edge, n: int = 12) -> list[tuple[float, float, float]]:
    out = []
    for k in range(n + 1):
        try:
            v = edge.position_at(k / n)
            out.append((float(v.X), float(v.Y), float(v.Z)))
        except Exception:                                    # noqa: BLE001
            continue
    return out


def footprint(shapes: list, tol: float = 0.3) -> tuple[list[tuple[float, float]], float]:
    """(support polygon CCW, support plane z) from the downward-facing flat faces at the bodies' lowest level."""
    from eitri import measure
    zmin = min(measure.bounding_box(s).min.Z for s in shapes)
    pts: list[tuple[float, float]] = []
    for s in shapes:
        for f in s.faces():
            try:
                bb = f.bounding_box()
                if bb.max.Z - bb.min.Z > 1e-3 or abs(bb.min.Z - zmin) > tol:
                    continue
                nrm = f.normal_at()
                if float(nrm.Z) > -0.99:
                    continue
            except Exception:                                # noqa: BLE001
                continue
            for e in f.edges():
                pts += [(x, y) for x, y, _z in _edge_points(e)]
    return hull(pts), float(zmin)


def vertical_through_holes(shapes: list, tol: float = 0.3) -> int:
    """Vertical through holes in the bodies that stand on the support plane (a hole in a standoff above the plate fixes nothing)."""
    from eitri import measure
    if not shapes:
        return 0
    zmin = min(measure.bounding_box(s).min.Z for s in shapes)
    n = 0
    for s in shapes:
        if measure.bounding_box(s).min.Z > zmin + tol:
            continue
        try:
            n += len(measure.holes(s, axis="Z", through=True))
        except Exception:                                    # noqa: BLE001
            continue
    return n


# ---------------------------------------------------------------------------- masses

def mass_items(parts, mech, links: dict[str, list[str]], bindings: dict, hardware: dict | None, print_fill: float,
               purchased=None) -> tuple[list[dict], list[str], list[str]]:
    """([{name, link, grams, com (zero-pose world) | None, kind, source}], unknown on a moving link, notes)."""
    from eitri import measure
    from . import catalog as parts_catalog
    from .kinematics import link_of
    hardware = hardware or {}
    of, _twice = link_of(links)
    base = mech.base
    items: list[dict] = []
    unknown_moving: list[str] = []
    notes: list[str] = []

    def catalog_mass(cp, moving: bool, who: str):
        """(grams, source text) of a catalog part, the conservative figure for where it rides: the higher published mass on a moving
        link, the lower on the base. An unverified figure: on a moving link it stops the check (unverified_moving); on the base it
        is left out (conservative for a body standing on the support)."""
        spec = cp.spec("mass_g") if moving else (cp.spec("mass_g_low") or cp.spec("mass_g"))
        if spec is None:
            return None, ""
        if not spec.verified:
            (unverified_moving if moving else notes).append(
                f"{who} = {cp.id} {float(spec.value):g} g [source: {spec.cite}]" if moving else
                f"{who} ({cp.id}, on the base) has only an unverified catalog mass: left out, which is conservative")
            return None, "unverified"
        return float(spec.value), f"catalog {cp.id} {float(spec.value):g} g [source: {spec.cite}]"

    unverified_moving: list[str] = []
    for p in parts:
        link = of.get(p.name, base)
        ref = hardware.get(p.name)
        ref = ref.get("id") if isinstance(ref, dict) else ref
        grams, src = None, ""
        if ref:
            cp = parts_catalog.lookup(str(ref))
            if cp is not None and cp.mass_g:
                grams, src = catalog_mass(cp, link != base, p.name)
                if src == "unverified":
                    continue
        if grams is None:
            mat = getattr(p, "material", None)
            if mat in measure.DENSITY:
                try:
                    vol = measure.mass_properties(p.shape)["volume"]
                except Exception:                            # noqa: BLE001
                    vol = None
                if vol:
                    grams = vol / 1000.0 * measure.DENSITY[mat] * print_fill
                    src = f"{mat} {measure.DENSITY[mat]:g} g/cm3 x {vol / 1000:.1f} cm3" + (f" x fill {print_fill:g}" if print_fill != 1.0 else "")
        if grams is None:
            (unknown_moving if link != base else notes).append(p.name if link != base else f"{p.name} (on the base) has no mass: left out, which is conservative")
            continue
        try:
            com = np.array(measure.mass_properties(p.shape)["center_of_mass"], dtype=float)
        except Exception:                                    # noqa: BLE001
            bb = measure.bounding_box(p.shape)
            com = np.array([(bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2, (bb.min.Z + bb.max.Z) / 2])
        items.append({"name": p.name, "link": link, "grams": grams, "com": com, "kind": "cad", "source": src,
                      "listed": p.name in of})
    from eitri.mechanism.model import catalog_actuator  # noqa: F401  (kept for the record: the kernel's actuator table has no mass)
    for j in mech.joints:
        a = j.actuator
        if a is None:
            continue
        b = (bindings.get("joints") or {}).get(j.name) or {}
        if b.get("part") and b.get("part") in {p.name for p in parts}:
            continue                                         # its CAD body is counted above
        grams, src = float(a.mass_g or 0.0), f"{j.name} Actuator mass_g (mechanism file)"
        if grams <= 0:
            cp = parts_catalog.lookup(str(a.name))
            if cp is not None and cp.mass_g:
                g, src = catalog_mass(cp, j.parent != base, f"actuator of {j.name}")
                if src == "unverified":
                    continue
                grams = g or 0.0
                src = f"{j.name} actuator {src}"
        if grams <= 0:
            (unknown_moving if j.parent != base else notes).append(f"actuator of {j.name}" if j.parent != base else f"actuator of {j.name} has no mass: left out")
            continue
        items.append({"name": f"{a.name} ({j.name})", "link": j.parent, "grams": grams, "com": None, "kind": "joint", "joint": j.name, "source": src})
    linked = set(links)
    for name, l in mech.links.items():
        if name in linked and any(i["link"] == name and i["kind"] == "cad" for i in items):
            continue
        if l.mass_g > 0:
            items.append({"name": f"link {name}", "link": name, "grams": float(l.mass_g), "com": None, "kind": "cg", "source": f"link {name} mass_g (mechanism file, no CAD body)"})
    for pm in mech.masses:
        if pm.mass_g > 0:
            items.append({"name": pm.name, "link": pm.link or mech.resolved_tip()[0], "grams": float(pm.mass_g), "com": None, "kind": "mass",
                          "source": f"{pm.kind} {pm.mass_g:g} g (mechanism file), carried in every pose"})
    cad_names = {p.name for p in parts}
    left = [str(x[0] if isinstance(x, (tuple, list)) else x) for x in (purchased or [])]
    left = [x for x in left if x not in cad_names]
    if left:
        notes.append("PURCHASED items that are not CAD bodies are left out: " + ", ".join(left[:8]))
    return items, unknown_moving, notes, unverified_moving


# ---------------------------------------------------------------------------- poses

def poses(mech, sequence: dict | None = None) -> list[tuple[str, dict[str, float]]]:
    from .kinematics import keyframes
    moving = list(mech.moving_joints)
    out = list(keyframes(mech, sequence))
    if not moving:
        return out
    per = max(3, min(9, int(MAX_POSES ** (1.0 / len(moving)))))
    grids = [np.linspace(float(j.limit[0]), float(j.limit[1]), per) for j in moving]
    for combo in itertools.product(*grids):
        out.append(("limits grid", {j.name: float(v) for j, v in zip(moving, combo)}))
    return out


def com_at(mech, items: list[dict], q: dict[str, float]) -> tuple[np.ndarray, float]:
    T = mech.transforms(q)
    pts = None
    total, moment = 0.0, np.zeros(3)
    for it in items:
        if it["kind"] == "cad":
            M = T.get(it["link"], np.eye(4))
            w = M[:3, :3] @ it["com"] + M[:3, 3]
        else:
            if pts is None:
                pts = mech.points(q)
            w = (pts["joints"][it["joint"]] if it["kind"] == "joint" else pts["cg"][it["link"]] if it["kind"] == "cg"
                 else pts["masses"][it["name"]])
        total += it["grams"]
        moment += it["grams"] * np.asarray(w, dtype=float)
    return moment / max(total, 1e-12), total


def _fmt_q(q: dict[str, float]) -> str:
    return "{" + ", ".join(f"{k} {v:.0f}°" for k, v in q.items()) + "}"


# ---------------------------------------------------------------------------- the check

def check(parts, mech, coupling: dict, links: dict[str, list[str]], bindings: dict, hardware: dict | None = None,
          purchased=None, sequence: dict | None = None) -> tuple[HCheck, dict]:
    """(the tipping check, its data) for a CAD model and the mechanism coupled to it. links: the effective links map. Never raises:
    an error inside is a "not run" with the error, never a pass."""
    try:
        return _check_tipping(parts, mech, coupling, links, bindings, hardware, purchased, sequence)
    except Exception as exc:                                 # noqa: BLE001
        return not_run(f"the check itself failed ({type(exc).__name__}: {exc})"), {}


def _check_tipping(parts, mech, coupling, links, bindings, hardware, purchased, sequence) -> tuple[HCheck, dict]:
    coupling = coupling if isinstance(coupling, dict) else {}
    data: dict[str, Any] = {}
    base_link = mech.base
    by_name = {p.name: p for p in parts}
    from .kinematics import link_of
    of, _twice = link_of(links)
    # every body riding on the fixed link: those COUPLING lists under it, and those it lists under no link (fixed to the base)
    base_names = [p.name for p in parts if of.get(p.name, base_link) == base_link]
    decl = str(coupling.get("base_fixing") or "").strip().lower()
    mount = getattr(mech, "mounted_on", "table")
    if decl and decl not in FASTENED + FREE:
        return _check("fail", f"COUPLING base_fixing={coupling.get('base_fixing')!r} is not one of 'bolted', 'clamped', 'free'",
                      "base_fixing = 'bolted' | 'clamped' | 'free'"), data
    if decl in FASTENED:
        holes = vertical_through_holes([by_name[n].shape for n in base_names]) if base_names else 0
        data.update(basis="declared", fixing=decl, through_holes=holes)
        if decl == "bolted" and base_names and holes == 0:
            return _check("warn", f"declared bolted (mechanism COUPLING base_fixing), but the base ({', '.join(base_names)}) has no vertical "
                                  "through hole for a bolt: add the holes, or declare 'clamped' or 'free'", "bolt holes through the base", base_names), data
        return _check("pass", f"not a free-standing base: declared {decl} down (mechanism COUPLING base_fixing)"
                              + (f", {holes} vertical through hole(s) in the base" if decl == "bolted" else "")
                              + "; tipping does not apply to a fastened base", "a fastened base, or a free-standing one that does not tip", base_names), data
    if not decl and mount in ("wall", "ceiling", "edge"):
        data.update(basis="mount", fixing=mount)
        return _check("pass", f"not a free-standing base: the mechanism is mounted on a {mount} (m.mount), which fastens it; tipping does not apply",
                      "a fastened base, or a free-standing one that does not tip"), data
    if not base_names:
        return not_run(f"COUPLING links lists no CAD body under the fixed link {base_link!r}, so the base's footprint is unknown"), data
    base_shapes = [by_name[n].shape for n in base_names]
    holes = vertical_through_holes(base_shapes)
    if decl in FREE:
        basis = "declared free-standing (mechanism COUPLING base_fixing='free')"
    elif holes == 0:
        basis = ("taken as free-standing: nothing declares how the base is fixed (COUPLING base_fixing) and the base has no vertical "
                 "through hole, so nothing bolts it down")
    else:
        basis = ""
    poly, z0 = footprint(base_shapes)
    data.update(fixing=decl or None, through_holes=holes, support=[[round(x, 2), round(y, 2)] for x, y in poly], support_z=round(z0, 2))
    if len(poly) < 3:
        return not_run(f"no flat underside found on the base bodies ({', '.join(base_names)}), so no support polygon"), data
    fill = float(coupling.get("print_fill", 1.0))
    items, unknown, notes, unverified = mass_items(parts, mech, links, bindings, hardware, fill, purchased)
    if unverified:                                           # never a verdict on a mass nobody compared with a datasheet
        return not_run("ASSUMPTION: unverified catalog figure for a moving body: " + "; ".join(unverified[:6])
                       + ". Give the part's mass from its datasheet (Actuator mass_g= in the mechanism file, or a verified catalog entry)",
                       [u.split(" =")[0] for u in unverified]), data
    if unknown:
        return not_run(f"no mass for {', '.join(unknown[:6])} on a moving link (give it a MATERIALS entry or a HARDWARE catalog id)", unknown), data
    tan_t = math.tan(math.radians(TILT_DEG))
    worst = None
    n = 0
    for label, q in poses(mech, sequence):
        q = mech.clamp(q)
        c, total = com_at(mech, items, q)
        h = max(0.0, float(c[2]) - z0)
        d = inside_distance(poly, float(c[0]), float(c[1]))
        need = h * tan_t
        n += 1
        if worst is None or d - need < worst["d"] - worst["need"]:
            worst = {"label": label, "q": q, "com": c, "d": d, "need": need, "h": h}
    total = sum(i["grams"] for i in items)
    moving_g = sum(i["grams"] for i in items if i["link"] != base_link)
    data.update(poses=n, total_g=round(total, 1), moving_g=round(moving_g, 1), worst={
        "pose": {k: round(v, 1) for k, v in worst["q"].items()}, "label": worst["label"], "com": [round(float(x), 1) for x in worst["com"]],
        "inside_mm": round(worst["d"], 1), "needed_mm": round(worst["need"], 1)},
        masses=[{"name": i["name"], "link": i["link"], "g": round(i["grams"], 1), "source": i["source"]} for i in items], notes=notes)
    srcs = "; ".join(f"{i['name']} {i['grams']:.0f} g ({i['source']})" for i in sorted(items, key=lambda i: -i["grams"])[:8])
    pw = (f"{len(poly)}-point support polygon x {min(p[0] for p in poly):.0f}..{max(p[0] for p in poly):.0f}, "
          f"y {min(p[1] for p in poly):.0f}..{max(p[1] for p in poly):.0f} mm")
    where = (f"at {worst['label']} {_fmt_q(worst['q'])} the centre of mass ({total:.0f} g, {moving_g:.0f} g of it moving) is at "
             f"x {worst['com'][0]:.0f}, y {worst['com'][1]:.0f}, {worst['h']:.0f} mm up")
    ok = worst["d"] >= worst["need"]
    if ok:
        msg = (f"{where}: {worst['d']:.1f} mm inside the {pw}, needed {worst['need']:.1f} mm (h x tan {TILT_DEG:g}°) in the worst of {n} poses")
    elif worst["d"] >= 0:
        msg = (f"{where}: only {worst['d']:.1f} mm inside the {pw}; a {TILT_DEG:g}° tilt needs {worst['need']:.1f} mm ({n} poses). "
               "Fix: a wider or heavier base, a lighter arm or payload, a smaller reach, or bolt/clamp it down (and declare base_fixing)")
    else:
        msg = (f"{where}: {-worst['d']:.1f} mm OUTSIDE the {pw}: it tips over ({n} poses). "
               "Fix: a wider or heavier base, a lighter arm or payload, a smaller reach, or bolt/clamp it down (and declare base_fixing)")
    tail = f". Masses: {srcs}" + (f". {'; '.join(notes)}" if notes else "") + ". Payload carried in every pose; static, no accelerations"
    expected = f"centre of mass inside the support polygon by >= h x tan {TILT_DEG:g}° in every pose [basis: {TILT_BASIS}]"
    if not basis:
        verdict = "it would stand" if ok else "it would tip"
        return not_run(f"the base ({', '.join(base_names)}) has {holes} vertical through hole(s), so it may be bolted down, and the mechanism "
                       f"file does not say: declare COUPLING base_fixing='bolted', 'clamped' or 'free'. Free-standing, {verdict}: {msg}{tail}",
                       base_names), data
    return _check("pass" if ok else "fail", f"{basis}. {msg}{tail}", expected, base_names), data
