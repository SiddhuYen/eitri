"""The electronics report produced after every build: summary text for the agent, structured data for the page."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..checks import Check, from_verify, checks_not_run, not_run_line
from ..model import Checker
from .analysis import INF, Analysis
from .checks import board_draw, i2c_buses, rail_limit, rail_loads, run_checks
from .model import Circuit, PinRef, amps_text


@dataclass
class ElectronicsReport:
    name: str
    part_count: int
    net_count: int
    rails: list[dict] = field(default_factory=list)          # {net, volts, source, draw_A, limit_A, basis}
    strings: list[dict] = field(default_factory=list)        # {parts, from, to, current_A}
    checks: list[Check] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)         # the source ids the build used
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
        d["checks_not_run"] = checks_not_run(self.checks)     # checks switched off by an unset input
        return d


def rails_of(c: Circuit, an: Analysis) -> list[dict]:
    out = []
    for n, info in an.nets.items():
        if info.kind != "rail" or not info.sources:
            continue
        src = info.sources[0]
        p = c.parts[src.part]
        draw = sum(i for _, i, _ in rail_loads(c, an, n))
        lim, basis, _s = rail_limit(c, an, p, src.pin, n)
        out.append({"net": n, "volts": info.volts, "source": str(src), "draw_A": round(draw, 6), "limit_A": lim, "basis": basis})
    return out


def used_sources(c: Circuit) -> list[str]:
    ids = {"nxp-um10204", "iec-60063", "te-coil-suppression"}
    for p in c.parts.values():
        ids |= {s for s in p.srcs.values() if s and s != "model"}
        for ps in p.pins.values():
            if ps.src and ps.src != "model":
                ids.add(ps.src)
            if ps.logic and ps.logic.src:
                ids.add(ps.logic.src)
    from .sources import SOURCES
    return sorted(i for i in ids if i in SOURCES)


def analyze(c: Circuit, checker: Checker | None = None) -> ElectronicsReport:
    t0 = time.time()
    checks, an = run_checks(c)
    for r in (checker.results if checker else []):
        checks.append(from_verify(r))
    rep = ElectronicsReport(c.name, len(c.parts), len(c.nets), rails_of(c, an),
                            [{"parts": s.parts, "from": s.flow[0], "to": s.flow[1], "current_A": None if s.current == INF else round(s.current, 6),
                              "typical_A": None if s.typical is None or s.typical == INF else round(s.typical, 6)}
                             for s in an.strings if s.current], checks, sources=used_sources(c))
    rep.analysis_seconds = round(time.time() - t0, 3)
    c._analysis = an
    return rep


def summarize(rep: ElectronicsReport) -> str:
    n_fail, n_warn = len(rep.failed), len(rep.warnings)
    lines = [f"Circuit {rep.name!r}: {rep.part_count} parts, {rep.net_count} nets, {len(rep.rails)} rail(s)",
             f"Checks: {len(rep.checks) - n_fail - n_warn} pass, {n_warn} warn, {n_fail} FAIL"]
    _nr = not_run_line(rep.checks)
    if _nr:
        lines.append(_nr)
    if rep.rails:
        lines.append("Rails:")
        for r in rep.rails:
            lim = f" of {amps_text(r['limit_A'])} ({r['basis']})" if r["limit_A"] else f" ({r['basis']})"
            lines.append(f"  {r['net']}: {r['volts']:g} V from {r['source']}, {amps_text(r['draw_A'])} drawn{lim}")
    if rep.strings:
        lines.append("Currents:")
        for s in rep.strings[:20]:
            typ = s.get("typical_A")
            shared = typ is not None and s["current_A"] is not None and abs(typ - s["current_A"]) > 1e-9
            lines.append(f"  {s['from']} -> {' -> '.join(s['parts'])} -> {s['to']}: " + ("unlimited" if s["current_A"] is None else amps_text(s["current_A"]))
                         + (f" worst case ({amps_text(typ)} at typical Vf)" if shared else ""))
    order = {"fail": 0, "warn": 1, "pass": 2}
    for ch in sorted(rep.checks, key=lambda c: order[c.severity]):
        lines.append(ch.line())
    for i in rep.issues:
        lines.append(f"ISSUE: {i}")
    return "\n".join(lines)


def circuit_data(c: Circuit, rep: ElectronicsReport | None = None) -> dict[str, Any]:
    """The circuit as plain data (result.json "electronics"): what the editor shows and edits."""
    from .catalog import BOARDS, LEDS, MODULES, PARTS, SERVOS
    an = getattr(c, "_analysis", None)
    cur = {}
    if an is not None:
        for p in c.parts.values():
            i = an.part_current(p.name)
            if i:
                cur[p.name] = None if i == INF else round(i, 6)
    net_kind = {n: (an.nets[n].kind, an.nets[n].volts) for n in c.nets} if an is not None else {}
    return {
        "name": c.name, "ambient_C": c.ambient_C,
        "parts": [{"name": p.name, "kind": p.kind, "catalog": p.catalog, "title": p.title, "value": p.value_text, "part_number": p.part_number,
                   "spec": {k: (list(v) if isinstance(v, tuple) else v) for k, v in p.spec.items() if isinstance(v, (int, float, str, bool, tuple, type(None)))},
                   "pins": [{"name": n, "kind": ps.kind, "net": c._net_of.get(PinRef(p.name, n)), "roles": list(ps.roles), "required": ps.required,
                             "mode": p.mode(n) if p.kind == "board" and ps.kind == "io" else None} for n, ps in p.pins.items()],
                   "current_A": cur.get(p.name)} for p in c.parts.values()],
        "nets": [{"name": n.name, "pins": [str(r) for r in n.pins], "auto": n.auto, "kind": net_kind.get(n.name, ("signal", None))[0],
                  "volts": net_kind.get(n.name, (None, None))[1]} for n in c.nets.values()],
        "rails": rep.rails if rep else [],
        "buses": [{"sda": sda, "scl": scl, "devices": devs, "speed_hz": (c.buses[sda].speed_hz if sda in c.buses else 100_000),
                   "internal_pullups": (c.buses[sda].internal_pullups if sda in c.buses else True),
                   "avr": any(c.parts[d].spec.get("wire_pullups") for d in devs)} for sda, scl, devs in i2c_buses(c)],
        "layout": {k: list(v) for k, v in c.layout.items()},
        "catalog": {"boards": sorted(BOARDS), "modules": sorted(MODULES), "servos": sorted(SERVOS), "leds": sorted(LEDS),
                    "parts": {k: v["kind"] for k, v in PARTS.items()}},
    }


def describe_selection(data: dict, ids: list[str], checks: list[dict] | None = None) -> str:
    """What the user selected in the editor ("part:R1", "net:SDA"), as text for the agent."""
    parts = {p["name"]: p for p in data.get("parts", [])}
    nets = {n["name"]: n for n in data.get("nets", [])}
    lines = []
    for sid in ids:
        kind, _, name = str(sid).partition(":")
        if kind == "part" and name in parts:
            p = parts[name]
            on = [f"{q['name']}={q['net']}" for q in p["pins"] if q["net"]]
            lines.append(f"- part {name}: {p['kind']} {p['value']}" + (f" ({p['title']})" if p.get("title") and p["title"] != p["kind"] else "")
                         + (f", {amps_text(p['current_A'])} through it" if p.get("current_A") else "") + "; pins " + (", ".join(on) or "none connected"))
        elif kind == "net" and name in nets:
            n = nets[name]
            lines.append(f"- net {name} ({n['kind']}" + (f", {n['volts']:g} V" if n.get("volts") is not None else "") + f"): {', '.join(n['pins'])}")
        else:
            lines.append(f"- {sid} (no longer exists)")
            continue
        for ch in checks or []:
            if name in (ch.get("refs") or []) and ch.get("severity") in ("fail", "warn"):
                lines.append(f"    {ch['severity'].upper()}: {ch['name']}: {ch['measured']}")
    return "\n".join(lines)
