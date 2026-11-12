"""The pneumatics rule table: every check the circuit grades, its source, and how a check's source follows the model (a
component's own max_bar or C, b; the kind's default rating; the estimates for tubing). See eitri.check_sources.

A check whose name matches no rule here gets no source, and tests/test_check_sources.py fails on it.
"""
from __future__ import annotations

import re

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
    "valve": doc("SMC SY3000 body ported, 2 position single, P->A/B: C 0.61-0.76, b 0.29-0.44; default the lowest, C 0.6 b 0.29",
                 "https://static.smc.eu/pdf/SY_EU.pdf", "SMC CAT.EUS11-83 (SY_EU.pdf) p.3 Flow characteristics, re-read 2026-11-12"),
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
            out += _part_docs(comp, "max_bar")
        elif comp.kind in PRESSURE_DOCS and pressure_rating(comp)[0] is not None:
            out.append(PRESSURE_DOCS[comp.kind])
    src = circ.components.get(c.refs[0]) if c.refs else None
    if src is not None:
        out.insert(0, model_file(f"{src.name} {'pressure_bar' if src.kind == 'supply' else 'set_bar'}"))
    return out


# The catalogue parts' documents (eitri.pneumatics.catalog SOURCES, fetched 2026-11-12) and the documents the newer checks cite.
def _cat_doc(sid: str):
    from .catalog import SOURCES
    s = SOURCES[sid]
    return doc(s.doc, s.url, f"{s.doc}; fetched {s.fetched}")


SMC_RB = doc("SMC Absorber Series RB, Model Selection Note 1: collision speed v = 2 x the average speed; E = 1/2 m v^2",
             "https://static.smc.eu/pdf/RB_EU.pdf", "SMC RB_EU.pdf p.5, fetched 2026-11-12")
SMC_VALVE_PRECAUTIONS = doc("SMC 3/4/5-port solenoid valve precautions M-E03-3-02, p.1 items 3 and 7, p.2 (double solenoid)",
                            "https://www.smcworld.com/upfiles/pl/M-E03-3-02.pdf", "SMC M-E03-3-02, fetched 2026-11-12")
SMC_SY_SYMBOLS = doc("SMC SY series CAT.EUS11-83: the valves' ISO 1219 symbols (rest positions, centres)", "https://static.smc.eu/pdf/SY_EU.pdf",
                     "SMC CAT.EUS11-83 (SY_EU.pdf) p.1-3, fetched 2026-11-12")
THEORETICAL_FORCE = rule("theoretical force = pressure x piston area (rod side: minus the rod's area)")


def _part_docs(comp, field_name: str):
    """A component's catalogue documents when it was filled from a part number (part=), else the model file's own figure."""
    ids = list(((getattr(comp, "catalog", None) or {}).get("source_ids")) or [])
    if ids:
        try:
            return [_cat_doc(i) for i in ids]
        except KeyError:
            pass
    return [model_file(f"{getattr(comp, 'name', '?')} {field_name}")]


def _ref_comp(c, circ, kinds=None):
    for r in c.refs:
        comp = circ.components.get(r)
        if comp is not None and (kinds is None or comp.kind in kinds):
            return comp
    return None


def _datasheet_force(c, circ):
    cyl = _cyl(c, circ)
    return [THEORETICAL_FORCE] + (_part_docs(cyl, "force figures") if cyl is not None else [])


def _min_pressure(c, circ):
    comp = _ref_comp(c, circ)
    return _part_docs(comp, "min_bar") if comp is not None else []


def _piston_speed(c, circ):
    cyl = _cyl(c, circ)
    return [requirement("speed_mm_s")] + (_part_docs(cyl, "speed range") if cyl is not None else [])


def _cushioning(c, circ):
    cyl = _cyl(c, circ)
    out = [model_file(f"{cyl.name} load_mass_kg") if cyl is not None else model_file("load_mass_kg"), SMC_RB]
    return out + (_part_docs(cyl, "allowable_ke_J") if cyl is not None else [])


def _coil(c, circ):
    v = _ref_comp(c, circ, ("valve",))
    return [requirement("control_voltage")] + (_part_docs(v, "coil_V") if v is not None else [])


def _sensor(c, circ):
    m = re.match(r"sensor (\S+) ", c.name)
    s = circ.sensors.get(m.group(1)) if m else None
    out = _part_docs(s, "voltage range / fits") if s is not None else []
    if c.category == "electrical":
        return [requirement("control_voltage")] + out
    cyl = _cyl(c, circ)
    return out + (_part_docs(cyl, "magnet") if cyl is not None else [])


def _failsafe(c, circ):
    attr = "on_power_loss" if "electrical power" in c.name else "on_air_loss"
    cyl = _cyl(c, circ)
    return [requirement(f"{cyl.name if cyl is not None else ''} {attr}".strip()), SMC_VALVE_PRECAUTIONS, SMC_SY_SYMBOLS]


def _manifold_doc(c, circ):
    m = _ref_comp(c, circ, ("manifold",))
    return _part_docs(m, "stations") if m is not None else []


def _manifold_stations(c, circ):
    return [rule("a manifold base holds at most its stations' valves; an empty station takes a blanking plate")] + _manifold_doc(c, circ)


def _manifold_supply(c, circ):
    return ([ISO_6358, DROP_LIMIT, rule("simultaneous demand: the declared sequence's steps, else every cylinder at once")]
            + _manifold_doc(c, circ) + _flow_sources(c.refs[1:], circ))


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
    Rule(P + "air", "air", r"air ", (doc("free-air consumption per stroke V = A x stroke x p_abs / P_ATM (ISO 1217 basis, ANR)", "", "ISO 1217"),
                                     _cat_doc("smc-best-tech"))),
    Rule(P + "supply-capacity", "air", r"supply .+ capacity$", (model_file("supply flow_l_min"),)),
    Rule(P + "pressure-ratings", "pressure", r"pressure ratings downstream of ", tuple(PRESSURE_DOCS.values()) + (model_file("max_bar"),), _pressure),
    Rule(P + "datasheet-force", "force", r"datasheet force ", (THEORETICAL_FORCE, _cat_doc("festo-dsnu-20-50-p-a"), _cat_doc("smc-cq2")),
         _datasheet_force),
    Rule(P + "minimum-pressure", "pressure", r"minimum pressure ", (_cat_doc("smc-cq2-z"), _cat_doc("festo-dsnu-20-50-p-a"), _cat_doc("smc-sy"),
                                                                   model_file("min_bar")), _min_pressure),
    Rule(P + "piston-speed-range", "speed", r"piston speed range ", (requirement("speed_mm_s"), _cat_doc("smc-cq2-z"), model_file("speed range")),
         _piston_speed),
    Rule(P + "cushioning", "cushioning", r"end-of-stroke energy ", (model_file("load_mass_kg"), SMC_RB, _cat_doc("smc-cq2-z"),
                                                                    _cat_doc("festo-dsnu-20-50-p-a"), model_file("allowable_ke_J")), _cushioning),
    Rule(P + "coil-voltage", "electrical", r"coil voltage ", (requirement("control_voltage"), _cat_doc("smc-sy"), model_file("coil_V")), _coil),
    Rule(P + "sensor-voltage", "electrical", r"sensor .+ voltage$", (requirement("control_voltage"), _cat_doc("smc-d-m9"),
                                                                      _cat_doc("festo-smt-8m-a"), model_file("V_min, V_max")), _sensor),
    Rule(P + "sensor-target", "sensors", r"sensor .+ target$", (_cat_doc("smc-d-m9"), _cat_doc("festo-smt-8m-a"), _cat_doc("smc-cq2-z"),
                                                                 model_file("magnet")), _sensor),
    Rule(P + "fail-safe", "fail-safe", r"on loss of ", (requirement("on_power_loss / on_air_loss"), SMC_VALVE_PRECAUTIONS,
                                                                                SMC_SY_SYMBOLS), _failsafe),
    Rule(P + "manifold-stations", "manifold", r"manifold .+ stations$",
         (rule("a manifold base holds at most its stations' valves; an empty station takes a blanking plate"), _cat_doc("smc-sy-manifold"),
          model_file("stations")), _manifold_stations),
    Rule(P + "manifold-supply", "manifold", r"manifold .+ common supply$",
         (ISO_6358, DROP_LIMIT, rule("simultaneous demand: the declared sequence's steps, else every cylinder at once"), _cat_doc("smc-sy-manifold"),
          TUBE_ESTIMATE), _manifold_supply),
    Rule(P + "flow-data", "flow data", r"flow data estimated$", (rule("every component graded with estimated flow data is named"),) + tuple(FLOW_DOCS.values()),
         _estimated),
]
