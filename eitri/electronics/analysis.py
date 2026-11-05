"""The DC kernel: what every net is (a rail at a voltage, a ground, a pin a microcontroller drives, a transistor's switched low
side, or a plain signal), the series strings of two-terminal parts between them (a pin, a resistor and an LED to ground; a rail,
a relay coil and a transistor's drain), the current in each string at its worst, and what every rail supplies.

Deterministic and closed form: a string carries I = (Va - Vb - the forward voltages) / (the resistances), or a coil load's own
rated current; a node where three or more parts conduct and that is not a rail is a branch the kernel does not solve (the
build says so). Switches count closed, transistors on, and a GPIO at either level (the worst case of each).

Conventions (each a conservative choice, stated where it is used):
- currents are taken with a GPIO high at its full supply voltage (the most current a resistor string can draw);
- logic thresholds use the datasheet's guaranteed VOH min and VIH min (the least margin);
- a servo or module draws its catalog current (a servo: its stall current) on its supply pin.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .model import Circuit, Part, PinRef

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
    branches: dict[str, list[str]]              # a branch net -> the parts conducting into it (not solved)
    vdd: dict[str, float | None]                # every part's supply voltage (a module: its VCC rail; a board: its logic level)

    def through(self, part: str) -> list[String]:
        return [s for s in self.strings if part in s.parts]

    def part_current(self, part: str) -> float:
        return max((s.current for s in self.through(part)), default=0.0)

    def at_net(self, net: str) -> list[String]:
        return [s for s in self.strings if net in (s.a, s.b)]


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


def find_strings(c: Circuit, nets: dict[str, NetInfo]) -> tuple[list[String], dict[str, list[str]]]:
    strings: list[String] = []
    vmax = max((max(i.states) for i in nets.values() if i.states), default=0.0)
    branches: dict[str, list[str]] = {}
    seen: set[tuple] = set()
    for start, info in nets.items():
        if not info.fixed:
            continue
        for ref in info.conducting:
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
                if nets[nxt].fixed:
                    end = nxt
                    break
                if _blocked_diode(c, nets, PinRef(part.name, leave), vmax):   # it never conducts into this node: no current this way
                    ok = False
                    break
                cond = [r for r in nets[nxt].conducting if not (r.part == part.name and r.pin == leave) and not _blocked_diode(c, nets, r, vmax)]
                if len(cond) != 1 or nxt in visited:
                    if len(cond) > 1:
                        branches.setdefault(nxt, sorted({r.part for r in nets[nxt].conducting}))
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


def analyze(c: Circuit) -> Analysis:
    nets = classify(c)
    strings, branches = find_strings(c, nets)
    for s in strings:
        best = None
        for va, vb in itertools.product(nets[s.a].states, nets[s.b].states):
            i, hi, lo, node = _solve(c, s, va, vb)
            s.combos.append((va, vb, i, node))
            if best is None or i > best[0]:
                best = (i, hi, lo, va, vb, node)
        if best:
            s.current, s.flow, s.va, s.vb, s.node_v = best[0], (best[1], best[2]), best[3], best[4], best[5]
    return Analysis(nets, strings, branches, supply_voltages(c, nets))


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
    for s in an.strings:
        if net not in s.inner:
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
