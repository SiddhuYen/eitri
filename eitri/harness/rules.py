"""The harness rule table: every check the harness grades, its source and how a check's source follows the model (a
connector family's datasheet, a board's supply pin). The figures themselves stay where they are (catalog.py, eitri.power);
this file only names, for each check, the document its limit comes from. See eitri.check_sources.

A check whose name matches no rule here gets no source, and tests/test_check_sources.py fails on it.
"""
from __future__ import annotations

import re

from .. import power
from ..check_sources import Rule, assumption, doc, model_file, requirement, rule
from .catalog import CATALOG, normalize_type

# The documents behind catalog.py (its RATING_SOURCES / AWG_SOURCES text and comments), as structured records. A URL is given only
# where the table gives one; a table that names only a site keeps it in the text.
CHASSIS_AMPACITY = doc("chassis-wiring ampacity, Handbook of Electronic Tables and Formulas for AWG (PowerStream table)",
                       "https://www.powerstream.com/Wire_Size.htm", "PowerStream Wire_Size page dated 2023-04-18, fetched 2026-10-26")
NEC_TABLE_8 = doc("NFPA 70 (NEC) Chapter 9 Table 8, copper at 75 C (K = 12.9)", "", "NFPA 70 Chapter 9 Table 8")
DROP_DEFAULT = assumption("max_drop_pct is 3 % when the model does not give it: no cited source (the usual NEC 210.19 informational-note "
                          "figure; its wording and number vary by edition and were not checked)")
TE_FASTON = doc("TE 108-106496 Rev C (FASTON terminals), s3.2 by wire size", "", "TE product specification 108-106496 Rev C, 27 Jun 2025")
MOLEX_PS43045 = doc("Molex PS-43045 Rev P s4.2 (Micro-Fit 3.0, wire-to-wire)", "https://cdn.eicom.ru/media/PDF/183992.pdf",
                    "Molex PS-43045 Product Specification Rev P, 2017-10-27 (copy at cdn.eicom.ru; molex.com timed out)")
RATING_DOCS = {
    "JST-XH": doc("JST XH series page (jst-mfg.com, series 277): 3 A", "", "fetched 2026-10-26"),
    "JST-PH": doc("JST PH series page (jst-mfg.com, series 199): 2 A", "", "fetched 2026-10-26"),
    "DUPONT-2.54": doc("Amphenol FCI Mini-PV 48235-000LF: 3 A (as listed by Heilind / RS)", "", "fetched 2026-10-26"),
    "MOLEX-MICROFIT-3.0": MOLEX_PS43045,
    "XT30": doc("Amass XT30U-M product page (china-amass.net): 20 A", "", "fetched 2026-10-26"),
    "XT60": doc("Amass XT60 datasheet V1.2: 30 A, 60 A instantaneous", "",
                "Amass XT60 datasheet V1.2; XT60-M page re-read 2026-11-07 (35 A there; the lower 30 A is used)"),
    "XT90": doc("Amass XT90E-M product page (china-amass.net): 45 A; burst 90 A from Holybro's AMASS rating table", "", "fetched 2026-10-26"),
    "SPADE-2.8": TE_FASTON, "SPADE-4.8": TE_FASTON, "SPADE-6.3": TE_FASTON,
}
AWG_DOCS = {
    "JST-XH": doc("JST XH series page (series 277): AWG #30 to #22", "", "fetched 2026-10-26"),
    "JST-PH": doc("JST PH series page (series 199): AWG #32 to #24", "", "fetched 2026-10-26"),
    "DUPONT-2.54": doc("Amphenol FCI Mini-PV contacts 48235-000LF 22-26 AWG, 48237-000LF 28-32 AWG (Heilind, Farnell)", "", "fetched 2026-10-26"),
    "MOLEX-MICROFIT-3.0": doc("Molex Micro-Fit 3.0 43030 terminals: 18-30 AWG (element14, RS)", "", "fetched 2026-10-26"),
}

# ---------------------------------------------------------------------------- resolvers: (check, harness) -> sources


def _connector(h, c):
    for r in c.refs:
        if r in h.connectors:
            return h.connectors[r]
    return None


def _connector_rating(c, h):
    con = _connector(h, c)
    if con is None:
        return []
    if con.rating_a is not None:
        return [model_file(f"{con.name} rating_a")]
    if con.spec is not None and con.spec.by_wire:
        return [MOLEX_PS43045 if con.spec.key == "MOLEX-MICROFIT-3.0" else TE_FASTON]
    key = normalize_type(con.type)
    if key in RATING_DOCS:
        return [RATING_DOCS[key]]
    return [rule("an unknown connector type is graded only with rating_a")]


def _terminal_fit(c, h):
    con = _connector(h, c)
    key = normalize_type(con.type) if con is not None else ""
    return [AWG_DOCS[key]] if key in AWG_DOCS else []


def _drop(c, h):
    m = re.match(r"voltage drop (\S+)", c.name)
    net = h.nets.get(m.group(1)) if m else None
    what = f"{net.name} max_drop_pct {net.max_drop_pct:g} %" if net is not None else "max_drop_pct"
    out = [requirement(what), NEC_TABLE_8]
    if net is not None and net.max_drop_pct == 3.0:        # the default: the model may not have given it
        out.append(DROP_DEFAULT)
    return out


def _supply(c, h):
    con = _connector(h, c)
    if con is None:
        return []
    m = re.match(rf"supply {re.escape(con.name)}\.(\S+) \(", c.name)
    sp = power.supply_pin_name(m.group(1)) if m else None
    from .catalog import BOARD_TYPES
    if sp is None or con.type not in BOARD_TYPES:
        return []
    lim = power.supply_limit(BOARD_TYPES[con.type], sp, con.powered, con.vin_V)
    return power.power_sources(lim.source) or [rule(f"not graded: {lim.basis}")]


R = "harness."
RULES = [
    Rule(R + "pins-connected", "connectivity", r"all pins connected or declared NC$", (rule("every connector pin is wired or declared NC"),)),
    Rule(R + "one-wire-per-pin", "connectivity", r"one wire per pin", (rule("one wire per pin; wires meet only at a splice"),)),
    Rule(R + "no-duplicate-wires", "connectivity", r"no duplicate wires$", (rule("each pair of endpoints is joined once"),)),
    Rule(R + "no-dangling-splices", "connectivity", r"no dangling splices$", (rule("a splice joins two or more wires"),)),
    Rule(R + "splice-one-net", "connectivity", r"splices join one net$", (rule("a splice joins wires of one net"),)),
    Rule(R + "wire-length", "connectivity", r"every wire has a length$", (rule("every wire has a length > 0"),)),
    Rule(R + "wire-net", "connectivity", r"every wire belongs to a net$", (rule("every wire is on a net (else it is not graded)"),)),
    Rule(R + "net-used", "connectivity", r"every net is used$", (rule("every declared net carries a wire"),)),
    Rule(R + "wire-ampacity", "ampacity", r"ampacity ", (CHASSIS_AMPACITY,)),
    Rule(R + "connector-rating", "ampacity", r"connector rating ", tuple(dict.fromkeys(RATING_DOCS.values())) + (model_file("rating_a"),),
         _connector_rating),
    Rule(R + "terminal-fit", "terminal fit", r"wires fit the ", tuple(AWG_DOCS.values()), _terminal_fit),
    Rule(R + "voltage-drop", "voltage drop", r"voltage drop ", (requirement("max_drop_pct"), NEC_TABLE_8, DROP_DEFAULT), _drop),
    Rule(R + "one-gauge-per-net", "consistency", r"one gauge per net$", (rule("a thinner wire carries no more than a thicker one of its net"),)),
    Rule(R + "one-colour-per-net", "consistency", r"one colour per net$", (rule("one wire colour per net (a convention)"),)),
    Rule(R + "shield-terminated", "consistency", r"shield terminated: ", (rule("a cable's shield is connected at one end or both"),)),
    Rule(R + "board-supply-pin", "power", r"supply ", tuple(dict.fromkeys(power.POWER_DOCS.values())), _supply),
    Rule(R + "shared-ground", "power", r"shared ground ", (rule("a load that takes a board's signal shares the board's ground"),)),
]
