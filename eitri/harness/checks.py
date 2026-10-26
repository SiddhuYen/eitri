"""The harness checks: what every build is graded on (the analog of the geometry report).

    connectivity  unconnected pins (unless NC), several wires on one pin (only a splice may join wires), duplicate
                  wires, dangling splices, nets shorted together at a splice, wires without a length
    ampacity      wire gauge against the wire's current (chassis-wiring table), connector pins against their rating
    voltage drop  V = 2*K*I*L/CM along every source-to-load path of a net, against the net's max_drop_pct
    consistency   one gauge and one colour per net, shielded cables terminated
    power         a board's 5V / 3V3 pin against what it can supply (eitri.power, sourced limits); everything that takes a
                  signal from a board's I/O pin shares the board's ground

Each check is a HCheck: pass, warn or fail, with `refs` naming the wires / connectors it is about (the editor
highlights them). Nothing here raises on a bad design: a bad design is a failed check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..checks import Check, check
from ..model import CheckResult  # noqa: F401
from .. import power
from .catalog import BOARD_TYPES, nearest_awg, voltage_drop, wire_rating
from .model import Endpoint, Harness, Link, PinRef, SpliceRef

WARN_FRACTION = 0.8          # within 20 % of the limit: a warning


HCheck = Check                                   # the harness's checks are the shared structured Check (eitri.checks)


def _check(category: str, name: str, severity: str, measured: str, expected: str = "", refs=()) -> HCheck:
    return check(category, name, severity, measured, expected, refs)


def _fmt_a(x: float) -> str:
    return f"{x * 1e6:.3g} µA" if 0 < abs(x) < 1e-3 else f"{x:.3g} A"


def _pct(x: float) -> str:
    return f"{x:.2f}%" if x < 10 else f"{x:.1f}%"


# --------------------------------------------------------------------------- per-net numbers


@dataclass
class NetInfo:
    name: str
    current_A: float | None
    voltage_V: float | None
    max_drop_pct: float
    round_trip: bool
    wires: list[str] = field(default_factory=list)
    drop_V: float | None = None                 # the worst source-to-load path
    drop_pct: float | None = None
    worst_path: list[str] = field(default_factory=list)       # wire ids along it
    length_mm: float = 0.0
    status: str = "ungraded"                    # pass | fail | ungraded
    graded: bool = False


def link_drop_v(h: Harness, l: Link) -> float | None:
    """Volts lost in one wire (round trip when its net says so)."""
    i = h.wire_current(l)
    if i is None:
        return None
    net = h.net_of(l)
    return voltage_drop(i, l.length_mm, l.gauge, round_trip=net.round_trip if net else True)


def _worst_path(h: Harness, links: list[Link]) -> tuple[float, list[str]]:
    """The largest summed drop over the directed paths of a net (from nodes nothing in the net feeds, to the ends)."""
    out: dict[Endpoint, list[Link]] = {}
    has_in: set[Endpoint] = set()
    for l in links:
        out.setdefault(l.a, []).append(l)
        has_in.add(l.b)
    nodes = set(out) | has_in
    roots = [n for n in out if n not in has_in] or list(out)       # a net that loops: start anywhere
    best: tuple[float, list[str]] = (0.0, [])

    def walk(node: Endpoint, seen: frozenset, total: float, path: list[str]) -> None:
        nonlocal best
        nxt = [l for l in out.get(node, []) if l.id not in seen]
        if not nxt:
            if total > best[0]:
                best = (total, path)
            return
        for l in nxt:
            v = link_drop_v(h, l) or 0.0
            walk(l.b, seen | {l.id}, total + v, path + [l.id])

    for r in roots:
        walk(r, frozenset(), 0.0, [])
    return best


def net_stats(h: Harness) -> dict[str, NetInfo]:
    stats: dict[str, NetInfo] = {}
    for name, n in h.nets.items():
        links = [l for l in h.links if l.net == name]
        info = NetInfo(name, n.current_A, n.voltage_V, n.max_drop_pct, n.round_trip, [l.id for l in links],
                       length_mm=sum(l.length_mm for l in links))
        rated = [l for l in links if h.wire_current(l) is not None]
        if rated:
            v, path = _worst_path(h, rated)
            info.drop_V, info.worst_path = v, path
            if n.voltage_V:
                info.drop_pct = v / n.voltage_V * 100
                info.graded = True
                info.status = "pass" if info.drop_pct <= n.max_drop_pct else "fail"
        stats[name] = info
    return stats


# --------------------------------------------------------------------------- the checks


def run_checks(h: Harness) -> tuple[list[HCheck], dict[str, NetInfo]]:
    checks: list[HCheck] = []
    stats = net_stats(h)
    checks += _connectivity(h)
    checks += _ampacity(h)
    checks += _terminal_fit(h)
    checks += _drops(h, stats)
    checks += _consistency(h)
    checks += _power(h)
    return checks, stats


def _connectivity(h: Harness) -> list[HCheck]:
    out: list[HCheck] = []
    shield_pins = {p for c in h.cables.values() for p in c.shield_ends}

    open_pins, total_pins = [], 0
    for c in h.connectors.values():
        for pin in c.pin_names:
            total_pins += 1
            ref = PinRef(c.name, pin)
            if pin in c.nc or ref in shield_pins or h.links_at(ref):
                continue
            open_pins.append(ref)
    if open_pins:
        out.append(_check("connectivity", "all pins connected or declared NC", "fail",
                          f"{len(open_pins)} unconnected: " + ", ".join(map(str, open_pins[:12])) + (" ..." if len(open_pins) > 12 else ""),
                          "0 (connect them, or declare NC)", [p.connector for p in open_pins]))
    else:
        out.append(_check("connectivity", "all pins connected or declared NC", "pass", f"{total_pins} pins accounted for"))

    multi = []
    for c in h.connectors.values():
        for pin in c.pin_names:
            ls = h.links_at(PinRef(c.name, pin))
            if len(ls) > 1:
                multi.append((PinRef(c.name, pin), ls))
    if multi:
        out.append(_check("connectivity", "one wire per pin (wires meet only at a splice)", "fail",
                          "; ".join(f"{p} has {len(ls)} wires ({', '.join(l.id for l in ls)})" for p, ls in multi[:6]),
                          "1 per pin; join wires with a splice", [l.id for _p, ls in multi for l in ls] + [p.connector for p, _ in multi]))
    else:
        out.append(_check("connectivity", "one wire per pin (wires meet only at a splice)", "pass", "no pin has two wires"))

    seen: dict[frozenset, list[Link]] = {}
    for l in h.links:
        seen.setdefault(frozenset((l.a, l.b)), []).append(l)
    dup = [ls for ls in seen.values() if len(ls) > 1]
    loops = [l for l in h.links if l.a == l.b]
    if dup or loops:
        parts = [f"{' and '.join(l.id for l in ls)} both join {ls[0].a} and {ls[0].b}" for ls in dup]
        parts += [f"{l.id} starts and ends at {l.a}" for l in loops]
        out.append(_check("connectivity", "no duplicate wires", "fail", "; ".join(parts), "each pair of endpoints joined once",
                          [l.id for ls in dup for l in ls] + [l.id for l in loops]))
    else:
        out.append(_check("connectivity", "no duplicate wires", "pass", f"{len(h.links)} wires, all distinct"))

    if h.splices:
        dangling = [s for s in h.splices.values() if len(h.links_at(s.ref)) < 2]
        if dangling:
            out.append(_check("connectivity", "no dangling splices", "fail",
                              "; ".join(f"{s.name} has {len(h.links_at(s.ref))} wire(s)" for s in dangling), "a splice joins 2 or more wires",
                              [s.name for s in dangling] + [l.id for s in dangling for l in h.links_at(s.ref)]))
        else:
            out.append(_check("connectivity", "no dangling splices", "pass", f"{len(h.splices)} splice(s), each joining 2+ wires"))
        mixed = []
        for s in h.splices.values():
            nets = {l.net for l in h.links_at(s.ref) if l.net}
            if len(nets) > 1:
                mixed.append((s, sorted(nets)))
        if mixed:
            out.append(_check("connectivity", "splices join one net", "fail",
                              "; ".join(f"{s.name} joins nets {', '.join(n)}" for s, n in mixed), "one net per splice",
                              [s.name for s, _ in mixed] + [l.id for s, _ in mixed for l in h.links_at(s.ref)]))
        else:
            out.append(_check("connectivity", "splices join one net", "pass", "no splice shorts two nets"))

    bad_len = [l for l in h.links if not l.length_mm > 0]
    if bad_len:
        out.append(_check("connectivity", "every wire has a length", "fail", ", ".join(f"{l.id} = {l.length_mm:g} mm" for l in bad_len),
                          "> 0 mm", [l.id for l in bad_len]))
    elif h.links:
        out.append(_check("connectivity", "every wire has a length", "pass", f"{len(h.links)} wires"))

    bare = [l for l in h.links if not l.net and l.kind == "wire"]
    if bare:
        out.append(_check("connectivity", "every wire belongs to a net", "warn",
                          f"{len(bare)} wire(s) without a net: " + ", ".join(l.id for l in bare[:10]) + "; ampacity and voltage drop are not graded for them",
                          "h.net(...) and connect(..., net=...)", [l.id for l in bare]))
    unused = [n.name for n in h.nets.values() if not any(l.net == n.name for l in h.links)]
    if unused:
        out.append(_check("connectivity", "every net is used", "warn", "no wires on net(s): " + ", ".join(unused), "at least one wire per net"))
    return out


def _ampacity(h: Harness) -> list[HCheck]:
    out: list[HCheck] = []
    for l in h.links:
        i = h.wire_current(l)
        if i is None:
            continue
        amp = l.gauge.ampacity
        frac = i / amp if amp else float("inf")
        sev = "fail" if i > amp else "warn" if frac >= WARN_FRACTION else "pass"
        net = f" [{l.net}]" if l.net else ""
        out.append(_check("ampacity", f"ampacity {l.id} {l.a}->{l.b} {l.gauge.label}{net}", sev,
                          f"{_fmt_a(i)} of {_fmt_a(amp)} ({frac * 100:.0f}%)", f"<= {_fmt_a(amp)}" + (" (within 20% of the limit)" if sev == "warn" else ""),
                          [l.id]))
    # connector pins against their rating
    for c in h.connectors.values():
        rating = c.rating
        by_wire = c.rating_a is None and c.spec is not None and c.spec.by_wire
        loads = []
        for pin in c.pin_names:
            ref = PinRef(c.name, pin)
            cur = [h.wire_current(l) for l in h.links_at(ref)]
            cur = [x for x in cur if x is not None]
            if cur:
                pin_rating = min(wire_rating(c.spec, l.gauge) for l in h.links_at(ref)) if by_wire else rating
                loads.append((sum(cur), ref, pin_rating))
        if not loads:
            continue
        worst, wref, pin_rating = max(loads, key=lambda t: t[0] / t[2] if t[2] else t[0])
        refs = [c.name] + [l.id for l in h.links_at(wref)]
        if by_wire:                                   # rated by the wire crimped into it (spades: TE 108-106496; Micro-Fit: by terminal)
            g = min((l.gauge for l in h.links_at(wref)), key=lambda gg: wire_rating(c.spec, gg))
            frac = worst / pin_rating
            sev = "fail" if worst > pin_rating else "warn" if frac >= WARN_FRACTION else "pass"
            out.append(_check("ampacity", f"connector rating {c.name} ({c.type}, by wire size)", sev,
                              f"worst pin {wref}: {_fmt_a(worst)} of {_fmt_a(pin_rating)} with {g.label} crimped in ({frac * 100:.0f}%)",
                              f"<= {_fmt_a(pin_rating)}" + (" (within 20% of the limit)" if sev == "warn" else ""), refs))
            continue
        if rating is None:
            if not c.known and worst > 0:
                out.append(_check("ampacity", f"connector rating {c.name} ({c.type})", "warn",
                                  f"unknown type {c.type!r}: {_fmt_a(worst)} on {wref} is not graded", "give Connector(..., rating_a=<A per pin>)", refs))
            continue
        frac = worst / rating
        burst = c.spec.burst_a if c.spec and c.rating_a is None else None
        if worst > rating and burst is not None and worst <= burst:
            out.append(_check("ampacity", f"connector rating {c.name} ({c.type}, {_fmt_a(rating)}/pin)", "warn",
                              f"worst pin {wref}: {_fmt_a(worst)} ({frac * 100:.0f}%), above the {_fmt_a(rating)} continuous rating, "
                              f"within the {_fmt_a(burst)} short-time rating: fine for peaks under a minute, not for a steady load",
                              f"<= {_fmt_a(rating)} continuous, or a larger connector", refs))
            continue
        sev = "fail" if worst > rating else "warn" if frac >= WARN_FRACTION else "pass"
        out.append(_check("ampacity", f"connector rating {c.name} ({c.type}, {_fmt_a(rating)}/pin)", sev,
                          f"worst pin {wref}: {_fmt_a(worst)} ({frac * 100:.0f}%)", f"<= {_fmt_a(rating)}" + (" (within 20% of the limit)" if sev == "warn" else ""), refs))
    return out


def _terminal_fit(h: Harness) -> list[HCheck]:
    """Every wire crimped into a catalog connector is a size its family's contacts take (catalog.AWG_SOURCES)."""
    out: list[HCheck] = []
    for c in h.connectors.values():
        spec = c.spec
        if spec is None or spec.awg is None or c.termination != "crimp":
            continue
        thick, thin = spec.awg
        wires, bad = [], []
        for pin in c.pin_names:
            for l in h.links_at(PinRef(c.name, pin)):
                n = l.gauge.awg if l.gauge.awg is not None else nearest_awg(l.gauge.mm2)
                wires.append(l)
                if not thick <= n <= thin:
                    bad.append((l, pin))
        if not wires:
            continue
        name = f"wires fit the {c.name} contacts ({c.type}, AWG {thick}-{thin})"
        if bad:
            out.append(_check("terminal fit", name, "fail",
                              "; ".join(f"{l.id} {l.gauge.label} on {c.name}.{pin}" for l, pin in bad[:8])
                              + f": {c.type} contacts take AWG {thick} to {thin}, so {'it does' if len(bad) == 1 else 'they do'} not crimp",
                              f"AWG {thick}-{thin}: a thinner wire if its current allows, or a connector made for this size",
                              [c.name] + [l.id for l, _p in bad]))
        else:
            out.append(_check("terminal fit", name, "pass", f"{len(wires)} wire(s), all within AWG {thick}-{thin}", f"AWG {thick}-{thin}", [c.name]))
    return out


def _drops(h: Harness, stats: dict[str, NetInfo]) -> list[HCheck]:
    out: list[HCheck] = []
    for name, n in stats.items():
        if n.drop_V is None:
            continue
        if not n.graded:
            out.append(_check("voltage drop", f"voltage drop {name}", "warn",
                              f"{n.drop_V:.3f} V over {' + '.join(n.worst_path)}; the net has no voltage_V, so the percentage is not graded",
                              "h.net(..., voltage_V=...)", n.worst_path))
            continue
        sev = "pass" if n.drop_pct <= n.max_drop_pct else "fail"
        rt = "round trip" if n.round_trip else "one way"
        out.append(_check("voltage drop", f"voltage drop {name} ({n.voltage_V:g} V, {_fmt_a(n.current_A) if n.current_A is not None else 'per-wire current'})", sev,
                          f"{_pct(n.drop_pct)} = {n.drop_V:.3f} V ({rt}, over {' + '.join(n.worst_path)})", f"<= {n.max_drop_pct:g}%", n.worst_path))
    return out


def _consistency(h: Harness) -> list[HCheck]:
    out: list[HCheck] = []
    fails, warns, refs_f, refs_w = [], [], [], []
    for name in h.nets:
        links = [l for l in h.links if l.net == name and l.kind == "wire"]
        gauges = {l.gauge.key for l in links}
        if len(gauges) < 2:
            continue
        for a in links:
            for b in links:
                if a.gauge.key == b.gauge.key or a.gauge.mm2 >= b.gauge.mm2:
                    continue
                thin, thick = a, b                          # a is the thinner wire
                it, ik = h.wire_current(thin), h.wire_current(thick)
                if it is not None and ik is not None and it > ik * 1.0001:
                    fails.append(f"{name}: {thin.id} ({thin.gauge.label}) carries {_fmt_a(it)}, more than {thick.id} ({thick.gauge.label}, {_fmt_a(ik)})")
                    refs_f += [thin.id, thick.id]
                elif it == ik:
                    warns.append(f"{name}: {thin.id} is {thin.gauge.label} but {thick.id} is {thick.gauge.label} at the same current")
                    refs_w += [thin.id, thick.id]
    if fails:
        out.append(_check("consistency", "one gauge per net", "fail", "; ".join(fails[:5]), "a thinner wire carries no more than a thicker one of its net", refs_f))
    elif warns:
        out.append(_check("consistency", "one gauge per net", "warn", "; ".join(warns[:5]), "one gauge per net, or a lower current on the thinner wire", refs_w))
    else:
        out.append(_check("consistency", "one gauge per net", "pass", "no net changes gauge at the same current"))

    odd = []
    for name in h.nets:
        links = [l for l in h.links if l.net == name and l.kind == "wire"]
        colors = {l.color for l in links}
        if len(colors) > 1:
            odd.append((name, sorted(colors), [l.id for l in links]))
    if odd:
        out.append(_check("consistency", "one colour per net", "warn", "; ".join(f"{n} uses {', '.join(c)}" for n, c, _ in odd[:5]),
                          "one colour per net", [i for _n, _c, ids in odd for i in ids]))
    else:
        out.append(_check("consistency", "one colour per net", "pass", "every net keeps its colour"))

    for cab in h.cables.values():
        if not cab.shield:
            continue
        ends = cab.shield_ends
        refs = [cab.name] + [l.id for l in h.links if l.cable == cab.name]
        if not ends:
            out.append(_check("consistency", f"shield terminated: {cab.name}", "fail", "the shield is not connected to anything",
                              "connect_cable(..., shield_a=pin and/or shield_b=pin)", refs))
        else:
            where = " and ".join(str(p) for p in ends)
            out.append(_check("consistency", f"shield terminated: {cab.name}", "pass",
                              f"{'one end' if len(ends) == 1 else 'both ends'}: {where}", "", refs))
    return out


# --------------------------------------------------------------------------- power: board supply pins, shared ground

_IO = re.compile(r"^(D\d+|A\d+)$", re.I)       # a board I/O pin: D0..D13, A0..A7


def _nodes(h: Harness) -> dict:
    """Electrical nodes: every endpoint (pin or splice) mapped to a representative, joined by the wires."""
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for l in h.links:
        if l.kind == "shield":
            continue
        ra, rb = find(l.a), find(l.b)
        if ra != rb:
            parent[ra] = rb
    return {x: find(x) for x in list(parent)}


def _power(h: Harness) -> list[HCheck]:
    out: list[HCheck] = []
    boards = [c for c in h.connectors.values() if c.type in BOARD_TYPES]
    if not boards:
        return out
    node = _nodes(h)
    of = lambda e: node.get(e, e)
    for b in boards:
        key = BOARD_TYPES[b.type]
        label = power.BOARDS[key]
        # the supply pins: what the wires leaving them carry, against the pin's limit for how the board is powered
        for pin in b.pin_names:
            sp = power.supply_pin_name(pin)
            if sp is None:
                continue
            links = h.links_at(PinRef(b.name, pin))
            if not links:
                continue
            cur = [h.wire_current(l) for l in links]
            refs = [b.name] + [l.id for l in links]
            nets = sorted({l.net for l in links if l.net})
            lim = power.supply_limit(key, sp, b.powered, b.vin_V)
            how = f"{label} on {'USB' if b.powered == 'usb' else f'VIN {b.vin_V:g} V' if b.vin_V is not None else 'VIN'}"
            name = f"supply {b.name}.{pin} ({how})"
            if any(c is None for c in cur):
                out.append(_check("power", name, "warn", f"{len(links)} wire(s) leave {b.name}.{pin} but not all of them have a current: not graded",
                                  "give the net (or the wire) current_A: what the loads draw, at their worst (a servo's stall current)", refs))
                continue
            draw = sum(cur)
            sev = power.grade(draw, lim)
            src = f" [source: {lim.source}]" if lim.source else ""
            if lim.limit_A is None:
                out.append(_check("power", name, "warn", f"{_fmt_a(draw)} drawn; not graded: {lim.basis}{src}",
                                  "the board's supply limit (power the load separately if unsure)", refs))
            elif sev == "fail":
                out.append(_check("power", name, "fail",
                                  f"{_fmt_a(draw)} drawn from {b.name}.{pin}" + (f" (net {', '.join(nets)})" if nets else "")
                                  + f", but the pin can supply {_fmt_a(lim.limit_A)}, limited by {lim.basis}. Fix: {power.advice(pin)}.{src}",
                                  f"<= {_fmt_a(lim.limit_A)}", refs))
            else:
                out.append(_check("power", name, sev,
                                  f"{_fmt_a(draw)} of {_fmt_a(lim.limit_A)} ({draw / lim.limit_A * 100:.0f}%), limited by {lim.basis}"
                                  + ("; the board's own draw shares this budget" if sev == "warn" else "") + src,
                                  f"<= {_fmt_a(lim.limit_A)}" + (" (within 20% of the limit)" if sev == "warn" else ""), refs))
        # shared ground: a load that takes a signal from the board must have a pin on the board's ground node
        gnd = {of(PinRef(b.name, p)) for p in b.pin_names if p.strip().upper().startswith("GND")}
        loads: dict[str, list[str]] = {}
        for pin in b.pin_names:
            if not _IO.match(pin.strip()):
                continue
            n = of(PinRef(b.name, pin))
            for e, rep_ in node.items():
                if rep_ == n and isinstance(e, PinRef) and e.connector != b.name:
                    loads.setdefault(e.connector, []).append(f"{b.name}.{pin}")
        for cname, sigs in sorted(loads.items()):
            c = h.connectors[cname]
            shared = any(of(PinRef(cname, p)) in gnd for p in c.pin_names)
            name = f"shared ground {cname} - {b.name}"
            sig = ", ".join(sorted(set(sigs)))
            if shared:
                out.append(_check("power", name, "pass", f"{cname} takes {sig} and shares {b.name}'s GND", "a common ground", [cname, b.name]))
            elif not gnd:
                out.append(_check("power", name, "fail", f"{cname} takes {sig}, but {b.name} has no GND pin in the harness: the signal has no reference",
                                  f"add {b.name}'s GND pin and wire it to {cname}'s ground (or to its supply's ground)", [cname, b.name]))
            else:
                out.append(_check("power", name, "fail",
                                  f"{cname} takes {sig}, but none of its pins reaches {b.name}'s GND: a signal needs a common ground. "
                                  f"When {cname} has its own supply, wire that supply's ground (or {cname}'s ground) to {b.name} GND",
                                  "a common ground", [cname, b.name]))
    return out
