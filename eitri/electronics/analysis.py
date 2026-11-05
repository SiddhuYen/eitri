"""The DC kernel: what every net is (a rail at a voltage, a ground, a pin a microcontroller drives, a transistor's switched low
side, or a plain signal), the two-terminal parts between them, the current through each at its worst, and what every rail
supplies.

Two solvers, one element model (an LED or diode: a fixed forward voltage; a resistor or relay coil: a resistance; a switch or
closed contacts: no resistance; a motor or solenoid: its rated current):
- a series string between two fixed nets (a pin, a resistor and an LED to ground) is solved in closed form:
  I = (Va - Vb - the forward voltages) / (the resistances), or a coil load's own rated current;
- where three or more parts meet at a node that is not a rail (two LEDs sharing one resistor, a divider feeding two loads),
  the connected network is solved by nodal analysis (eitri.electronics.nodal: MNA with an ideal-diode iteration). The pieces of
  it between nodes are reported as strings too, so every check reads one list.
Both give the same answer on a series string (tests/test_electronics_nodal.py checks it).

Conventions (each a conservative choice, stated where it is used):
- currents are taken with a GPIO at either level (the worst case of each), switches closed, transistors on;
- a driven pin high sits at its full supply voltage (the most current a resistor string can draw);
- logic thresholds use the datasheet's guaranteed VOH min and VIH min (the least margin);
- LEDs use typical Vf (a lower Vf means more current). LEDs that share a node are also solved with each one at typical Vf and
  the others at their maximum: the one with the lowest Vf takes the shared current (current hogging), so each is graded on that;
- a servo or module draws its catalog current (a servo: its stall current) on its supply pin.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .model import Circuit, Part, PinRef
from .nodal import Elem, solve as nodal_solve

CONDUCTING = {"resistor": (("1", "2"),), "led": (("A", "K"),), "diode": (("A", "K"),), "switch": (("1", "2"),),
              "relay": (("COIL1", "COIL2"), ("COM", "NO")), "motor": (("+", "-"),), "solenoid": (("+", "-"),)}   # a relay's contacts: energized


def conducts(kind: str, pin: str) -> bool:
    return any(pin in pair for pair in CONDUCTING.get(kind, ()))
INF = float("inf")


@dataclass
class NetInfo:
    name: str
    kind: str                                   # rail | gnd | driven | switched | base | signal | short
    volts: float | None = None                  # a rail's voltage
    sources: list[PinRef] = field(default_factory=list)     # the power_out pins on it
    states: tuple[float, ...] = ()              # the voltages it can be at as a string's end (a GPIO: 0 and VDD)
    drivers: list[PinRef] = field(default_factory=list)     # GPIO / output pins that drive it
    conducting: list[PinRef] = field(default_factory=list)  # pins of two-terminal parts on it

    @property
    def fixed(self) -> bool:
        return bool(self.states)


@dataclass
class Element:
    part: str
    kind: str
    enter: str                                  # the pin the walk enters by
    leave: str


@dataclass
class String:
    """Two-terminal parts in series between two fixed nets."""
    a: str                                      # end nets
    b: str
    elements: list[Element]
    inner: list[str]                            # the nets between them
    current: float = 0.0                        # the worst case (A); INF: nothing limits it
    flow: tuple[str, str] = ("", "")            # (from net, to net) in that case
    va: float = 0.0
    vb: float = 0.0
    node_v: dict[str, float] = field(default_factory=dict)  # voltage of every inner net in that case
    combos: list[tuple[float, float, float, dict]] = field(default_factory=list)   # (va, vb, I, node voltages) for every end state
    network: int | None = None                  # the index of the branched network it belongs to (solved by nodal analysis), or None
    typical: float | None = None                # a network's piece: its worst current with every LED at typical Vf

    @property
    def parts(self) -> list[str]:
        """The parts in the order the current flows through them."""
        els = self.elements if self.flow[0] in ("", self.a) else self.elements[::-1]
        return [e.part for e in els]

    def describe(self) -> str:
        return " -> ".join([self.flow[0] or self.a] + self.parts + [self.flow[1] or self.b])


@dataclass
class Analysis:
    nets: dict[str, NetInfo]
    strings: list[String]
    branches: dict[str, list[str]]              # a branch net -> the parts conducting into it, where no solution was found
    vdd: dict[str, float | None]                # every part's supply voltage (a module: its VCC rail; a board: its logic level)
    networks: list[dict] = field(default_factory=list)   # the branched networks solved by nodal analysis: {"nodes": {net: parts}, ...}

    def through(self, part: str) -> list[String]:
        return [s for s in self.strings if part in s.parts]

    def part_current(self, part: str) -> float:
        return max((s.current for s in self.through(part)), default=0.0)

    def at_net(self, net: str) -> list[String]:
        return [s for s in self.strings if net in (s.a, s.b)]

    def voltages_at(self, net: str) -> list[tuple[float, String, tuple]]:
        """Every voltage a net that is not fixed was solved at: (volts, the string that says so, its case (va, vb, I, node))."""
        out = []
        for s in self.strings:
            for combo in s.combos:
                va, vb, _i, node = combo
                if net in node:
                    out.append((node[net], s, combo))
                elif s.network is not None and net in (s.a, s.b) and not self.nets[net].fixed:
                    out.append((va if s.a == net else vb, s, combo))
        return out


# --------------------------------------------------------------------------- the nets


def _board_vdd(p: Part) -> float | None:
    return p.spec.get("vdd")


def classify(c: Circuit) -> dict[str, NetInfo]:
    out: dict[str, NetInfo] = {}
    for name, net in c.nets.items():
        info = NetInfo(name, "signal")
        gnd = False
        for ref in net.pins:
            part, ps = c.parts[ref.part], c.pin_spec(ref)
            if ps.kind == "power_out":
                info.sources.append(ref)
            elif ps.kind == "gnd":
                gnd = True
            if conducts(part.kind, ref.pin):
                info.conducting.append(ref)
        if info.sources and gnd:
            info.kind = "short"
        elif info.sources:
            volts = {round(float(c.pin_spec(s).volts), 3) for s in info.sources}
            info.kind, info.volts = ("rail", max(volts)) if len(volts) == 1 else ("short", max(volts))
            info.states = (info.volts,)
        elif gnd:
            info.kind, info.volts, info.states = "gnd", 0.0, (0.0,)
        out[name] = info
    vdd = supply_voltages(c, out)
    for name, net in c.nets.items():
        info = out[name]
        if info.kind != "signal":
            continue
        levels = set()
        for ref in net.pins:
            part, ps = c.parts[ref.part], c.pin_spec(ref)
            if ps.kind == "io" and part.kind == "board" and part.mode(ref.pin) != "in" and "input_only" not in ps.roles:
                info.drivers.append(ref)
                if vdd.get(part.name) is not None:
                    levels |= {0.0, vdd[part.name]}
            elif ps.kind == "out" and vdd.get(part.name) is not None:
                info.drivers.append(ref)
                levels |= {0.0, vdd[part.name]}
        if info.drivers:
            od = open_drain(c, name)                                    # an I2C line: every pin only pulls low; the pull-ups make the high
            info.kind, info.states = "driven", (0.0,) if od else (tuple(sorted(levels)) or (0.0,))
            continue
        for ref in net.pins:
            part = c.parts[ref.part]
            if part.kind in ("mosfet", "bjt") and ref.pin in ("D", "C") and _emitter_grounded(c, part, out):
                info.kind, info.states = "switched", (0.0,)
                break
            if part.kind == "bjt" and ref.pin == "B" and _emitter_grounded(c, part, out):
                info.kind, info.states = "base", (float(part.spec.get("vbe_on", 0.7)),)
                break
    return out


def open_drain(c: Circuit, net: str) -> bool:
    """An I2C line (or any net with an open-drain pin): its devices only pull it low."""
    return any(c.pin_spec(r).kind == "od" or {"i2c_sda", "i2c_scl"} & set(c.pin_spec(r).roles) and c.parts[r.part].kind != "board"
               for r in c.nets[net].pins)


def _emitter_grounded(c: Circuit, part: Part, nets: dict[str, NetInfo]) -> bool:
    low = "S" if part.kind == "mosfet" else "E"
    n = c._net_of.get(PinRef(part.name, low))
    return bool(n and nets[n].kind == "gnd")


def supply_voltages(c: Circuit, nets: dict[str, NetInfo]) -> dict[str, float | None]:
    """Each part's supply voltage: a board's logic level; a module, servo or load: the rail on its first power_in pin."""
    out: dict[str, float | None] = {}
    for p in c.parts.values():
        if p.kind == "board":
            out[p.name] = _board_vdd(p)
            continue
        v = None
        for n, ps in p.pins.items():
            if ps.kind == "power_in":
                net = c._net_of.get(PinRef(p.name, n))
                if net and nets[net].kind == "rail":
                    v = nets[net].volts
                break
        if p.kind == "regulator":
            v = p.spec.get("vout")
        out[p.name] = v
    return out


# --------------------------------------------------------------------------- the strings


def _other(part: Part, pin: str) -> str:
    for a, b in CONDUCTING[part.kind]:
        if pin in (a, b):
            return b if pin == a else a
    raise KeyError(pin)


def _blocked_diode(c: Circuit, nets: dict[str, NetInfo], r: PinRef, vmax: float) -> bool:
    """A diode at a node that can never conduct out of it: its cathode here and its anode on ground (a flyback diode across a load
    whose low side is ground), or its anode here and its cathode on a rail at the circuit's highest voltage. It is not a branch."""
    p = c.parts[r.part]
    if p.kind != "diode":
        return False
    other = c._net_of.get(PinRef(p.name, "A" if r.pin == "K" else "K"))
    if not other:
        return True
    o = nets[other]
    if r.pin == "K" and o.kind == "gnd":
        return True
    return r.pin == "A" and o.kind == "rail" and o.volts is not None and o.volts >= vmax - 1e-9


def _live(c: Circuit, nets: dict[str, NetInfo], net: str, vmax: float) -> list[PinRef]:
    """The pins on a net that can carry current into or out of it (a diode that can never conduct here does not count)."""
    return [r for r in nets[net].conducting if not _blocked_diode(c, nets, r, vmax)]


def find_strings(c: Circuit, nets: dict[str, NetInfo]) -> tuple[list[String], dict[str, list[str]]]:
    """Every chain of two-terminal parts between terminals: fixed nets, and branch nodes (a net that is not fixed where three or
    more parts can conduct). Returns the strings and the branch nodes (net -> its parts)."""
    strings: list[String] = []
    vmax = max((max(i.states) for i in nets.values() if i.states), default=0.0)
    branches = {n: sorted({r.part for r in i.conducting}) for n, i in nets.items()
                if not i.fixed and len(_live(c, nets, n, vmax)) >= 3}
    seen: set[tuple] = set()
    for start, info in nets.items():
        if not (info.fixed or start in branches):
            continue
        starts = info.conducting if info.fixed else _live(c, nets, start, vmax)
        for ref in starts:
            elements, inner, net, pin, part = [], [], start, ref.pin, c.parts[ref.part]
            visited = {start}
            ok = True
            while True:
                leave = _other(part, pin)
                elements.append(Element(part.name, part.kind, pin, leave))
                nxt = c._net_of.get(PinRef(part.name, leave))
                if nxt is None:                               # an open end: no current
                    ok = False
                    break
                if _blocked_diode(c, nets, PinRef(part.name, leave), vmax) and not nets[nxt].fixed:   # it never conducts into this node
                    ok = False
                    break
                if nets[nxt].fixed or nxt in branches:
                    end = nxt
                    break
                cond = [r for r in _live(c, nets, nxt, vmax) if not (r.part == part.name and r.pin == leave)]
                if len(cond) != 1 or nxt in visited:
                    ok = False
                    break
                visited.add(nxt)
                inner.append(nxt)
                part, pin = c.parts[cond[0].part], cond[0].pin
            if not ok:
                continue
            key = tuple(sorted([(start, tuple(e.part for e in elements)), (end, tuple(e.part for e in reversed(elements)))]))
            if key in seen:
                continue
            seen.add(key)
            strings.append(String(start, end, elements, inner))
    return strings, branches


def _solve(c: Circuit, s: String, va: float, vb: float) -> tuple[float, str, str, dict[str, float]]:
    """The current from the higher end to the lower one, and each inner net's voltage."""
    if va >= vb:
        hi, lo, els, nets_in, vhi = s.a, s.b, s.elements, s.inner, va
    else:
        hi, lo, els, nets_in, vhi = s.b, s.a, [Element(e.part, e.kind, e.leave, e.enter) for e in reversed(s.elements)], s.inner[::-1], vb
    dv = abs(va - vb)
    r_total, vf_total, fixed_i = 0.0, 0.0, []
    for e in els:
        p = c.parts[e.part]
        if e.kind in ("led", "diode"):
            if e.enter != "A":                              # reverse biased: it blocks
                return 0.0, hi, lo, {}
            vf_total += float(p.spec["vf"])
        elif e.kind == "resistor":
            r_total += float(p.spec["ohm"])
        elif e.kind == "relay" and e.enter.startswith("COIL"):
            r_total += float(p.spec["coil_ohm"])
        elif e.kind in ("motor", "solenoid"):
            fixed_i.append(float(p.spec["current_A"]))
    drive = dv - vf_total
    if drive <= 1e-9:
        return 0.0, hi, lo, {}
    if fixed_i:
        i = min(fixed_i)
        if r_total > 0:
            i = min(i, drive / r_total)
    elif r_total > 0:
        i = drive / r_total
    else:
        i = INF
    # inner net voltages, walking down from the high end
    node: dict[str, float] = {}
    v = vhi
    for k, e in enumerate(els[:-1]):
        p = c.parts[e.part]
        if e.kind in ("led", "diode"):
            v -= float(p.spec["vf"])
        elif e.kind == "resistor" and i != INF:
            v -= i * float(p.spec["ohm"])
        elif e.kind == "relay" and e.enter.startswith("COIL") and i != INF:
            v -= i * float(p.spec["coil_ohm"])
        node[nets_in[k]] = round(v, 4)
    return i, hi, lo, node


def elem_of(c: Circuit, e: Element) -> Elem:
    """The nodal-solver element of one part in a string (a relay's coil and its contacts are two elements)."""
    p = c.parts[e.part]
    pair = next(pr for pr in CONDUCTING[p.kind] if e.enter in pr)
    na, nb = c._net_of[PinRef(p.name, pair[0])], c._net_of[PinRef(p.name, pair[1])]
    eid = f"{p.name}:{pair[0]}"
    if e.kind in ("led", "diode"):
        return Elem(eid, "DIODE", na, nb, float(p.spec["vf"]))
    if e.kind == "resistor":
        return Elem(eid, "R", na, nb, float(p.spec["ohm"]))
    if e.kind == "relay" and pair[0] == "COIL1":
        return Elem(eid, "R", na, nb, float(p.spec["coil_ohm"]))
    if e.kind in ("motor", "solenoid"):
        return Elem(eid, "LIMIT", na, nb, float(p.spec["current_A"]))
    return Elem(eid, "WIRE", na, nb)                     # a switch, relay contacts


def _walk_current(c: Circuit, s: String, i: dict[str, float]) -> float:
    """A string's current in its walk direction (a -> b), from the nodal solution of its first element."""
    e = s.elements[0]
    el = elem_of(c, e)
    x = i.get(el.id, 0.0)
    if x == INF or x == -INF:
        return INF
    p = c.parts[e.part]
    first = next(pr for pr in CONDUCTING[p.kind] if e.enter in pr)[0]
    return x if e.enter == first else -x


MAX_SCENARIOS = 2048


def _solve_network(c: Circuit, nets: dict[str, NetInfo], members: list[String]) -> bool:
    """Solve one branched network for every combination of its fixed nets' states (and, with two or more LEDs, each LED at typical
    Vf with the others at maximum); fill in each member string's cases. False when no consistent solution was found."""
    elems = [elem_of(c, e) for s in members for e in s.elements]
    bounds = sorted({n for s in members for n in (s.a, s.b) if nets[n].fixed})
    leds = sorted({e.part for s in members for e in s.elements if e.kind == "led"})
    variants: list[str | None] = [None] + (leds if len(leds) >= 2 else [])
    combos = list(itertools.product(*(nets[n].states for n in bounds)))
    if len(combos) * len(variants) > MAX_SCENARIOS:
        return False
    for s in members:
        s.combos, s.current, s.typical = [], 0.0, 0.0
    best: dict[int, tuple] = {}
    for states in combos:
        fixed = dict(zip(bounds, states))
        for var in variants:
            es = []
            for el in elems:
                part = c.parts[el.id.split(":")[0]]
                if var is not None and part.kind == "led" and part.name != var:
                    el = Elem(el.id, el.kind, el.a, el.b, float(part.spec.get("vf_max", part.spec["vf"])))
                es.append(el)
            sol = nodal_solve(es, fixed)
            if sol is None:
                return False
            for k, s in enumerate(members):
                iw = _walk_current(c, s, sol.i)
                va, vb = sol.v.get(s.a, fixed.get(s.a, 0.0)), sol.v.get(s.b, fixed.get(s.b, 0.0))
                node = {n: round(sol.v[n], 4) for n in s.inner if n in sol.v}
                i = abs(iw)
                s.combos.append((round(va, 4), round(vb, 4), i, node))
                if var is None:
                    s.typical = max(s.typical, i)
                if k not in best or i > best[k][0]:
                    flow = (s.a, s.b) if (iw > 0 or (iw == 0 and va >= vb)) else (s.b, s.a)
                    best[k] = (i, flow, va, vb, node)
    for k, s in enumerate(members):
        s.current, s.flow, s.va, s.vb, s.node_v = best[k][0], best[k][1], round(best[k][2], 4), round(best[k][3], 4), best[k][4]
    return True


def series_by_nodal(c: Circuit, s: String, va: float, vb: float) -> float:
    """A series string's current solved by the nodal solver instead of in closed form (the two must agree; the tests compare)."""
    fixed = {s.a: va, s.b: vb} if s.a != s.b else {s.a: va}
    sol = nodal_solve([elem_of(c, e) for e in s.elements], fixed)
    return abs(_walk_current(c, s, sol.i)) if sol else float("nan")


def analyze(c: Circuit) -> Analysis:
    nets = classify(c)
    strings, branch_nodes = find_strings(c, nets)
    series = [s for s in strings if nets[s.a].fixed and nets[s.b].fixed]
    for s in series:
        best = None
        for va, vb in itertools.product(nets[s.a].states, nets[s.b].states):
            i, hi, lo, node = _solve(c, s, va, vb)
            s.combos.append((va, vb, i, node))
            if best is None or i > best[0]:
                best = (i, hi, lo, va, vb, node)
        if best:
            s.current, s.flow, s.va, s.vb, s.node_v = best[0], (best[1], best[2]), best[3], best[4], best[5]
        s.typical = s.current
    # the branched networks: strings joined at branch nodes
    rest = [s for s in strings if s not in series]
    group: dict[str, int] = {}
    networks: list[list[String]] = []
    for s in rest:
        ends = [n for n in (s.a, s.b) if n in branch_nodes]
        ids = {group[n] for n in ends if n in group}
        if not ids:
            gid = len(networks)
            networks.append([])
        else:
            gid = min(ids)
            for other in sorted(ids - {gid}):          # merge
                for t in networks[other]:
                    networks[gid].append(t)
                networks[other] = []
                for n, g in list(group.items()):
                    if g == other:
                        group[n] = gid
        networks[gid].append(s)
        for n in ends:
            group[n] = gid
    solved_strings, unsolved, info = list(series), {}, []
    for members in networks:
        if not members:
            continue
        nodes = sorted({n for s in members for n in (s.a, s.b) if n in branch_nodes})
        if not any(nets[n].fixed for s in members for n in (s.a, s.b)):
            continue                                    # nothing drives it: no current
        if _solve_network(c, nets, members):
            idx = len(info)
            for s in members:
                s.network = idx
            info.append({"nodes": {n: branch_nodes[n] for n in nodes},
                         "parts": sorted({e.part for s in members for e in s.elements}),
                         "leds": sorted({e.part for s in members for e in s.elements if e.kind == "led"})})
            solved_strings += members
        else:
            for n in nodes:
                unsolved[n] = branch_nodes[n]
    return Analysis(nets, solved_strings, unsolved, supply_voltages(c, nets), info)


def net_high(c: Circuit, an: Analysis, net: str) -> tuple[float | None, list[str]]:
    """The highest voltage a net can sit at, and what puts it there (a driver's supply, a divider's node, a pull-up's rail)."""
    info = an.nets[net]
    if info.kind in ("rail", "short"):
        return info.volts, [f"rail {net}"]
    cands: list[tuple[float, str]] = []
    od = open_drain(c, net)
    for d in ([] if od else info.drivers):
        v = an.vdd.get(d.part)
        if v is not None:
            cands.append((v, f"{d} drives it to {v:g} V"))
    for v, s, _combo in an.voltages_at(net):
        if s.network is not None:
            cands.append((v, f"{v:g} V at the node of {', '.join(s.parts)} (nodal solution)"))
    for s in an.strings:
        if net not in s.inner or s.network is not None:
            continue
        for va, vb, i, node in s.combos:
            if net in node:
                cands.append((node[net], f"{node[net]:g} V through {', '.join(s.parts)}"))
            elif i == 0:                                  # no current: the node sits at the string's higher end
                hi = max(va, vb)
                cands.append((hi, f"{hi:g} V through {', '.join(s.parts)} (no current)"))
    reads = [] if od else [r for r in c.nets[net].pins if c.pin_spec(r).kind == "io" and c.parts[r.part].kind == "board" and c.parts[r.part].mode(r.pin) == "io"]
    if reads:                                             # a pin that may be an input: whatever a string drives toward it reaches it
        for s in an.strings:
            if net in (s.a, s.b) and not s.inner:
                other = s.b if s.a == net else s.a
                if an.nets[other].kind != "gnd" and an.nets[other].states:
                    v = max(an.nets[other].states)
                    cands.append((v, f"{v:g} V from {other} through {', '.join(s.parts)} while {reads[0]} reads (declare c.gpio(..., 'in') if it only reads)"))
    if not info.fixed or od:
        for who, _r, v, rail in pullups_on(c, an, net):
            cands.append((v, f"pulled up to {rail} ({v:g} V) by {who}"))
    if not cands:
        return None, []
    best = max(v for v, _ in cands)
    return best, [w for v, w in cands if abs(v - best) < 1e-9][:2]


def pullups_on(c: Circuit, an: Analysis, net: str) -> list[tuple[str, float, float, str]]:
    """Pull-up resistors from a rail onto this net: (part or 'MODULE (on board)', ohms, rail volts, rail net). Includes the
    on-board pull-ups of the modules on it (to their own supply)."""
    out = []
    for ref in c.nets[net].pins:
        p = c.parts[ref.part]
        if p.kind == "resistor":
            other = c._net_of.get(PinRef(p.name, _other(p, ref.pin)))
            if other and an.nets[other].kind == "rail":
                out.append((p.name, float(p.spec["ohm"]), an.nets[other].volts, other))
        ps = p.pins[ref.pin]
        if ps.pullup_ohm:
            out.append((f"{p.name} (on board)", float(ps.pullup_ohm), an.vdd.get(p.name) or 0.0, f"{p.name} VCC"))
    return out


def resistors_to_fixed(c: Circuit, an: Analysis, net: str) -> list[tuple[str, str]]:
    """Resistors from this net to a fixed net (a rail, a ground, a driven pin): (resistor, the other net)."""
    out = []
    for ref in c.nets[net].pins:
        p = c.parts[ref.part]
        if p.kind == "resistor":
            other = c._net_of.get(PinRef(p.name, _other(p, ref.pin)))
            if other and other != net and an.nets[other].fixed:
                out.append((p.name, other))
    return out


def pulldowns_on(c: Circuit, an: Analysis, net: str) -> list[tuple[str, float]]:
    out = []
    for ref in c.nets[net].pins:
        p = c.parts[ref.part]
        if p.kind == "resistor":
            other = c._net_of.get(PinRef(p.name, _other(p, ref.pin)))
            if other and an.nets[other].kind == "gnd":
                out.append((p.name, float(p.spec["ohm"])))
    return out
