"""What the agent's `inspect` tool has in scope for an electronics project (names are plain functions that print).

    c              the built Circuit (also `parts`); p is PARAMS
    nets()         every net: what it is (rail, ground, driven, switched, signal), its voltage, its pins
    currents()     every series string the kernel solved: its parts, its ends, its worst-case current, and each end state
    rails()        every rail: its source, what it supplies (item by item) and its limit
    part(name)     one part: its numbers with their sources, and its pins' nets
    sources()      the sources table (every number a check can use)
    report()       the full text report again
"""
from __future__ import annotations

from .model import Circuit, PinRef, amps_text


def electronics_scope(c: Circuit, p=None) -> dict:
    import eitri.electronics as E
    from .checks import rail_limit, rail_loads
    from .report import analyze, summarize
    from .sources import cite, sources_table

    def _an():
        rep = analyze(c)
        return rep, c._analysis

    def nets():
        _rep, an = _an()
        for n in c.nets.values():
            i = an.nets[n.name]
            print(f"{n.name}: {i.kind}" + (f" {i.volts:g} V" if i.volts is not None else "") + f"; pins {', '.join(map(str, n.pins))}")

    def currents():
        _rep, an = _an()
        for s in an.strings:
            print(f"{s.flow[0] or s.a} -> {' -> '.join(s.parts)} -> {s.flow[1] or s.b}: {'unlimited' if s.current == float('inf') else amps_text(s.current)}"
                  + "".join(f"\n    {s.a}={va:g} V, {s.b}={vb:g} V: {'unlimited' if i == float('inf') else amps_text(i)}" + (f" {node}" if node else "")
                            for va, vb, i, node in s.combos))
        for net, ps in an.branches.items():
            print(f"not solved: {net} is a branch of {', '.join(ps)}")

    def rails():
        _rep, an = _an()
        for n, i in an.nets.items():
            if i.kind != "rail" or not i.sources:
                continue
            src = i.sources[0]
            lim, basis, source = rail_limit(c, an, c.parts[src.part], src.pin, n)
            items = rail_loads(c, an, n)
            print(f"{n}: {i.volts:g} V from {src}; {amps_text(sum(x for _, x, _ in items))} of {amps_text(lim) if lim else 'ungraded'} ({basis})")
            for what, amps, _r in items:
                print(f"    {what}")

    def part(name):
        q = c.part(name)
        print(f"{q.name}: {q.kind} {q.value_text} ({q.title})")
        for k, v in q.spec.items():
            print(f"    {k} = {v}" + (f"   [{cite(q.srcs[k])}]" if q.srcs.get(k) else ""))
        for n, ps in q.pins.items():
            print(f"    pin {n} ({ps.kind}{', ' + ', '.join(ps.roles) if ps.roles else ''}): net {c._net_of.get(PinRef(q.name, n)) or '-'}")

    def sources():
        print(sources_table())

    def report():
        print(summarize(analyze(c)))

    return {**{k: getattr(E, k) for k in E.__all__}, "c": c, "parts": c, "p": p, "nets": nets, "currents": currents, "rails": rails,
            "part": part, "sources": sources, "report": report}
