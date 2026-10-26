"""The pneumatics checks: what every build is graded on (all closed-form, see eitri/pneumatics/flow.py for the formulas).

    circuit       every cylinder port fed from a supply through a valve in some position, and able to exhaust in another; valve
                  ports connected as their class needs; no dead-headed lines (a tee or an inline component with one connection)
    force         usable cylinder force (pressure at the cylinder x area x (1 - friction allowance)) against the load for each
                  stroke: FAIL below the load, WARN when the load ratio (load / theoretical force) exceeds 0.7 (the usual dynamic
                  sizing guidance, an ESTIMATE); a single-acting return is its spring
    air           free-air consumption per cycle and per minute (swept volumes plus the dead volume of the lines between valve
                  and cylinder), summed against the supply's capacity (FAIL above, WARN above 80 %)
    speed         ISO 6358 flow available through the fill path (supply -> regulator -> valve -> tube) and the exhaust path
                  (cylinder -> valve -> silencer), against the flow the required speed needs; the achievable speed is the lesser
    pressure drop the drop across every tube of a fill path at the required flow: WARN above 0.3 bar or 5 % of the pressure, FAIL
                  when the flow cannot pass at all (choked)
    flow data     one warning naming every component or tube graded with an estimated C, b (conservative defaults)

Nothing here raises on a bad design: a bad design is a failed check.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..checks import Check, check as _check, level
from .flow import DEFAULT_C, consumption_per_cycle_l, cylinder_forces, drop_for_flow, flow_l_s, required_flow_l_s, series, tube_data
from .model import EXHAUST_PORTS, P_ATM, Circuit, Cylinder, FlowControl, Node, PortRef, Regulator, Silencer, Supply, Tube, Valve

LOAD_RATIO_WARN = 0.7          # ESTIMATE: manufacturers' dynamic sizing guidance (SMC / Festo selection guides: 50-70 %)
SUPPLY_WARN = 0.8
DROP_WARN_BAR = 0.3
FREE_FLOW_C = 3.0              # ESTIMATE: the bypass check of a speed controller, or a node: no significant restriction


@dataclass
class Step:
    kind: str                  # tube | component
    name: str
    C: float
    b: float
    estimated: bool
    note: str = ""
    tube: Tube | None = None


@dataclass
class PathInfo:
    found: bool
    steps: list[Step] = field(default_factory=list)      # in flow order
    valve_positions: dict[str, int] = field(default_factory=dict)
    C_eff: float | None = None
    b_eff: float | None = None
    pressure_bar: float | None = None                    # gauge pressure at the cylinder side (supply paths)
    supply: str | None = None
    dead_volume_mm3: float = 0.0                         # tubes between the valve and the cylinder

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.steps]


@dataclass
class StrokeInfo:
    name: str                                            # extend | retract
    port: str
    load_N: float
    theoretical_N: float | None
    usable_N: float | None
    load_ratio: float | None
    speed_required: float | None
    speed_available: float | None
    flow_required_l_s: float | None
    flow_fill_l_s: float | None
    flow_exhaust_l_s: float | None
    fill: PathInfo | None = None
    exhaust: PathInfo | None = None
    status: str = "ungraded"


@dataclass
class CylinderInfo:
    name: str
    bore_mm: float
    rod_mm: float
    stroke_mm: float
    acting: str
    pressure_bar: float | None
    strokes: list[StrokeInfo] = field(default_factory=list)
    air_per_cycle_l: float = 0.0
    air_per_minute_l: float = 0.0
    dead_volume_l: float = 0.0
    cycles_per_min: float = 0.0


@dataclass
class SupplyInfo:
    name: str
    pressure_bar: float
    capacity_l_min: float | None
    demand_l_min: float
    status: str


# --------------------------------------------------------------------------- the port graph

def _comp_step(comp, from_port: str, to_port: str) -> Step | None:
    """How air passes through a component between two of its ports (None: no restriction worth counting)."""
    if isinstance(comp, Node):
        return None
    if isinstance(comp, FlowControl):
        if comp.throttles(from_port, to_port):
            c, b = (comp.C, comp.b) if comp.has_flow_data else DEFAULT_C["flow_control"]
            return Step("component", comp.name, c, b, not comp.has_flow_data, f"meter-{comp.meter} throttle {from_port}->{to_port}")
        return Step("component", comp.name, FREE_FLOW_C, 0.3, False, f"free flow {from_port}->{to_port} (bypass check)")
    if isinstance(comp, (Valve, Regulator, Silencer)):
        c, b = (comp.C, comp.b) if comp.has_flow_data else DEFAULT_C[comp.kind]
        return Step("component", comp.name, c, b, not comp.has_flow_data, f"{from_port}->{to_port}")
    return None


def find_path(c: Circuit, start: PortRef, goal: str) -> PathInfo:
    """DFS from a cylinder port outward to a supply ("supply") or to the atmosphere ("exhaust"), using one position per valve.
    Steps come back in flow order (supply -> cylinder for a supply path, cylinder -> air for an exhaust path)."""
    best: PathInfo | None = None

    def done(ref: PortRef, comp) -> bool:
        if goal == "supply":
            return isinstance(comp, Supply)
        return ref in c.vents or isinstance(comp, Silencer)

    def dfs(ref: PortRef, visited: set, positions: dict[str, int], steps: list[Step], depth: int) -> PathInfo | None:
        if depth > 40:
            return None
        comp = c.components[ref.component]
        # arriving at a port: can we finish here?
        if done(ref, comp) and steps:
            if isinstance(comp, Silencer):
                s = _comp_step(comp, "IN", "OUT")
                return PathInfo(True, steps + ([s] if s else []), dict(positions), supply=None)
            return PathInfo(True, steps, dict(positions), supply=comp.name if isinstance(comp, Supply) else None)
        # pass through the component to its other ports
        if isinstance(comp, Valve):
            options = []
            for i, pos in enumerate(comp.positions):
                if comp.name in positions and positions[comp.name] != i:
                    continue
                for a, b in pos:
                    if a == ref.port:
                        options.append((i, b))
                    elif b == ref.port:
                        options.append((i, a))
            for i, other in options:
                out = PortRef(comp.name, other)
                if out in visited:
                    continue
                s = _comp_step(comp, ref.port, other)
                r = _leave(c, out, visited | {ref, out}, {**positions, comp.name: i}, steps + ([s] if s else []), depth + 1, dfs)
                if r is not None:
                    return r
            return None
        if isinstance(comp, (Regulator, FlowControl)):
            for a, b in comp.passages():
                other = b if a == ref.port else a if b == ref.port else None
                if other is None:
                    continue
                out = PortRef(comp.name, other)
                if out in visited:
                    continue
                s = _comp_step(comp, ref.port, other)
                r = _leave(c, out, visited | {ref, out}, positions, steps + ([s] if s else []), depth + 1, dfs)
                if r is not None:
                    return r
            return None
        if isinstance(comp, Node):
            return _leave(c, ref, visited | {ref}, positions, steps, depth + 1, dfs, skip=None)
        return None

    def _leave(c, port, visited, positions, steps, depth, rec, skip=None):
        for kind, tube, other in c.connections_at(port):
            if other in visited:
                continue
            new_steps = steps
            if kind == "tube":
                cc, bb, est = tube_data(tube)
                new_steps = steps + [Step("tube", tube.name, cc, bb, est, f"{tube.id_mm:g} mm x {tube.length_m:g} m", tube)]
            r = rec(other, visited | {other}, positions, new_steps, depth)
            if r is not None:
                return r
        return None

    r = _leave(c, start, {start}, {}, [], 0, dfs)
    if r is None:
        return PathInfo(False)
    if goal == "supply":
        r.steps = list(reversed(r.steps))                  # flow order: supply -> cylinder
    # the dead volume: tubes between the cylinder and the first valve (flow order: after the last valve)
    comps = {s.name: c.components.get(s.name) for s in r.steps if s.kind == "component"}
    seen_valve = goal == "exhaust"
    dead = 0.0
    for s in (r.steps if goal == "supply" else []):
        if s.kind == "component" and isinstance(comps.get(s.name), Valve):
            seen_valve = True
        elif s.kind == "tube" and seen_valve and s.tube is not None:
            dead += s.tube.area_mm2 * s.tube.length_m * 1000.0
    r.dead_volume_mm3 = dead
    C, b = series([(s.C, s.b) for s in r.steps])
    r.C_eff, r.b_eff = C, b
    if goal == "supply":
        regs = [c.components[s.name] for s in r.steps if s.kind == "component" and isinstance(c.components.get(s.name), Regulator)]
        sup = c.components[r.supply] if r.supply else None
        p = sup.pressure_bar if sup else None
        for reg in regs:
            p = min(p, reg.set_bar) if p is not None else reg.set_bar
        r.pressure_bar = p
    return r


# --------------------------------------------------------------------------- the checks

def run_checks(c: Circuit) -> tuple[list[Check], list[CylinderInfo], list[SupplyInfo]]:
    checks: list[Check] = []
    estimated: dict[str, str] = {}
    infos: list[CylinderInfo] = []
    demand: dict[str, float] = {}

    if not c.supplies:
        checks.append(_check("circuit", "a supply", "fail", "the circuit has no air supply", "c.supply(name, pressure_bar=..., flow_l_min=...)"))
    if not c.cylinders:
        checks.append(_check("circuit", "an actuator", "warn", "the circuit has no cylinder; nothing to size", "c.cylinder(...)"))

    net = _nets(c)
    for cyl in c.cylinders:
        if cyl.acting == "double" and "CAP" in cyl.ports and "ROD" in cyl.ports:
            cap, rod = net(cyl.port("CAP")), net(cyl.port("ROD"))
            if cap == rod:
                checks.append(_check("circuit", f"{cyl.name}.CAP and {cyl.name}.ROD on separate lines", "fail",
                                     f"{cyl.name}'s CAP and ROD ports are on one line (joined by tubes" + (" and a tee" if any(x.kind == "node" for x in c.components.values()) else "")
                                     + "): both chambers always see the same pressure, so the piston cannot be driven either way",
                                     "each port on its own line, each to its own valve port (A and B of a 5/2)", [cyl.name]))
            else:
                checks.append(_check("circuit", f"{cyl.name}.CAP and {cyl.name}.ROD on separate lines", "pass",
                                     "the two chambers are fed by different lines", "", [cyl.name]))
    for cyl in c.cylinders:
        info = CylinderInfo(cyl.name, cyl.bore_mm, cyl.rod_mm, cyl.stroke_mm, cyl.acting, None, cycles_per_min=cyl.cycles_per_min)
        fed: dict[str, PathInfo] = {}
        exh: dict[str, PathInfo] = {}
        for port in cyl.ports:
            ref = cyl.port(port)
            fed[port] = find_path(c, ref, "supply")
            exh[port] = find_path(c, ref, "exhaust")
            if fed[port].found:
                for s in fed[port].steps:
                    if s.estimated:
                        estimated[s.name] = s.note
                checks.append(_check("circuit", f"{cyl.name}.{port} fed from supply", "pass",
                                     f"{' -> '.join(fed[port].names)} ({', '.join(f'{v} pos {i + 1}' for v, i in fed[port].valve_positions.items())}), {fed[port].pressure_bar:g} bar at the cylinder",
                                     "", [cyl.name] + fed[port].names))
            else:
                checks.append(_check("circuit", f"{cyl.name}.{port} fed from supply", "fail", f"no path from a supply through a valve to {ref}", "supply -> regulator -> valve -> cylinder", [cyl.name]))
            if exh[port].found:
                for s in exh[port].steps:
                    if s.estimated:
                        estimated[s.name] = s.note
                checks.append(_check("circuit", f"{cyl.name}.{port} can exhaust", "pass", f"{' -> '.join(exh[port].names) or 'direct'} ({', '.join(f'{v} pos {i + 1}' for v, i in exh[port].valve_positions.items())})",
                                     "", [cyl.name] + exh[port].names))
            else:
                bare = [str(v.port(e)) for v in c.components.values() if v.kind == "valve" for e in EXHAUST_PORTS if e in v.ports
                        and v.port(e) not in c.attached and v.port(e) not in c.vents and not c._tubes_at(v.port(e))]
                why = (f"; {', '.join(bare)} {'has' if len(bare) == 1 else 'have'} nothing on {'it' if len(bare) == 1 else 'them'}, and an "
                       "undeclared exhaust port counts as plugged: c.silencer(v[\"R\"]) or c.exhaust(v[\"R\"])") if bare else ""
                checks.append(_check("circuit", f"{cyl.name}.{port} can exhaust", "fail", f"{ref} cannot vent in any valve position: the chamber is dead-headed{why}",
                                     "a valve position joining the port to an exhaust", [cyl.name]))
        p_cyl = next((f.pressure_bar for f in fed.values() if f.found and f.pressure_bar is not None), None)
        info.pressure_bar = p_cyl
        forces = cylinder_forces(cyl, p_cyl) if p_cyl is not None else None
        # strokes
        for stroke, port, load, speed_req in (("extend", "CAP", cyl.load_N, cyl.speed_mm_s),
                                              ("retract", "ROD" if cyl.acting == "double" else "CAP", cyl.load_retract_N, cyl.speed_retract_mm_s or cyl.speed_mm_s)):
            fill_area = cyl.area_bore_mm2 if stroke == "extend" else cyl.area_annulus_mm2
            exh_area = cyl.area_annulus_mm2 if stroke == "extend" else cyl.area_bore_mm2
            si = StrokeInfo(stroke, port, load, None, None, None, speed_req, None, None, None, None)
            # force
            if forces is None:
                checks.append(_check("force", f"force {cyl.name} {stroke}", "fail", "no supply reaches the cylinder, so there is no force to grade", "", [cyl.name]))
            elif stroke == "retract" and cyl.acting == "single":
                if cyl.spring_N is None:
                    checks.append(_check("force", f"force {cyl.name} retract", "warn", f"single-acting: the return spring's force is not given, so the retract stroke is not graded (load {load:g} N)", "spring_N=", [cyl.name]))
                else:
                    si.theoretical_N = si.usable_N = cyl.spring_N
                    si.load_ratio = load / cyl.spring_N if cyl.spring_N else None
                    sev = "fail" if load > cyl.spring_N else "pass"
                    si.status = sev
                    checks.append(_check("force", f"force {cyl.name} retract", sev, f"spring {cyl.spring_N:g} N returns against {load:g} N", f">= {load:g} N", [cyl.name]))
            else:
                th, us = (forces["extend_theoretical_N"], forces["extend_usable_N"]) if stroke == "extend" else (forces["retract_theoretical_N"], forces["retract_usable_N"])
                si.theoretical_N, si.usable_N = th, us
                si.load_ratio = load / th if th else None
                sev = "fail" if load > us else "warn" if si.load_ratio is not None and si.load_ratio > LOAD_RATIO_WARN else "pass"
                si.status = sev
                area = fill_area
                checks.append(_check("force", f"force {cyl.name} {stroke}", sev,
                                     f"{us:.0f} N usable ({th:.0f} N theoretical: {p_cyl:g} bar x {area:.0f} mm², less {cyl.friction_fraction:.0%} friction allowance) vs {load:g} N load, load ratio {si.load_ratio:.2f}",
                                     f"load <= {us:.0f} N usable, load ratio <= {LOAD_RATIO_WARN:g} for a dynamic application" if load else "", [cyl.name]))
            # speed
            fill, ex = fed.get(port if stroke == "extend" else port), exh.get("ROD" if (stroke == "extend" and cyl.acting == "double") else "CAP" if stroke == "retract" else port)
            if stroke == "extend":
                fill, ex = fed["CAP"], (exh["ROD"] if cyl.acting == "double" else None)
            else:
                fill, ex = (fed["ROD"] if cyl.acting == "double" else None), exh["CAP"]
            si.fill, si.exhaust = fill, ex
            if speed_req is not None and p_cyl is not None and (fill is not None and fill.found or stroke == "retract" and cyl.acting == "single"):
                q_req = required_flow_l_s(fill_area, speed_req, p_cyl)
                si.flow_required_l_s = q_req
                p1 = p_cyl + P_ATM
                caps = []
                if fill is not None and fill.found:
                    p_load = load / (fill_area * 0.1 * (1 - cyl.friction_fraction)) + P_ATM if fill_area else P_ATM      # the pressure the load needs
                    q_fill = flow_l_s(fill.C_eff, fill.b_eff, p1, min(p_load, p1 - 0.05))
                    si.flow_fill_l_s = q_fill
                    caps.append(q_fill / fill_area * 1e6 / ((p_cyl + P_ATM) / P_ATM))
                if ex is not None and ex.found and exh_area > 0:
                    q_ex = flow_l_s(ex.C_eff, ex.b_eff, p1, P_ATM)
                    si.flow_exhaust_l_s = q_ex
                    caps.append(q_ex / exh_area * 1e6 / ((p_cyl + P_ATM) / P_ATM))
                if caps:
                    v_avail = min(caps)
                    si.speed_available = v_avail
                    sev = level(speed_req, v_avail, 0.8)
                    parts = []
                    if si.flow_fill_l_s is not None:
                        parts.append(f"fill path passes {si.flow_fill_l_s:.2f} L/s (C_eff {fill.C_eff:.2f} L/(s·bar))")
                    if si.flow_exhaust_l_s is not None:
                        parts.append(f"exhaust path passes {si.flow_exhaust_l_s:.2f} L/s (C_eff {ex.C_eff:.2f} L/(s·bar))")
                    limiting = ("fill" if (si.flow_fill_l_s is not None and caps[0] == v_avail) else "exhaust") if len(caps) > 1 else ("fill" if si.flow_fill_l_s is not None else "exhaust")
                    detail = f"{speed_req:g} mm/s needs {q_req:.2f} L/s ANR; " + ", ".join(parts) + f": {v_avail:.0f} mm/s achievable, limited by the {limiting} path"
                    si.status = "fail" if sev == "fail" else si.status
                    checks.append(_check("speed", f"speed {cyl.name} {stroke}", sev, detail, f"{speed_req:g} mm/s" + (" (within 20% of what the paths pass)" if sev == "warn" else ""),
                                         [cyl.name] + (fill.names if fill and fill.found else []) + (ex.names if ex and ex.found else [])))
            elif speed_req is not None:
                checks.append(_check("speed", f"speed {cyl.name} {stroke}", "fail", f"{speed_req:g} mm/s required but no supply path feeds the stroke", "", [cyl.name]))
            # pressure drop across each tube of the fill path at the required flow
            if si.flow_required_l_s and fill is not None and fill.found:
                p = p1 if p_cyl is not None else None
                for s in fill.steps:
                    if s.kind != "tube" or p is None:
                        continue
                    dp = drop_for_flow(s.C, s.b, p, si.flow_required_l_s)
                    if dp is None:
                        checks.append(_check("pressure drop", f"pressure drop {s.name} ({cyl.name} {stroke})", "fail",
                                             f"{s.note} cannot pass {si.flow_required_l_s:.2f} L/s ANR at all (choked at {s.C * p:.2f} L/s): a larger bore or a shorter run", f"<= {DROP_WARN_BAR:g} bar", [s.name, cyl.name]))
                    else:
                        sev = "warn" if dp > max(DROP_WARN_BAR, 0.05 * p_cyl) else "pass"
                        checks.append(_check("pressure drop", f"pressure drop {s.name} ({cyl.name} {stroke})", sev,
                                             f"{dp:.2f} bar across {s.note} at {si.flow_required_l_s:.2f} L/s ANR" + (f" (C {s.C:.2f} estimated)" if s.estimated else ""), f"<= {DROP_WARN_BAR:g} bar", [s.name, cyl.name]))
                        p = p - dp
            info.strokes.append(si)
        # air
        if p_cyl is not None:
            dead = sum(f.dead_volume_mm3 for f in fed.values() if f.found)
            cons = consumption_per_cycle_l(cyl, p_cyl, dead)
            info.air_per_cycle_l, info.air_per_minute_l, info.dead_volume_l = cons["per_cycle_l"], cons["per_minute_l"], cons["dead_l"]
            sup = next((f.supply for f in fed.values() if f.found and f.supply), None)
            if sup:
                demand[sup] = demand.get(sup, 0.0) + cons["per_minute_l"]
            checks.append(_check("air", f"air {cyl.name}", "pass",
                                 f"{cons['per_cycle_l']:.3f} L ANR per cycle ({cons['swept_l']:.3f} swept + {cons['dead_l']:.3f} dead volume at {p_cyl:g} bar)"
                                 + (f"; {cons['per_minute_l']:.1f} L/min at {cyl.cycles_per_min:g} cycles/min" if cyl.cycles_per_min else "; no cycle rate given"), "", [cyl.name]))
        infos.append(info)

    sinfos: list[SupplyInfo] = []
    for sup in c.supplies:
        d = demand.get(sup.name, 0.0)
        if sup.flow_l_min is None:
            status = "warn"
            checks.append(_check("air", f"supply {sup.name} capacity", "warn", f"{d:.1f} L/min ANR drawn; the supply declares no capacity (flow_l_min), so it is not graded", "flow_l_min=", [sup.name]))
        else:
            frac = d / sup.flow_l_min
            status = "fail" if frac > 1 else "warn" if frac > SUPPLY_WARN else "pass"
            checks.append(_check("air", f"supply {sup.name} capacity", status, f"{d:.1f} L/min ANR drawn of {sup.flow_l_min:g} L/min ({frac:.0%})", f"<= {sup.flow_l_min:g} L/min" + (" (within 20%)" if status == "warn" else ""), [sup.name]))
        sinfos.append(SupplyInfo(sup.name, sup.pressure_bar, sup.flow_l_min, round(d, 2), status))

    # valves: ports connected as the class needs
    for v in [x for x in c.components.values() if isinstance(x, Valve)]:
        bad = []
        for port in v.ports:
            ref = v.port(port)
            has = bool(c.connections_at(ref)) or ref in c.vents
            if port == "P" and not has:
                bad.append("P (supply) is unconnected")
            elif port in ("A", "B") and not has:
                bad.append(f"work port {port} is unconnected (it would blow air)")
        if bad:
            checks.append(_check("circuit", f"valve {v.name} ports", "fail", "; ".join(bad), "P supplied, every work port connected", [v.name]))
        else:
            checks.append(_check("circuit", f"valve {v.name} ports", "pass", f"{v.cls} {v.actuation}/{v.returns}: P, {', '.join(p for p in v.ports if p in ('A', 'B'))} connected" + (", exhausts " + ", ".join(p for p in v.ports if p in EXHAUST_PORTS) if any(p in EXHAUST_PORTS for p in v.ports) else ""), "", [v.name]))
    # dead-headed lines
    dead = []
    for comp in c.components.values():
        if isinstance(comp, Node) and len(c.connections_at(comp.port("N"))) < 2:
            dead.append(f"node {comp.name} has {len(c.connections_at(comp.port('N')))} connection(s)")
        if isinstance(comp, (Regulator, FlowControl)):
            conn = [p for p in comp.ports if c.connections_at(comp.port(p))]
            if len(conn) == 1:
                dead.append(f"{comp.name} is connected only at {conn[0]}")
    if dead:
        checks.append(_check("circuit", "no dead-headed lines", "fail", "; ".join(dead), "every line leads somewhere", [d.split()[1] if d.startswith('node') else d.split()[0] for d in dead]))
    elif c.tubes:
        checks.append(_check("circuit", "no dead-headed lines", "pass", f"{len(c.tubes)} tube(s), every line leads somewhere"))
    if estimated:
        checks.append(_check("flow data", "flow data estimated", "warn",
                             "no C, b from a datasheet for " + ", ".join(f"{k} ({v})" for k, v in list(estimated.items())[:8]) + ("..." if len(estimated) > 8 else "") + ": graded with conservative estimates",
                             "C= and b= from the datasheet on every component and tube", list(estimated)))
    return checks, infos, sinfos


def _nets(c: Circuit):
    """The pneumatic lines: ports joined by tubes, directly or through a tee / manifold node (not through a valve, a flow
    control or any other component, which separate lines). Returns a function port -> its line's representative."""
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for t in c.tubes:
        ra, rb = find(t.a), find(t.b)
        if ra != rb:
            parent[ra] = rb
    return find
