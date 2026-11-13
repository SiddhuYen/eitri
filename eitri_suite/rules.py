"""The suite's rule table: every cross-domain check gets a rule id, a kind and structured sources (core eitri.check_sources), so no
check shows "extension suite gives no source" and a change package's source column is filled for them too.

    attach_sources(checks, model)   called on every check the extension returns (projectchecks.extras)

How a check's sources are found, in order:
  1. the rule's own sources (what the rule is: a connectivity definition, the model file's figure, a document);
  2. every "[source: ...]" citation in the check's text, as a document record, with its URL where the cited record has one
     (core eitri.electronics.sources, eitri.harness.rules, eitri_suite.elec_sources, the suite's own documents below);
  3. an ASSUMPTION / ESTIMATE / unverified figure named in the text adds an assumption source (listed, never hidden).
A check no rule covers keeps no source here, and tests/suite/test_suite_sources.py fails on it.
"""
from __future__ import annotations

import re
from typing import Any

from eitri.check_sources import CheckSource, Rule, assumption, doc, find_rule, model_file, rule

SUITE_CATALOG = assumption("eitri parts catalog figures: the maker's published figures as remembered, "
                           "verified=False")
DENSITY = assumption("material densities eitri.measure.DENSITY (typical handbook values, not cited) x the CAD volume")
IEC_62368 = assumption("tipping margin h x tan 10 deg borrowed from IEC 62368-1 cl. 8.6 (stability of equipment, a 10 deg tilt): a "
                       "standard for AV/ICT equipment, used here as a design margin for a machine; the clause and the 10 deg tilt confirmed in "
                       "published summaries, the normative text not read")
POWERSTREAM = doc("chassis-wiring ampacity, PowerStream Wire_Size table", "https://www.powerstream.com/Wire_Size.htm",
                  "dated 2023-04-18, fetched 2026-10-26")
NEC = doc("NFPA 70 (NEC) Chapter 9 Table 8, copper at 75 C (K = 12.9)", "", "NFPA 70 Chapter 9 Table 8")
ISO_68 = doc("ISO 68-1 metric thread profile (screw major diameter), as published in the AMES thread chart", "", "fetched 2026-11-12")
CONNECTIVITY = rule("connectivity: the files name the same pins and nets")
GEOMETRY = rule("geometry of the CAD bodies posed through the mechanism (contact within 0.5 mm, overlap above 1 mm^3)")
SEQUENCE = model_file("the firmware SEQUENCE and the mechanism's joint limits; a hobby servo turns 0-180 deg")


def _r(rid: str, category: str, name: str, *source: CheckSource) -> Rule:
    return Rule(f"suite.{rid}", category, name, tuple(source) or (rule(rid),))


RULES: list[Rule] = [
    # CAD <-> harness (crossdomain.py)
    _r("route-fit", "integration", r"route '.*' fits", rule("bundle diameter + clearance through the declared hole"),
       assumption("bundle diameter from estimated wire outside diameters (ESTIMATE in the check text)")),
    _r("route-length", "integration", r"route '.*' wire length", rule("every routed wire at least the path + its service loop")),
    _r("route-wires", "integration", r"route '.*' wires exist", CONNECTIVITY),
    _r("route", "integration", r"route '", rule("a declared cable route")),
    _r("mating-face", "integration", r"mating face", rule("a clear corridor in front of a declared mating face")),
    _r("integration-declared", "integration", r"(INTEGRATION|COUPLING)", CONNECTIVITY),
    _r("sibling-builds", "integration", r"the other artifact builds", rule("the cross-domain checks need the sibling file's own build")),
    # electronics.py
    _r("elec-pins", "integration", r"pins: ", CONNECTIVITY),
    _r("elec-harness-power", "integration", r"harness power: ", POWERSTREAM, NEC),
    _r("elec-actuators", "integration", r"actuators: ", CONNECTIVITY),
    _r("elec-dissipation", "integration", r"dissipation: ", rule("Tj = Ta + P x theta_JA against Tj max (the electronics domain's formula)")),
    _r("elec-mounting", "integration", r"mounting: ", ISO_68),
    # stability.py, power.py
    _r("stability", "integration", r"stability: the base does not tip over", IEC_62368, DENSITY),
    _r("power-together", "integration", r"power: the supply covers the actuators that move together", SEQUENCE),
    # firmware <-> harness (crossdomain.py, assembly.py)
    _r("pinout", "pinout", r".", CONNECTIVITY),
    _r("sequence", "sequence", r".", SEQUENCE),
    # the assembly (assembly.py)
    _r("anchors", "assembly", r"every harness connector has a CAD anchor", CONNECTIVITY),
    _r("wire-cut", "assembly", r"wire cut lengths cover", rule("a wire's cut length covers its 3D path; under the harness's 10 % service loop warns")),
    _r("wire-paths", "assembly", r"wire paths", GEOMETRY),
    _r("board-rests", "assembly", r".* rests on its mounts", GEOMETRY),
    _r("assembly", "assembly", r".", GEOMETRY),
    # mechanism <-> CAD (coupling.py, kinematics.py)
    _r("link-masses", "coupling", r"link masses come from the CAD model", DENSITY, SUITE_CATALOG),
    _r("cad-masses", "coupling", r"every coupled CAD part has a mass", model_file("MATERIALS / HARDWARE of the CAD file")),
    _r("torque-cad", "coupling", r"(torque|force) .*\(masses from CAD\)", DENSITY),
    _r("actuator-at-joint", "coupling", r"every actuator is at the joint", rule("output face within 2 mm of the joint point, axis within 5 deg")),
    _r("coupling", "coupling", r".", GEOMETRY),
]

_INLINE = re.compile(r"\[source: ([^\[\]]+(?:\[[^\[\]]*\][^\[\]]*)*)\]")
_FLAGS = re.compile(r"\b(ASSUMPTION|ESTIMATE|unverified)\b", re.I)


def _known() -> dict[str, CheckSource]:
    """Every document a suite check text can cite, by the short text its citation starts with."""
    out: dict[str, CheckSource] = {}
    try:
        from eitri.electronics.sources import SOURCES as ES
        for s in ES.values():
            basis = "assumption" if getattr(s, "assumption", False) else "document"
            out[s.short] = CheckSource(s.short, s.url, f"{s.doc}, fetched {s.fetched}", basis)
    except Exception:                                       # noqa: BLE001
        pass
    from .elec_sources import FETCHED, SOURCES as SS
    for _k, s in SS.items():
        basis = "assumption" if "ASSUMPTION" in s.get("quote", "") or "ASSUMPTION" in s["title"] else "document"
        out[s["title"]] = CheckSource(s["title"], s.get("url", ""), f"fetched {FETCHED}", basis)
    for s in (POWERSTREAM, NEC):
        out[s.text] = s
    out["PowerStream chassis-wiring ampacity table"] = POWERSTREAM
    out["V = K I L / CM per conductor"] = NEC
    return out


def _inline_sources(text: str, known: dict[str, CheckSource]) -> list[CheckSource]:
    out = []
    for m in _INLINE.finditer(text):
        cited = m.group(1).strip()
        hit = next((v for k, v in known.items() if k and cited.startswith(k)), None)
        if hit is None:
            low = cited.lower()
            basis = "model" if low.startswith("the model file") else "assumption" if _FLAGS.search(cited) else "document"
            hit = CheckSource(cited[:300], "", "", basis)
        out.append(hit)
    return out


def attach_sources(checks, model: Any = None) -> None:
    """Rule id, kind and sources for every check of the suite (a check that already has sources keeps them)."""
    known = None
    for c in checks:
        r = find_rule(RULES, getattr(c, "category", ""), c.name)
        if r is None:
            continue
        own = [s for s in (getattr(c, "source", None) or []) if "gives no source" not in s.text]
        if own:                                             # a check that came with its domain's sources (the torque re-run) keeps them
            c.rule, c.kind, c.source = c.rule or r.id, r.kind, list(dict.fromkeys(own + list(r.source)))
            continue
        if known is None:
            known = _known()
        text = f"{c.measured} {c.expected}"
        got = list(r.source) + _inline_sources(text, known)
        if _FLAGS.search(text) and not any(s.basis == "assumption" for s in got):
            got.append(assumption("a figure the check text marks ASSUMPTION / ESTIMATE / unverified"))
        c.rule, c.kind, c.source = r.id, r.kind, list(dict.fromkeys(got))


def rule_for(category: str, name: str) -> Rule | None:
    return find_rule(RULES, category, name)
