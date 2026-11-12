"""The pneumatics rule table: every check the circuit grades, its source, and how a check's source follows the model (a
component's own max_bar or C, b; the kind's default rating; the estimates for tubing). See eitri.check_sources.

A check whose name matches no rule here gets no source, and tests/test_check_sources.py fails on it.
"""
from __future__ import annotations

from ..check_sources import Rule, assumption, doc, model_file, requirement, rule

SMC_LOAD_RATIO = doc("SMC Best Pneumatics, Air Cylinders Model Selection, Step 1: load ratio 0.5 moving, 0.7 static, 1.0 guided",
                     "https://ca01.smcworld.com/catalog/BEST-technical-data-en/pdf/6-2-1-m21-43-tech_en.pdf",
                     "SMC Best Pneumatics technical data, catalogue p.1570 (re-read 2026-11-07)")
ISO_6358 = doc("ISO 6358: flow through C, b (choked and subsonic)", "", "ISO 6358")
ISO_1219 = rule("an ISO 1219 circuit: every cylinder port fed through a valve and able to exhaust")
FRICTION = assumption("friction allowance 10 % of theoretical force (ESTIMATE, the usual sizing allowance)")
TUBE_ESTIMATE = assumption("tube C, b estimated (Darcy lambda 0.025, 30 bore diameters per fitting, b 0.5: ESTIMATEs; S = 5 C from SMC)")
DROP_LIMIT = assumption("pressure-drop warning above 0.3 bar or 5 % of the pressure (an eitri threshold)")
PRESSURE_DOCS = {
    "regulator": doc("SMC AR20-AR60 regulators: 1.0 MPa max (AR_EU.pdf)", "", "fetched 2026-10-26"),
    "frl": doc("Festo MS4-LFR filter regulators: lowest listed maximum 10 bar", "", "fetched 2026-10-26"),
    "valve": doc("SMC SY3000 5-port solenoid valve: 0.1-0.7 MPa (internal pilot)", "", "fetched 2026-10-26"),
    "flow_control": doc("SMC AS speed controllers: 1 MPa max", "", "fetched 2026-10-26"),
    "cylinder": doc("Festo DSNU round cylinders (ISO 6432): 10 bar max", "", "fetched 2026-10-26"),
    "silencer": doc("Festo U silencers: 0-10 bar", "", "fetched 2026-10-26"),
    "node": doc("SMC KQ2 one-touch fittings (KQ2T): -100 kPa to 1 MPa", "", "fetched 2026-10-26"),
}
FLOW_DOCS = {
    "valve": doc("SMC SY3000 series: C 1.1 dm3/(s.bar), b 0.28-0.30 (default C 1.0)", "", "smcworld SY series list"),
    "silencer": doc("SMC AN silencers: AN05-M5 C 1 dm3/(s.bar)", "https://content2.smcetech.com/pdf/AN.pdf", "SMC AN catalogue (re-read 2026-11-07)"),
    "flow_control": doc("SMC AS speed controllers: M5 S 1.5 mm2, C = S/5 = 0.3", "https://static.smc.eu/pdf/AS_EU.pdf",
                        "SMC AS_EU catalogue (re-read 2026-11-07)"),
    "regulator": assumption("regulator C 1.5, b 0.35 (ESTIMATE for a 1/8\"-1/4\" unit, no datasheet)"),
    "frl": assumption("FRL C 1.5, b 0.35 (ESTIMATE for a 1/8\"-1/4\" unit, no datasheet)"),
}
B_ESTIMATE = assumption("b 0.30 for valves, silencers and flow controls where SMC publishes none (ESTIMATE)")


def _cyl(c, circ):
    return next((circ.components[r] for r in c.refs if r in circ.components and circ.components[r].kind == "cylinder"), None)


def _force(c, circ):
    cyl = _cyl(c, circ)
    out = [requirement("load_N")]
    if c.name.endswith("retract") and cyl is not None and cyl.acting == "single":
        return out + [model_file(f"{cyl.name} spring_N")]
    out.append(SMC_LOAD_RATIO)
    if cyl is not None and abs(cyl.friction_fraction - 0.10) < 1e-12:
        out.append(FRICTION)
    elif cyl is not None:
        out.append(model_file(f"{cyl.name} friction_fraction"))
    return out


def _flow_sources(names, circ):
    out = []
    for n in names:
        comp = circ.components.get(n)
        if comp is not None:
            if getattr(comp, "has_flow_data", False):
                out.append(model_file(f"{n} C, b"))
            elif comp.kind in FLOW_DOCS:
                out.append(FLOW_DOCS[comp.kind])
                if comp.kind in ("valve", "silencer", "flow_control"):
                    out.append(B_ESTIMATE)
            continue
        tube = next((t for t in circ.tubes if t.name == n), None)
        if tube is not None:
            out.append(model_file(f"{n} C, b") if getattr(tube, "C", None) is not None else TUBE_ESTIMATE)
    return out


def _speed(c, circ):
    return [requirement("speed_mm_s"), ISO_6358] + _flow_sources(c.refs, circ)


def _drop(c, circ):
    return [ISO_6358, DROP_LIMIT] + _flow_sources(c.refs[:1], circ)


def _pressure(c, circ):
    from .model import pressure_rating
    out = []
    for r in c.refs[1:]:
        comp = circ.components.get(r)
        if comp is None:
            continue
        if comp.max_bar is not None:
            out.append(model_file(f"{r} max_bar"))
        elif comp.kind in PRESSURE_DOCS and pressure_rating(comp)[0] is not None:
            out.append(PRESSURE_DOCS[comp.kind])
    src = circ.components.get(c.refs[0]) if c.refs else None
    if src is not None:
        out.insert(0, model_file(f"{src.name} {'pressure_bar' if src.kind == 'supply' else 'set_bar'}"))
    return out


def _estimated(c, circ):
    return [rule("every component graded with estimated flow data is named")] + _flow_sources(c.refs, circ)


P = "pneumatics."
RULES = [
    Rule(P + "supply", "circuit", r"a supply$", (rule("a circuit has an air supply"),)),
    Rule(P + "actuator", "circuit", r"an actuator$", (rule("a circuit has a cylinder to size"),)),
    Rule(P + "separate-lines", "circuit", r".+\.CAP and .+\.ROD on separate lines$", (rule("a double-acting cylinder's two chambers are on separate lines"),)),
    Rule(P + "fed", "circuit", r".+ fed from supply$", (ISO_1219,)),
    Rule(P + "exhaust", "circuit", r".+ can exhaust$", (ISO_1219,)),
    Rule(P + "valve-ports", "circuit", r"valve .+ ports$", (rule("a valve's P is supplied and every work port connected"),)),
    Rule(P + "dead-headed", "circuit", r"no dead-headed lines$", (rule("every line leads somewhere (no tee or inline part with one connection)"),)),
    Rule(P + "force", "force", r"force ", (requirement("load_N"), SMC_LOAD_RATIO, FRICTION, model_file("spring_N")), _force),
    Rule(P + "speed", "speed", r"speed ", (requirement("speed_mm_s"), ISO_6358, TUBE_ESTIMATE, B_ESTIMATE) + tuple(FLOW_DOCS.values()), _speed),
    Rule(P + "pressure-drop", "pressure drop", r"pressure drop ", (ISO_6358, DROP_LIMIT, TUBE_ESTIMATE), _drop),
    Rule(P + "air", "air", r"air ", (doc("free-air consumption per stroke V = A x stroke x p_abs / P_ATM (ISO 1217 basis, ANR)", "", "ISO 1217"),)),
    Rule(P + "supply-capacity", "air", r"supply .+ capacity$", (model_file("supply flow_l_min"),)),
    Rule(P + "pressure-ratings", "pressure", r"pressure ratings downstream of ", tuple(PRESSURE_DOCS.values()) + (model_file("max_bar"),), _pressure),
    Rule(P + "flow-data", "flow data", r"flow data estimated$", (rule("every component graded with estimated flow data is named"),) + tuple(FLOW_DOCS.values()),
         _estimated),
]
