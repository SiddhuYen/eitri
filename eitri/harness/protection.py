"""Overcurrent protection: the harness checks for fuses, PTC resettable fuses and DC breakers (DC up to 60 V).

Every check starts from the sources the model declares (`h.source(pin, voltage_V, max_current_A=, short_circuit_A=)`) and
walks the wires from there. A protective device (`Fuse`, `Breaker`) is a two-terminal part in a wire: the walk stops at it,
and what lies beyond its far terminal is the branch it protects.

    protection        every branch fed from a source that can deliver more than its thinnest wire's ampacity passes through
                      a protective device before it reaches a load
    rating vs wire    a device's rating (a PTC: its trip current) is at most the ampacity of the thinnest wire it protects
    continuous load   the steady current through a device is within its maker's continuous-load derating (no nuisance opening)
    voltage rating    the device's DC voltage rating covers the system voltage
    interrupting      the source's short-circuit current is within the device's interrupting rating
    placement         the device sits at the source end: the unprotected lead from the source is within the length limit
    sources declared  every supply net is fed by a declared source (otherwise its protection is "not run")
    signal nets       a net declared signal=True carries no more than a logic output's rating
    scope             DC up to 60 V; a net above it fails (out of scope, not graded); AC sources are refused at h.source()

A missing input never passes silently: the check reports "not run: ..." and the build report lists it under checks_not_run
(eitri.checks.switched_off). Every number cites its source in eitri.harness.fuses.
"""
from __future__ import annotations

import heapq
import math

from ..checks import Check, check, switched_off
from . import fuses as F
from .model import MAX_DC_V, Endpoint, Fuse, Harness, Link, PinRef, Source

CAT = "protection"


def _a(x: float | None) -> str:
    if x is None:
        return "?"
    if math.isinf(x):
        return "unlimited"
    return f"{x * 1000:.3g} mA" if 0 < abs(x) < 1 else f"{x:.3g} A" if abs(x) < 1000 else f"{x:.0f} A"


class _Graph:
    """The harness's conductors as a graph: endpoints joined by wires (shields excluded). Protective devices are nodes too;
    region() stops at their terminals."""

    def __init__(self, h: Harness):
        self.h = h
        self.adj: dict[Endpoint, list[tuple[Link, Endpoint]]] = {}
        for l in h.links:
            if l.kind == "shield":
                continue
            self.adj.setdefault(l.a, []).append((l, l.b))
            self.adj.setdefault(l.b, []).append((l, l.a))
        self.fuses = h.fuses
        self.dev_pins = {PinRef(f.name, p): f for f in self.fuses.values() for p in f.pin_names}

    def region(self, start: Endpoint) -> tuple[list[Link], set[Endpoint], dict[PinRef, float]]:
        """Wires and endpoints reachable from `start` without passing a protective device, and every device terminal met on
        the way with the shortest conductor length (mm) from `start` to it."""
        dist: dict[Endpoint, float] = {start: 0.0}
        wires: dict[str, Link] = {}
        devs: dict[PinRef, float] = {}
        pq: list = [(0.0, 0, start)]
        tick = 0
        while pq:
            d, _, node = heapq.heappop(pq)
            if d > dist.get(node, math.inf):
                continue
            if node != start and node in self.dev_pins:
                devs[node] = min(devs.get(node, math.inf), d)
                continue                                  # the walk stops at a protective device
            for l, other in self.adj.get(node, []):
                wires[l.id] = l
                nd = d + l.length_mm
                if nd < dist.get(other, math.inf):
                    dist[other] = nd
                    tick += 1
                    heapq.heappush(pq, (nd, tick, other))
        return list(wires.values()), set(dist), devs

    def loads(self, nodes: set[Endpoint], start: Endpoint) -> list[PinRef]:
        """Connector pins in a region that are not protective-device terminals or the start: what the region feeds."""
        return [n for n in nodes if isinstance(n, PinRef) and n != start and n not in self.dev_pins]


class _Fed:
    """One protective device as reached from a source: its line terminal, the source, the device upstream of it (None: fed
    straight from the source), the unprotected length from the source and the wires on that lead."""

    def __init__(self, dev: Fuse, line: PinRef, src: Source, parent: Fuse | None, lead_mm: float, lead: list[Link]):
        self.dev, self.line, self.src, self.parent, self.lead_mm, self.lead = dev, line, src, parent, lead_mm, lead

    @property
    def load(self) -> PinRef:
        return next(PinRef(self.dev.name, p) for p in self.dev.pin_names if p != self.line.pin)


def _deliverable(src: Source) -> tuple[float, str]:
    d = src.deliverable_A
    if d is None:
        return math.inf, (f"{src.pin} declares no max_current_A or short_circuit_A, so it is taken as able to deliver more than "
                          f"any wire can carry (a battery or an unlimited supply)")
    which = "short-circuit current" if src.short_circuit_A is not None and d == src.short_circuit_A else "current limit"
    return d, f"{src.pin} can deliver {_a(d)} ({which} as declared)"


def _shortest_lead(g: _Graph, start: Endpoint, target: PinRef) -> list[Link]:
    """The wires on the shortest conductor path from start to a device terminal, not crossing another device."""
    dist: dict[Endpoint, float] = {start: 0.0}
    prev: dict[Endpoint, tuple[Endpoint, Link]] = {}
    pq: list = [(0.0, 0, start)]
    tick = 0
    while pq:
        d, _, node = heapq.heappop(pq)
        if node == target:
            break
        if d > dist.get(node, math.inf) or (node != start and node in g.dev_pins):
            continue
        for l, other in g.adj.get(node, []):
            nd = d + l.length_mm
            if nd < dist.get(other, math.inf):
                dist[other], prev[other] = nd, (node, l)
                tick += 1
                heapq.heappush(pq, (nd, tick, other))
    path, node = [], target
    while node in prev:
        node, l = prev[node]
        path.append(l)
    return list(reversed(path))


def protection_checks(h: Harness) -> list[Check]:
    out: list[Check] = []
    g = _Graph(h)
    out += _scope(h)
    fed: dict[str, _Fed] = {}                             # device name -> how it is first reached
    reached: set[str] = set()                             # wire ids reachable from some source (through devices too)

    for src in h.sources.values():
        deliver, basis = _deliverable(src)
        wires, nodes, devs = g.region(src.pin)
        reached.update(l.id for l in wires)
        loads = g.loads(nodes, src.pin)
        thin = sorted((l for l in wires if l.gauge.ampacity < deliver), key=lambda l: l.gauge.ampacity)
        name = f"overcurrent protection from {src.pin}"
        refs = [src.pin.connector] + [l.id for l in wires]
        if not wires:
            out.append(check(CAT, name, "fail", f"{src.pin} is declared a source but no wire leaves it",
                             "wire the source, or remove its h.source(...)", [src.pin.connector]))
        elif loads and thin:
            worst = thin[0]
            pick = F.suggest("ATO", min(l.gauge.ampacity for l in thin)) or F.suggest("MF-R", min(l.gauge.ampacity for l in thin))
            out.append(check(CAT, name, "fail",
                             f"{', '.join(f'{l.id} ({l.gauge.label}, {_a(l.gauge.ampacity)})' for l in thin[:6])} "
                             f"reach{'es' if len(thin) == 1 else ''} {', '.join(map(str, sorted(loads, key=str)[:4]))} with no protective "
                             f"device; {basis}. A fault would overheat {worst.id} before anything opens",
                             f"a fuse at the source end of the branch ({(pick or 'Fuse(name, ...)').replace('name', chr(34) + 'F1' + chr(34))} right after {src.pin}, wired "
                             f"{src.pin} -> F1[\"LINE\"], F1[\"LOAD\"] -> the loads), rated at most the thinnest wire's "
                             f"{_a(worst.gauge.ampacity)}; or a supply whose current limit is below it",
                             [src.pin.connector] + [l.id for l in thin]))
        elif loads:
            out.append(check(CAT, name, "pass",
                             f"no protective device needed: {basis}, within the ampacity of every wire it feeds directly "
                             f"(thinnest {min(wires, key=lambda l: l.gauge.ampacity).gauge.label}, "
                             f"{_a(min(l.gauge.ampacity for l in wires))})", "", refs))
        else:
            out.append(check(CAT, name, "pass",
                             f"every branch from {src.pin} passes through {', '.join(sorted(g.dev_pins[p].name for p in devs))} "
                             f"before it reaches a load", "", refs))
        # follow the devices downstream: the first time a device is reached fixes its line side and its upstream
        todo = [(p, d, None, _shortest_lead(g, src.pin, p)) for p, d in sorted(devs.items(), key=lambda t: str(t[0]))]
        while todo:
            line, dist, parent, lead = todo.pop(0)
            dev = g.dev_pins[line]
            if dev.name in fed:
                continue
            fed[dev.name] = f = _Fed(dev, line, src, parent, dist, lead)
            sub_w, _sub_n, sub_d = g.region(f.load)
            reached.update(l.id for l in sub_w)
            for p, d in sorted(sub_d.items(), key=lambda t: str(t[0])):
                if g.dev_pins[p].name not in fed:
                    todo.append((p, d, dev, []))

    for dev in g.fuses.values():
        f = fed.get(dev.name)
        if f is None:
            out.append(switched_off(check(CAT, f"{dev.name} on a branch from a declared source", "warn",
                                          f"{dev.name} ({dev.title}) is not reached from any declared source, so its rating, "
                                          f"placement, voltage and interrupting checks are not run",
                                          "declare the supply that feeds it: h.source(PSU[\"+\"], voltage_V, ...)", [dev.name]),
                                    f"{dev.name}.source"))
            continue
        out += _device_checks(h, g, f)

    out += _undeclared(h, reached)
    return out


def _protected_wires(g: _Graph, f: _Fed) -> list[Link]:
    wires, _n, _d = g.region(f.load)
    return wires


def _device_checks(h: Harness, g: _Graph, f: _Fed) -> list[Check]:
    out: list[Check] = []
    dev, d = f.dev, f.dev.device
    src = f.src
    wires = _protected_wires(g, f)
    refs = [dev.name] + [l.id for l in wires]
    label = f"{dev.name} ({d.title})"

    # 1. rating vs the thinnest wire it protects (a PTC: its trip current, the least it is sure to open at)
    name = f"{dev.name} rating within the wire it protects"
    opens_at, what = (d.trip_A, "trip current") if d.kind == "ptc" else (d.rating_A, "rating")
    if not wires:
        pass                                              # nothing on the load side: connectivity reports the open pin
    elif opens_at is None:
        out.append(switched_off(check(CAT, name, "warn", f"{label} has no {what}, so the wire it protects is not graded",
                                      f"give Fuse(..., {'trip_A' if d.kind == 'ptc' else 'rating_A'}=...)", refs),
                                f"{dev.name}.{'trip_A' if d.kind == 'ptc' else 'rating_A'}"))
    else:
        thin = min(wires, key=lambda l: l.gauge.ampacity)
        amp = thin.gauge.ampacity
        if opens_at > amp + 1e-9:
            pick = F.suggest(d.family, amp) if d.family else None
            out.append(check(CAT, name, "fail",
                             f"{label} {what} {_a(opens_at)} is above the {_a(amp)} ampacity of {thin.id} ({thin.gauge.label}) "
                             f"that it protects: a fault between {_a(amp)} and {_a(opens_at)} overheats {thin.id} without opening it "
                             f"[source: {F.SIZING_SOURCE}]",
                             f"<= {_a(amp)}: " + (pick.replace("name", repr(dev.name)) if pick else "a lower-rated device")
                             + f", or a thicker {thin.id}", refs))
        else:
            out.append(check(CAT, name, "pass",
                             f"{what} {_a(opens_at)} <= {_a(amp)}, the ampacity of the thinnest wire it protects ({thin.id}, "
                             f"{thin.gauge.label}) [source: {F.SIZING_SOURCE}]", f"<= {_a(amp)}", refs))

    # 2. steady load within the maker's continuous-load derating
    name = f"{dev.name} continuous load"
    def through(pin: PinRef) -> float | None:
        cs = [h.wire_current(l) for l in h.links_at(pin)]
        return None if not cs or any(c is None for c in cs) else sum(cs)
    # the current it carries: what its LOAD terminal feeds (a fuse block's feed may carry the trunk's current), else its LINE side
    load = through(f.load)
    if load is None:
        load = through(f.line)
    nets = sorted({l.net for pin in dev.pin_names for l in h.links_at(PinRef(dev.name, pin)) if l.net})
    if load is None:
        out.append(switched_off(check(CAT, name, "warn", f"the current through {label} is not known, so its continuous load is not graded",
                                      "give the net (or the wire through it) current_A", refs),
                                *([f"{n}.current_A" for n in nets] or [f"{dev.name}.current_A"])))
    else:
        frac, basis, src_txt = d.continuous(h.ambient_C)
        if frac is None or d.rating_A is None:
            missing = "continuous_fraction" if d.rating_A is not None else "rating_A"
            out.append(switched_off(check(CAT, name, "warn", f"{_a(load)} through {label}; {basis}",
                                          f"give Fuse(..., {missing}=...) from its datasheet", refs), f"{dev.name}.{missing}"))
        else:
            limit = frac * d.rating_A
            sev = "fail" if load > limit + 1e-9 else "pass"
            msg = (f"{_a(load)} steady through {label}, limit {_a(limit)} = {frac:.3g} x {_a(d.rating_A)} ({basis})"
                   + (". It would open on a normal load or age early: a larger rating (still within the wire), or less load" if sev == "fail" else "")
                   + f" [source: {src_txt}]")
            out.append(check(CAT, name, sev, msg, f"<= {_a(limit)}", refs))

    # 3. DC voltage rating covers the system voltage
    name = f"{dev.name} voltage rating"
    sysv = [v for v in [src.voltage_V] + [h.nets[n].voltage_V for n in nets if n in h.nets] if v is not None]
    if not sysv:
        out.append(switched_off(check(CAT, name, "warn", f"no system voltage: {src.pin} and its nets give no voltage_V",
                                      "h.source(..., voltage_V=...)", refs), f"{src.pin}.voltage_V"))
    elif d.voltage_rating_V is None:
        out.append(switched_off(check(CAT, name, "warn", f"{label} has no DC voltage rating, so it is not graded against {max(sysv):g} V",
                                      "give Fuse(..., voltage_rating_V=<DC rating from its datasheet>)", refs),
                                f"{dev.name}.voltage_rating_V"))
    else:
        v = max(sysv)
        sev = "fail" if v > d.voltage_rating_V else "pass"
        out.append(check(CAT, name, sev,
                         f"{v:g} V system, {label} rated {d.voltage_rating_V:g} V DC"
                         + (": above its rating it may not clear a fault (it can arc across)" if sev == "fail" else "")
                         + (f" [source: {d.sources.get('voltage', '')}]" if d.sources.get("voltage") else ""),
                         f"<= {d.voltage_rating_V:g} V DC", refs))

    # 4. interrupting rating vs the source's short-circuit current
    name = f"{dev.name} interrupting rating"
    if src.short_circuit_A is None:
        out.append(switched_off(check(CAT, name, "warn",
                                      f"the source data for {src.pin} gives no short-circuit current, so {label}'s interrupting "
                                      f"rating ({_a(d.interrupt_A)}) is not checked against it",
                                      "h.source(..., short_circuit_A=<from the supply's or battery's datasheet>)", refs),
                                f"{src.pin}.short_circuit_A"))
    elif d.interrupt_A is None:
        out.append(switched_off(check(CAT, name, "warn", f"{label} has no interrupting rating, so the {_a(src.short_circuit_A)} fault "
                                      f"from {src.pin} is not checked", "give Fuse(..., interrupt_A=<from its datasheet>)", refs),
                                f"{dev.name}.interrupt_A"))
    else:
        sev = "fail" if src.short_circuit_A > d.interrupt_A else "pass"
        out.append(check(CAT, name, sev,
                         f"{_a(src.short_circuit_A)} prospective fault from {src.pin}, {label} interrupts {_a(d.interrupt_A)}"
                         + (": past that it may not clear the fault safely (a fuse with a higher interrupting rating, or a "
                            "breaker rated for it)" if sev == "fail" else "")
                         + (f" [source: {d.sources.get('interrupt', '')}]" if d.sources.get("interrupt") else ""),
                         f"<= {_a(d.interrupt_A)}", refs))

    # 5. placement: at the source end of the branch
    name = f"{dev.name} at the source end"
    if f.parent is not None:
        out.append(check(CAT, name, "pass", f"fed through {f.parent.name}, which protects its line side", "", [dev.name, f.parent.name]))
    else:
        deliver, basis = _deliverable(src)
        lead = f.lead
        lead_refs = [dev.name, src.pin.connector] + [l.id for l in lead]
        lim = F.PLACEMENT_MAX_MM
        if lead and all(l.gauge.ampacity >= deliver for l in lead):
            out.append(check(CAT, name, "pass",
                             f"{f.lead_mm:.0f} mm from {src.pin}; every wire on that lead is rated for the {_a(deliver)} the source can "
                             f"deliver", "", lead_refs))
        elif f.lead_mm > lim + 1e-9:
            out.append(check(CAT, name, "fail",
                             f"{f.lead_mm:.0f} mm of unprotected wire ({' + '.join(l.id for l in lead)}) between {src.pin} and "
                             f"{dev.name}; {basis}. A fault on that lead is not protected [source: {F.PLACEMENT_SOURCE}]",
                             f"<= {lim:.0f} mm: move {dev.name} to the source (shorten {lead[0].id if lead else 'the lead'})", lead_refs))
        else:
            out.append(check(CAT, name, "pass",
                             f"{f.lead_mm:.0f} mm of unprotected lead from {src.pin} [source: {F.PLACEMENT_SOURCE}]",
                             f"<= {lim:.0f} mm", lead_refs))
    return out


def _scope(h: Harness) -> list[Check]:
    out: list[Check] = []
    high = [n for n in h.nets.values() if n.voltage_V is not None and n.voltage_V > MAX_DC_V and any(l.net == n.name for l in h.links)]
    for n in high:
        out.append(check(CAT, f"in scope: net {n.name}", "fail",
                         f"{n.name} is {n.voltage_V:g} V: eitri's overcurrent checks cover DC up to {MAX_DC_V:g} V only, so this "
                         f"net's protection is not graded. Mains / high-voltage wiring needs a qualified electrician's design "
                         f"(IEC 60364, NFPA 70)", f"<= {MAX_DC_V:g} V DC",
                         [l.id for l in h.links if l.net == n.name]))
    return out


def _undeclared(h: Harness, reached: set[str]) -> list[Check]:
    """Supply nets no declared source reaches (their protection is not run), and the signal nets' current."""
    out: list[Check] = []
    used = [n for n in h.nets.values() if any(l.net == n.name and l.kind == "wire" for l in h.links)]
    supply = [n for n in used if n.round_trip and not n.signal]
    orphan = [n for n in supply if not any(l.id in reached for l in h.links if l.net == n.name)]
    if orphan:
        out.append(switched_off(check(CAT, "every supply net fed by a declared source", "warn",
                                      f"no declared source feeds net{'s' if len(orphan) > 1 else ''} "
                                      f"{', '.join(n.name for n in orphan)}, so whether {'they need' if len(orphan) > 1 else 'it needs'} "
                                      f"overcurrent protection is not graded",
                                      "declare each supply with h.source(PSU[\"+\"], voltage_V, max_current_A=..., short_circuit_A=...); "
                                      "mark a net driven by a logic pin h.net(..., signal=True)",
                                      [l.id for l in h.links if l.net in {n.name for n in orphan}]),
                                *[f"{n.name}.source" for n in orphan]))
    elif supply and h.sources:
        out.append(check(CAT, "every supply net fed by a declared source", "pass",
                         f"{len(supply)} supply net(s), each reached from {', '.join(str(s.pin) for s in h.sources.values())}"))
    for n in used:
        if not n.signal:
            continue
        links = [l for l in h.links if l.net == n.name]
        if any(l.id in reached for l in links):
            continue                                       # a declared source feeds it: graded as a supply above
        cur = [h.wire_current(l) for l in links]
        name = f"signal net {n.name}"
        if any(c is None for c in cur):
            out.append(switched_off(check(CAT, name, "warn", f"{n.name} is declared a signal but has no current, so it is not graded",
                                          f"h.net({n.name!r}, current_A=..., signal=True)", [l.id for l in links]), f"{n.name}.current_A"))
            continue
        worst = max(cur)
        sev = "fail" if worst > F.SIGNAL_MAX_A + 1e-12 else "pass"
        out.append(check(CAT, name, sev,
                         f"{_a(worst)} on a net driven by a logic output (limit {_a(F.SIGNAL_MAX_A)})"
                         + (": that is a supply, not a signal; declare its source with h.source(...)" if sev == "fail" else "")
                         + f" [source: {F.SIGNAL_SOURCE}]", f"<= {_a(F.SIGNAL_MAX_A)}", [l.id for l in links]))
    return out
