"""What the agent's `inspect` tool has in scope for a pneumatics project.

    c                      the built Circuit (also `parts`); p is PARAMS
    cylinders()            force, load ratio, consumption and achievable speed of every cylinder
    paths()                the fill and exhaust path of every cylinder port with its effective conductance
    flow(C, b, p1, p2)     ISO 6358 flow (L/s ANR) for absolute pressures
    tube_C(id_mm, length_m, fittings=2)   the tube conductance estimate
    report()               the full text report again
"""
from __future__ import annotations

from .checks import find_path
from .flow import drop_for_flow, flow_l_s, required_flow_l_s, series, tube_conductance
from .model import Circuit


def circuit_scope(c: Circuit, p=None) -> dict:
    import eitri.pneumatics as P
    from .report import analyze, summarize

    def cylinders():
        rep = analyze(c)
        for cy in rep.cylinders:
            print(f"{cy.name}: Ø{cy.bore_mm:g}/{cy.rod_mm:g} x {cy.stroke_mm:g} mm, {cy.acting}-acting" + (f", {cy.pressure_bar:g} bar at the cylinder" if cy.pressure_bar is not None else ", no supply"))
            for s in cy.strokes:
                bits = []
                if s.usable_N is not None:
                    bits.append(f"{s.usable_N:.0f} N usable vs {s.load_N:g} N load" + (f" (ratio {s.load_ratio:.2f})" if s.load_ratio is not None else ""))
                if s.speed_required is not None:
                    bits.append(f"speed {s.speed_available:.0f} of {s.speed_required:g} mm/s" if s.speed_available is not None else f"speed {s.speed_required:g} mm/s required, not graded")
                print(f"   {s.name}: " + "; ".join(bits) + f"; {s.status}")
            print(f"   air {cy.air_per_cycle_l:.3f} L/cycle, {cy.air_per_minute_l:.1f} L/min")

    def paths():
        for cy in c.cylinders:
            for port in cy.ports:
                for goal in ("supply", "exhaust"):
                    pi = find_path(c, cy.port(port), goal)
                    if pi.found:
                        print(f"{cy.name}.{port} {goal}: {' -> '.join(pi.names)}; C_eff {pi.C_eff:.2f} L/(s·bar), b {pi.b_eff:.2f}" + (f"; {pi.pressure_bar:g} bar" if pi.pressure_bar is not None else "")
                              + (f"; estimated: {', '.join(s.name for s in pi.steps if s.estimated)}" if any(s.estimated for s in pi.steps) else ""))
                    else:
                        print(f"{cy.name}.{port} {goal}: no path")

    def report():
        print(summarize(analyze(c)))

    return {"c": c, "parts": c, "p": p, "cylinders": cylinders, "paths": paths, "flow": flow_l_s, "drop_for_flow": drop_for_flow, "series": series,
            "tube_C": tube_conductance, "required_flow": required_flow_l_s, "report": report, **{k: getattr(P, k) for k in P.__all__}}
