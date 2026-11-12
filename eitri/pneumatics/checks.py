"""The pneumatics checks: what every build is graded on (all closed-form, see eitri/pneumatics/flow.py for the formulas).

    circuit       every cylinder port fed from a supply through a valve in some position, and able to exhaust in another; valve
                  ports connected as their class needs; no dead-headed lines (a tee or an inline component with one connection)
    force         usable cylinder force (pressure at the cylinder x area x (1 - friction allowance)) against the load for each
                  stroke: FAIL below the load, WARN when the load ratio (load / theoretical force) exceeds SMC's limit for the
                  cylinder's duty (moving 0.5, static 0.7, guided 1.0: model.LOAD_RATIOS); a single-acting return is its spring
    air           free-air consumption per cycle and per minute (swept volumes plus the dead volume of the lines between valve
                  and cylinder), summed against the supply's capacity (FAIL above, WARN above 80 %)
    speed         ISO 6358 flow available through the fill path (supply -> regulator -> valve -> tube) and the exhaust path
                  (cylinder -> valve -> silencer), against the flow the required speed needs; the achievable speed is the lesser
    pressure drop the drop across every tube of a fill path at the required flow: WARN above 0.3 bar or 5 % of the pressure, FAIL
                  when the flow cannot pass at all (choked)
    flow data     one warning naming every component or tube graded with an estimated C, b (conservative defaults)
    catalogue     from the parts' datasheets (part= or the same figures given by hand): minimum operating pressure of cylinders and
                  valves, the piston speed range, the end-of-stroke kinetic energy against the cushioning's allowable energy, the
                  coil voltage against the control voltage, and each position sensor's cylinder (magnetic piston, series) and voltage

Nothing here raises on a bad design: a bad design is a failed check.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..checks import Check, check as _check, level, switched_off
from .flow import DEFAULT_C, consumption_per_cycle_l, cylinder_forces, drop_for_flow, flow_l_s, required_flow_l_s, series, tube_data
from .model import Manifold, EXHAUST_PORTS, LOAD_RATIOS, pressure_rating, P_ATM, Circuit, Cylinder, FlowControl, Node, PortRef, Regulator, Silencer, Supply, Tube, Valve

# The speed at the end of the stroke is higher than the mean speed (stroke / stroke time) a speed controller is set to. SMC Shock
# Absorber Series RB catalogue (https://static.smc.eu/pdf/RB_EU.pdf, p.5 Model Selection, Note 1, fetched 2026-11-12): "The collision
# speed is v = 2va when the speed (average speed va) is calculated from the air cylinder's stroke time." SMC's MY2 rodless cylinder
# manual uses 1.4 x average for its stopper check (seen in a search result only, not opened): the higher, 2, is used.
IMPACT_FACTOR = 2.0
IMPACT_SOURCE = "SMC RB_EU.pdf p.5 Note 1: collision speed = 2 x average speed; E = 1/2 m v^2"
LOAD_RATIO_WARN = LOAD_RATIOS["moving"]   # SMC: dynamic operation, the load moved vertically or horizontally (model.LOAD_RATIOS)
SUPPLY_WARN = 0.8
DROP_WARN_BAR = 0.3


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
        # SMC AS catalogue (AS_EU.pdf): one effective area for "Controlled (Free) flow" with the needle fully open, so the
        # bypass direction passes no more than the throttle's own C (was a flat 3.0, more than any M5 to 1/4" AS passes)
        c, b = (comp.C, comp.b) if comp.has_flow_data else DEFAULT_C["flow_control"]
        return Step("component", comp.name, c, b, not comp.has_flow_data, f"free flow {from_port}->{to_port} (bypass check)")
    if isinstance(comp, Valve):
        cb = comp.flow_data(from_port, to_port)
        c, b = cb if cb is not None else DEFAULT_C["valve"]
        return Step("component", comp.name, c, b, cb is None, f"{from_port}->{to_port}")
    if isinstance(comp, (Regulator, Silencer)):
        c, b = (comp.C, comp.b) if comp.has_flow_data else DEFAULT_C[comp.kind]
        return Step("component", comp.name, c, b, not comp.has_flow_data, f"{from_port}->{to_port}")
    return None


def find_path(c: Circuit, start: PortRef, goal: str, positions: dict[str, int] | None = None) -> PathInfo:
    """DFS from a cylinder port outward to a supply ("supply") or to the atmosphere ("exhaust"), using one position per valve.
    Steps come back in flow order (supply -> cylinder for a supply path, cylinder -> air for an exhaust path). `positions`
    fixes some valves' positions (valve name -> position index), as after a loss of power or air (failsafe.py)."""
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
        if isinstance(comp, (Node, Manifold)):                   # a tee, or a manifold's gallery: on to its other connections
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

    r = _leave(c, start, {start}, dict(positions or {}), [], 0, dfs)
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

def _pressure_ratings(c: Circuit) -> list[Check]:
    """Each pressure source (a supply at its pressure, a regulator at its set pressure) against the lowest-rated component it reaches
    before the next regulator: through tubes, nodes, flow controls and every valve passage, never into an exhaust port (R, S)."""
    out: list[Check] = []
    sources = [(s, s.pressure_bar) for s in c.supplies] + [(r, r.set_bar) for r in c.components.values() if r.kind in ("regulator", "frl")]
    for src, bar in sources:
        seen_ports, reached, tubes = set(), {}, {}
        todo = [src.port("OUT")]
        while todo:
            ref = todo.pop()
            if ref in seen_ports:
                continue
            seen_ports.add(ref)
            for t in c._tubes_at(ref):
                tubes[t.name] = t
            nxt = [t.b if t.a == ref else t.a for t in c._tubes_at(ref)]
            if ref in c.attached:
                nxt.append(c.attached[ref])
            nxt += [o for k, _t, o in c.connections_at(ref) if k == "manifold"]
            comp = c.components[ref.component]
            if comp is not src:
                reached[comp.name] = comp
                if comp.kind not in ("regulator", "frl"):            # a regulator ends this source's reach: it sets its own
                    for a, b in comp.passages(None):
                        for x, y in ((a, b), (b, a)):
                            if x == ref.port and y not in EXHAUST_PORTS:
                                nxt.append(comp.port(y))
            todo += [n for n in nxt if n not in seen_ports and not (c.components[n.component].kind == "valve" and n.port in EXHAUST_PORTS)]
        rated = [(pressure_rating(comp), comp) for comp in reached.values()]
        rated = [((t.max_bar, "as given" if not t.part_number else f"{t.part_number} datasheet"), t) for t in tubes.values() if t.max_bar is not None] + rated
        rated = sorted(((r, why, comp) for (r, why), comp in rated if r is not None), key=lambda t: t[0])
        if not rated:
            continue
        name = f"pressure ratings downstream of {src.name}"
        low, why, comp = rated[0]
        over = [(r, w, k) for r, w, k in rated if bar > r + 1e-9]
        if over:
            others = "; also over: " + ", ".join(f"{k.name} ({r:g} bar)" for r, _w, k in over[1:]) if len(over) > 1 else ""
            out.append(_check("pressure", name, "fail",
                              f"{bar:g} bar from {src.name} exceeds {comp.name}'s {low:g} bar ({why}){others}",
                              f"<= {low:g} bar: a regulator set at or below {low:g} bar ahead of {comp.name}, or parts rated for {bar:g} bar (max_bar= from their datasheets)",
                              [src.name] + [k.name for _r, _w, k in over]))
        else:
            out.append(_check("pressure", name, "pass", f"{bar:g} bar from {src.name}; the lowest rating it reaches is {comp.name}'s {low:g} bar ({why})",
                              f"<= {low:g} bar", [src.name, comp.name]))
    return out


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
                        and v.port(e) not in c.attached and v.port(e) not in c.vents and not c._tubes_at(v.port(e)) and v.name not in c.mounts]
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
                    checks.append(switched_off(_check("force", f"force {cyl.name} retract", "warn", f"single-acting: the return spring's force is not given, so the retract stroke is not graded (load {load:g} N)", "spring_N=", [cyl.name]),
                                               f"{cyl.name}.spring_N"))
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
                lim = LOAD_RATIOS[cyl.duty]
                sev = "fail" if load > us else "warn" if si.load_ratio is not None and si.load_ratio > lim else "pass"
                si.status = sev
                area = fill_area
                checks.append(_check("force", f"force {cyl.name} {stroke}", sev,
                                     f"{us:.0f} N usable ({th:.0f} N theoretical: {p_cyl:g} bar x {area:.0f} mm², less {cyl.friction_fraction:.0%} friction allowance) vs {load:g} N load, load ratio {si.load_ratio:.2f}",
                                     (f"load <= {us:.0f} N usable, load ratio <= {lim:g} for a {cyl.duty} load (duty={cyl.duty!r}; SMC: moving 0.5, "
                                      "static/clamping 0.7, guided 1.0)") if load else "", [cyl.name]))
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
            checks.append(switched_off(_check("air", f"supply {sup.name} capacity", "warn", f"{d:.1f} L/min ANR drawn; the supply declares no capacity (flow_l_min), so it is not graded", "flow_l_min=", [sup.name]),
                                       f"{sup.name}.flow_l_min"))
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
    checks += _pressure_ratings(c)
    checks += _catalog_checks(c, infos)
    checks += _failsafe_checks(c)
    checks += _manifold_checks(c, infos)
    checks += _sequence_checks(c, infos)
    if estimated:
        checks.append(_check("flow data", "flow data estimated", "warn",
                             "no C, b from a datasheet for " + ", ".join(f"{k} ({v})" for k, v in list(estimated.items())[:8]) + ("..." if len(estimated) > 8 else "") + ": graded with conservative estimates",
                             "C= and b= from the datasheet on every component and tube", list(estimated)))
    return checks, infos, sinfos


def _throttled(path: PathInfo | None) -> bool:
    return bool(path and path.found and any(s.kind == "component" and "throttle" in s.note for s in path.steps))


def _catalog_checks(c: Circuit, infos: list[CylinderInfo]) -> list[Check]:
    """Checks on figures a part's datasheet gives (catalogue parts, or the same fields given by hand); a check whose figure is not
    given does not run."""
    out: list[Check] = []
    by_name = {i.name: i for i in infos}
    for cyl in c.cylinders:
        info = by_name.get(cyl.name)
        p = info.pressure_bar if info else None
        src = f" [{cyl.part_number} datasheet]" if cyl.part_number else ""
        figs = (cyl.catalog or {}).get("figures") or {}
        for key, stroke in (("force_extend_N", "extend"), ("force_retract_N", "retract")):
            if key not in figs:
                continue
            want, at_bar = figs[key]
            got = cylinder_forces(cyl, at_bar)[f"{stroke}_theoretical_N"]
            sev = "pass" if abs(got - want) <= 0.01 * want + 0.5 else "fail"
            out.append(_check("force", f"datasheet force {cyl.name} {stroke}", sev,
                              f"eitri computes {got:.1f} N at {at_bar:g} bar; the {cyl.part_number} datasheet prints {want:g} N",
                              "within 1 % (the bore, rod and force formula agree with the catalogue)", [cyl.name]))
        if cyl.min_bar is not None and p is not None:
            sev = "fail" if p < cyl.min_bar else "pass"
            out.append(_check("pressure", f"minimum pressure {cyl.name}", sev, f"{p:g} bar at the cylinder; it needs at least {cyl.min_bar:g} bar to move{src}",
                              f">= {cyl.min_bar:g} bar", [cyl.name]))
        for si in (info.strokes if info else []):
            v_req = si.speed_required
            if v_req is not None and (cyl.speed_max_mm_s is not None or cyl.speed_min_mm_s is not None):
                lo, hi = cyl.speed_min_mm_s, cyl.speed_max_mm_s
                if hi is not None and v_req > hi:
                    sev, why = "fail", f"above the {hi:g} mm/s the cylinder is rated for"
                elif lo is not None and v_req < lo:
                    sev, why = "warn", f"below the {lo:g} mm/s minimum (stick-slip: a low-speed cylinder runs slower)"
                else:
                    sev, why = "pass", "within the catalogue piston speed range"
                out.append(_check("speed", f"piston speed range {cyl.name} {si.name}", sev, f"{v_req:g} mm/s: {why}{src}",
                                  f"{lo if lo is not None else 0:g}-{hi if hi is not None else float('inf'):g} mm/s", [cyl.name]))
            if cyl.allowable_ke_J is not None:
                name = f"end-of-stroke energy {cyl.name} {si.name}"
                if cyl.load_mass_kg is None:
                    out.append(switched_off(_check("cushioning", name, "warn", f"the moving load's mass is not given, so the kinetic energy at the end of "
                                                   f"the stroke is not graded against the cushioning's {cyl.allowable_ke_J:g} J{src}", "load_mass_kg=", [cyl.name]),
                                            f"{cyl.name}.load_mass_kg"))
                    continue
                throttled = _throttled(si.exhaust) or _throttled(si.fill)
                if v_req is not None and throttled:
                    v, how = v_req, "the required speed (a speed controller on the stroke is set to it)"
                elif si.speed_available is not None:
                    v, how = max(si.speed_available, v_req or 0.0), "what the open paths pass (no speed controller limits this stroke)"
                elif v_req is not None:
                    v, how = v_req, "the required speed"
                else:
                    out.append(switched_off(_check("cushioning", name, "warn", "no speed is given for the stroke, so its kinetic energy is not graded",
                                                   "speed_mm_s=", [cyl.name]), f"{cyl.name}.speed_mm_s"))
                    continue
                m = cyl.load_mass_kg + (cyl.piston_mass_kg or 0.0)
                v_hit = IMPACT_FACTOR * v
                e = 0.5 * m * (v_hit / 1000.0) ** 2
                sev = level(e, cyl.allowable_ke_J, 0.8)
                out.append(_check("cushioning", name, sev,
                                  f"{e:.3f} J = 1/2 x {m:.3f} kg x ({v_hit:.0f} mm/s)^2: impact speed {IMPACT_FACTOR:g} x the mean speed ({v:.0f} mm/s, {how}); "
                                  f"the cushioning absorbs {cyl.allowable_ke_J:g} J{src} [{IMPACT_SOURCE}]",
                                  f"<= {cyl.allowable_ke_J:g} J (a slower stroke, a speed controller, a bigger bore or an external stop)", [cyl.name]))
    for r in [x for x in c.components.values() if isinstance(x, Regulator) and (x.set_min_bar is not None or x.set_max_bar is not None)]:
        lo, hi = r.set_min_bar if r.set_min_bar is not None else 0.0, r.set_max_bar if r.set_max_bar is not None else float("inf")
        sev = "pass" if lo <= r.set_bar <= hi else "fail"
        out.append(_check("pressure", f"regulator {r.name} setting", sev,
                          f"set to {r.set_bar:g} bar; it regulates {lo:g}-{hi:g} bar" + (f" [{r.part_number} datasheet]" if r.part_number else ""),
                          f"{lo:g}-{hi:g} bar (a unit with a wider regulation range)", [r.name]))
    for v in [x for x in c.components.values() if isinstance(x, Valve)]:
        src = f" [{v.part_number} datasheet]" if v.part_number else ""
        if v.min_bar is not None:
            path = find_path(c, v.port("P"), "supply")
            if path.found and path.pressure_bar is not None:
                sev = "fail" if path.pressure_bar < v.min_bar else "pass"
                out.append(_check("pressure", f"minimum pressure {v.name}", sev, f"{path.pressure_bar:g} bar at P; the internal pilot needs at least {v.min_bar:g} bar to shift{src}",
                                  f">= {v.min_bar:g} bar", [v.name]))
        if v.coils and v.coil_V is not None and c.control_V is not None:
            sev = "pass" if abs(v.coil_V - c.control_V) < 1e-6 else "fail"
            out.append(_check("electrical", f"coil voltage {v.name}", sev, f"{v.coil_V:g} V DC coil on the {c.control_V:g} V DC control supply{src}",
                              f"{c.control_V:g} V coil", [v.name]))
    for s in c.sensors.values():
        cyl = c.components.get(s.cylinder)
        src = f" [{s.part_number} datasheet]" if s.part_number else ""
        if isinstance(cyl, Cylinder):
            if cyl.magnet is False:
                out.append(_check("sensors", f"sensor {s.name} target", "fail", f"{cyl.name} ({cyl.part_number or 'cylinder'}) has no magnetic piston: {s.name} cannot see it",
                                  "a cylinder with a built-in magnet (SMC CDQ2..., Festo ...-A)", [cyl.name]))
            elif cyl.magnet is None:
                out.append(_check("sensors", f"sensor {s.name} target", "warn", f"{cyl.name} does not say whether its piston is magnetic", "magnet=True from the datasheet", [cyl.name]))
            else:
                ok = not s.fits or any(f.upper() in (cyl.part_number or "").upper() for f in s.fits)
                out.append(_check("sensors", f"sensor {s.name} target", "pass" if ok else "warn",
                                  f"{s.part_number or s.name} on {cyl.name} ({cyl.part_number or 'magnetic piston'}), {s.position}" + ("" if ok else f"; the sensor is listed for {', '.join(s.fits)} cylinders: check its mounting"),
                                  "", [cyl.name]))
        if c.control_V is not None and (s.V_min is not None or s.V_max is not None):
            lo, hi = s.V_min or 0.0, s.V_max if s.V_max is not None else float("inf")
            sev = "pass" if lo <= c.control_V <= hi else "fail"
            out.append(_check("electrical", f"sensor {s.name} voltage", sev, f"{c.control_V:g} V DC control supply; the sensor runs on {lo:g}-{hi:g} V{src}",
                              f"{lo:g}-{hi:g} V", [s.cylinder]))
    return out


def simultaneous_groups(c: Circuit) -> tuple[list[set[str]], str]:
    """Which cylinders' strokes may draw air at the same time: from the declared sequence (one group per step), else all of them
    (the conservative assumption)."""
    seq = getattr(c, "sequence", None)
    if seq:
        return [set(st.cylinders()) for st in seq], "per step of the declared sequence"
    return [{cy.name for cy in c.cylinders}], "all cylinders at once (no sequence declared: c.step(...))"


def _manifold_checks(c: Circuit, infos: list[CylinderInfo]) -> list[Check]:
    """Each manifold: valves vs stations (empty stations take blanking plates), and the common supply against the air the valves on
    it draw at the same time."""
    out: list[Check] = []
    by = {i.name: i for i in infos}
    for m in [x for x in c.components.values() if isinstance(x, Manifold)]:
        on = sorted((st, v) for v, (mn, st) in c.mounts.items() if mn == m.name)
        src = f" [{m.part_number} catalogue]" if m.part_number else ""
        name = f"manifold {m.name} stations"
        bad = [f"station {st} ({v})" for st, v in on if st > m.stations]
        if len(on) > m.stations or bad:
            out.append(_check("manifold", name, "fail", f"{len(on)} valve(s) on a {m.stations}-station base" + (f"; {', '.join(bad)} beyond its stations" if bad else ""),
                              f"<= {m.stations} valves (a base with more stations)", [m.name] + [v for _s, v in on]))
        else:
            empty = m.stations - len(on)
            sev = "warn" if (m.both_sides_above and m.stations > m.both_sides_above) else "pass"
            out.append(_check("manifold", name, sev, f"{len(on)} valve(s) on {m.stations} stations" + (f"; {empty} empty station(s) take a blanking plate"
                              + (f" ({m.blanking_plate}, in the BOM)" if m.blanking_plate else "") if empty else "")
                              + (f"; above {m.both_sides_above} stations the catalogue asks for supply and exhaust on both sides{src}" if sev == "warn" else ""),
                              f"<= {m.stations} valves", [m.name] + [v for _s, v in on]))
        # the common supply against the simultaneous demand
        mounted = {v for _s, v in on}
        demand_by_cyl: dict[str, float] = {}
        for cyl in c.cylinders:
            info = by.get(cyl.name)
            if not info:
                continue
            for si in info.strokes:
                if si.flow_required_l_s and si.fill is not None and si.fill.found and mounted & set(si.fill.names):
                    demand_by_cyl[cyl.name] = max(demand_by_cyl.get(cyl.name, 0.0), si.flow_required_l_s)
        if not demand_by_cyl:
            continue
        groups, how = simultaneous_groups(c)
        q, worst = max(((sum(demand_by_cyl.get(n, 0.0) for n in g), g) for g in groups), key=lambda t: t[0])
        path = find_path(c, m.port("P"), "supply")
        cname = f"manifold {m.name} common supply"
        if not path.found or path.pressure_bar is None:
            out.append(_check("manifold", cname, "fail", f"{m.name}.P is not fed from a supply", "a tube from the air preparation to the manifold's P", [m.name]))
            continue
        p1 = path.pressure_bar + P_ATM
        dp = drop_for_flow(path.C_eff, path.b_eff, p1, q) if q > 0 else 0.0
        users = ", ".join(sorted(n for n in worst if n in demand_by_cyl)) or "none"
        est = " (estimated C on: " + ", ".join(s.name for s in path.steps if s.estimated) + ")" if any(s.estimated for s in path.steps) else ""
        if dp is None:
            sev, meas = "fail", f"{q:.2f} L/s ANR drawn at once ({users}; {how}) is more than the supply path to {m.name} passes choked ({path.C_eff * p1:.2f} L/s, C_eff {path.C_eff:.2f}){est}"
        else:
            sev = "warn" if dp > max(DROP_WARN_BAR, 0.05 * path.pressure_bar) else "pass"
            meas = f"{q:.2f} L/s ANR drawn at once ({users}; {how}) drops {dp:.2f} bar from {' -> '.join(path.names)} (C_eff {path.C_eff:.2f}){est}"
        out.append(_check("manifold", cname, sev, meas, f"<= {DROP_WARN_BAR:g} bar drop at the simultaneous demand", [m.name] + path.names))
    return out


def _sequence_checks(c: Circuit, infos: list[CylinderInfo]) -> list[Check]:
    """The declared step sequence (sequence.py): every step ends on a sensor that exists at the end its move reaches; no step commands
    a state its valves cannot give; the cycle closes; the ESTIMATED cycle time against the cylinders' cycles_per_min. A circuit run by
    a controller (a control voltage or position sensors) with no sequence gets one not-run notice."""
    from . import sequence as seq
    out: list[Check] = []
    p = seq.plan(c, infos)
    if p is None:
        if c.control_V is not None or c.sensors:
            out.append(switched_off(_check("sequence", "sequence declared", "warn",
                                           "the circuit is controller-run (control voltage or sensors) but no step sequence is declared, so the steps' "
                                           "sensors, valve commands and cycle time are not graded", "c.step(name, 'C1+', until='B2') per step", []),
                                    "c.step"))
        return out
    for sp in p.steps:
        k, st = sp.index, sp.step
        refs = [m.cylinder for m in sp.moves] + [m.valve for m in sp.moves if m.valve]
        sev = "fail" if any(e.severity == "fail" for e in sp.ends) else "warn" if any(e.severity == "warn" for e in sp.ends) else "pass"
        what = "; ".join(e.why for e in sp.ends) or (f"ends after its {st.dwell_s:g} s dwell" if st.dwell_s else "")
        out.append(_check("sequence", f"step {k} {st.name} ends on a sensor", sev, what,
                          "every step with a move ends on a sensor that exists, at the end the move reaches", refs + list(st.until)))
        if sp.moves:
            bad = [m for m in sp.moves if m.problem]
            idle = [m for m in sp.moves if m.already and not m.problem]
            sev = "fail" if bad else "warn" if idle else "pass"
            text = "; ".join([m.problem for m in bad] + [f"{m.cylinder} is {'extended' if m.motion == 'extend' else 'retracted'} already: the move does nothing"
                                                          for m in idle] + [f"{m.cylinder}{'+' if m.motion == 'extend' else '-'}: {m.command}" for m in sp.moves if m.command])
            out.append(_check("sequence", f"step {k} {st.name} valve commands", sev, text,
                              "every move is a position its valve can be switched to by the controller (a solenoid valve; one position per valve per step)", refs))
    if p.end != p.start:
        diff = ", ".join(f"{k} ends {p.end[k]} (starts {p.start[k]})" for k in p.start if p.end.get(k) != p.start[k])
        out.append(_check("sequence", "sequence returns to its start", "warn", f"{diff}: the next cycle does not start where this one did",
                          "every cylinder back at its start position after the last step", [k for k in p.start if p.end.get(k) != p.start[k]]))
    else:
        out.append(_check("sequence", "sequence returns to its start", "pass", f"{len(p.steps)} steps; every cylinder ends where it started", ""))
    rates = [cy.cycles_per_min for cy in c.cylinders if cy.cycles_per_min]
    if p.cycle_s is None:
        out.append(switched_off(_check("cycle time", "cycle time estimate", "warn", "the cycle time cannot be estimated: " + "; ".join(p.unknown),
                                       "speed_mm_s= on the moves, response_ms= on the valves", [m.split(":")[0] for m in p.unknown]),
                                *[u.split(" (")[0] for u in p.unknown]))
    elif not rates:
        out.append(switched_off(_check("cycle time", "cycle time estimate", "warn",
                                       f"ESTIMATE {p.cycle_s:.2f} s per cycle; no cylinder gives cycles_per_min, so it is not graded", "cycles_per_min=", []),
                                "cycles_per_min"))
    else:
        target = 60.0 / max(rates)
        sev = level(p.cycle_s, target, 0.8)
        out.append(_check("cycle time", "cycle time estimate", sev,
                          f"ESTIMATE {p.cycle_s:.2f} s per cycle (a lower bound: " + "; ".join(seq.ASSUMPTIONS[1:3]) + f") against {target:.2f} s for "
                          f"{max(rates):g} cycles/min", f"<= {target:.2f} s (within 80 % passes; the estimate leaves out pressure build-up)",
                          [cy.name for cy in c.cylinders]))
    return out


def _failsafe_checks(c: Circuit) -> list[Check]:
    """Each cylinder's state after a loss of electrical power and after a loss of air against what the design declares."""
    from . import failsafe
    out: list[Check] = []
    for cyl in c.cylinders:
        for event, attr, lost in (("power", "on_power_loss", "electrical power"), ("air", "on_air_loss", "air")):
            label = f"loss of {lost}"
            name = f"on loss of {lost} {cyl.name}"
            req = getattr(cyl, attr)
            got, how = failsafe.outcome(c, cyl, event)
            if req is None:
                out.append(switched_off(_check("fail-safe", name, "warn",
                                               f"what {cyl.name} must do on a {label} is not declared, so the valves' behaviour is not graded "
                                               f"(they give: {got}; {how})", f"{attr}= hold | extend | retract | release", [cyl.name]),
                                        f"{cyl.name}.{attr}"))
                continue
            sev = failsafe.grade(req, got)
            note = (" (air trapped behind a closed centre: SMC says it may not hold for an extended period; add pilot check valves "
                    "or a mechanical lock for a guaranteed hold)") if got == "trapped" else ""
            out.append(_check("fail-safe", name, sev, f"{cyl.name} must {req}; its valves give: {got} ({how}){note} [{failsafe.SOURCE}]",
                              f"{req}: a valve whose de-energised / no-air state gives it (5/2 spring return: its rest position; 5/2 double "
                              "solenoid: holds on power loss; 5/3: its centre)", [cyl.name] + [v.name for v in failsafe._valves_of(c, cyl)]))
    return out


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
