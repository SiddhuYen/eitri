"""The mechanism rule table: every check the mechanism grades, its source, and how a check's source follows the model (the
actuator's own figures, the catalog's datasheet, the assumed continuous share). See eitri.check_sources.

A check whose name matches no rule here gets no source, and tests/test_check_sources.py fails on it.
"""
from __future__ import annotations

from fractions import Fraction

from ..check_sources import Rule, assumption, doc, model_file, requirement, rule
from .model import ASSUMED_RATED_FRACTION, catalog_actuator

ISO_80000_3 = doc("standard gravity g_n = 9.80665 m/s^2 defines the kilogram-force (1 kgf.cm = 0.0980665 N.m): NIST SP 811",
                  "https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication811e2008.pdf",
                  "NIST Special Publication 811, 2008 edition, Appendix B.8 and footnote 23 p.53 ('1 kgf = 9.806 65 E+00 N exactly'); "
                  "read 2026-11-17 (ISO 80000-3:2019 not read)")
STS3215 = doc("Feetech STS3215 product specification, edition A/0, s5: rated 4 kg.cm at 6 V",
              "https://www.feetechrc.com/Data/feetechrc/upload/file/20260622/6391772523943436695270694.pdf",
              "Feetech STS3215 product specification edition A/0, 2020-04-10 (re-read 2026-11-07)")
ROBOTIS = doc("ROBOTIS e-Manual (XL-320 / AX-12A): a steady load of 1/5 of stall", "https://emanual.robotis.com/docs/en/dxl/x/xl320/",
              "ROBOTIS e-Manual, XL-320 and AX-12A pages (no page date), specifications NOTE: 'Stable motions are possible with robots "
              "designed for loads with 1/5 or less of the stall torque'; read 2026-11-17")
CATALOG_DOCS = {
    "STS3215": STS3215,
    "MG996R": doc("TowerPro MG996R datasheet: stall 9.4 kgf.cm at 4.8 V", "https://towerpro.com.tw/product/mg996r/",
                  "TowerPro MG996R product page (no page date): 'Stall torque: 9.4kg/cm (4.8v); 11kg/cm (6.0v)'; read 2026-11-17"),
    "SG90": doc("TowerPro SG90 datasheet: stall 1.8 kgf.cm at 4.8 V", "https://towerpro.com.tw/product/sg90-7/",
                "TowerPro SG90 product page (no page date): 'Stall torque: 1.8kg/cm(4.8v)'; read 2026-11-17"),
}
RATED_ASSUMED = assumption(f"continuous = {Fraction(ASSUMED_RATED_FRACTION).limit_denominator(20)} of stall where the maker publishes no "
                           "rating (the lower of Feetech STS3215's 0.24 and ROBOTIS's 1/5)")


def _joint(m, c):
    for r in c.refs:
        for j in m.joints:
            if j.name == r:
                return j
    return None


def _torque(c, m):
    j = _joint(m, c)
    if j is None or j.actuator is None:
        return [rule("a moving joint with no actuator is not graded")]
    a = j.actuator
    out = [model_file(f"{a.name} stall_{'kgcm' if j.type == 'revolute' else 'N'}"), ISO_80000_3]
    if (a.rated_kgcm if j.type == "revolute" else a.rated_N) is not None:
        out.append(model_file(f"{a.name} rated_{'kgcm' if j.type == 'revolute' else 'N'}"))
    else:
        spec = catalog_actuator(a.name) if j.type == "revolute" else None
        if spec is not None and spec.rated_kgcm is not None:
            out.append(CATALOG_DOCS[spec.key])
        else:
            out += [RATED_ASSUMED, STS3215, ROBOTIS]
    return out


def _speed(c, m):
    j = _joint(m, c)
    out = []
    if j is not None and m.speeds.get(j.name) is not None:
        out.append(requirement(f"speed of {j.name}"))
    if j is not None and j.actuator is not None and (j.actuator.speed_deg_s if j.type == "revolute" else j.actuator.speed_mm_s) is not None:
        out.append(model_file(f"{j.actuator.name} no-load speed"))
    return out or [rule("a speed is graded only when the actuator declares one")]


def _limits(c, m):
    j = _joint(m, c)
    out = [requirement(f"{j.name} limit") if j is not None else requirement("joint limit")]
    if j is not None and j.actuator is not None:
        a = j.actuator
        if (a.travel_deg if j.type == "revolute" else a.travel_mm) is not None:
            out.append(model_file(f"{a.name} travel"))
        elif a.continuous:
            out.append(model_file(f"{a.name} continuous=True"))
        else:
            out.append(rule("a range is graded against the actuator's travel only when it declares one"))
    return out


def _reach(c, m):
    return [requirement("reach_mm")] if m.reach_mm is not None else [rule("the reach envelope over the sampled poses (reported, no limit)")]


R = "mechanism."
RULES = [
    Rule(R + "tree", "structure", r"mechanism is a tree from the ground link$", (rule("the joints form a tree from one fixed link"),)),
    Rule(R + "mass-rollup", "mass", r"mass rollup$", (model_file("link, motor and payload masses"),)),
    Rule(R + "joint-load", "torque|force", r"(torque|force) joint ", (model_file("actuator stall"), ISO_80000_3, STS3215, ROBOTIS, RATED_ASSUMED), _torque),
    Rule(R + "speed", "speed", r"speed joint ", (requirement("joint speed"), model_file("actuator no-load speed")), _speed),
    Rule(R + "limits", "limits", r"joint limits ", (requirement("joint limit"), model_file("actuator travel")), _limits),
    Rule(R + "target", "reach", r"target .* reachable$", (requirement("target point; tol_mm, 2 mm when not given"),)),
    Rule(R + "reach", "reach", r"reach envelope$", (requirement("reach_mm"),), _reach),
    Rule(R + "base-plane", "reach", r"stays above its base plane$", (rule("a table-mounted mechanism stays above z = 0 (0.5 mm tolerance)"),)),
]
