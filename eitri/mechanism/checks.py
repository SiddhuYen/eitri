"""The mechanism checks: what every build is graded on.

    structure      the joints form a tree from the ground link (anything else is reported, not raised)
    mass rollup    every link's mass with what it carries (point masses, motors mounted on it), the payload, the total
    torque/force   the static holding torque (revolute) or force (prismatic) each joint needs at the worst sampled pose, against
                   its actuator's stall and rated (recommended continuous) output after the transmission
    speed          each joint's required speed against the actuator's no-load speed after the transmission
    joint limits   the declared range against the actuator's own travel (a 180 deg servo cannot sweep 270)
    targets        every stated target point reachable by the tip within its tolerance (bounded IK inside the limits; when it
                   is not, whether it would be reachable with the limits ignored)
    reach          the tip's radius from the first joint over the sampled poses, against `reach_mm` if declared

Statics are gravity only (no acceleration, friction or cable loads); g = 9.80665 m/s^2 (ISO 80000-3), 1 kgf.cm = 0.0980665 N.m.
The worst pose is the maximum over sampled poses (home, every joint alone at each limit, the {min, mid, max} grid, and random
poses from a fixed seed): a sampled maximum, not a proof; for the usual arm the maximum is the extended horizontal pose, which
the grid contains when the zero pose is declared extended. Nothing here raises on a bad design: a bad design is a failed check.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np

from ..checks import Check, check as _check, level
from .model import G, KGCM_PER_NM, Joint, Mechanism, MechanismError

WARN_FRACTION = 0.8
GRAVITY = np.array([0.0, 0.0, -1.0])


@dataclass
class JointInfo:
    name: str
    type: str
    unit: str
    limit: tuple[float, float]
    home: float
    actuator: str | None = None
    required: float | None = None            # kgf.cm (revolute) or N (prismatic) at the worst pose
    load_unit: str = "kg·cm"
    stall: float | None = None               # after the transmission
    rated: float | None = None
    rated_is_estimate: bool = True
    margin_stall: float | None = None
    margin_rated: float | None = None
    worst_pose: dict[str, float] = field(default_factory=dict)
    speed_required: float | None = None
    speed_available: float | None = None
    travel: float | None = None
    moving_mass_g: float = 0.0
    status: str = "ungraded"                 # pass | warn | fail | ungraded


@dataclass
class TargetInfo:
    name: str
    at: list[float]
    tol_mm: float
    reachable: bool
    distance_mm: float
    pose: dict[str, float] = field(default_factory=dict)
    unlimited_mm: float | None = None        # the closest approach with the limits ignored, when not reachable within them


# --------------------------------------------------------------------------- masses and loads

def mass_items(m: Mechanism) -> list[tuple[str, str, float]]:
    """(name, link it rides on, grams) for every mass: links, point masses, motors (on the parent link of their joint)."""
    out = [(l.name, l.name, l.mass_g) for l in m.links.values() if l.mass_g > 0]
    tip_link = m.resolved_tip()[0]
    out += [(pm.name, pm.link or tip_link, pm.mass_g) for pm in m.masses if pm.mass_g > 0]
    out += [(f"{j.actuator.name} ({j.name})", j.parent, j.actuator.mass_g) for j in m.joints if j.actuator is not None and j.actuator.mass_g > 0]
    return out


def mass_rollup(m: Mechanism) -> dict:
    per_link: dict[str, float] = {n: 0.0 for n in m.links}
    for _name, link, g in mass_items(m):
        per_link[link] = per_link.get(link, 0.0) + g
    payload = sum(pm.mass_g for pm in m.masses if pm.kind == "payload")
    moving = {}
    for j in m.moving_joints:
        sub = m.subtree(j.child)
        moving[j.name] = round(sum(g for _n, link, g in mass_items(m) if link in sub), 2)
    return {"links_g": {k: round(v, 2) for k, v in per_link.items()}, "payload_g": round(payload, 2),
            "total_g": round(sum(per_link.values()), 2), "moving_g": moving}


def _mass_positions(m: Mechanism, q: dict[str, float]) -> list[tuple[str, float, np.ndarray]]:
    """(link, grams, world point) for every mass at pose q."""
    pts = m.points(q)
    out = [(l.name, l.mass_g, pts["cg"][l.name]) for l in m.links.values() if l.mass_g > 0]
    tip_link = m.resolved_tip()[0]
    out += [(pm.link or tip_link, pm.mass_g, pts["masses"][pm.name]) for pm in m.masses if pm.mass_g > 0]
    out += [(j.parent, j.actuator.mass_g, pts["joints"][j.name]) for j in m.joints if j.actuator is not None and j.actuator.mass_g > 0]
    return out


def joint_loads(m: Mechanism, q: dict[str, float] | None = None) -> dict[str, float]:
    """Static holding load of every moving joint at pose q: |moment of gravity about the axis| in kgf.cm (revolute) or |gravity
    component along the axis| in N (prismatic), counting every mass the joint carries."""
    q = m.clamp(q or {})
    pts = m.points(q)
    masses = _mass_positions(m, q)
    out: dict[str, float] = {}
    for j in m.moving_joints:
        o, a = pts["joints"][j.name], pts["axes"][j.name]
        down = m.subtree(j.child)
        if j.type == "revolute":
            tau = np.zeros(3)
            for link, g, p in masses:
                if link in down:
                    tau += np.cross((p - o) / 1000.0, GRAVITY * (g / 1000.0 * G))
            out[j.name] = abs(float(tau @ a)) * KGCM_PER_NM
        else:
            f = sum(float((GRAVITY * (g / 1000.0 * G)) @ a) for link, g, _p in masses if link in down)
            out[j.name] = abs(f)
    return out


# --------------------------------------------------------------------------- poses

def sample_poses(m: Mechanism, seed: int = 0) -> list[dict[str, float]]:
    """Deterministic poses: home, every joint alone at each limit, the {min, mid, max} grid (a random subset of it beyond 81
    combinations), and m.poses random poses from a fixed seed."""
    js = m.moving_joints
    home = m.home_pose()
    poses = [dict(home)]
    for j in js:
        for v in j.limit:
            p = dict(home)
            p[j.name] = v
            poses.append(p)
    combos = [[]]
    for j in js:
        lo, hi = j.limit
        combos = [c + [v] for c in combos for v in (lo, (lo + hi) / 2, hi)]
    rng = random.Random(seed)
    if len(combos) > 81:
        combos = rng.sample(combos, 81)
    poses += [{j.name: v for j, v in zip(js, c)} for c in combos]
    for _ in range(max(0, int(m.poses))):
        poses.append({j.name: rng.uniform(*j.limit) for j in js})
    seen, out = set(), []
    for p in poses:
        k = tuple(round(p[j.name], 3) for j in js)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def worst_loads(m: Mechanism, poses: list[dict[str, float]]) -> dict[str, tuple[float, dict[str, float]]]:
    worst: dict[str, tuple[float, dict[str, float]]] = {j.name: (-1.0, {}) for j in m.moving_joints}
    for p in poses:
        for name, load in joint_loads(m, p).items():
            if load > worst[name][0]:
                worst[name] = (load, p)
    return worst


def solve_ik(m: Mechanism, target: np.ndarray, poses: list[dict[str, float]], within_limits: bool = True) -> tuple[float, dict[str, float]]:
    """The closest the tip gets to `target`: (distance mm, pose). Local optimisation from the best sampled starts, bounded by the
    joint limits (or by a wide range when within_limits is False)."""
    js = m.moving_joints
    names = [j.name for j in js]
    if not js:
        return float(np.linalg.norm(m.tip_position({}) - target)), {}

    def f(x) -> float:
        return float(np.sum((m.tip_position(dict(zip(names, x))) - target) ** 2))

    ranked = sorted(poses, key=lambda p: f([p[n] for n in names]))
    starts = ranked[:5]
    best = min(((f([p[n] for n in names]), dict(p)) for p in starts), key=lambda t: t[0])
    if within_limits:
        bounds = [j.limit for j in js]
    else:
        bounds = [(j.limit[0] - (720 if j.type == "revolute" else 10 * (j.limit[1] - j.limit[0])), j.limit[1] + (720 if j.type == "revolute" else 10 * (j.limit[1] - j.limit[0]))) for j in js]
    try:
        from scipy.optimize import minimize
    except Exception:                                   # no scipy: the best sampled pose is the answer
        return math.sqrt(best[0]), best[1]
    for p in starts:
        x0 = np.array([p[n] for n in names], dtype=float)
        try:
            r = minimize(f, x0, method="L-BFGS-B", bounds=bounds, options={"maxiter": 200})
        except Exception:
            continue
        if r.fun < best[0]:
            best = (float(r.fun), {n: float(v) for n, v in zip(names, r.x)})
        if best[0] < 1e-6:
            break
    return math.sqrt(max(best[0], 0.0)), best[1]


def _fmt_pose(p: dict[str, float], m: Mechanism) -> str:
    units = {j.name: j.unit for j in m.joints}
    return ", ".join(f"{k} {v:.0f}{'°' if units.get(k) == 'deg' else ' mm'}" for k, v in p.items())


# --------------------------------------------------------------------------- the checks

def run_checks(m: Mechanism) -> tuple[list[Check], list[JointInfo], list[TargetInfo], dict, dict]:
    checks: list[Check] = []
    try:
        order = m.order()
        tip_link, _ = m.resolved_tip()
        for pm in m.masses:
            if pm.link and pm.link not in m.links:
                raise MechanismError(f"{pm.name}: no link {pm.link!r}")
    except MechanismError as exc:
        checks.append(_check("structure", "mechanism is a tree from the ground link", "fail", str(exc), "every link moved by one joint, one fixed link"))
        return checks, [], [], {}, {}
    moving = [j for j in order if j.moving]
    checks.append(_check("structure", "mechanism is a tree from the ground link", "pass",
                         f"{len(m.links)} links, {len(moving)} moving joint(s) ({', '.join(j.name for j in moving) or 'none'}), tip on {tip_link}",
                         "", [j.name for j in moving]))
    masses = mass_rollup(m)
    checks.append(_check("mass", "mass rollup", "pass",
                         ", ".join(f"{k} {v:g} g" for k, v in masses["links_g"].items() if v)
                         + (f" (the {masses['payload_g']:g} g payload counted on {m.resolved_tip()[0]})" if masses["payload_g"] else "; no payload")
                         + f"; total {masses['total_g']:g} g", "", list(m.links)))
    if not moving:
        return checks, [], [], {"poses": 0}, masses

    poses = sample_poses(m)
    worst = worst_loads(m, poses)
    infos: list[JointInfo] = []
    for j in moving:
        load, pose = worst[j.name]
        info = JointInfo(j.name, j.type, j.unit, j.limit, j.home, j.actuator.name if j.actuator else None, round(load, 4),
                         "kg·cm" if j.type == "revolute" else "N", worst_pose={k: round(v, 1) for k, v in pose.items()},
                         speed_required=m.speeds.get(j.name), moving_mass_g=masses["moving_g"][j.name])
        kind = "torque" if j.type == "revolute" else "force"
        what = f"{kind} joint {j.name}"
        if j.actuator is None:
            checks.append(_check(kind, what, "warn", f"{load:.2f} {info.load_unit} required at the worst pose ({_fmt_pose(pose, m)}); no actuator declared, so it is not graded",
                                 "actuator=Actuator(stall_kgcm=..., speed_deg_s=...)", [j.name]))
            infos.append(info)
            continue
        a = j.actuator
        info.actuator = a.name
        stall, (rated, est) = a.stall_out(j.type), a.rated_out(j.type)
        info.stall, info.rated, info.rated_is_estimate = stall, rated, est
        info.speed_available, info.travel = a.speed_out(j.type), a.travel_out(j.type)
        label = f"{a.name}" + (f" x{a.gear_ratio:g}" if a.gear_ratio != 1 else "") + (f" @ {a.efficiency:.0%}" if a.efficiency != 1 else "")
        if stall is None:
            checks.append(_check(kind, f"{what} ({label})", "fail", f"{load:.2f} {info.load_unit} required; the actuator declares no {'stall_kgcm' if j.type == 'revolute' else 'stall_N'}",
                                 "an actuator with a stall value", [j.name]))
            info.status = "fail"
            infos.append(info)
            continue
        ms = (stall / load) if load > 1e-9 else float("inf")
        mr = (rated / load) if load > 1e-9 else float("inf")
        info.margin_stall, info.margin_rated = (None if math.isinf(ms) else round(ms, 3)), (None if math.isinf(mr) else round(mr, 3))
        sev = "fail" if load > stall else "warn" if load > rated else "pass"
        info.status = sev
        if load < 0.005:
            margin = f"no gravity load on this axis (inertial load is not graded); stall {stall:.1f}, rated {rated:.1f} {info.load_unit}{' (an estimate)' if est else ''}"
        else:
            margin = f"margin {ms:.2f}x stall, {mr:.2f}x rated continuous ({rated:.1f} {info.load_unit}{', an estimate of ' + f'{a.stall_out(j.type) and 0.5:g}' + 'x stall' if est else ''})"
        checks.append(_check(kind, f"{what} ({label})", sev,
                             f"{load:.1f} {info.load_unit} required at the worst pose ({_fmt_pose(pose, m)}) vs {stall:.1f} {info.load_unit} stall, {margin}",
                             f"required <= {rated:.1f} {info.load_unit} rated, never > {stall:.1f} stall", [j.name]))
        infos.append(info)

    # speed
    for j, info in zip(moving, infos):
        req, avail = info.speed_required, info.speed_available
        unit = "°/s" if j.type == "revolute" else "mm/s"
        if req is None and avail is None:
            continue
        if req is None:
            rng = j.limit[1] - j.limit[0]
            checks.append(_check("speed", f"speed joint {j.name}", "pass", f"{avail:.0f} {unit} at the joint; {rng / avail:.2f} s across its {rng:g} {j.unit} range (no requirement declared)", "", [j.name]))
            continue
        if avail is None:
            checks.append(_check("speed", f"speed joint {j.name}", "warn", f"{req:g} {unit} required; the actuator declares no speed, so it is not graded", "speed_deg_s= / speed_mm_s= on the actuator", [j.name]))
            continue
        sev = level(req, avail, WARN_FRACTION)
        checks.append(_check("speed", f"speed joint {j.name}", sev, f"{req:g} {unit} required vs {avail:.0f} {unit} available ({info.actuator}{' through the gearing' if j.actuator and j.actuator.gear_ratio != 1 else ''}), {avail / req:.2f}x",
                             f"required <= {avail:.0f} {unit}" + (" (within 20% of it)" if sev == "warn" else ""), [j.name]))

    # joint limits
    for j, info in zip(moving, infos):
        lo, hi = j.limit
        rng = hi - lo
        if info.travel is not None and rng > info.travel + 1e-9:
            checks.append(_check("limits", f"joint limits {j.name}", "fail", f"declared range {lo:g}..{hi:g} {j.unit} ({rng:g}) exceeds the actuator's travel of {info.travel:g} {j.unit}",
                                 f"range <= {info.travel:g} {j.unit}", [j.name]))
        else:
            checks.append(_check("limits", f"joint limits {j.name}", "pass", f"{lo:g}..{hi:g} {j.unit} (range {rng:g}), home {j.home:g}" + (f", within the actuator's {info.travel:g} {j.unit} travel" if info.travel is not None else ""), "", [j.name]))

    # targets
    tinfos: list[TargetInfo] = []
    for t in m.targets:
        d, pose = solve_ik(m, t.at, poses, within_limits=True)
        ok = d <= t.tol_mm
        ti = TargetInfo(t.name, [round(float(x), 2) for x in t.at], t.tol_mm, ok, round(d, 3), {k: round(v, 2) for k, v in pose.items()})
        at = f"({t.at[0]:g}, {t.at[1]:g}, {t.at[2]:g})"
        if ok:
            checks.append(_check("reach", f"target {t.name} reachable", "pass", f"{at} reached within {d:.2f} mm at {_fmt_pose(pose, m)}", f"<= {t.tol_mm:g} mm", [t.name]))
        else:
            d2, pose2 = solve_ik(m, t.at, poses, within_limits=False)
            ti.unlimited_mm = round(d2, 3)
            why = (f"; it would be reached within {d2:.1f} mm with the limits ignored (at {_fmt_pose(pose2, m)}): widen the limits or move the target"
                   if d2 <= t.tol_mm else "; it is out of reach even with the limits ignored: lengthen the links or move the target")
            checks.append(_check("reach", f"target {t.name} reachable", "fail", f"{at}: closest approach {d:.1f} mm at {_fmt_pose(pose, m)}{why}", f"<= {t.tol_mm:g} mm", [t.name]))
        tinfos.append(ti)

    # reach envelope
    base_pt = m.points(m.home_pose())["joints"][moving[0].name]
    tips = [m.tip_position(p) for p in poses]
    radii = [float(np.linalg.norm(t - base_pt)) for t in tips]
    horiz = [float(np.linalg.norm((t - base_pt)[:2])) for t in tips]
    reach = {"max_radius_mm": round(max(radii), 1), "min_radius_mm": round(min(radii), 1), "max_horizontal_mm": round(max(horiz), 1),
             "z_min_mm": round(min(float(t[2]) for t in tips), 1), "z_max_mm": round(max(float(t[2]) for t in tips), 1), "poses": len(poses),
             "from": [round(float(x), 2) for x in base_pt]}
    text = f"tip radius up to {reach['max_radius_mm']:g} mm from joint {moving[0].name} (horizontal {reach['max_horizontal_mm']:g} mm, height {reach['z_min_mm']:g}..{reach['z_max_mm']:g} mm) over {len(poses)} sampled poses"
    if m.reach_mm is not None:
        sev = "pass" if reach["max_radius_mm"] >= m.reach_mm else "fail"
        checks.append(_check("reach", "reach envelope", sev, text, f">= {m.reach_mm:g} mm", [tip_link]))
    else:
        checks.append(_check("reach", "reach envelope", "pass", text, "", [tip_link]))
    return checks, infos, tinfos, reach, masses
