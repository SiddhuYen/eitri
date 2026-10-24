"""The mechanism report produced after every build: summary text for the agent, structured data for the page (mirrors
eitri/harness/report.py)."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..checks import Check, from_verify
from ..model import Checker
from .checks import JointInfo, TargetInfo, run_checks
from .model import Mechanism


@dataclass
class MechanismReport:
    name: str
    links: int
    joints: int
    targets: int
    joint_info: list[JointInfo] = field(default_factory=list)
    target_info: list[TargetInfo] = field(default_factory=list)
    reach: dict = field(default_factory=dict)
    masses: dict = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    analysis_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.severity == "fail"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.severity == "warn"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze(m: Mechanism, checker: Checker | None = None) -> MechanismReport:
    t0 = time.time()
    checks, joints, targets, reach, masses = run_checks(m)
    for r in (checker.results if checker else []):
        checks.append(from_verify(r))
    rep = MechanismReport(name=m.name, links=len(m.links), joints=len([j for j in m.joints if j.moving]), targets=len(m.targets),
                          joint_info=joints, target_info=targets, reach=reach, masses=masses, checks=checks)
    rep.analysis_seconds = round(time.time() - t0, 3)
    return rep


def summarize(rep: MechanismReport) -> str:
    n_fail, n_warn = len(rep.failed), len(rep.warnings)
    lines = [f"Mechanism {rep.name!r}: {rep.links} links, {rep.joints} moving joint(s), {rep.targets} target(s); "
             f"total mass {rep.masses.get('total_g', 0):g} g" + (f", payload {rep.masses['payload_g']:g} g" if rep.masses.get("payload_g") else ""),
             f"Checks: {len(rep.checks) - n_fail - n_warn} pass, {n_warn} warn, {n_fail} FAIL"]
    if rep.joint_info:
        lines.append("Joints (worst-pose load vs actuator):")
        for j in rep.joint_info:
            bits = [f"{j.limit[0]:g}..{j.limit[1]:g} {j.unit}"]
            if j.required is not None:
                bits.append(f"needs {j.required:.2f} {j.load_unit}")
            if j.stall is not None:
                bits.append(f"{j.actuator}: stall {j.stall:.1f}, rated {j.rated:.1f} {j.load_unit}{' (est.)' if j.rated_is_estimate else ''}")
            if j.margin_stall is not None:
                bits.append(f"margin {j.margin_stall:.2f}x stall / {j.margin_rated:.2f}x rated")
            if j.speed_available is not None:
                bits.append(f"{j.speed_available:.0f} {'°/s' if j.unit == 'deg' else 'mm/s'}" + (f" vs {j.speed_required:g} required" if j.speed_required else ""))
            bits.append(f"moves {j.moving_mass_g:g} g")
            lines.append(f"  {j.name} ({j.type}): " + ", ".join(bits))
    if rep.target_info:
        lines.append("Targets: " + "; ".join(f"{t.name} {'reached' if t.reachable else 'MISSED by ' + f'{t.distance_mm:g} mm'}" for t in rep.target_info))
    if rep.reach:
        lines.append(f"Reach: radius up to {rep.reach.get('max_radius_mm', 0):g} mm, height {rep.reach.get('z_min_mm', 0):g}..{rep.reach.get('z_max_mm', 0):g} mm")
    order = {"fail": 0, "warn": 1, "pass": 2}
    for c in sorted(rep.checks, key=lambda c: order[c.severity]):
        lines.append(c.line())
    for i in rep.issues:
        lines.append(f"ISSUE: {i}")
    return "\n".join(lines)


def mechanism_data(m: Mechanism, rep: MechanismReport | None = None) -> dict[str, Any]:
    """The mechanism as plain data (result.json "mechanism"): what the editor draws and edits."""
    try:
        order = m.order()
        base = m.base
    except Exception:
        order, base = list(m.joints), None
    jinfo = {j.name: j for j in (rep.joint_info if rep else [])}
    pts_home = m.points(m.home_pose()) if base else None
    return {
        "name": m.name, "base": base,
        "links": [{"name": l.name, "length_mm": l.length_mm, "mass_g": l.mass_g, "cg": [round(float(x), 2) for x in l.cg], "fixed": l.fixed,
                   "direction": [round(float(x), 4) for x in l.direction], "parent_joint": next((j.name for j in m.joints if j.child == l.name), None)} for l in m.links.values()],
        "joints": [{"name": j.name, "type": j.type, "parent": j.parent, "child": j.child, "axis": [round(float(x), 4) for x in j.axis],
                    "at": [round(float(x), 2) for x in m.joint_at(j)], "limit": list(j.limit) if j.limit else None, "home": j.home, "unit": j.unit,
                    "actuator": ({"name": j.actuator.name, "stall_kgcm": j.actuator.stall_kgcm, "rated_kgcm": j.actuator.rated_kgcm, "stall_N": j.actuator.stall_N,
                                  "rated_N": j.actuator.rated_N, "speed_deg_s": j.actuator.speed_deg_s, "speed_mm_s": j.actuator.speed_mm_s,
                                  "gear_ratio": j.actuator.gear_ratio, "efficiency": j.actuator.efficiency, "travel_deg": j.actuator.travel_deg,
                                  "travel_mm": j.actuator.travel_mm, "mass_g": j.actuator.mass_g} if j.actuator else None),
                    "speed_required": m.speeds.get(j.name), "status": jinfo[j.name].status if j.name in jinfo else None,
                    "required": jinfo[j.name].required if j.name in jinfo else None, "load_unit": jinfo[j.name].load_unit if j.name in jinfo else None,
                    "stall": jinfo[j.name].stall if j.name in jinfo else None, "rated": jinfo[j.name].rated if j.name in jinfo else None,
                    "margin_stall": jinfo[j.name].margin_stall if j.name in jinfo else None,
                    "world_at": [round(float(x), 2) for x in pts_home["joints"][j.name]] if pts_home and j.name in pts_home["joints"] else None} for j in order],
        "masses": [{"name": pm.name, "link": pm.link or (m.resolved_tip()[0] if base else None), "mass_g": pm.mass_g, "kind": pm.kind,
                    "at": [round(float(x), 2) for x in pm.at] if pm.at is not None else None} for pm in m.masses],
        "targets": [{"name": t.name, "at": [round(float(x), 2) for x in t.at], "tol_mm": t.tol_mm,
                     "reachable": next((ti.reachable for ti in (rep.target_info if rep else []) if ti.name == t.name), None)} for t in m.targets],
        "tip": {"link": m.resolved_tip()[0], "at": [round(float(x), 2) for x in m.resolved_tip()[1]]} if base else None,
        "speeds": dict(m.speeds), "reach_mm": m.reach_mm, "layout": {k: list(v) for k, v in m.layout.items()},
    }


def describe_selection(data: dict, ids: list[str], checks: list[dict] | None = None) -> str:
    """What the user selected in the mechanism editor ("joint:elbow", "link:fore", "target:pick"), as text for the agent."""
    joints = {j["name"]: j for j in data.get("joints", [])}
    links = {l["name"]: l for l in data.get("links", [])}
    targets = {t["name"]: t for t in data.get("targets", [])}
    lines = []
    for sid in ids:
        kind, _, name = str(sid).partition(":")
        if kind == "joint" and name in joints:
            j = joints[name]
            a = j.get("actuator") or {}
            act = (f", actuator {a.get('name')}" + (f" stall {a.get('stall_kgcm') or a.get('stall_N')}" if (a.get('stall_kgcm') or a.get('stall_N')) else "")
                   + (f", speed {a.get('speed_deg_s') or a.get('speed_mm_s')}" if (a.get('speed_deg_s') or a.get('speed_mm_s')) else "")
                   + (f", ratio {a.get('gear_ratio')}" if a.get('gear_ratio') not in (None, 1, 1.0) else "")) if a else ", no actuator"
            lim = j.get("limit")
            lines.append(f"- joint {name}: {j['type']} {j['parent']} -> {j['child']}, axis {j['axis']}, limits {lim[0] if lim else '?'}..{lim[1] if lim else '?'} {j['unit']}{act}"
                         + (f"; needs {j['required']:.2f} {j['load_unit']} vs {j['stall']:.1f} stall" if j.get("required") is not None and j.get("stall") else ""))
        elif kind == "link" and name in links:
            l = links[name]
            lines.append(f"- link {name}: length {l['length_mm']:g} mm, mass {l['mass_g']:g} g, direction {l['direction']}" + (", fixed (ground)" if l["fixed"] else f", moved by {l.get('parent_joint')}"))
        elif kind == "target" and name in targets:
            t = targets[name]
            lines.append(f"- target {name}: at {t['at']}, tolerance {t['tol_mm']:g} mm, {'reached' if t.get('reachable') else 'NOT reached'}")
        else:
            lines.append(f"- {sid} (no longer exists)")
            continue
        for c in checks or []:
            if name in (c.get("refs") or []) and c.get("severity") in ("fail", "warn"):
                lines.append(f"    {c['severity'].upper()}: {c['name']}: {c['measured']}")
    return "\n".join(lines)
