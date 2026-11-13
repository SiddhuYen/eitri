"""Electronics <-> the other domains of a combined project: the checks an electronics file's build adds (the suite extension, kind
"electronics"). The circuit is built; its siblings (harness, firmware, mechanism, CAD) are loaded from the same folder. Every number a
check compares against comes from a source: the electronics catalog's (eitri.electronics.sources), the harness catalog's (ampacity:
PowerStream's chassis-wiring table; voltage drop: NEC Chapter 9 Table 8 copper at 75 C), or eitri_suite.elec_sources (outlines, mounting
holes, screws). A check whose inputs are missing is reported as "not run: <why>" (a warning), never as a pass.

The electronics file says how it ties to the others in a module-level COUPLING (every key optional):

    COUPLING = dict(
        harness="wiring.py", firmware="fw.py", mechanism="mech.py", cad="model.py",     # default: the project's only file of that kind
        connectors={"UNO": "UNO", "PSU": "PSU"},          # harness connector -> circuit part (default: the part of the same name)
        actuators={"yaw": "SERVO_YAW"},                    # mechanism joint -> the circuit servo / motor / solenoid that drives it
                                                           # (default: through the firmware's COUPLING joints and its servo pins)
        mounts={"UNO": dict(anchor="UNO", stand_in="controller", carrier=["standoffs"], screw="M3", clearance_mm=2.0)},
    )

THE CHECKS (category "integration")
  pins: harness   every harness connector pin of a mapped connector is a pin of its circuit part; the pins one harness net joins (through
                  its splices) are on one circuit net; the harness pins one circuit net holds are joined by the harness.
  pins: firmware  the firmware's board is the circuit's board; every firmware pin is wired in the circuit, in a compatible mode (an output
                  is not an input), with the same internal pull-up; a firmware servo pin reaches a servo's SIG (or a module input); a
                  servo the firmware says has its own supply is not powered from the board, and the other way round; every GPIO the
                  circuit wires is declared in the firmware.
  harness power   on every harness net that carries a circuit rail or its return: each wire's current, from the circuit's loads
                  downstream of it (a servo at its stall current, a module at its datasheet draw), against the wire's ampacity and the
                  connector pins' rating; the voltage left at each load after the harness's supply and return drops, against the load's
                  minimum supply voltage (a regulator's: its output plus its dropout); the supply against everything it feeds through the
                  harness, and against the power supply the BOM buys.
  mounting        every board and breakout with a published outline: its mounting holes against holes in the CAD part that carries it
                  (a screw of the mount's size passes both), its outline against every other CAD body (no overlap), and the clearance
                  around it against the mount's clearance_mm.
  actuators       every actuated mechanism joint has its servo / motor in the circuit (the same model); every actuator's stall
                  current, all stalled at once, against what carries it (a MOSFET's ID, a BJT's IC, a relay's contacts, a pass-through
                  module, the rail's source).
  dissipation     a regulator or switching driver carrying actuator current: its dissipation at the actuators' stall current, against
                  the electronics domain's junction limits (theta JA, Tj max: the same figures and formulas as its own checks).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from eitri.harness.checks import HCheck

from . import elec_sources as es

CAT = "integration"
WARN_FRACTION = 0.8                  # within 20 % of a limit: a warning (eitri.electronics.checks and eitri.harness.checks grade the same)
SIBLING_KEYS = {"harness": "harness", "firmware": "firmware", "mechanism": "mechanism", "cad": "cad"}
ACTUATOR_KINDS = ("servo", "motor", "solenoid")


def _check(name: str, severity: str, measured: str, expected: str = "", refs=()) -> HCheck:
    return HCheck(name=name, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category=CAT,
                  refs=list(dict.fromkeys(str(r) for r in refs if r)))


def not_run(name: str, why: str, expected: str = "", refs=()) -> HCheck:
    """A check whose inputs are missing: status not_run (core eitri.checks), a warning whose text starts "not run:" for older readers."""
    c = _check(name, "warn", f"not run: {why}", expected, refs)
    c.not_run = [why[:120]]
    return c


def _lvl(value: float, limit: float) -> str:
    return "fail" if value > limit + 1e-12 else "warn" if value >= WARN_FRACTION * limit else "pass"


def _a(x: float) -> str:
    from eitri.electronics.model import amps_text
    return amps_text(x)


def _ecite(sid: str) -> str:
    from eitri.electronics.sources import cite
    return cite(sid)


def coupling_of(module) -> dict:
    c = getattr(module, "COUPLING", None)
    return c if isinstance(c, dict) else {}


def declared_counts(module) -> dict[str, dict[str, int]]:
    """What an electronics file's COUPLING declares, for the project guard."""
    c = coupling_of(module)
    if not c:
        return {}
    return {"electronics": {"connectors": len(c.get("connectors") or {}), "actuators": len(c.get("actuators") or {}),
                            "mounts": len(c.get("mounts") or {})}}


def is_electronics_module(module) -> bool:
    try:
        from eitri.electronics.model import Circuit
    except Exception:                                       # noqa: BLE001
        return False
    return getattr(module, "Circuit", None) is Circuit


# ---------------------------------------------------------------------------- the siblings

class Siblings:
    """The other model files of the project, each built once on first use: get(kind) -> (built, module, path, why-not)."""

    def __init__(self, folder: Path, coupling: dict):
        from .projectfiles import files_by_kind
        self.folder, self.coupling = Path(folder), coupling
        self.by_kind = files_by_kind(self.folder)
        self._cache: dict[str, tuple] = {}

    def path(self, kind: str) -> tuple[Path | None, str | None]:
        name = self.coupling.get(SIBLING_KEYS[kind])
        files = self.by_kind.get(kind, [])
        if name:
            p = self.folder / str(name)
            return (p, None) if p.is_file() else (None, f"COUPLING {SIBLING_KEYS[kind]}={name!r} is not a file of the project")
        if len(files) == 1:
            return files[0], None
        if not files:
            return None, f"the project has no {kind} file"
        return None, f"the project has {len(files)} {kind} files: name one in COUPLING {SIBLING_KEYS[kind]}="

    def get(self, kind: str):
        if kind in self._cache:
            return self._cache[kind]
        path, why = self.path(kind)
        out = (None, None, path, why)
        if path is not None:
            try:
                from eitri.model import load_model
                lm = load_model(path)
                built = lm.build()
                out = (built, lm.module, path, None)
                if not _is_kind(built, kind):
                    out = (None, lm.module, path, f"{path.name} does not build a {kind} model")
            except Exception as exc:                        # noqa: BLE001 - its own build reports its error
                out = (None, None, path, f"{path.name} does not build ({type(exc).__name__}: {str(exc)[:160]}); its own build says why")
        self._cache[kind] = out
        return out


def _is_kind(built, kind: str) -> bool:
    if kind == "harness":
        from eitri.harness.model import Harness
        return isinstance(built, Harness)
    if kind == "firmware":
        from eitri.firmware.model import Firmware
        return isinstance(built, Firmware)
    if kind == "mechanism":
        from eitri.mechanism.model import Mechanism
        return isinstance(built, Mechanism)
    return built is not None


# ---------------------------------------------------------------------------- harness <-> circuit: the pin map

def _cpin(c, part: str, pin: str):
    from eitri.electronics.model import CircuitError, PinRef
    try:
        r = c.parts[part].pin(pin)
    except (CircuitError, KeyError):
        return None
    return PinRef(r.part, r.pin)


class DeviceLink:
    """A protective device of the harness (a fuse, PTC or breaker: core eitri.harness Fuse) as a conductor between its LINE and LOAD
    terminals, so the harness's connected groups and current paths run through it (in normal operation it conducts)."""
    kind = "device"
    gauge = None
    length_mm = 0.0

    def __init__(self, fuse, a, b):
        self.device, self.a, self.b, self.id = fuse, a, b, fuse.name


def device_links(h) -> list:
    from eitri.harness.model import PinRef as HPin
    out = []
    for name, f in (getattr(h, "fuses", None) or {}).items():
        pins = list(getattr(f, "pin_names", None) or ["LINE", "LOAD"])
        if "LINE" in pins and "LOAD" in pins:
            out.append(DeviceLink(f, HPin(name, "LINE"), HPin(name, "LOAD")))
    return out


def _groups_of(links: list) -> dict:
    adj: dict[Any, set] = {}
    for l in links:
        adj.setdefault(l.a, set()).add(l.b)
        adj.setdefault(l.b, set()).add(l.a)
    comp, k = {}, 0
    for n in adj:
        if n in comp:
            continue
        stack, comp[n] = [n], k
        while stack:
            for m in adj[stack.pop()]:
                if m not in comp:
                    comp[m] = k
                    stack.append(m)
        k += 1
    return comp


class PinMap:
    """The harness connectors mapped to circuit parts, each harness pin to its circuit pin, and the harness's connected groups (through
    its protective devices: a fuse's LINE and LOAD are one conductor here)."""

    def __init__(self, c, h, coupling: dict):
        from eitri.harness.model import PinRef as HPin
        self.c, self.h = c, h
        explicit = {str(k): str(v) for k, v in (coupling.get("connectors") or {}).items()}
        self.problems: list[str] = []
        self.parts: dict[str, str] = {}
        for k, v in explicit.items():
            if k not in h.connectors:
                self.problems.append(f"COUPLING connectors names {k!r}, which is not a harness connector")
            elif v not in c.parts:
                self.problems.append(f"COUPLING connectors maps {k} to {v!r}, which is not a circuit part")
            else:
                self.parts[k] = v
        self.unmapped = []
        self.devices = sorted((getattr(h, "fuses", None) or {}).keys())
        for name in h.connectors:
            if name in self.parts or name in explicit or name in self.devices:
                continue
            if name in c.parts:
                self.parts[name] = name
            else:
                self.unmapped.append(name)
        self.pin: dict[Any, Any] = {}                       # harness PinRef -> circuit PinRef
        self.missing: list[str] = []                        # harness pins of mapped connectors that the circuit part does not have
        for cname, part in self.parts.items():
            for pn in h.connectors[cname].pin_names:
                hp = HPin(cname, pn)
                cp = _cpin(c, part, pn)
                if cp is None:
                    self.missing.append(f"{cname}.{pn}")
                else:
                    self.pin[hp] = cp
        self.links = list(h.links) + device_links(h)          # wires and the protective devices between them
        self.comp = _groups_of(self.links)                  # endpoint -> group id (wired endpoints only)
        self.groups: dict[int, list] = {}
        for e, k in self.comp.items():
            self.groups.setdefault(k, []).append(e)

    def net(self, cp) -> str | None:
        return self.c._net_of.get(cp)

    def group_net(self, k: int) -> tuple[str | None, set]:
        """(the circuit net a harness group carries, every circuit net its mapped pins are on: more than one is a miswire)."""
        nets = {self.net(self.pin[e]) for e in self.groups[k] if e in self.pin}
        real = {n for n in nets if n}
        return (next(iter(real)) if len(real) == 1 and None not in nets else None), nets


def harness_pin_checks(pm: PinMap) -> list[HCheck]:
    out: list[HCheck] = []
    name = "pins: harness connectors are circuit parts"
    bad = pm.problems + [f"{p} is not a pin of circuit part {pm.parts[p.split('.')[0]]}" for p in pm.missing]
    if not pm.parts:
        out.append(not_run(name, "no harness connector is named as a circuit part (or mapped in COUPLING connectors)",
                           "COUPLING connectors={'<connector>': '<part>'}", list(pm.h.connectors)))
        return out
    if bad:
        out.append(_check(name, "fail", "; ".join(bad[:6]), "every pin of a mapped connector is a pin of its part", list(pm.parts)))
    else:
        txt = f"{len(pm.parts)} connector(s) mapped: " + ", ".join(f"{k} -> {v}" for k, v in pm.parts.items())
        if pm.unmapped:
            txt += f"; not a circuit part (their pins are not compared): {', '.join(pm.unmapped)}"
        out.append(_check(name, "warn" if pm.unmapped else "pass", txt, "every connector a circuit part", list(pm.parts)))
    # a harness group (wires and splices) whose mapped pins are on more than one circuit net: a miswire or a short
    for k, ends in pm.groups.items():
        _net, nets = pm.group_net(k)
        mapped = [e for e in ends if e in pm.pin]
        if len(mapped) < 2 or (len({n for n in nets}) == 1 and None not in nets):
            continue
        detail = ", ".join(f"{e} on {pm.net(pm.pin[e]) or 'no net'}" for e in mapped[:8])
        out.append(_check(f"pins: harness joins only one circuit net ({', '.join(str(e) for e in mapped[:3])})", "fail",
                          f"the harness wires these together but the circuit does not: {detail}. Fix the harness wire, or the circuit",
                          "one circuit net per harness net", [str(e) for e in mapped]))
    # a circuit net whose harness pins the harness does not join
    by_net: dict[str, list] = {}
    for hp, cp in pm.pin.items():
        n = pm.net(cp)
        if n:
            by_net.setdefault(n, []).append(hp)
    for n, hps in sorted(by_net.items()):
        if len(hps) < 2:
            continue
        groups: dict[Any, list] = {}
        for hp in hps:
            groups.setdefault(pm.comp.get(hp, ("unwired", str(hp))), []).append(hp)
        parts = {pm.pin[hp].part for hp in hps}
        label = f"pins: circuit net {n} is carried by the harness"
        if len(groups) == 1 or len(parts) == 1:
            out.append(_check(label, "pass", f"{', '.join(map(str, hps))} are joined in the harness, as the circuit joins them", "", [n] + [str(h) for h in hps]))
            continue
        apart = "; ".join("+".join(map(str, g)) + (" (unwired)" if isinstance(k, tuple) else "") for k, g in groups.items())
        out.append(_check(label, "fail", f"the circuit joins {', '.join(map(str, hps))} on net {n}, the harness does not: {apart}. "
                          "Fix: a wire (or splice) for it in the harness", "joined in the harness", [n] + [str(h) for h in hps]))
    return out


# ---------------------------------------------------------------------------- firmware <-> circuit

_OUT_ROLES = {"digital_out", "pwm", "dac", "servo", "serial_tx"}
_IN_ROLES = {"digital_in", "analog_in", "interrupt", "serial_rx"}


def _boards(c) -> list:
    return [p for p in c.parts.values() if p.kind == "board"]


def firmware_pin_checks(c, fw, coupling: dict) -> list[HCheck]:
    from eitri.electronics.model import PinRef
    out: list[HCheck] = []
    boards = _boards(c)
    fwkey = str(getattr(fw, "board_key", "") or "").lower()
    if not boards:
        return [not_run("pins: firmware board is the circuit's board", "the circuit has no board (c.board(...))")]
    named = coupling.get("board")
    b = next((p for p in boards if p.name == named), None) if named else None
    b = b or next((p for p in boards if p.catalog == fwkey or fwkey.startswith(p.catalog or "?")), None) or boards[0]
    same = bool(b.catalog) and (fwkey == b.catalog or fwkey.startswith(b.catalog))
    out.append(_check("pins: firmware board is the circuit's board", "pass" if same else "fail",
                      f"firmware board {fwkey or '?'}, circuit board {b.name} ({b.catalog})", "the same board", [b.name]))
    board = fw.board
    used: set[str] = set()
    per_power = {per.name: (per.params or {}).get("power") for per in getattr(fw, "peripherals", []) if per.kind == "servo"}
    for p in fw.pins:
        name = f"pins: firmware pin {p.net} matches the circuit"
        cap = board.resolve(p.pin) if board else None
        pin_name = cap.name if cap else p.pin
        cp = _cpin(c, b.name, pin_name)
        if cp is None:
            out.append(_check(name, "fail", f"{p.net} is on {p.pin}, which {b.name} ({b.catalog}) does not have", "a pin of the board", [b.name]))
            continue
        used.add(cp.pin)
        net = c._net_of.get(cp)
        if not net or len(c.nets[net].pins) < 2:
            out.append(_check(name, "fail", f"the firmware uses {cp.pin} for {p.net} ({p.role}) but nothing is wired to {b.name}.{cp.pin} in the circuit",
                              "the pin wired in the circuit", [b.name]))
            continue
        mode = b.mode(cp.pin)
        problems = []
        if p.role in _OUT_ROLES and mode == "in":
            problems.append(f"the firmware drives {cp.pin} ({p.role}) but the circuit declares it an input (c.gpio(..., 'in'))")
        if p.role in _IN_ROLES and mode == "out":
            problems.append(f"the firmware reads {cp.pin} ({p.role}) but the circuit declares it an output")
        if p.direction == "in" and bool(p.pullup) != (cp.pin in b.pullups):
            problems.append(f"the firmware {'turns on' if p.pullup else 'leaves off'} {cp.pin}'s internal pull-up; the circuit "
                            f"{'has it on' if cp.pin in b.pullups else 'does not'} (c.gpio(..., 'in', pullup={bool(p.pullup)}))")
        members = [r for r in c.nets[net].pins if r.part != b.name]
        if p.role == "servo":
            sig = [r for r in members if c.parts[r.part].kind == "servo" and r.pin == "SIG"]
            mod_in = [r for r in members if c.parts[r.part].kind == "module" and c.pin_spec(r).kind in ("in", "io")]
            if not sig and not mod_in:
                problems.append(f"{cp.pin} is the firmware's servo {p.net} but no servo SIG (or module input) is on net {net} in the circuit")
            power = per_power.get(p.peripheral or p.net)
            for r in sig:
                vnet = c._net_of.get(PinRef(r.part, "V+"))
                from_board = bool(vnet) and any(c.parts[s.part].kind == "board" for s in c.nets[vnet].pins
                                                if c.pin_spec(s).kind == "power_out")
                if power == "external" and from_board:
                    problems.append(f"the firmware says servo {p.net} has its own supply; the circuit powers {r.part} from the board ({vnet})")
                elif power in ("5V", "3V3") and vnet and not from_board:
                    problems.append(f"the firmware says servo {p.net} runs from the board's {power}; the circuit powers {r.part} from {vnet}")
        if problems:
            out.append(_check(name, "fail", "; ".join(problems), "the same pin, role and pull-up on both sides", [b.name, net] + [m.part for m in members]))
        else:
            out.append(_check(name, "pass", f"{p.net}: firmware {p.pin} ({p.role}) <-> circuit {b.name}.{cp.pin} on net {net} "
                              f"({', '.join(sorted({m.part for m in members}))[:120]})", "", [b.name, net]))
    wired = []
    for pin, ps in b.pins.items():
        if ps.kind != "io" or pin in used:
            continue
        n = c._net_of.get(PinRef(b.name, pin))
        if n and len(c.nets[n].pins) >= 2:
            roles = set(ps.roles)
            if roles & {"i2c_sda", "i2c_scl"} and c.buses:              # the Wire library's pins: the firmware uses them through Wire
                continue
            wired.append(f"{pin} (net {n})")
    out.append(_check("pins: every circuit GPIO is in the firmware", "warn" if wired else "pass",
                      (f"the circuit wires {', '.join(wired)} but the firmware declares no pin there" if wired else
                       f"every GPIO the circuit wires on {b.name} is a firmware pin"), "a firmware pin for each wired GPIO", [b.name]))
    return out


# ---------------------------------------------------------------------------- the rails through the harness

def _loads_on(c, an, net: str) -> list[tuple[str, float, list[str]]]:
    from eitri.electronics.checks import rail_loads
    return rail_loads(c, an, net)


def _v_min(c, cp) -> tuple[float | None, str]:
    """The lowest supply voltage the part at a circuit pin accepts, and its source text."""
    part, ps = c.parts[cp.part], c.pin_spec(cp)
    if part.kind == "regulator" and cp.pin == "IN":
        s = part.spec
        return float(s["vout"]) + float(s["dropout"]), f"{part.catalog}: {s['vout']:g} V out + {s['dropout']:g} V dropout [source: {_ecite(part.srcs.get('dropout', ''))}]"
    if ps.v_range:
        return float(ps.v_range[0]), f"{part.catalog or part.kind} {ps.v_range[0]:g}-{ps.v_range[1]:g} V [source: {_ecite(ps.src)}]"
    return None, ""


def _side(adj: dict, start, banned_link) -> set:
    seen, stack = {start}, [start]
    while stack:
        n = stack.pop()
        for l, m in adj.get(n, []):
            if l is banned_link or m in seen:
                continue
            seen.add(m)
            stack.append(m)
    return seen


def wire_currents(links: list, sources: set, draw: dict) -> dict[str, float]:
    """The current in each wire of one connected harness net: what the endpoints on the far side from the source draw. With a source
    on both sides (two feeds, or a loop) the larger side is taken (the worst case)."""
    adj: dict[Any, list] = {}
    for l in links:
        adj.setdefault(l.a, []).append((l, l.b))
        adj.setdefault(l.b, []).append((l, l.a))
    nodes = set(adj)
    out = {}
    for l in links:
        A = _side(adj, l.a, l)
        if l.b in A:                                        # a loop: this wire may carry everything
            out[l.id] = sum(draw.get(n, 0.0) for n in nodes)
            continue
        B = nodes - A
        sa, sb = bool(A & sources), bool(B & sources)
        da, db = sum(draw.get(n, 0.0) for n in A), sum(draw.get(n, 0.0) for n in B)
        out[l.id] = db if sa and not sb else da if sb and not sa else max(da, db)
    return out


def _path(links: list, a, b) -> list | None:
    """The wires from endpoint a to endpoint b in one harness net (breadth first), or None."""
    adj: dict[Any, list] = {}
    for l in links:
        adj.setdefault(l.a, []).append((l, l.b))
        adj.setdefault(l.b, []).append((l, l.a))
    prev = {a: None}
    queue = [a]
    while queue:
        n = queue.pop(0)
        if n == b:
            break
        for l, m in adj.get(n, []):
            if m not in prev:
                prev[m] = (n, l)
                queue.append(m)
    if b not in prev:
        return None
    out, n = [], b
    while prev[n] is not None:
        n, l = prev[n]
        out.append(l)
    return out[::-1]


def _drop(l, i: float) -> float:
    """One conductor's voltage drop at current i: a wire's K I L / CM, a protective device's published drop (NaN when it has none)."""
    if getattr(l, "kind", "") == "device":
        from .elec_sources import fuse_drop_V
        v, _why = fuse_drop_V(getattr(l.device, "type", ""), float(getattr(l.device, "rating_A", 0) or 0), i)
        return math.nan if v is None else v
    from eitri.harness.catalog import voltage_drop
    return voltage_drop(i, l.length_mm, l.gauge, round_trip=False)


def _is_device(l) -> bool:
    return getattr(l, "kind", "") == "device"


def _source_rating(c, an, part, pin: str, net: str) -> tuple[float | None, str]:
    from eitri.electronics.checks import rail_limit
    lim, basis, src = rail_limit(c, an, part, pin, net)
    return lim, basis + (f" [source: {src}]" if src else "")


def _bom_power(cad_module) -> list:
    """The power supplies the CAD file's PURCHASED / HARDWARE buys (catalog parts of category power)."""
    from .catalog import lookup
    out = []
    if cad_module is None:
        return out
    ids = [str(x[0] if isinstance(x, (tuple, list)) else x) for x in (getattr(cad_module, "PURCHASED", None) or [])]
    ids += [str(v) for v in (getattr(cad_module, "HARDWARE", None) or {}).values()]
    for i in ids:
        p = lookup(i)
        if p is not None and p.category == "power" and p not in out:
            out.append(p)
    return out


def harness_power_checks(c, an, pm: PinMap, cad_module=None, stall: dict | None = None) -> tuple[list[HCheck], dict]:
    """Ampacity, connector pins, voltage at the loads and supply capacity on every harness net carrying a circuit rail (and its
    return). stall: {circuit part: amps} to use instead of the circuit's own figure where higher (the actuator check's)."""
    from eitri.electronics.model import PinRef
    from eitri.harness.model import PinRef as HPin
    out: list[HCheck] = []
    data: dict = {"rails": []}
    stall = stall or {}
    links_of: dict[int, list] = {}
    for l in pm.links:
        k = pm.comp.get(l.a)
        if k is not None:
            links_of.setdefault(k, []).append(l)
    rails = []
    for k, ends in pm.groups.items():
        net, _nets = pm.group_net(k)
        if net and an.nets[net].kind == "rail":
            rails.append((k, net))
    if not rails:
        out.append(not_run("harness power: rail currents in the harness", "no harness net carries a circuit rail (a supply, a board's 5V/3V3 or a "
                           "regulator output wired through mapped connectors)", "a rail through the harness", list(pm.parts)))
        return out, data
    part_draw: dict[str, float] = {}
    sinks_of_part: dict[str, str] = {}
    drop_at: dict[tuple, float] = {}                 # (rail group, endpoint) -> supply-side drop
    blocked: dict[tuple, str] = {}                   # (rail group, endpoint) -> why its drop is unknown (a device with no published drop)
    rail_info = []
    for k, net in rails:
        info = an.nets[net]
        ends = pm.groups[k]
        mapped = {e: pm.pin[e] for e in ends if e in pm.pin}
        src_pins = {PinRef(s.part, s.pin) for s in info.sources}
        sources = {e for e, cp in mapped.items() if cp in src_pins}
        items = _loads_on(c, an, net)
        draw: dict[Any, float] = {}
        attributed = set()
        notes = []
        for idx, (what, amps, refs) in enumerate(items):
            hit = next((e for r in refs for e, cp in mapped.items() if cp.part == r and e not in sources), None)
            if hit is None:
                continue
            attributed.add(idx)
            a = max(amps, stall.get(mapped[hit].part, 0.0))
            if a > amps:
                notes.append(f"{mapped[hit].part} at {_a(a)} stall (actuator check) instead of {_a(amps)}")
            draw[hit] = draw.get(hit, 0.0) + a
        rest = sum(amps for idx, (_w, amps, _r) in enumerate(items) if idx not in attributed)
        passthrough = [e for e, cp in mapped.items() if e not in sources and e not in draw]
        if rest > 1e-9:
            for e in passthrough:
                draw[e] = draw.get(e, 0.0) + rest
            notes.append(f"{_a(rest)} of {net}'s loads are not on a harness connector: counted at "
                         + (", ".join(map(str, passthrough)) if passthrough else "the source"))
        if not sources:
            out.append(not_run(f"harness power: {net} through the harness", f"the harness net carrying {net} has no mapped source pin "
                               f"(the supply or board pin that feeds {net})", "the source's connector in the harness", [net]))
            continue
        links = links_of.get(k, [])
        cur = wire_currents(links, sources, draw)
        if rest > 1e-9 and not passthrough:
            for l in links:
                if l.a in sources or l.b in sources:
                    cur[l.id] = cur.get(l.id, 0.0) + rest
        for e, cp in mapped.items():
            if e not in sources:
                part_draw[cp.part] = part_draw.get(cp.part, 0.0) + draw.get(e, 0.0)
        for s in sources:
            sp = c.parts[mapped[s].part]
            gpin = next((n for n, ps in sp.pins.items() if ps.kind == "gnd"), None)
            if gpin:
                sinks_of_part[sp.name] = gpin
        rail_info.append((k, net, info, mapped, sources, draw, cur, links, notes))
        # ampacity and connector pins
        rows, worst, worst_sev = [], None, "pass"
        conn_rows, conn_sev, unrated = [], "pass", set()
        for l in links:
            if _is_device(l):                                # a fuse is graded by the core's protection checks, not as a wire
                continue
            i = cur.get(l.id, 0.0)
            amp = l.gauge.ampacity
            sev = _lvl(i, amp)
            if worst is None or i / amp > worst[0]:
                worst = (i / amp, l, i, amp)
            if sev != "pass":
                rows.append(f"{l.id} {l.gauge.label} {_a(i)} of {amp:g} A")
            worst_sev = max(worst_sev, sev, key=["pass", "warn", "fail"].index)
            for e in (l.a, l.b):
                if isinstance(e, HPin) and e.connector not in pm.devices:
                    conn = pm.h.connectors[e.connector]
                    r = conn.rating
                    if r is None:
                        unrated.add(f"{conn.name} ({conn.type})")
                        continue
                    s2 = _lvl(i, r)
                    conn_sev = max(conn_sev, s2, key=["pass", "warn", "fail"].index)
                    if s2 != "pass":
                        conn_rows.append(f"{e} {_a(i)} of {r:g} A ({conn.type})")
        if worst is not None:
            _r, l, i, amp = worst
            src = "[source: PowerStream chassis-wiring ampacity table (eitri.harness.catalog), fetched 2026-10-26]"
            out.append(_check(f"harness power: wire ampacity on {net} (circuit currents)", worst_sev,
                              (("; ".join(rows[:6]) + ". Fix: a heavier gauge") if rows else
                               f"every wire within its rating; the most loaded is {l.id} ({l.gauge.label}) at {_a(i)} of {amp:g} A ({_r * 100:.0f}%)")
                              + (" (" + "; ".join(notes) + ")" if notes else "") + f" {src}",
                              "<= the gauge's ampacity", [net] + [x.id for x in links]))
        low = [f"{l.id} {_a(pm.h.wire_current(l))} declared, {_a(cur.get(l.id, 0.0))} in the circuit" for l in links
               if not _is_device(l) and pm.h.wire_current(l) is not None and pm.h.wire_current(l) + 1e-9 < cur.get(l.id, 0.0)]
        out.append(_check(f"harness power: the harness grades {net} on the circuit's currents", "warn" if low else "pass",
                          (("; ".join(low[:6]) + ": the harness's own ampacity and drop checks use the lower figure. Fix: current_A= in the "
                            "harness net or wire") if low else "every wire's declared current is at least what the circuit draws through it"),
                          "declared >= the circuit's", [net] + [x.id for x in links]))
        if conn_rows or any(isinstance(e, HPin) and e.connector not in pm.devices and pm.h.connectors[e.connector].rating is not None
                            for l in links for e in (l.a, l.b)):
            out.append(_check(f"harness power: connector pins on {net} (circuit currents)", conn_sev,
                              ("; ".join(conn_rows[:6]) if conn_rows else "every rated connector pin carries less than 80% of its rating")
                              + (f"; not graded (no rating in the catalog): {', '.join(sorted(unrated))}" if unrated else "")
                              + " [source: the harness connector catalog (eitri.harness.catalog)]", "<= the pin rating", [net]))
        elif unrated:
            out.append(not_run(f"harness power: connector pins on {net} (circuit currents)",
                               f"no connector on {net} has a rating in the catalog: {', '.join(sorted(unrated))}", "", [net]))
        for e in mapped:
            if e in sources:
                continue
            p = _path(links, next(iter(sources)), e)
            drop_at[(k, e)] = sum(_drop(x, cur.get(x.id, 0.0)) for x in p) if p else math.nan
            if p:
                for x in p:
                    if _is_device(x) and _drop(x, 1.0) != _drop(x, 1.0):
                        from .elec_sources import fuse_drop_V
                        blocked[(k, e)] = fuse_drop_V(getattr(x.device, "type", ""), float(getattr(x.device, "rating_A", 0) or 0), 1.0)[1]
    # the returns: harness nets on a ground, with the currents of the rails' loads
    ret_drop: dict[str, float] = {}
    for k, ends in pm.groups.items():
        net, _n = pm.group_net(k)
        if not net or an.nets[net].kind != "gnd":
            continue
        mapped = {e: pm.pin[e] for e in ends if e in pm.pin}
        sinks = {e for e, cp in mapped.items() if sinks_of_part.get(cp.part) == cp.pin}
        if not sinks:
            continue
        draw = {e: part_draw.get(cp.part, 0.0) for e, cp in mapped.items() if e not in sinks}
        links = links_of.get(k, [])
        cur = wire_currents(links, sinks, draw)
        worst = max(((cur.get(l.id, 0.0) / l.gauge.ampacity, l) for l in links if not _is_device(l)), default=None, key=lambda t: t[0])
        if worst is not None:
            r, l = worst
            sev = _lvl(r, 1.0)
            out.append(_check(f"harness power: wire ampacity on the return {net} (circuit currents)", sev,
                              f"the most loaded return wire is {l.id} ({l.gauge.label}) at {_a(cur.get(l.id, 0.0))} of {l.gauge.ampacity:g} A ({r * 100:.0f}%)"
                              " [source: PowerStream chassis-wiring ampacity table (eitri.harness.catalog), fetched 2026-10-26]",
                              "<= the gauge's ampacity", [net] + [x.id for x in links]))
        for e, cp in mapped.items():
            if e in sinks:
                continue
            p = _path(links, next(iter(sinks)), e)
            if p:
                d = sum(_drop(x, cur.get(x.id, 0.0)) for x in p)
                ret_drop[cp.part] = d if d != d else max(ret_drop.get(cp.part, 0.0), d)
    # the voltage at each load, and the supply's capacity
    from .catalog import normalize_id
    bom = _bom_power(cad_module)
    matched_bom = set()
    unverified_bom: list[str] = []
    for k, net, info, mapped, sources, draw, cur, links, notes in rail_info:
        vsrc = info.volts
        rows, worst, ungraded, ungraded_why = [], None, [], []
        for e, cp in mapped.items():
            if e in sources or draw.get(e, 0.0) <= 0 and c.pin_spec(cp).kind != "power_in":
                continue
            vmin, vtxt = _v_min(c, cp)
            if vmin is None:
                ungraded.append(f"{cp.part}")
                continue
            if (k, e) in blocked:
                ungraded_why.append(f"{cp.part} (through a device: {blocked[(k, e)]})")
                continue
            sup = drop_at.get((k, e), math.nan)
            if sup != sup:                                  # nan: not reached from the source
                continue
            if cp.part in ret_drop:
                ret, how = ret_drop[cp.part], "its return through the harness"
            else:
                ret, how = sup, "the return assumed equal to the supply side (its ground is not a mapped harness pin)"
            v = vsrc - sup - ret
            margin = v - vmin
            rows.append((margin, cp.part, v, vmin, sup, ret, how, vtxt))
        name = f"harness power: voltage at the loads on {net}"
        k_src = "[source: V = K I L / CM per conductor, K = 12.9 (NEC Chapter 9 Table 8, copper at 75 C; eitri.harness.catalog)]"
        devs = [x for x in links if _is_device(x)]
        if devs:
            from .elec_sources import fuse_drop_V
            k_src += " + fuse drop at the rated current, scaled to the current " + " ".join(sorted({
                f"({x.id}: {fuse_drop_V(getattr(x.device, 'type', ''), float(getattr(x.device, 'rating_A', 0) or 0), 1.0)[1]})" for x in devs}))
        if ungraded_why:                                    # a load whose drop cannot be computed: never a pass on the others alone
            out.append(not_run(name, "the drop to " + "; ".join(ungraded_why[:4]) + " is unknown", "a published drop for every device on the path", [net]))
        elif not rows:
            out.append(not_run(name, f"none of the loads on {net} has a published minimum supply voltage" + (f" ({', '.join(ungraded)})" if ungraded else ""),
                               "", [net]))
        else:
            rows.sort()
            m, part, v, vmin, sup, ret, how, vtxt = rows[0]
            sev = "fail" if m < -1e-9 else "pass"
            out.append(_check(name, sev, f"worst: {part} gets {v:.3f} V ({vsrc:g} V at the source - {sup * 1000:.0f} mV supply - {ret * 1000:.0f} mV return, "
                              f"{how}); it needs {vmin:g} V ({vtxt})" + (f". Fix: heavier or shorter wires on {net}, or a higher supply voltage" if sev == "fail" else f", margin {m * 1000:.0f} mV")
                              + (f"; not graded (no supply range): {', '.join(ungraded)}" if ungraded else "") + f" {k_src}",
                              f">= {vmin:g} V at {part}", [net, part] + [x.id for x in links]))
        # the source's capacity against what the harness carries from it
        for s in sources:
            sp_ref = mapped[s]
            sp = c.parts[sp_ref.part]
            through = max((cur.get(l.id, 0.0) for l in links if s in (l.a, l.b)), default=0.0)
            total = sum(cur.get(l.id, 0.0) for l in links if s in (l.a, l.b))
            lim, basis = _source_rating(c, an, sp, sp_ref.pin, net)
            limits = []
            if lim:
                limits.append((float(lim), f"{sp.name}: {basis}"))
            if sp.kind == "supply":
                for bp in bom:
                    if normalize_id(sp.part_number or "") == bp.id or normalize_id(sp.name) == bp.id:
                        matched_bom.add(bp.id)
                        oc, ov = bp.value("output_current_a"), bp.value("output_voltage_v")
                        ocs = bp.spec("output_current_a")
                        if oc and ocs is not None and not ocs.verified:
                            unverified_bom.append(f"{bp.id} {float(oc):g} A [source: {ocs.cite}]")
                        elif oc:
                            limits.append((float(oc), f"the BOM's {bp.name} ({oc:g} A) [source: {ocs.cite}]"))
                        if ov is not None and abs(float(ov) - float(sp.spec.get("volts") or 0)) > 1e-6:
                            out.append(_check(f"harness power: {sp.name} is the supply the BOM buys", "fail",
                                              f"the circuit's {sp.name} is {sp.spec.get('volts'):g} V, the BOM's {bp.id} {float(ov):g} V", "the same voltage", [sp.name]))
            cname = f"harness power: {sp.name} supplies what the harness carries"
            if unverified_bom:                               # the BOM's supply has only an unverified rating: no verdict
                out.append(not_run(cname, "ASSUMPTION: unverified catalog figure: " + "; ".join(unverified_bom)
                                   + ". The BOM must buy a supply whose rating is read from its datasheet", "", [sp.name, net]))
                unverified_bom.clear()
                continue
            if not limits:
                out.append(not_run(cname, f"{sp.name} has no current rating ({basis}); give the supply current_A=", "", [sp.name, net]))
                continue
            lim_v, lim_txt = min(limits)
            sev = _lvl(total, lim_v)
            out.append(_check(cname, sev, f"{_a(total)} leaves {s} through the harness, {_a(lim_v)} available ({total / lim_v * 100:.0f}%), limited by {lim_txt}"
                              + (f". Fix: a supply rated for at least {_a(total / WARN_FRACTION)}" if sev == "fail" else ""),
                              f"<= {_a(lim_v)}", [sp.name, net]))
        data["rails"].append({"net": net, "wires": {l.id: round(cur.get(l.id, 0.0), 4) for l in links}})
    unmatched = [bp.id for bp in bom if bp.id not in matched_bom]
    if unmatched:
        out.append(not_run("harness power: the BOM's power supply is a circuit supply",
                           f"the CAD file buys {', '.join(unmatched)} but no circuit supply names it (c.supply(..., part_number='{unmatched[0]}'))",
                           "part_number= of the circuit's supply", unmatched))
    return out, data


# ---------------------------------------------------------------------------- mounting: outline and holes vs the CAD

def _frame_loc(a: dict):
    import build123d as bd
    from .assembly import frame
    at, x, _y, z = frame(a)
    return bd.Location(bd.Plane(origin=tuple(float(v) for v in at), x_dir=tuple(float(v) for v in x), z_dir=tuple(float(v) for v in z)))


def mounting_checks(c, parts, anchors: list[dict], coupling: dict) -> tuple[list[HCheck], dict]:
    import build123d as bd
    import numpy as np
    from eitri import measure
    from .assembly import frame
    out: list[HCheck] = []
    data: dict = {}
    mounts = coupling.get("mounts") or {}
    by_anchor = {str(a["name"]).upper(): a for a in anchors}
    by_name = {p.name: p for p in parts}
    for part in c.parts.values():
        if part.kind not in ("board", "module"):
            continue
        ol = es.outline_for(part.catalog)
        base = f"mounting: {part.name}"
        m = mounts.get(part.name)
        if ol is None:
            out.append(not_run(f"{base} fits its CAD mount", f"eitri has no published outline and hole pattern for {part.catalog or part.kind}",
                               "a catalog outline", [part.name]))
            continue
        if m is None and part.name.upper() not in by_anchor:
            out.append(not_run(f"{base} fits its CAD mount", f"{part.name} has no mount: add COUPLING mounts={{'{part.name}': dict(anchor=..., "
                               "carrier=[...], clearance_mm=...)}} and an anchor for it in the CAD file", "a mount", [part.name]))
            continue
        m = dict(m or {})
        a = by_anchor.get(str(m.get("anchor") or part.name).upper())
        if a is None:
            out.append(_check(f"{base} fits its CAD mount", "fail", f"the CAD model has no anchor {m.get('anchor') or part.name!r}",
                              "an anchor where the board sits", [part.name]))
            continue
        stand_in = m.get("stand_in", a.get("part"))
        carriers = [str(x) for x in (m.get("carrier") or [])]
        missing = [x for x in carriers + ([stand_in] if stand_in and "stand_in" in m else []) if x not in by_name]
        if missing:
            out.append(_check(f"{base} fits its CAD mount", "fail", f"the CAD model has no part {', '.join(map(repr, missing))}", "", [part.name]))
            continue
        L, W, T = ol["size"]
        loc = _frame_loc(a)
        board = loc * bd.Pos(0, 0, -T / 2) * bd.Box(L, W, T)
        at, xv, yv, zv = frame(a)
        refs = [part.name, a["name"]]
        cite_ol = es.cite(ol["src"])
        # 1. the hole pattern
        screw = m.get("screw", ol["screw"])
        d = es.screw_diameter(screw)
        hole_name = f"{base} hole pattern matches the CAD"
        cand = [by_name[n] for n in (carriers or [p.name for p in parts if p.name != stand_in])]
        cad_holes = []
        for p in cand:
            try:
                cad_holes += [(p.name, h) for h in measure.holes(p.shape)]
            except Exception:                               # noqa: BLE001
                continue
        if d is None:
            out.append(_check(hole_name, "fail", f"screw {screw!r} is not a metric size (M3, M2.5)", "screw='M3'", refs))
        elif d > ol["hole_d"]:
            out.append(_check(hole_name, "fail", f"an {screw} screw ({d:g} mm) does not pass {part.name}'s Ø{ol['hole_d']:g} mm holes [source: {es.cite(ol['hole_src'])}]",
                              f"a screw <= {ol['hole_d']:g} mm", refs))
        else:
            rows, bad = [], []
            for hx, hy in ol["holes"]:
                c0 = at + xv * hx + yv * hy - zv * (T / 2)
                best = None
                for pname, h in cad_holes:
                    ends = [np.array([h.start.X, h.start.Y, h.start.Z]), np.array([h.end.X, h.end.Y, h.end.Z])]
                    off = max(float(np.linalg.norm(np.cross(e - c0, zv))) for e in ends)         # both ends of the CAD hole from the board hole's axis
                    allow = (ol["hole_d"] - d) / 2 + max(0.0, h.diameter - d) / 2
                    score = off - allow
                    if best is None or score < best[0]:
                        best = (score, pname, h, off, allow)
                if best is None or best[0] > 1e-6:
                    bad.append(f"({hx:g}, {hy:g})" + (f": nearest CAD hole Ø{best[2].diameter:.2f} in {best[1]} is {best[3]:.2f} mm off (an {screw} passes both within {best[4]:.2f} mm)" if best else ": no hole in the carrier"))
                else:
                    rows.append(f"Ø{best[2].diameter:.2f} in {best[1]} {best[3]:.2f} mm off")
            if bad:
                out.append(_check(hole_name, "fail", f"{len(bad)} of {len(ol['holes'])} {part.name} holes (board frame, mm) have no CAD hole an {screw} screw passes with them: "
                                  + "; ".join(bad[:4]) + f". Fix: put the CAD holes / standoffs at the board's pattern [source: {cite_ol}; {es.cite(ol['hole_src'])}; {es.cite('iso-metric-thread')}]",
                                  "a CAD hole under every board hole", refs + carriers))
            else:
                out.append(_check(hole_name, "pass", f"all {len(ol['holes'])} {part.name} holes (Ø{ol['hole_d']:g} mm) line up with CAD holes an {screw} screw passes: "
                                  + "; ".join(rows) + f" [source: {cite_ol}; {es.cite(ol['hole_src'])}; {es.cite('iso-metric-thread')}]", "", refs + carriers))
        # 2. the outline against every other body
        hits = []
        others = [p for p in parts if p.name != stand_in]
        bb = measure.bounding_box(board)
        for p in others:
            bo = measure.bounding_box(p.shape)
            if (bb.max.X < bo.min.X or bo.max.X < bb.min.X or bb.max.Y < bo.min.Y or bo.max.Y < bb.min.Y or bb.max.Z < bo.min.Z or bo.max.Z < bb.min.Z):
                continue
            try:
                v = measure.overlap_volume(board, p.shape)
            except Exception:                               # noqa: BLE001
                v = 0.0
            if v > 1e-3:
                hits.append(f"{p.name} ({v:.1f} mm³)")
        out.append(_check(f"{base} outline is clear of the CAD bodies", "fail" if hits else "pass",
                          (f"the {L:g} x {W:g} x {T:g} mm board at anchor {a['name']} overlaps " + ", ".join(hits)) if hits else
                          f"the {L:g} x {W:g} x {T:g} mm board at anchor {a['name']} overlaps no CAD body" + (f" (its stand-in {stand_in} excluded)" if stand_in else "")
                          + f" [source: {cite_ol}; thickness {es.cite(ol['thickness_src'])}]", "no overlap", refs + [h.split(' ')[0] for h in hits]))
        # 3. clearance to everything but its carriers
        cname = f"{base} clearance to the CAD bodies"
        if m.get("clearance_mm") is None:
            out.append(not_run(cname, f"no clearance_mm in COUPLING mounts['{part.name}'] (the gap the design needs around the board)",
                               "clearance_mm=", refs))
        else:
            need = float(m["clearance_mm"])
            near = []
            for p in others:
                if p.name in carriers:
                    continue
                try:
                    near.append((measure.min_distance(board, p.shape), p.name))
                except Exception:                           # noqa: BLE001
                    continue
            gap, who = min(near) if near else (math.inf, "")
            sev = "fail" if gap + 1e-9 < need else "pass"
            out.append(_check(cname, sev, (f"{gap:.1f} mm to {who}" if near else "no other body") + f"; the mount asks for {need:g} mm (the model file)"
                              + (" (carriers excluded: " + ", ".join(carriers) + ")" if carriers else ""), f">= {need:g} mm", refs + ([who] if who else [])))
        data[part.name] = {"anchor": a["name"], "outline": ol["size"], "screw": screw}
    return out, data


# ---------------------------------------------------------------------------- actuators: mechanism <-> circuit, and their drivers

def actuator_bindings(c, mech, fw, fw_module, coupling: dict) -> tuple[dict[str, str], list[str]]:
    """{mechanism joint: circuit part} and how each was found."""
    from eitri.electronics.model import PinRef
    joints = [j for j in mech.joints if j.actuator is not None]
    out, how = {}, []
    explicit = {str(k): str(v) for k, v in (coupling.get("actuators") or {}).items()}
    for j in joints:
        if j.name in explicit:
            out[j.name] = explicit[j.name]
    if fw is not None and fw_module is not None:
        fwc = getattr(fw_module, "COUPLING", None)
        fwj = (fwc or {}).get("joints") if isinstance(fwc, dict) else None
        boards = _boards(c)
        if fwj and boards:
            b = next((p for p in boards if str(fw.board_key).lower().startswith(p.catalog or "?")), boards[0])
            for p in fw.pins:
                joint = fwj.get(p.net)
                if p.role != "servo" or not joint or joint in out:
                    continue
                cap = fw.board.resolve(p.pin) if fw.board else None
                cp = _cpin(c, b.name, cap.name if cap else p.pin)
                n = c._net_of.get(cp) if cp else None
                sig = [r.part for r in (c.nets[n].pins if n else []) if c.parts[r.part].kind == "servo" and r.pin == "SIG"]
                if len(sig) == 1:
                    out[joint] = sig[0]
                    how.append(f"{joint} -> {sig[0]} through the firmware ({p.net} on {cap.name if cap else p.pin})")
    acts = [p.name for p in c.parts.values() if p.kind in ACTUATOR_KINDS]
    for j in joints:
        if j.name in out:
            continue
        hit = [n for n in acts if j.name.upper() in n.upper() and n not in out.values()]
        if len(hit) == 1:
            out[j.name] = hit[0]
            how.append(f"{j.name} -> {hit[0]} by name")
    return out, how


def stall_of(c, part_name: str, mech_actuator=None) -> tuple[float, str]:
    """An actuator's stall current: the circuit's figure (a servo: the electronics catalog's stall), or the suite catalog's when higher."""
    p = c.parts[part_name]
    i = float(p.spec.get("current_A") or 0.0)
    txt = f"{_a(i)} ({'catalog stall' if p.kind == 'servo' and p.srcs.get('current_A') != 'model' else 'the model file'}) [source: {_ecite(p.srcs.get('current_A', ''))}]"
    if mech_actuator is not None and hasattr(mech_actuator, "stall_current"):     # core 0.8.0 Actuator.stall_A / its catalogue figure
        try:
            sa, ssrc = mech_actuator.stall_current()
        except Exception:                                                        # noqa: BLE001
            sa, ssrc = None, ""
        if sa is not None and float(sa) > i + 1e-12:
            i, txt = float(sa), f"{_a(float(sa))} (the mechanism's {mech_actuator.name} stall) [source: {ssrc}]"
    from .catalog import lookup
    cat = lookup(p.catalog) if p.catalog else (lookup(mech_actuator.name) if mech_actuator is not None else None)
    sc = cat.spec("stall_current_a") if cat is not None else None
    if sc is not None and sc.verified:                       # only a figure read from the maker's document raises the draw
        v = sc.value
        hi = max(v.values()) if isinstance(v, dict) else float(v)
        if hi > i + 1e-12:
            i, txt = hi, f"{_a(hi)} (catalog {cat.id}; the circuit says {_a(float(p.spec.get('current_A') or 0))}) [source: {sc.cite}]"
    return i, txt


def _driver(c, an, part) -> tuple[str, Any, str | None]:
    """What carries an actuator's current: ("mosfet"|"bjt"|"relay"|"module"|"source"|"pin", the part or pin, the rail net)."""
    from eitri.electronics.model import PinRef
    pins = ("V+", "GND") if part.kind == "servo" else ("+", "-")
    hi, lo = (c._net_of.get(PinRef(part.name, p)) for p in pins)
    for n in (lo, hi):
        if not n:
            continue
        for r in c.nets[n].pins:
            q = c.parts[r.part]
            if q.kind == "mosfet" and r.pin == "D":
                return "mosfet", q, hi
            if q.kind == "bjt" and r.pin == "C":
                return "bjt", q, hi
            if q.kind == "relay" and r.pin in ("NO", "NC"):
                return "relay", q, hi
    if hi:
        info = an.nets[hi]
        for r in c.nets[hi].pins:
            q = c.parts[r.part]
            if q.kind == "module" and c.pin_spec(r).kind == "passive" and q.catalog in es.PASS_THROUGH_RATING:
                return "module", q, hi
        if info.kind == "driven" and info.drivers:
            return "pin", info.drivers[0], hi
        if info.kind == "rail" and info.sources:
            return "source", info.sources[0], hi
    return "", None, hi


def actuator_checks(c, an, mech, bindings: dict, how: list[str]) -> tuple[list[HCheck], dict[str, float], dict]:
    from eitri.mechanism.model import catalog_actuator
    out: list[HCheck] = []
    stall: dict[str, float] = {}
    joints = [j for j in mech.joints if j.actuator is not None]
    acts = [p.name for p in c.parts.values() if p.kind in ACTUATOR_KINDS and p.kind != "solenoid"]
    problems = []
    for j in joints:
        pn = bindings.get(j.name)
        if pn is None:
            problems.append(f"joint {j.name} ({j.actuator.name}) has no actuator in the circuit")
            continue
        if pn not in c.parts or c.parts[pn].kind not in ACTUATOR_KINDS:
            problems.append(f"joint {j.name} is mapped to {pn!r}, which is not a servo, motor or solenoid of the circuit")
            continue
        spec = catalog_actuator(j.actuator.name)
        cp = c.parts[pn]
        if spec is not None and cp.kind == "servo" and cp.catalog and spec.key != cp.catalog.upper():
            problems.append(f"joint {j.name} is driven by a {spec.key} in the mechanism and a {cp.catalog} in the circuit ({pn})")
    extra = [n for n in acts if n not in bindings.values()]
    sev = "fail" if problems else ("warn" if extra else "pass")
    out.append(_check("actuators: every actuated joint has its actuator in the circuit", sev,
                      ("; ".join(problems) if problems else f"{len(joints)} joint(s) bound: " + ", ".join(f"{k} -> {v}" for k, v in bindings.items()))
                      + (f"; in the circuit but driving no joint: {', '.join(extra)}" if extra else "")
                      + (f" ({'; '.join(how[:6])})" if how and not problems else ""),
                      f"{len(joints)} actuator(s), one per actuated joint", list(bindings.values()) + extra))
    groups: dict[str, dict] = {}
    for j in joints:
        pn = bindings.get(j.name)
        if pn not in c.parts or c.parts[pn].kind not in ACTUATOR_KINDS:
            continue
        i, txt = stall_of(c, pn, j.actuator)
        stall[pn] = i
        kind, drv, net = _driver(c, an, c.parts[pn])
        key = f"{kind}:{getattr(drv, 'name', drv)}"
        g = groups.setdefault(key, {"kind": kind, "drv": drv, "net": net, "items": []})
        g["items"].append((pn, i, txt))
    for key, g in groups.items():
        kind, drv, net = g["kind"], g["drv"], g["net"]
        total = sum(i for _, i, _ in g["items"])
        what = ", ".join(f"{pn} {t}" for pn, _i, t in g["items"])
        name = f"actuators: stall current vs {getattr(drv, 'name', drv) or 'its driver'}"
        rating, basis = None, ""
        if kind == "mosfet":
            rating, basis = float(drv.spec["id_max"]), f"{drv.catalog} ID {drv.spec['id_max']:g} A [source: {_ecite(drv.srcs.get('id_max', ''))}]"
        elif kind == "bjt":
            rating, basis = float(drv.spec["ic_max"]), f"{drv.catalog} IC {drv.spec['ic_max']:g} A [source: {_ecite(drv.srcs.get('ic_max', ''))}]"
        elif kind == "relay":
            rating, basis = float(drv.spec["contact_A"]), f"{drv.catalog} contacts {drv.spec['contact_A']:g} A [source: {_ecite(drv.srcs.get('contact_A', ''))}]"
        elif kind == "module":
            r, sid = es.PASS_THROUGH_RATING[drv.catalog]
            if r is None:
                out.append(not_run(name, f"{drv.name} ({drv.catalog}) passes {_a(total)} of stall current ({what}) to the servos, and its maker publishes no "
                                   f"current rating for that path [source: {es.cite(sid)}]", "a published rating", [drv.name] + [p for p, _i, _t in g["items"]]))
                continue
            rating, basis = r, es.cite(sid)
        elif kind == "pin":
            b = c.parts[drv.part]
            lim = b.spec.get("pin_limit_A")
            rating, basis = (float(lim), f"{b.title} {drv.pin} {lim * 1000:g} mA [source: {_ecite(b.srcs.get('pin_limit_A', ''))}]") if lim else (None, "")
        elif kind == "source":
            sp = c.parts[drv.part]
            rating, basis = _source_rating(c, an, sp, drv.pin, net)
            basis = f"{drv} on {net}: {basis}"
        if not rating:
            out.append(not_run(name, f"what carries {what} has no current rating ({basis or 'no driver found'})", "", [p for p, _i, _t in g["items"]]))
            continue
        g["rating"], g["basis"] = float(rating), basis
        sev = _lvl(total, rating)
        out.append(_check(name, sev, f"{len(g['items'])} actuator(s) stalled at once: {_a(total)} ({what}) of {_a(rating)} ({total / rating * 100:.0f}%), {basis}"
                          + (". Fix: a driver or supply rated for the stall current" if sev == "fail" else ""), f"<= {_a(rating)}",
                          [getattr(drv, 'name', str(drv))] + [p for p, _i, _t in g["items"]]))
    return out, stall, {"bindings": bindings, "groups": list(groups.values())}


def dissipation_checks(c, an, stall: dict[str, float]) -> list[HCheck]:
    """Regulators and switching drivers that carry actuator current, at the actuators' stall current (the electronics domain's limits)."""
    from eitri.electronics.checks import gate_voltage
    from eitri.electronics.model import PinRef
    out: list[HCheck] = []
    graded = False
    for q in c.parts.values():
        if q.kind == "regulator":
            nout = c._net_of.get(PinRef(q.name, "OUT"))
            nin = c._net_of.get(PinRef(q.name, "IN"))
            if not nout or not nin or an.nets[nin].kind != "rail":
                continue
            on = [p for p in stall if any(r.part == p for r in c.nets[nout].pins)]
            if not on:
                continue
            graded = True
            s = q.spec
            base = sum(i for what, i, refs in _loads_on(c, an, nout) if not (set(refs) & set(on)))
            iout = base + sum(stall[p] for p in on)
            vin = an.nets[nin].volts
            pd = (vin - s["vout"]) * iout + vin * float(s.get("iq", 0.0))
            tj = c.ambient_C + pd * s["theta_ja"]
            sev = _lvl(tj - c.ambient_C, s["tj_max"] - c.ambient_C)
            out.append(_check(f"dissipation: {q.name} ({q.catalog}) at the actuators' stall", sev,
                              f"{pd:.2f} W ({vin - s['vout']:.2f} V x {_a(iout)}, with {', '.join(on)} stalled): junction {tj:.0f} C at {c.ambient_C:g} C, "
                              f"{s['theta_ja']:g} C/W, limit {s['tj_max']:g} C [source: {_ecite(q.srcs.get('theta_ja', ''))}]"
                              + (". Fix: a switching regulator, a heatsink, or power the actuators from their own supply" if sev == "fail" else ""),
                              f"<= {s['tj_max']:g} C", [q.name] + on))
        elif q.kind in ("mosfet", "bjt"):
            col = c._net_of.get(PinRef(q.name, "D" if q.kind == "mosfet" else "C"))
            on = [p for p in stall if col and any(r.part == p for r in c.nets[col].pins)]
            if not on:
                continue
            graded = True
            i = sum(stall[p] for p in on)
            s = q.spec
            name = f"dissipation: {q.name} ({q.catalog}) at the actuators' stall"
            if q.kind == "bjt":
                out.append(not_run(name, f"{_a(i)} at stall ({', '.join(on)}): the catalog gives no junction-to-ambient figure for {q.catalog}", "", [q.name] + on))
                continue
            vg, how = gate_voltage(c, an, q)
            usable = [(v, r) for v, r in sorted(s["rds_on"]) if vg is not None and v <= vg + 1e-9]
            if not usable:
                out.append(not_run(name, f"no RDS(on) is specified at its gate drive ({how or 'undriven'}); the gate-drive check says so", "", [q.name]))
                continue
            rds = usable[-1][1]
            pd = i * i * rds
            tj = c.ambient_C + pd * s["theta_ja"]
            sev = "fail" if i > s["id_max"] else _lvl(tj - c.ambient_C, s["tj_max"] - c.ambient_C)
            out.append(_check(name, sev, f"{_a(i)} at stall ({', '.join(on)}) x {rds * 1000:g} mΩ = {pd:.2f} W: junction {tj:.0f} C ({s['theta_ja']:g} C/W, no heatsink), "
                              f"limit {s['tj_max']:g} C [source: {_ecite(q.srcs.get('theta_ja', ''))}]", f"<= {s['tj_max']:g} C", [q.name] + on))
    if not graded:
        out.append(_check("dissipation: regulators and drivers at the actuators' stall", "pass",
                          "no regulator or switching driver in the circuit carries an actuator's current (nothing here dissipates it); the "
                          "electronics domain's own regulator and MOSFET checks grade the rest", ""))
    return out


# ---------------------------------------------------------------------------- the extension's entry for an electronics build

def run(module, c, model_path: Path) -> tuple[list[HCheck], dict]:
    """(checks, data) for one electronics build of a combined project."""
    from eitri.electronics.analysis import analyze
    folder = Path(model_path).resolve().parent
    coupling = coupling_of(module)
    sib = Siblings(folder, coupling)
    an = analyze(c)
    checks: list[HCheck] = []
    data: dict = {}

    h, _hm, _hp, h_why = sib.get("harness")
    fw, fw_mod, _fp, fw_why = sib.get("firmware")
    mech, _mm, _mp, m_why = sib.get("mechanism")
    cad, cad_mod, _cp, cad_why = sib.get("cad")

    # actuators first: their stall currents feed the power checks
    stall: dict[str, float] = {}
    if mech is None:
        checks.append(not_run("actuators: every actuated joint has its actuator in the circuit", m_why or "no mechanism"))
        checks.append(not_run("actuators: stall current vs the drivers", m_why or "no mechanism"))
    else:
        bindings, how = actuator_bindings(c, mech, fw, fw_mod, coupling)
        a_checks, stall, a_data = actuator_checks(c, an, mech, bindings, how)
        checks += a_checks
        from . import power                         # sharper: only what the firmware moves together, plus the boards on that supply
        pw_checks, pw_data = power.circuit_checks(a_data.pop("groups", []), fw, fw_mod, mech, bindings, c)
        checks += pw_checks
        if pw_data:
            data["power"] = pw_data
        data["actuators"] = a_data
    if mech is None:
        checks.append(not_run("dissipation: regulators and drivers at the actuators' stall", m_why or "no mechanism"))
    else:
        checks += dissipation_checks(c, an, stall)

    if h is None:
        checks.append(not_run("pins: harness connectors are circuit parts", h_why or "no harness"))
        checks.append(not_run("harness power: rail currents in the harness", h_why or "no harness"))
    else:
        pm = PinMap(c, h, coupling)
        checks += harness_pin_checks(pm)
        p_checks, p_data = harness_power_checks(c, an, pm, cad_mod, stall)
        checks += p_checks
        data["harness"] = p_data

    if fw is None:
        checks.append(not_run("pins: firmware pins match the circuit", fw_why or "no firmware"))
    else:
        checks += firmware_pin_checks(c, fw, coupling)

    if cad is None:
        checks.append(not_run("mounting: boards and modules fit their CAD mounts", cad_why or "no CAD model"))
    elif not len(cad):
        checks.append(not_run("mounting: boards and modules fit their CAD mounts", "the CAD model is still blank"))
    else:
        from eitri.anchors import to_json as anchors_json
        m_checks, m_data = mounting_checks(c, cad, anchors_json(cad), coupling)
        checks += m_checks
        data["mounting"] = m_data
    data["not_run"] = sum(1 for k in checks if str(k.measured).startswith("not run:"))
    return checks, data
