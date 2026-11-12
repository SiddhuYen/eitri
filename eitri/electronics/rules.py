"""The electronics rule table: every check the circuit grades and its source. The electronics checks already name their
sources in their text ("[source: <citation>]", from eitri.electronics.sources); this table turns those citations back into
structured records, and gives the checks with no figure (connectivity, topology) their rule. See eitri.check_sources.

A check whose name matches no rule here gets no source, and tests/test_check_sources.py fails on it.
"""
from __future__ import annotations

import re

from .. import power
from ..check_sources import CheckSource, Rule, assumption, doc, model_file, rule
from .sources import SOURCES, cite

_INLINE = re.compile(r"\[source: ([^\]]*)\]")


def record(sid: str) -> CheckSource:
    """An electronics source id as a structured record."""
    s = SOURCES[sid]
    return CheckSource(s.short, s.url, f"{s.doc}; fetched {s.fetched}", "assumption" if s.assumption else "document")


def inline_sources(text: str) -> list[CheckSource]:
    """The records behind every "[source: ...]" a check's text names, in order ("the model file" is the model's own figure)."""
    out: list[CheckSource] = []
    for m in _INLINE.finditer(text):
        body = m.group(1)
        found = sorted(((body.find(cite(sid)), sid) for sid in SOURCES if cite(sid) and cite(sid) in body), key=lambda t: t[0])
        out += [record(sid) for _pos, sid in found]
        for text, rec in power.POWER_DOCS.items():       # a board supply limit from eitri.power quotes its source in full
            if text in body:
                out.append(rec)
        if "the model file" in body:
            out.append(model_file())
    return list(dict.fromkeys(out))


def _part_sources(c, circuit) -> list[CheckSource]:
    """The sources of the figures the parts a check names carry (Part.srcs), for a check whose text cites none."""
    out = []
    for name in c.refs:
        part = circuit.parts.get(name) if circuit is not None else None
        for sid in (part.srcs.values() if part is not None else ()):
            if sid in SOURCES:
                out.append(record(sid))
            elif sid == "model":
                out.append(model_file())
    return list(dict.fromkeys(out))


UNCITED = assumption("the check's text names no source for its figure")
_ASSUMED = re.compile(r"([^,;:()]+?)\s*\(ASSUMPTION: ([^;)\]]+)")
_UNGRADED = re.compile(r"not graded|not connected|unpowered|no per-pin limit|declares no|no vin_V|no supply drives")


def _inline(*fallback: CheckSource):
    """The check's own citations; else `fallback`; else the datasheets of the parts it names; else UNCITED."""
    def resolve(c, circuit):
        text = f"{c.measured} {c.expected}"
        got = (inline_sources(text) or list(fallback) or ([NO_FIGURE] if _UNGRADED.search(text) else [])
               or _part_sources(c, circuit) or [UNCITED])
        return got + [assumption(f"{m.group(1).strip(' +')}: {m.group(2).strip()}") for m in _ASSUMED.finditer(text)]     # a figure its text marks ASSUMPTION
    return resolve


def _logic_parts(c, circuit):
    """The logic-level datasheets of the parts a summary check names (each pin's logic.src)."""
    out = []
    for name in c.refs:
        part = circuit.parts.get(name)
        for ps in (part.pins.values() if part is not None else ()):
            if ps.logic is not None and ps.logic.src in SOURCES:
                out.append(record(ps.logic.src))
            elif ps.logic is not None and ps.logic.src == "model":
                out.append(model_file())
    return list(dict.fromkeys(out))


def _r(rid: str, category: str, name: str, *src: CheckSource) -> Rule:
    """A rule whose figures the check's own text cites; `src` is what it falls back to when the text names none."""
    listed = tuple(src) if src else (doc(f"{category}: the part datasheet figure the check's text cites (an id in eitri/electronics/sources.py)"),)
    return Rule("electronics." + rid, category, name, listed, _inline(*src))


NO_FIGURE = rule("graded against no figure when a part is unconnected or unpowered, or declares no range or limit (the check says which)")
E = "electronics."
RULES = [
    Rule(E + "required-pins", "connectivity", r"required pins connected$", (rule("every supply, ground and passive pin is on a net"),)),
    Rule(E + "dangling-nets", "connectivity", r"no dangling nets$", (rule("a net joins two or more pins"),)),
    Rule(E + "currents-solved", "connectivity", r"currents solved on every net$",
         (rule("every current is solved: series strings in closed form, branched networks by nodal analysis"),)),
    Rule(E + "shorts", "power", r"no shorted or back-fed rails$", (rule("one source per rail, never tied to ground"),)),
    _r("supply-voltage", "power", r"supply voltage "),
    _r("vin-range", "power", r"VIN "),
    _r("5v-pin-feed", "power", r"5V pin "),
    _r("rail-budget", "power", r"rail budget "),
    _r("regulator-headroom", "power", r"regulator headroom "),
    _r("regulator-temperature", "power", r"regulator temperature "),
    Rule(E + "common-ground", "power", r"common ground for every signal$", (rule("one ground net joins every part that exchanges signals"),)),
    Rule(E + "current-limited", "current", r"current limited: ", (rule("a resistance in every string between two fixed voltages"),)),
    _r("pin-current", "current", r"pin current "),
    _r("total-io-current", "current", r"total I/O current "),
    _r("led-current", "current", r"LED current "),
    _r("resistor-power", "current", r"resistor power "),
    _r("input-voltage", "logic", r"input voltage "),
    Rule(E + "inputs-abs-max", "logic", r"inputs within their absolute maximum$", (rule("logic: each input's absolute maximum, from its part's datasheet"),),
         _logic_parts),
    _r("high-level", "logic", r"high level "),
    Rule(E + "high-levels", "logic", r"high levels read as high$", (rule("logic: driver VOH against receiver VIH, from the parts' datasheets"),),
         _logic_parts),
    Rule(E + "input-defined", "logic", r"input defined ", (rule("every input has a pull-up, a pull-down or a driver"),)),
    _r("i2c-clock", "i2c", r"I2C clock "),
    _r("i2c-pullups", "i2c", r"I2C pull-ups "),
    _r("i2c-capacitance", "i2c", r"I2C bus capacitance "),
    Rule(E + "i2c-addresses", "i2c", r"I2C addresses unique on ", (rule("one device per address on a bus"),)),
    _r("avr-wire-pullups", "i2c", r"AVR internal pull-ups on I2C "),
    _r("flyback", "inductive", r"flyback diode "),
    _r("relay-contacts", "switching", r"relay contacts "),
    _r("gate-drive", "switching", r"gate drive "),
    _r("mosfet-dissipation", "switching", r"MOSFET dissipation "),
    _r("gate-pulldown", "switching", r"gate pull-down "),
    _r("transistor", "switching", r"transistor "),
    _r("e24", "parts", r"standard resistor values \(E24\)$"),
    _r("led-data", "parts", r"LED data from the LED's own datasheet$"),
    _r("capacitor-voltage", "parts", r"capacitor voltage ", model_file("capacitor v_rated")),
]
