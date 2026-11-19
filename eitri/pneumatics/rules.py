"""The pneumatics rule table: every check the circuit grades, its source, and how a check's source follows the model (a
component's own max_bar or C, b; the kind's default rating; the estimates for tubing). See eitri.check_sources.

A check whose name matches no rule here gets no source, and tests/test_check_sources.py fails on it.
"""
from __future__ import annotations

import re

from ..check_sources import Rule, assumption, doc, model_file, requirement, rule

SMC_LOAD_RATIO = doc("SMC Best Pneumatics, Air Cylinders Model Selection, Step 1: load ratio 0.5 moving, 0.7 static, 1.0 guided",
                     "https://ca01.smcworld.com/catalog/BEST-technical-data-en/pdf/6-2-1-m21-43-tech_en.pdf",
                     "SMC Best Pneumatics technical data 'Technical data for air cylinders' (6-2-1-m21-43-tech_en.pdf, no catalogue number "
                     "printed; PDF dated 2026-10-15), Step 1 load factor, catalogue p.1570; re-read 2026-11-18")
ISO_6358 = doc("ISO 6358: flow through C, b (choked and subsonic)", "https://www.iso.org/standard/56612.html",
               "ISO 6358-1:2013 (+Amd 1:2020), -2:2019, -3:2014 (paywalled, not read: the formula as SMC and Festo print it in their "
               "sizing guides); catalogue checked 2026-11-17")
ISO_1219 = rule("an ISO 1219 circuit: every cylinder port fed through a valve and able to exhaust")
FRICTION = assumption("friction allowance 10 % of theoretical force (ESTIMATE, the usual sizing allowance)")
TUBE_ESTIMATE = assumption("tube C, b estimated (Darcy lambda 0.025, 30 bore diameters per fitting, b 0.5: ESTIMATEs; S = 5 C from SMC)")
DROP_LIMIT = assumption("pressure-drop warning above 0.3 bar or 5 % of the pressure (an eitri threshold)")
PRESSURE_DOCS = {       # each figure re-read in the document on 2026-11-17
    "regulator": doc("SMC AR20-AR60 regulators: maximum operating pressure 1.0 MPa (AR_EU.pdf)", "https://static.smc.eu/pdf/AR_EU.pdf",
                     "SMC 'Regulator AR10 to 60' (AR_EU.pdf, printed pp.36-41, no catalogue number printed; PDF dated 2004-04-26), "
                     "p.36 Standard specifications; read 2026-11-17"),
    "frl": doc("Festo MS4-LFR filter regulators: lowest listed maximum 10 bar",
               "https://ftp.festo.com/public/pneumatic/SOFTWARE_SERVICE/DataSheet/EN_US/8098262.pdf",
               "Festo data sheet MS4-LFR-1/4-D6-E-P-VC-A8-B, part 8098262 (10/9/26), p.1 'Operating pressure 2 bar...10 bar' "
               "(parts 8098258 / 8175799: 1-10 bar; D7 variants, 526489: 0.8-14 bar); read 2026-11-17"),
    "valve": doc("SMC SY3000 5-port solenoid valve, internal pilot: maximum 0.7 MPa (0.15-0.7 single, 0.1-0.7 double solenoid)",
                 "https://static.smc.eu/pdf/SY_EU.pdf", "SMC CAT.EUS11-83 (SY_EU.pdf), printed p.2 Body Ported Specifications, operating "
                 "pressure range, internal pilot; read 2026-11-17"),
    "flow_control": doc("SMC AS speed controllers: maximum operating pressure 1 MPa", "https://static.smc.eu/pdf/AS_EU.pdf",
                        "SMC Speed Controller Series AS (AS_EU.pdf, no catalogue number printed; PDF dated 2021-05-11), p.1 Specifications; "
                        "read 2026-11-17"),
    "cylinder": doc("Festo DSNU round cylinders (ISO 6432): 10 bar max, bores 8-63",
                    "https://ftp.festo.com/public/PNEUMATIC/SOFTWARE_SERVICE/Documentation/2016/US/DSNU_ENUS.PDF",
                    "Festo catalogue 'Round cylinders DSNU/DSNUP/DSN/ESNU/ESN' 2016/03, p.13 Technical data; read 2026-11-17"),
    "silencer": doc("Festo U silencers: 0-10 bar", "https://ftp.festo.com/public/PNEUMATIC/SOFTWARE_SERVICE/DataSheet/EN_US/2307.pdf",
                    "Festo data sheet U-1/8, part 2307 (10/8/26), p.1 'Operating pressure 0 bar...10 bar' (U-M5, part 4645, 04/01/2014: "
                    "'Working pressure 0 ... 10 bar'); read 2026-11-17"),
    "node": doc("SMC KQ2 one-touch fittings (KQ2T): -100 kPa to 1 MPa", "https://static.smc.eu/pdf/KQ2_EU.pdf",
                "SMC One-touch Fittings Series KQ2 (KQ2_EU.pdf, no catalogue number printed; PDF dated 2010-06-25), p.2 Specifications "
                "'Max. operating pressure 1.0MPa', 'Operating vacuum pressure -100kPa'; read 2026-11-17"),
}
FLOW_DOCS = {
    "valve": doc("SMC SY3000 body ported, 2 position single, P->A/B: C 0.61-0.76, b 0.29-0.44; default the lowest, C 0.6 b 0.29",
                 "https://static.smc.eu/pdf/SY_EU.pdf", "SMC CAT.EUS11-83 (SY_EU.pdf) p.3 Flow characteristics, re-read 2026-11-12"),
    "silencer": doc("SMC AN silencers: AN05-M5 C 1 dm3/(s.bar)", "https://content2.smcetech.com/pdf/AN.pdf", "SMC Silencer Series AN (AN.pdf, catalogue p.1195, no catalogue number printed; PDF dated 2024-05-23), sonic conductance "
                    "table; re-read 2026-11-18"),
    "flow_control": doc("SMC AS speed controllers: M5 S 1.5 mm2, C = S/5 = 0.3", "https://static.smc.eu/pdf/AS_EU.pdf",
                        "SMC Speed Controller Series AS (AS_EU.pdf, no catalogue number printed; PDF dated 2021-05-11), Air Flow/Effective Area; "
                        "read 2026-11-07"),
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
             "https://static.smc.eu/pdf/RB_EU.pdf", "SMC Shock Absorber RB series (RB_EU.pdf, back page 'Printing DZ 00'; PDF dated 2026-02-24), p.5 Note 1); re-read 2026-11-18")
SMC_VALVE_PRECAUTIONS = doc("SMC 3/4/5-port solenoid valve precautions M-E03-3-02, p.1 items 3 and 7, p.2 (double solenoid)",
                            "https://www.smcworld.com/upfiles/pl/M-E03-3-02.pdf", "SMC M-E03-3-02 (no revision printed; PDF dated 2024-09-11), items re-read 2026-11-18")
SMC_SY_SYMBOLS = doc("SMC SY series CAT.EUS11-83: the valves' ISO 1219 symbols (rest positions, centres)", "https://static.smc.eu/pdf/SY_EU.pdf",
                     "SMC CAT.EUS11-83 (SY_EU.pdf) p.1-3, fetched 2026-11-12")
SMC_ACTUATOR_PRECAUTIONS = doc("SMC Actuators Precautions M-E03-3-03, p.1 items 8 (lurching when starting up after residual pressure is "
                               "exhausted), 9 (emergency stop), 11 (restarting), 12 (closed centre does not hold)",
                               "https://www.smcworld.com/upfiles/pl/M-E03-3-03.pdf", "SMC M-E03-3-03 (no revision printed; PDF dated 2024-09-11), items re-read 2026-11-18")
SMC_SAFETY = doc("SMC Safety Instructions M-E03-3-01 p.1 item 3: before restart, take measures to prevent unexpected operation",
                 "https://www.smcworld.com/upfiles/pl/M-E03-3-01.pdf", "SMC M-E03-3-01 (no revision printed; PDF dated 2024-02-05), item re-read 2026-11-18")
FESTO_VUVG = doc("Festo VUVG: data sheet 574371 (internal pilot air, pilot pressure 0.3-0.8 MPa, mechanical spring reset); catalogue "
                 "2010/08 p.12 (5/2 spring box 1-2, 4-5; 5/3 C / U / E normal positions)",
                 "https://ftp.festo.com/public/PNEUMATIC/SOFTWARE_SERVICE/DataSheet/EN_GB/574371.pdf", "Festo 574371 (10/8/26); VUVG catalogue 2010/08")
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


def _regulator_setting(c, circ):
    r = _ref_comp(c, circ, ("regulator", "frl"))
    return [requirement(f"{r.name} set_bar" if r is not None else "set_bar")] + (_part_docs(r, "set_min_bar, set_max_bar") if r is not None else [])


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


def _start_state(c, circ):
    cyl = _cyl(c, circ)
    out = [requirement("c.start / c.step (the idle state and sequence)"), SMC_SY_SYMBOLS, SMC_VALVE_PRECAUTIONS, SMC_ACTUATOR_PRECAUTIONS, FESTO_VUVG]
    if cyl is not None and circ.safeguards_for(cyl.name, "start"):
        out.append(model_file(f"{cyl.name} safeguard (declared, for a person to verify)"))
    return out


def _air_return(c, circ):
    cyl = _cyl(c, circ)
    out = [SMC_ACTUATOR_PRECAUTIONS, SMC_SAFETY, SMC_VALVE_PRECAUTIONS, _cat_doc("smc-sy"), FESTO_VUVG]
    if cyl is not None and circ.safeguards_for(cyl.name, "air return"):
        out.append(model_file(f"{cyl.name} safeguard (declared, for a person to verify)"))
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
    Rule(P + "valve-ports", "circuit", r"valve .+ ports$", (rule("a valve's supply port (1) is fed and every work port (2, 4) connected"),)),
    Rule(P + "dead-headed", "circuit", r"no dead-headed lines$", (rule("every line leads somewhere (no tee or inline part with one connection)"),)),
    Rule(P + "force", "force", r"force ", (requirement("load_N"), SMC_LOAD_RATIO, FRICTION, model_file("spring_N")), _force),
    Rule(P + "speed", "speed", r"speed ", (requirement("speed_mm_s"), ISO_6358, TUBE_ESTIMATE, B_ESTIMATE) + tuple(FLOW_DOCS.values()), _speed),
    Rule(P + "pressure-drop", "pressure drop", r"pressure drop ", (ISO_6358, DROP_LIMIT, TUBE_ESTIMATE), _drop),
    Rule(P + "air", "air", r"air ", (doc("free-air consumption per stroke V = A x stroke x p_abs / P_ATM (ISO 1217 basis, ANR)", "https://www.iso.org/standard/44769.html",
                                         "ISO 1217:2009 (+Amd 1:2016), paywalled, not read; ANR per ISO 8778:2003 Table 1 is 100 kPa, "
                                         "20 C, 65 % (eitri divides by 1.01325 bar: 1.3 % lower); catalogue checked 2026-11-17"),
                                     _cat_doc("smc-best-tech"))),
    Rule(P + "supply-capacity", "air", r"supply .+ capacity$", (model_file("supply flow_l_min"),)),
    Rule(P + "pressure-ratings", "pressure", r"pressure ratings downstream of ", tuple(PRESSURE_DOCS.values()) + (model_file("max_bar"),), _pressure),
    Rule(P + "datasheet-force", "force", r"datasheet force ", (THEORETICAL_FORCE, _cat_doc("festo-dsnu-20-50-p-a"), _cat_doc("smc-cq2"),
                                                               _cat_doc("festo-dsnu-cat"), _cat_doc("festo-dsbc"), _cat_doc("smc-mgp-z")),
         _datasheet_force),
    Rule(P + "minimum-pressure", "pressure", r"minimum pressure ", (_cat_doc("smc-cq2-z"), _cat_doc("festo-dsnu-20-50-p-a"), _cat_doc("smc-sy"),
                                                                   _cat_doc("festo-dsnu-cat"), _cat_doc("festo-dsbc"), _cat_doc("smc-mgp-z"),
                                                                   model_file("min_bar")), _min_pressure),
    Rule(P + "regulator-setting", "pressure", r"regulator .+ setting$", (requirement("set_bar"), _cat_doc("smc-aw"), _cat_doc("festo-ms4-lfr"),
                                                                      model_file("set_min_bar, set_max_bar")), _regulator_setting),
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
    Rule(P + "sequence-declared", "sequence", r"sequence declared$", (requirement("c.step(...) sequence"),
                                                                       rule("a controller-run circuit declares its step sequence"))),
    Rule(P + "step-ends-on-sensor", "sequence", r"step .+ ends on a sensor$",
         (requirement("the step sequence (c.step until=)"), rule("a step ends on a sensor that exists, at the end its move reaches"))),
    Rule(P + "step-valve-commands", "sequence", r"step .+ valve commands$",
         (requirement("the step sequence (c.step moves)"), SMC_VALVE_PRECAUTIONS, SMC_SY_SYMBOLS,
          rule("a move is a valve position the controller can switch to; one position per valve per step"))),
    Rule(P + "sequence-closes", "sequence", r"sequence returns to its start$", (rule("every cylinder ends the cycle where it started"),)),
    Rule(P + "cycle-time", "cycle time", r"cycle time estimate$",
         (requirement("cycles_per_min"), _cat_doc("smc-sy"), _cat_doc("festo-smt-8m-a"), ISO_6358,
          assumption("cycle time is a lower bound: pressure build-up, load acceleration, controller scan and cushioning are left out; a sensor "
                     "with no switch-on time is taken at 1.3 ms (Festo SMT-8M-A)"))),
    Rule(P + "start-state", "fail-safe", r"start state", (requirement("c.start / c.step (the idle state and sequence)"), SMC_SY_SYMBOLS,
                                                          SMC_VALVE_PRECAUTIONS, SMC_ACTUATOR_PRECAUTIONS, FESTO_VUVG, model_file("safeguard")), _start_state),
    Rule(P + "air-return", "fail-safe", r"air return ", (SMC_ACTUATOR_PRECAUTIONS, SMC_SAFETY, SMC_VALVE_PRECAUTIONS, _cat_doc("smc-sy"),
                                                         FESTO_VUVG, model_file("safeguard")), _air_return),
    Rule(P + "flow-data", "flow data", r"flow data estimated$", (rule("every component graded with estimated flow data is named"),) + tuple(FLOW_DOCS.values()),
         _estimated),
]
