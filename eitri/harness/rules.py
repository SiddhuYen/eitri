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


# ---------------------------------------------------------------------------- overcurrent protection (eitri.harness.protection)
# The protection checks cite a short source in their text ("[source: Littelfuse ATOF 287 datasheet rev. 12/06/2022, p.1]");
# eitri.harness.fuses.FUSE_SOURCES holds each one's full quote with the address the document was read at. Here they become records:
# the short cite as the text (it names the revision and page), the address from the quote as the URL.
_ADDR = re.compile(r"((?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s,;)']+)")


def _fuse_doc(short: str):
    from .fuses import FUSE_SOURCES
    full = FUSE_SOURCES.get(short)
    if full is None:
        return None
    m = _ADDR.search(full)
    return doc(short, "https://" + m.group(1).rstrip(".") if m else "", short)


def _fuse_docs(*shorts):
    return tuple(d for d in (_fuse_doc(x) for x in shorts) if d is not None)


def _all_fuse_docs():
    from .fuses import FUSE_SOURCES
    return _fuse_docs(*FUSE_SOURCES)


GIVEN = "as given in the model file (not from eitri's catalog)"
OVERCURRENT = rule("a branch from a declared source passes an overcurrent device before any wire thinner than what the source can deliver")
DECLARED = rule("every supply net is fed by a declared source (h.source) or marked a signal net")
SCOPE = rule("eitri's overcurrent checks cover DC up to 60 V; mains and high voltage are out of scope (IEC 60364, NFPA 70)")
AMBIENT_DEFAULT = assumption("40 C ambient around the fuses when Harness(ambient_C=) is not given (a warm machine enclosure)")


def _cited(c):
    """The short sources the check's text cites, as records (a model-given figure: the model file)."""
    out = []
    for m in re.findall(r"\[source: ([^\]]+)\]", f"{c.measured} {c.expected}"):
        for part in [x.strip() for x in m.split("; ") if x.strip()]:
            if part == GIVEN:
                out.append(model_file("the device's own figure"))
                continue
            d = _fuse_doc(part)
            if d is not None:
                out.append(d)
    return out


def _protection(extra):
    def resolve(c, h):
        out = _cited(c) + list(extra(c, h) if callable(extra) else extra)
        if "ASSUMPTION: Harness(ambient_C" in c.measured:
            out.append(AMBIENT_DEFAULT)
        return out
    return resolve


def _device(c, h):
    dev = h.connectors.get(c.refs[0]) if c.refs else None
    return getattr(dev, "device", None)


def _device_docs(*keys):
    """The device's own sources for these figures (a catalog part's datasheet; a part the model describes: the model file)."""
    def extra(c, h):
        d = _device(c, h)
        out = []
        for k in keys:
            t = (getattr(d, "sources", None) or {}).get(k) if d is not None else None
            if t == GIVEN:
                out.append(model_file(f"{c.refs[0]} {k}"))
            elif t:
                out.append(_fuse_doc(t) or model_file(f"{c.refs[0]} {k}"))
        return out
    return extra


def _both(*fns):
    return lambda c, h: [x for f in fns for x in (f(c, h) if callable(f) else f)]


def _source_figures(c, h):
    return [model_file("h.source max_current_A / short_circuit_A"), CHASSIS_AMPACITY]


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
    Rule(R + "overcurrent-protection", "protection", r"overcurrent protection from ", (OVERCURRENT, CHASSIS_AMPACITY,
         model_file("h.source max_current_A / short_circuit_A")), _protection(lambda c, h: [OVERCURRENT] + _source_figures(c, h))),
    Rule(R + "fuse-rating-vs-wire", "protection", r".+ rating within the wire it protects$", _fuse_docs(
         "Blue Sea Systems 'DC Circuit Protection' 2005: rating <= wire ampacity") + (CHASSIS_AMPACITY, model_file("rating_A / trip_A")),
         _protection(_both([CHASSIS_AMPACITY], _device_docs("rating")))),
    Rule(R + "fuse-continuous-load", "protection", r".+ continuous load$", _all_fuse_docs() + (requirement("current_A"), AMBIENT_DEFAULT,
         model_file("continuous_fraction")), _protection(_both([requirement("current_A")], _device_docs("continuous", "rerate")))),
    Rule(R + "fuse-voltage-rating", "protection", r".+ voltage rating$", _all_fuse_docs() + (model_file("voltage_rating_V / h.source voltage_V"),),
         _protection(_both([model_file("h.source voltage_V")], _device_docs("voltage")))),
    Rule(R + "fuse-interrupting-rating", "protection", r".+ interrupting rating$", _all_fuse_docs() + (model_file("h.source short_circuit_A"),),
         _protection(_both([model_file("h.source short_circuit_A")], _device_docs("interrupt")))),
    Rule(R + "fuse-at-source-end", "protection", r".+ at the source end$", _fuse_docs(
         "ABYC E-11 7 in (175 mm), per Blue Sea Systems 'DC Circuit Protection' 2005") + (CHASSIS_AMPACITY,),
         _protection(lambda c, h: [] if _cited(c) else [CHASSIS_AMPACITY])),
    Rule(R + "fuse-on-a-branch", "protection", r".+ on a branch from a declared source$", (DECLARED,)),
    Rule(R + "supply-nets-declared", "protection", r"every supply net fed by a declared source$", (DECLARED,)),
    Rule(R + "signal-net", "protection", r"signal net ", _fuse_docs("Arduino UNO R3 pinout: 20 mA per I/O pin") + (requirement("current_A"),),
         _protection([requirement("current_A")])),
    Rule(R + "in-scope", "protection", r"in scope: net ", (SCOPE,)),
]
