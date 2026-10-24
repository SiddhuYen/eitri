"""Closed-form pneumatic sizing: ISO 6358 flow through a component, series combination, tube conductance estimates, cylinder
force and air consumption. Every estimate is named as such in the function it comes from.

ISO 6358 (also in SMC's and Festo's sizing guides): the volume flow (ANR) through a component with sonic conductance C and
critical pressure ratio b, at absolute upstream pressure p1 and downstream p2 (T0 = T1 assumed), is
    Q = C p1                                          if p2/p1 <= b  (choked, sonic)
    Q = C p1 sqrt(1 - ((p2/p1 - b) / (1 - b))^2)      otherwise      (subsonic)
with Q in L/s ANR for C in L/(s·bar) and p in bar abs.

Series components: 1/C_eff^2 = sum 1/C_i^2 (the usual approximation for components in series with similar b; SMC's "composite
effective area" S = (sum 1/S_i^2)^-1/2), with b_eff = min b_i: conservative.

Tubing without datasheet data: effective area Se = A / sqrt(1 + lambda L_eq / d) with lambda = 0.025 (a Darcy friction factor
for turbulent flow in smooth tube, ESTIMATE), L_eq = the length plus 30 bore diameters per fitting (the Crane TP-410 order of
magnitude for an elbow), then C = Se / 5 (the SMC conversion S [mm^2] = 5 C [L/(s·bar)]) and b = 0.5 (typical of tubing, ESTIMATE).

Cylinder force: F = p_gauge [bar] x 0.1 [N/mm^2 per bar] x A [mm^2], x (1 - friction fraction) for the usable force. Free-air
consumption per stroke: V = A x stroke x (p_abs / P_ATM), ANR (ISO 1217 style free-air basis).
"""
from __future__ import annotations

import math

from .model import P_ATM, Cylinder, Tube

LAMBDA_TUBE = 0.025          # ESTIMATE: Darcy friction factor, turbulent flow in smooth tube
FITTING_EQ_DIAMETERS = 30.0  # ESTIMATE: equivalent length per fitting, in bore diameters (Crane TP-410 order of magnitude for an elbow)
S_PER_C = 5.0                # SMC: effective area S [mm^2] = 5 x C [L/(s·bar)]
TUBE_B = 0.5                 # ESTIMATE: critical pressure ratio of tubing
DEFAULT_C = {"valve": (1.0, 0.30), "regulator": (1.5, 0.35), "frl": (1.5, 0.35), "flow_control": (0.5, 0.30), "silencer": (3.0, 0.30)}   # ESTIMATE: small (1/8" - 1/4") hobby components
NPA = 0.1                    # N/mm^2 per bar


def flow_l_s(C: float, b: float, p1_abs: float, p2_abs: float) -> float:
    """ISO 6358 volume flow (L/s ANR) through C, b from p1 to p2 (bar abs); 0 when p2 >= p1."""
    if p1_abs <= 0 or p2_abs >= p1_abs:
        return 0.0
    r = max(p2_abs, 0.0) / p1_abs
    if r <= b:
        return C * p1_abs
    return C * p1_abs * math.sqrt(max(0.0, 1.0 - ((r - b) / (1.0 - b)) ** 2))


def drop_for_flow(C: float, b: float, p1_abs: float, q_l_s: float) -> float | None:
    """The pressure drop (bar) across C, b at flow q from p1; None when q exceeds the choked flow C p1 (it cannot pass)."""
    if q_l_s <= 0:
        return 0.0
    qc = C * p1_abs
    if q_l_s >= qc:
        return None
    x = q_l_s / qc
    r = b + (1.0 - b) * math.sqrt(max(0.0, 1.0 - x * x))
    return p1_abs * (1.0 - r)


def series(items: list[tuple[float, float]]) -> tuple[float, float]:
    """(C_eff, b_eff) of components in series."""
    if not items:
        return float("inf"), 0.0
    inv = sum(1.0 / (c * c) for c, _b in items if c > 0)
    return (1.0 / math.sqrt(inv) if inv > 0 else float("inf")), min(b for _c, b in items)


def tube_conductance(id_mm: float, length_m: float, fittings: int = 2) -> tuple[float, float]:
    """(C, b) estimated for a tube run from its bore, length and fittings (see the module docstring)."""
    d_m = id_mm / 1000.0
    l_eq = length_m + fittings * FITTING_EQ_DIAMETERS * d_m
    area = math.pi / 4 * id_mm ** 2
    se = area / math.sqrt(1.0 + LAMBDA_TUBE * l_eq / d_m)
    return se / S_PER_C, TUBE_B


def tube_data(t: Tube) -> tuple[float, float, bool]:
    """(C, b, estimated) of a tube: the datasheet values when given, else the estimate."""
    if t.C is not None:
        return t.C, (t.b_ratio if t.b_ratio is not None else TUBE_B), False
    c, b = tube_conductance(t.id_mm, t.length_m, t.fittings)
    return c, b, True


def required_flow_l_s(area_mm2: float, speed_mm_s: float, p_gauge_bar: float) -> float:
    """Free-air flow (L/s ANR) to fill `area` at `speed` with air at p_gauge."""
    return area_mm2 * speed_mm_s / 1e6 * ((p_gauge_bar + P_ATM) / P_ATM)


def cylinder_forces(cyl: Cylinder, p_gauge_bar: float) -> dict:
    """Theoretical and usable (friction allowance) forces, N, for both strokes."""
    f_ext = p_gauge_bar * NPA * cyl.area_bore_mm2
    f_ret = p_gauge_bar * NPA * cyl.area_annulus_mm2 if cyl.acting == "double" else (cyl.spring_N or 0.0)
    k = 1.0 - cyl.friction_fraction
    return {"extend_theoretical_N": f_ext, "extend_usable_N": f_ext * k, "retract_theoretical_N": f_ret,
            "retract_usable_N": (f_ret * k if cyl.acting == "double" else f_ret), "pressure_bar": p_gauge_bar}


def consumption_per_cycle_l(cyl: Cylinder, p_gauge_bar: float, dead_volume_mm3: float = 0.0) -> dict:
    """Free air per cycle (L ANR): both strokes' swept volumes (one for a spring return) plus the dead volume of the lines
    between valve and cylinder (filled and vented each stroke), all expanded to atmospheric."""
    ratio = (p_gauge_bar + P_ATM) / P_ATM
    swept = cyl.area_bore_mm2 * cyl.stroke_mm + (cyl.area_annulus_mm2 * cyl.stroke_mm if cyl.acting == "double" else 0.0)
    return {"swept_l": swept / 1e6 * ratio, "dead_l": dead_volume_mm3 / 1e6 * ratio, "per_cycle_l": (swept + dead_volume_mm3) / 1e6 * ratio,
            "per_minute_l": (swept + dead_volume_mm3) / 1e6 * ratio * cyl.cycles_per_min}
