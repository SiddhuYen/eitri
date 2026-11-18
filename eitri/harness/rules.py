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
NEC_TABLE_8 = doc("NFPA 70 (NEC) Chapter 9 Table 8, stranded uncoated copper at 75 C (K = 12.9; e.g. 12 AWG 1.98 ohm/kft)",
                  "https://www.nfpa.org/codes-and-standards/nfpa-70-standard-development/70",
                  "NFPA 70 Chapter 9 Table 8; values read in the 2011 edition (reproduction at necaibewelectricians.com); current edition "
                  "2026 (free read-only access at nfpa.org), its table not read; read 2026-11-17")
# The 3 % default (max_drop_pct when the model gives none): the NEC's branch-circuit recommendation. It is an informational note
# (advice, not an enforceable requirement); ABYC E-11 uses the same 3 % for critical DC circuits (10 % for the rest).
DROP_DEFAULT = doc("max_drop_pct is 3 % when the model does not give it: NFPA 70 (NEC) 2023, 210.19 Informational Note, a "
                   "recommendation ('a voltage drop exceeding 3 percent at the farthest outlet'), not a requirement",
                   "https://docinfofiles.nfpa.org/files/AboutTheCodes/70/70_A2025_NEC_P02_FD_PrelimFR.pdf",
                   "NFPA 70 2023 edition, 210.19 Informational Note (feeders: 215.2(A)(2) Informational Note No. 2; 2017: 210.19(A)(1) "
                   "IN No. 4), wording read as reproduced in NFPA's CMP-2 A2025 first-draft report FR-7573; 2026 numbering not checked; "
                   "read 2026-11-17")
DROP_DC = doc("ABYC E-11: 3 % maximum voltage drop for critical DC circuits (navigation lights, electronics, main feeders), 10 % for the rest",
              "https://abycinc.org/what-is-voltage-drop", "ABYC 'What Is Voltage Drop?' (08/18/2026), on E-11; read 2026-11-17")
TE_FASTON = doc("TE 108-106496 Rev C (FASTON terminals), s3.4 current by wire size at 30 C rise",
                "https://www.te.com/commerce/DocumentDelivery/DDEController?Action=srchrtrv&DocNm=108-106496&DocType=SS&DocLang=EN",
                "TE product specification 108-106496 Rev C, 27 Jun 2025, s3.4 Temperature Rising p.3 (s3.2 gives only 3-24 A); read 2026-11-17")
MOLEX_PS43045 = doc("Molex PS-43045 Rev P s4.2 (Micro-Fit 3.0, wire-to-wire)", "https://cdn.eicom.ru/media/PDF/183992.pdf",
                    "Molex PS-43045 Product Specification Rev P, 2017-10-27 (copy at cdn.eicom.ru; molex.com timed out)")
RATING_DOCS = {
    "JST-XH": doc("JST XH series catalogue (series 277): 3 A AC/DC (AWG #22)", "https://www.jst-mfg.com/product/pdf/eng/eXH.pdf",
                  "JST eXH.pdf p.1 (no revision printed; file dated 2025-07-30); read 2026-11-17"),
    "JST-PH": doc("JST PH series catalogue (series 199): 2 A AC/DC (AWG #24)", "https://www.jst-mfg.com/product/pdf/eng/ePH.pdf",
                  "JST ePH.pdf p.1 (no revision printed; file dated 2026-01-27); read 2026-11-17"),
    "DUPONT-2.54": doc("Amphenol FCI Mini-PV (PV wire-to-board): 3.0 A single circuit with 32 AWG, larger wires allow more",
                       "https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/6257/bwb_pv_wtb.pdf",
                       "Amphenol 'PV Wire-to-Board and Wire-to-Wire Connector System' BWBPVWTB0323EA4 (2023-03), p.2 (copy at digikey; "
                       "amphenol-cs.com answers 403; product spec BUS-12-067 not found); read 2026-11-17"),
    "MOLEX-MICROFIT-3.0": MOLEX_PS43045,
    "XT30": doc("Amass XT30U-M specification: 20 A (16 AWG, rise <= 85 K)", "https://www.china-amass.net/uploads/94.XT30U-M-SPEC-2025V0.pdf",
                "Amass XT30U-M specification Version 2025V0, p.1 (also the xt30u-m-product page); read 2026-11-17"),
    "XT60": doc("Amass XT60 datasheet V1.2: 30 A, 60 A instantaneous",
                "https://mrsdprojects.ri.cmu.edu/2025teamb/wp-content/uploads/sites/84/2025/05/xt60.pdf",
                "Amass XT60 specification V1.2 pp.1-2 (copy on a CMU project site; Amass's 2025V0 XT60-M spec would not open); the current "
                "XT60-M page says 35 A (12 AWG): the lower 30 A is used; Holybro's AMASS table also 30 A / 60 A; read 2026-11-17"),
    "XT90": doc("Amass XT90E-M specification: 45 A (10 AWG); burst 90 A (1 minute) from Holybro's AMASS rating table",
                "https://www.china-amass.net/uploads/80.XT90E-M-SPEC-2025V0.pdf",
                "Amass XT90E-M specification Version 2025V0, p.1; burst from Holybro 'Connector & Wire Rating' (row 'XT90', no date, "
                "docs.holybro.com/power-module-and-pdb/power-module/connector-and-wire-rating); read 2026-11-17"),
    "SPADE-2.8": TE_FASTON, "SPADE-4.8": TE_FASTON, "SPADE-6.3": TE_FASTON,
    "M8-3": doc("Pepperl+Fuchs V3-WM-10M-PUR data sheet p.1: 4 A, 60 V DC / 48 V AC (M8 3-pin A-coded female cordset)",
                "https://files.pepperl-fuchs.com/selector_files/navi/productInfo/pds/116463_eng.pdf",
                "Pepperl+Fuchs 116463_eng.pdf, release date 2026-07-29, page 1"),
    "M8-4": doc("Pepperl+Fuchs V31-GM-6,3M-PVC data sheet: 3 A, 30 V AC/DC (M8 4-pin female cordset; V31-GMV4A-5M-PUR says 4 A: the "
                "lower is used)", "https://files.pepperl-fuchs.com/webcat/navi/productInfo/pds/293011_eng.pdf",
                "Pepperl+Fuchs 293011_eng.pdf, release date 2016-05-09, page 1; 201892_eng.pdf, 2026-01-02"),
    "M12-4": doc("Pepperl+Fuchs V1-G-BK5M-PUR data sheet p.1: 4 A, 250 V AC/DC (M12 4-pin A-coded female cordset)",
                 "https://files.pepperl-fuchs.com/selector_files/navi/productInfo/pds/70100962_eng.pdf",
                 "Pepperl+Fuchs 70100962_eng.pdf, release date 2026-01-23, page 1"),
    "M12-5": doc("Pepperl+Fuchs V15-G-2M-PVC data sheet p.1: 4 A, 60 V DC / 48 V AC (M12 5-pin A-coded female cordset)",
                 "https://files.pepperl-fuchs.com/webcat/navi/productInfo/pds/035322_eng.pdf",
                 "Pepperl+Fuchs 035322_eng.pdf, release date 2026-01-28, page 1"),
    "WAGO-2002-1201": doc("WAGO 2002-1201 data sheet: 20 A, 600 V per UL 1059 (24 A, 800 V per IEC/EN 60664-1: the lower is used); "
                          "wire 0.25-4 mm2", "https://cdn.gescan.com/PIM_Docs/Docs/STEP_ASSETS_PDF/376915570.pdf",
                          "WAGO data sheet 2002-1201, version 12.05.2023 (copy at cdn.gescan.com), pages 1-2"),
}
AWG_DOCS = {
    "JST-XH": doc("JST XH series catalogue (series 277): AWG #30 to #22", "https://www.jst-mfg.com/product/pdf/eng/eXH.pdf",
                  "JST eXH.pdf p.1 (no revision printed; file dated 2025-07-30); read 2026-11-17"),
    "JST-PH": doc("JST PH series catalogue (series 199): AWG #32 to #24", "https://www.jst-mfg.com/product/pdf/eng/ePH.pdf",
                  "JST ePH.pdf p.1 (no revision printed; file dated 2026-01-27); read 2026-11-17"),
    "DUPONT-2.54": doc("Amphenol FCI Mini-PV contacts 48235-000LF 22-26 AWG, 48237-000LF 28-32 AWG",
                       "https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/6257/bwb_pv_wtb.pdf",
                       "Amphenol 'PV Wire-to-Board and Wire-to-Wire Connector System' BWBPVWTB0323EA4 (2023-03), p.4 contact table; "
                       "read 2026-11-17"),
    "MOLEX-MICROFIT-3.0": doc("Molex PS-43045 Rev P: Micro-Fit 3.0 system (43030 female crimp terminal) for 18-30 AWG stranded wire",
                              "https://cdn.eicom.ru/media/PDF/183992.pdf",
                              "Molex PS-43045 Product Specification Rev P, 2017-10-27, p.1 scope (the system range; each 43030 part "
                              "takes a narrower range); read 2026-11-17"),
}
AWG_DOCS["WAGO-2002-1201"] = RATING_DOCS["WAGO-2002-1201"]

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


def _connector_voltage(c, h):
    con = _connector(h, c)
    key = normalize_type(con.type) if con is not None else ""
    return [RATING_DOCS[key], model_file("net voltage_V")] if key in RATING_DOCS else [model_file("net voltage_V")]


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
        out += [DROP_DEFAULT, DROP_DC]
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
# eitri.harness.fuses.FUSE_SOURCES holds each one's full quote and FUSE_URLS the address of the document it was read from. Here they
# become records: the short cite as the text (it names the revision and page), the document's address as the URL.


def _fuse_doc_list(short: str) -> list:
    from .fuses import FUSE_SOURCES, FUSE_URLS
    if short not in FUSE_SOURCES:
        return []
    out = []
    for u in FUSE_URLS.get(short) or ("",):
        url, rev = u if isinstance(u, tuple) else (u, short)
        out.append(doc(short, url, rev))
    return out


def _fuse_docs(*shorts):
    return tuple(d for x in shorts for d in _fuse_doc_list(x))


def _all_fuse_docs():
    from .fuses import FUSE_SOURCES
    return _fuse_docs(*FUSE_SOURCES)


GIVEN = "as given in the model file (not from eitri's catalog)"
IEC_INPUT_TYPES = doc("IEC 61131-2 input types 1/2/3 (ON 15.2 / 11.2-30 V, 2-15 / 6-30 mA; OFF current 0.4 / 1.9 / 1.3 mA), as drawn by TI in "
                      "the ISO1228 data sheet, Figure 8-1 p.27 (graph-read; the standard itself not read)",
                      "https://www.ti.com/lit/ds/symlink/iso1228.pdf", "TI SLLSFN5B (June 2023, revised September 2025), Figure 8-1, page 27")
SIGNAL_DECLARE = rule("a signal net is graded against the devices declared at its ends (a microcontroller board pin, h.plc_input, "
                      "h.sensor_output); with none it does not run")
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
            out += _fuse_doc_list(part)
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
                out += _fuse_doc_list(t) or [model_file(f"{c.refs[0]} {k}")]
        return out
    return extra


def _both(*fns):
    return lambda c, h: [x for f in fns for x in (f(c, h) if callable(f) else f)]


def _signal(c, h):
    """A signal net's sources: the documents of the devices at its ends (eitri.harness.signals)."""
    from .signals import IEC_SOURCE_SHORT, IEC_SOURCE_URL, IEC_SOURCE_REV
    from .catalog import BOARD_TYPES
    from .model import PinRef
    links = [l for l in h.links if l.id in set(c.refs)]
    pins = list(dict.fromkeys(e for l in links for e in (l.a, l.b) if isinstance(e, PinRef)))
    out = []
    for ref in pins:
        dev = h.signal_ends.get(ref)
        con = h.connectors.get(ref.connector)
        board = dev.board if dev is not None and dev.kind == "mcu_pin" else BOARD_TYPES.get(con.type) if con is not None and dev is None else None
        if board:
            out += power.power_sources(power.io_pin_limit(board).source)
        elif dev is not None and dev.kind == "plc_input":
            out.append(IEC_INPUT_TYPES)
        elif dev is not None:
            out.append(doc(dev.source, dev.url, dev.rev) if dev.url else model_file(f"{ref} sensor_output figures"))
    if not out:
        return [SIGNAL_DECLARE]
    if " input " in c.name:
        out = [x for x in out if x is not IEC_INPUT_TYPES] + [IEC_INPUT_TYPES, model_file("net voltage_V")]
    else:
        out.append(requirement("current_A"))
    return out or [SIGNAL_DECLARE]


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
    Rule(R + "connector-voltage", "voltage rating", r"connector voltage ", tuple(RATING_DOCS[k] for k in ("M8-3", "M8-4", "M12-4", "M12-5",
         "WAGO-2002-1201")) + (model_file("net voltage_V"),), _connector_voltage),
    Rule(R + "voltage-drop", "voltage drop", r"voltage drop ", (requirement("max_drop_pct"), NEC_TABLE_8, DROP_DEFAULT, DROP_DC), _drop),
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
    Rule(R + "signal-net-input", "protection", r"signal net .+ input ", (IEC_INPUT_TYPES, model_file("net voltage_V"), SIGNAL_DECLARE), _signal),
    Rule(R + "signal-net", "protection", r"signal net ", tuple(dict.fromkeys(power.POWER_DOCS[k] for k in (power.SRC_UNO_IO, power.SRC_NANO_IO)))
         + (IEC_INPUT_TYPES, requirement("current_A"), model_file("sensor_output max_output_A"), SIGNAL_DECLARE), _signal),
    Rule(R + "in-scope", "protection", r"in scope: net ", (SCOPE,)),
]
