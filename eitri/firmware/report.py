"""The firmware report produced after every build: summary text for the agent, structured data for the page."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..checks import Check, from_verify
from ..model import Checker
from .checks import PinInfo, run_checks
from .compile import CompileResult
from .model import Firmware


@dataclass
class FirmwareReport:
    name: str
    board: str
    fqbn: str
    pins: list[PinInfo] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    compile: dict = field(default_factory=dict)
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


def analyze(fw: Firmware, checker: Checker | None = None, compiled: CompileResult | None = None) -> FirmwareReport:
    t0 = time.time()
    checks, infos, comp = run_checks(fw, compiled)
    for r in (checker.results if checker else []):
        checks.append(from_verify(r))
    b = fw.board
    rep = FirmwareReport(fw.name, fw.board_key, b.fqbn if b else "", infos, [s.name for s in fw.sources], comp, checks)
    rep.analysis_seconds = round(time.time() - t0, 3)
    return rep


def summarize(rep: FirmwareReport) -> str:
    n_fail, n_warn = len(rep.failed), len(rep.warnings)
    lines = [f"Firmware {rep.name!r}: {rep.board} ({rep.fqbn or 'unknown board'}), {len(rep.pins)} pin(s), sources {', '.join(rep.sources) or 'none'}",
             f"Checks: {len(rep.checks) - n_fail - n_warn} pass, {n_warn} warn, {n_fail} FAIL"]
    if rep.pins:
        lines.append("Pinout:")
        for p in rep.pins:
            lines.append(f"  {p.symbol} = {p.canonical or p.pin}" + (f" ({p.number})" if p.number is not None else "") + f": {p.direction} {p.role}" + (f", {p.peripheral}" if p.peripheral else "")
                         + (f"; used: {', '.join(p.uses[:3])}" if p.uses else "") + ("" if p.status == "pass" else f"  [{p.status.upper()}]"))
    c = rep.compile
    if c:
        lines.append("Compile: " + (c["message"] if not c.get("available") else ("ok" if c.get("ok") else "FAILED")) + (f", flash {c['flash_pct']}%, RAM {c['ram_pct']}%" if c.get("flash_pct") is not None else ""))
    order = {"fail": 0, "warn": 1, "pass": 2}
    for ch in sorted(rep.checks, key=lambda c: order[c.severity]):
        lines.append(ch.line())
    for i in rep.issues:
        lines.append(f"ISSUE: {i}")
    return "\n".join(lines)


def firmware_data(fw: Firmware, rep: FirmwareReport | None = None) -> dict[str, Any]:
    """The project as plain data (result.json "firmware"): what the editor shows and edits."""
    b = fw.board
    info = {p.net: p for p in (rep.pins if rep else [])}
    return {
        "name": fw.name, "board": fw.board_key, "board_name": b.name if b else None, "fqbn": b.fqbn if b else None, "wifi": fw.wifi,
        "boards": sorted(__import__("eitri.firmware.boards", fromlist=["BOARDS"]).BOARDS),
        "board_pins": [{"name": p.name, "number": p.number, "pwm": p.pwm, "analog_in": p.analog_in, "digital_out": p.digital_out, "interrupt": p.interrupt,
                        "forbidden": p.forbidden, "notes": list(p.notes)} for p in (b.pins.values() if b else [])],
        "pins": [{"pin": d.pin, "canonical": info[d.net].canonical if d.net in info else None, "number": info[d.net].number if d.net in info else None,
                  "direction": d.direction, "role": d.role, "net": d.net, "symbol": d.symbol, "peripheral": d.peripheral, "pullup": d.pullup,
                  "uses": info[d.net].uses if d.net in info else [], "status": info[d.net].status if d.net in info else None} for d in fw.pins],
        "peripherals": [{"name": p.name, "kind": p.kind, "pins": dict(p.pins), "params": dict(p.params)} for p in fw.peripherals],
        "sources": [{"name": s.name, "code": s.code} for s in fw.sources] + [{"name": "eitri_pins.h", "code": fw.pins_header(), "generated": True}],
        "compile": {k: v for k, v in (rep.compile if rep else {}).items() if k != "log"}, "compile_log": (rep.compile if rep else {}).get("log", ""),
        "roles": list(__import__("eitri.firmware.model", fromlist=["ROLES"]).ROLES), "layout": {k: list(v) for k, v in fw.layout.items()},
    }


def describe_selection(data: dict, ids: list[str], checks: list[dict] | None = None) -> str:
    pins = {(p.get("canonical") or p["pin"]): p for p in data.get("pins", [])}
    by_net = {p["net"]: p for p in data.get("pins", [])}
    lines = []
    for sid in ids:
        kind, _, name = str(sid).partition(":")
        p = pins.get(name) or by_net.get(name)
        if kind == "pin" and p:
            lines.append(f"- pin {p.get('canonical') or p['pin']} ({p['symbol']}): {p['direction']} {p['role']}" + (f", {p['peripheral']}" if p["peripheral"] else "")
                         + (f"; used: {', '.join(p['uses'][:3])}" if p.get("uses") else "; not used in the sources"))
        elif kind == "peripheral" and any(q["name"] == name for q in data.get("peripherals", [])):
            q = next(q for q in data["peripherals"] if q["name"] == name)
            lines.append(f"- peripheral {name}: {q['kind']} on " + ", ".join(f"{k} {v}" for k, v in q["pins"].items())
                         + (f" ({', '.join(f'{k} {v}' for k, v in q['params'].items())})" if q.get("params") else ""))
        elif kind == "source" and any(s["name"] == name for s in data.get("sources", [])):
            src = next(s for s in data["sources"] if s["name"] == name)
            lines.append(f"- source {name} ({len(src['code'].splitlines())} lines)" + (" (generated from the pinout)" if src.get("generated") else ""))
        else:
            lines.append(f"- {sid} (no longer exists)")
            continue
        for ch in checks or []:
            if name in (ch.get("refs") or []) and ch.get("severity") in ("fail", "warn"):
                lines.append(f"    {ch['severity'].upper()}: {ch['name']}: {ch['measured']}")
    return "\n".join(lines)
