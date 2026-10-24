"""The pneumatics report produced after every build: summary text for the agent, structured data for the page."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..checks import Check, from_verify
from ..model import Checker
from .checks import CylinderInfo, PathInfo, StrokeInfo, SupplyInfo, run_checks
from .model import Circuit


@dataclass
class CircuitReport:
    name: str
    components: int
    tubes: int
    cylinders: list[CylinderInfo] = field(default_factory=list)
    supplies: list[SupplyInfo] = field(default_factory=list)
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
        d = asdict(self)
        for cy in d["cylinders"]:                                  # PathInfo holds Tube objects: keep names only
            for s in cy["strokes"]:
                for k in ("fill", "exhaust"):
                    p = s.get(k)
                    if p:
                        s[k] = {"found": p["found"], "names": [st["name"] for st in p["steps"]], "C_eff": p["C_eff"], "b_eff": p["b_eff"], "pressure_bar": p["pressure_bar"],
                                "valve_positions": p["valve_positions"], "estimated": [st["name"] for st in p["steps"] if st["estimated"]]}
        return d


def analyze(c: Circuit, checker: Checker | None = None) -> CircuitReport:
    t0 = time.time()
    checks, cyls, sups = run_checks(c)
    for r in (checker.results if checker else []):
        checks.append(from_verify(r))
    rep = CircuitReport(name=c.name, components=len(c.components), tubes=len(c.tubes), cylinders=cyls, supplies=sups, checks=checks)
    rep.analysis_seconds = round(time.time() - t0, 3)
    return rep


def summarize(rep: CircuitReport) -> str:
    n_fail, n_warn = len(rep.failed), len(rep.warnings)
    lines = [f"Pneumatic circuit {rep.name!r}: {rep.components} components, {rep.tubes} tubes, {len(rep.cylinders)} cylinder(s)",
             f"Checks: {len(rep.checks) - n_fail - n_warn} pass, {n_warn} warn, {n_fail} FAIL"]
    for cy in rep.cylinders:
        bits = [f"Ø{cy.bore_mm:g}/{cy.rod_mm:g} x {cy.stroke_mm:g} mm {cy.acting}-acting" + (f" at {cy.pressure_bar:g} bar" if cy.pressure_bar is not None else ", NO SUPPLY")]
        for s in cy.strokes:
            if s.usable_N is not None:
                bits.append(f"{s.name} {s.usable_N:.0f} N usable vs {s.load_N:g} N" + (f" ({s.load_ratio:.2f})" if s.load_ratio is not None else ""))
            if s.speed_required is not None and s.speed_available is not None:
                bits.append(f"{s.name} speed {s.speed_available:.0f} of {s.speed_required:g} mm/s")
        if cy.air_per_cycle_l:
            bits.append(f"{cy.air_per_cycle_l:.3f} L/cycle" + (f", {cy.air_per_minute_l:.1f} L/min" if cy.cycles_per_min else ""))
        lines.append(f"  {cy.name}: " + "; ".join(bits))
    for s in rep.supplies:
        lines.append(f"  supply {s.name}: {s.pressure_bar:g} bar, {s.demand_l_min:g} L/min drawn" + (f" of {s.capacity_l_min:g}" if s.capacity_l_min else ", capacity not given"))
    order = {"fail": 0, "warn": 1, "pass": 2}
    for c in sorted(rep.checks, key=lambda c: order[c.severity]):
        lines.append(c.line())
    for i in rep.issues:
        lines.append(f"ISSUE: {i}")
    return "\n".join(lines)


def circuit_data(c: Circuit, rep: CircuitReport | None = None) -> dict[str, Any]:
    """The circuit as plain data (result.json "pneumatics"): what the editor draws and edits."""
    cinfo = {cy.name: cy for cy in (rep.cylinders if rep else [])}
    comps = []
    for comp in c.components.values():
        d: dict[str, Any] = {"name": comp.name, "kind": comp.kind, "ports": list(comp.ports), "part_number": comp.part_number, "C": comp.C, "b": comp.b, "notes": comp.notes}
        for k in ("pressure_bar", "flow_l_min", "set_bar", "frl", "lubricator", "cls", "actuation", "returns", "normally", "center", "bore_mm", "rod_mm", "stroke_mm",
                  "acting", "load_N", "load_retract_N", "speed_mm_s", "speed_retract_mm_s", "cycles_per_min", "spring_N", "friction_fraction", "meter"):
            if hasattr(comp, k):
                d[k] = getattr(comp, k)
        if comp.name in cinfo:
            cy = cinfo[comp.name]
            d["pressure_at"] = cy.pressure_bar
            d["strokes"] = [{"name": s.name, "usable_N": s.usable_N, "load_N": s.load_N, "load_ratio": s.load_ratio, "speed_required": s.speed_required,
                             "speed_available": s.speed_available, "status": s.status} for s in cy.strokes]
            d["air_per_minute_l"] = cy.air_per_minute_l
        comps.append(d)
    return {
        "name": c.name,
        "components": comps,
        "tubes": [{"name": t.name, "a": str(t.a), "b": str(t.b), "id_mm": t.id_mm, "length_m": t.length_m, "fittings": t.fittings, "C": t.C, "part_number": t.part_number} for t in c.tubes],
        "vents": [str(v) for v in sorted(c.vents, key=str)], "attached": {str(k): str(v) for k, v in c.attached.items()},
        "layout": {k: list(v) for k, v in c.layout.items()},
        "valve_classes": sorted(__import__("eitri.pneumatics.model", fromlist=["VALVE_CLASSES"]).VALVE_CLASSES),
    }


def describe_selection(data: dict, ids: list[str], checks: list[dict] | None = None) -> str:
    comps = {x["name"]: x for x in data.get("components", [])}
    tubes = {t["name"]: t for t in data.get("tubes", [])}
    lines = []
    for sid in ids:
        kind, _, name = str(sid).partition(":")
        if kind == "component" and name in comps:
            x = comps[name]
            if x["kind"] == "cylinder":
                desc = f"cylinder Ø{x['bore_mm']:g}/{x['rod_mm']:g} x {x['stroke_mm']:g} mm, {x['acting']}-acting, load {x['load_N']:g} N" + (f", speed {x['speed_mm_s']:g} mm/s" if x.get("speed_mm_s") else "") \
                    + (f", {x['cycles_per_min']:g} cycles/min" if x.get("cycles_per_min") else "") + (f"; {x['pressure_at']:g} bar at the cylinder" if x.get("pressure_at") is not None else "")
                for s in x.get("strokes") or []:
                    if s["usable_N"] is not None:
                        desc += f"; {s['name']} {s['usable_N']:.0f} N usable vs {s['load_N']:g} N"
            elif x["kind"] == "valve":
                desc = f"{x['cls']} valve, {x['actuation']} / {x['returns']} return" + (f", C {x['C']:g} b {x['b']:g}" if x.get("C") else ", no C/b (estimated)")
            elif x["kind"] in ("regulator", "frl"):
                desc = f"{x['kind']} set to {x['set_bar']:g} bar" + (f", C {x['C']:g}" if x.get("C") else ", no C/b (estimated)")
            elif x["kind"] == "supply":
                desc = f"supply {x['pressure_bar']:g} bar" + (f", {x['flow_l_min']:g} L/min" if x.get("flow_l_min") else ", capacity not given")
            elif x["kind"] == "flow_control":
                desc = f"flow control meter-{x['meter']}" + (f", C {x['C']:g}" if x.get("C") else ", no C/b (estimated)")
            else:
                desc = x["kind"]
            lines.append(f"- {name}: {desc}")
        elif kind == "tube" and name in tubes:
            t = tubes[name]
            lines.append(f"- tube {name}: {t['a']} -> {t['b']}, {t['id_mm']:g} mm bore, {t['length_m']:g} m, {t['fittings']} fittings" + (f", C {t['C']:g}" if t.get("C") else " (C estimated)"))
        else:
            lines.append(f"- {sid} (no longer exists)")
            continue
        for ch in checks or []:
            if name in (ch.get("refs") or []) and ch.get("severity") in ("fail", "warn"):
                lines.append(f"    {ch['severity'].upper()}: {ch['name']}: {ch['measured']}")
    return "\n".join(lines)
