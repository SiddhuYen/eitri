"""The harness report produced after every build: summary text for the agent, structured data for the page.

Mirrors eitri/report.py: `analyze()` runs the built-in checks and the model's own verify() checks;
`summarize()` is the compact text; `to_dict()` is what goes into out/result.json.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..model import Checker, CheckResult
from ..power import amps
from .checks import HCheck, NetInfo, run_checks
from .model import Harness, endpoint_to_json


@dataclass
class HarnessReport:
    name: str
    connectors: int
    wires: int
    splices: int
    cables: int
    total_wire_mm: float
    nets: list[NetInfo] = field(default_factory=list)
    checks: list[HCheck] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    analysis_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def failed(self) -> list[HCheck]:
        return [c for c in self.checks if c.severity == "fail"]

    @property
    def warnings(self) -> list[HCheck]:
        return [c for c in self.checks if c.severity == "warn"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze(h: Harness, checker: Checker | None = None) -> HarnessReport:
    t0 = time.time()
    checks, stats = run_checks(h)
    for r in (checker.results if checker else []):
        checks.append(_from_verify(r))
    rep = HarnessReport(
        name=h.name, connectors=len(h.connectors), wires=len(h.links), splices=len(h.splices), cables=len(h.cables),
        total_wire_mm=round(sum(l.length_mm for l in h.links), 1), nets=list(stats.values()), checks=checks)
    rep.analysis_seconds = round(time.time() - t0, 3)
    return rep


def _from_verify(r: CheckResult) -> HCheck:
    return HCheck(name=r.name, ok=r.ok, measured=r.measured, expected=r.expected, severity="pass" if r.ok else "fail", category="verify")


def summarize(rep: HarnessReport) -> str:
    n_fail, n_warn = len(rep.failed), len(rep.warnings)
    n_pass = len(rep.checks) - n_fail - n_warn
    lines = [f"Harness {rep.name!r}: {rep.connectors} connectors, {rep.wires} wires, {rep.splices} splices, {len(rep.nets)} nets; "
             f"{rep.total_wire_mm / 1000:.2f} m of wire",
             f"Checks: {n_pass} pass, {n_warn} warn, {n_fail} FAIL"]
    if rep.nets:
        lines.append("Nets:")
        for n in rep.nets:
            bits = []
            if n.voltage_V is not None:
                bits.append(f"{n.voltage_V:g} V")
            if n.current_A is not None:
                bits.append(amps(n.current_A))
            if n.drop_V is not None:
                drop = f"drop {n.drop_V:.3f} V" + (f" = {n.drop_pct:.2f}% (limit {n.max_drop_pct:g}%)" if n.drop_pct is not None else "")
                bits.append(drop + (f" over {' + '.join(n.worst_path)}" if n.worst_path else ""))
            bits.append(f"{len(n.wires)} wire(s), {n.length_mm:.0f} mm")
            lines.append(f"  {n.name}: " + ", ".join(bits))
    order = {"fail": 0, "warn": 1, "pass": 2}
    for c in sorted(rep.checks, key=lambda c: order[c.severity]):
        lines.append(c.line())
    for i in rep.issues:
        lines.append(f"ISSUE: {i}")
    return "\n".join(lines)


def harness_data(h: Harness) -> dict[str, Any]:
    """The harness as plain data (out/result.json "harness"): what the editor draws and edits."""
    from .catalog import CATALOG
    return {
        "name": h.name,
        "connectors": [{"name": c.name, "type": c.type, "pins": list(c.pin_names), "labels": dict(c.pin_labels), "nc": list(c.nc),
                        "part_number": c.part_number, "gender": c.gender, "notes": c.notes, "rating_a": c.rating,
                        "pitch_mm": c.pitch, "known": c.known} for c in h.connectors.values()],
        "splices": [{"name": s.name, "kind": s.kind} for s in h.splices.values()],
        "wires": [{"id": l.id, "a": endpoint_to_json(l.a), "b": endpoint_to_json(l.b), "gauge": l.gauge.to_json(), "gauge_label": l.gauge.label,
                   "color": l.color, "length_mm": l.length_mm, "net": l.net, "current_A": h.wire_current(l), "cable": l.cable,
                   "core": l.core, "label": l.label} for l in h.links],
        "nets": [{"name": n.name, "current_A": n.current_A, "voltage_V": n.voltage_V, "max_drop_pct": n.max_drop_pct} for n in h.nets.values()],
        "cables": [{"name": c.name, "shield": c.shield, "shield_ends": [str(p) for p in c.shield_ends], "jacket_color": c.jacket_color} for c in h.cables.values()],
        "layout": {k: list(v) for k, v in h.layout.items()},
        "catalog": sorted(CATALOG),
    }


def describe_selection(data: dict, ids: list[str], checks: list[dict] | None = None) -> str:
    """What the user selected in the harness editor ("wire:W3", "conn:J2", "splice:PDB"), as text for the agent."""
    wires = {w["id"]: w for w in data.get("wires", [])}
    conns = {c["name"]: c for c in data.get("connectors", [])}
    splices = {s["name"]: s for s in data.get("splices", [])}
    ref = lambda e: f"{e['connector']}.{e['pin']}" if "connector" in e else e["splice"]
    lines = []
    for sid in ids:
        kind, _, name = str(sid).partition(":")
        if kind == "wire" and name in wires:
            w = wires[name]
            lines.append(f"- wire {name}: {ref(w['a'])} -> {ref(w['b'])}, {w['gauge_label']} {w['color']}, {w['length_mm']:g} mm"
                         + (f", net {w['net']}" if w.get("net") else ", no net") + (f", {amps(w['current_A'])}" if w.get("current_A") is not None else "")
                         + (f" (core {w['core']} of cable {w['cable']})" if w.get("cable") else ""))
        elif kind == "conn" and name in conns:
            c = conns[name]
            on = [f"{ref(w['a'])}->{ref(w['b'])} ({w['id']})" for w in wires.values() if name in (w["a"].get("connector"), w["b"].get("connector"))]
            lines.append(f"- connector {name}: {c['type']}, pins {', '.join(c['pins'])}"
                         + (f", rating {c['rating_a']:g} A/pin" if c.get("rating_a") is not None else "") + (f", NC {', '.join(c['nc'])}" if c.get("nc") else "")
                         + (f"; wires: {'; '.join(on)}" if on else "; no wires"))
        elif kind == "splice" and name in splices:
            on = [w["id"] for w in wires.values() if name in (w["a"].get("splice"), w["b"].get("splice"))]
            lines.append(f"- splice {name} ({splices[name]['kind']}): wires {', '.join(on) or 'none'}")
        else:
            lines.append(f"- {sid} (no longer exists)")
            continue
        for c in checks or []:
            if name in (c.get("refs") or []) and c.get("severity") in ("fail", "warn"):
                lines.append(f"    {c['severity'].upper()}: {c['name']}: {c['measured']}")
    return "\n".join(lines)
