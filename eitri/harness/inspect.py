"""What the agent's `inspect` tool has in scope for a harness project (names are plain functions that print).

    h                 the built Harness (also `parts`); p is PARAMS
    nets()            every net: voltage, current, computed drop, wires, length
    wires(net=None)   every wire (or one net's): ends, gauge, colour, length, current, ampacity use, drop
    connectors()      every connector: type, rating, and what is on each pin
    drop(a, b)        voltage drop along the directed wires from endpoint a to endpoint b ("BATT.+", "ESC1.+", "PDB")
    lengths()         total wire by gauge and colour, with the 10% service loop
    report()          the full text report again
"""
from __future__ import annotations

from ..power import amps
from .catalog import voltage_drop
from .checks import link_drop_v, net_stats
from .model import Harness, PinRef


def harness_scope(h: Harness, p=None) -> dict:
    import eitri.harness as H
    from .report import analyze, summarize

    def nets():
        for n in net_stats(h).values():
            v = f"{n.voltage_V:g} V" if n.voltage_V is not None else "no voltage"
            i = amps(n.current_A) if n.current_A is not None else "no current"
            d = f"drop {n.drop_V:.4f} V" + (f" = {n.drop_pct:.3f}% of {n.max_drop_pct:g}% allowed" if n.drop_pct is not None else "") if n.drop_V is not None else "drop not computed"
            print(f"{n.name}: {v}, {i}, {d}; {len(n.wires)} wire(s) {', '.join(n.wires)}; {n.length_mm:.0f} mm; worst path: {' + '.join(n.worst_path) or '-'}")

    def wires(net=None):
        for l in h.links:
            if net and l.net != net:
                continue
            i = h.wire_current(l)
            amp = l.gauge.ampacity
            dv = link_drop_v(h, l)
            print(f"{l.id}: {l.a} -> {l.b}  {l.gauge.label} {l.color}  {l.length_mm:g} mm  net={l.net}  "
                  f"I={'-' if i is None else amps(i)}  ampacity {amp:.3g} A" + ("" if i is None else f" ({i / amp * 100:.0f}% used)")
                  + ("" if dv is None else f"  drop {dv:.4f} V"))

    def connectors():
        for c in h.connectors.values():
            print(f"{c.name}: {c.type}, {c.pins} pins, rating {'-' if c.rating is None else f'{c.rating:g} A/pin'}, NC {c.nc or '-'}")
            for pin in c.pin_names:
                ls = h.links_at(PinRef(c.name, pin))
                lab = c.pin_labels.get(pin, "")
                print(f"   {pin:>4} {lab:<8} " + (", ".join(f"{l.id}->{l.other(PinRef(c.name, pin))}" for l in ls) or "(free)"))

    def drop(a, b):
        src, dst = h._endpoint(a), h._endpoint(b)
        best = None
        stack = [(src, [], 0.0)]
        while stack:
            node, path, v = stack.pop()
            if node == dst:
                best = max(best, (v, path), key=lambda t: t[0]) if best else (v, path)
                continue
            for l in h.links:
                if l.a == node and l.id not in path:
                    stack.append((l.b, path + [l.id], v + (link_drop_v(h, l) or 0.0)))
        if best is None:
            print(f"no directed path of wires from {src} to {dst}")
            return None
        v, path = best
        net = h.net_of(h.link(path[0]))
        pct = f" = {v / net.voltage_V * 100:.3f}% of {net.voltage_V:g} V" if net and net.voltage_V else ""
        print(f"{' + '.join(path)}: {v:.4f} V{pct}")
        return v

    def lengths():
        from .outputs import bom
        for r in bom(h):
            if r["category"] in ("wire", "cable"):
                print(f"{r['description']}: {r['qty']} {r['unit']}  ({r['notes']})")

    def report():
        print(summarize(analyze(h)))

    return {"h": h, "parts": h, "p": p, "nets": nets, "wires": wires, "connectors": connectors, "drop": drop, "lengths": lengths,
            "report": report, "voltage_drop": voltage_drop, "net_stats": net_stats,
            **{k: getattr(H, k) for k in H.__all__}}
