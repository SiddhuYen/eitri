"""Metric fastener tables (ISO) used by the hole and boss helpers.

All dimensions in mm. Sources: ISO 273 (clearance holes), ISO 4762 (socket head
cap screws), ISO 10642 (countersunk), ISO 4032 (hex nuts), ISO 7089 (washers),
common brass heat-set insert catalogues (hole sizes for FDM prints).
"""
from __future__ import annotations

__all__ = ["METRIC", "screw_clearance", "tap_drill", "counterbore_dims", "countersink_dims", "nut_dims",
           "heat_set_insert_dims", "washer_dims", "screw_pitch"]

# size: clearance (close, normal, loose), tap drill (coarse pitch), pitch,
#       SHCS head dia / height, counterbore dia (SHCS), countersink dia (flat head, 90 deg),
#       hex nut across-flats / height, washer od / thickness, heat-set insert hole dia / depth
METRIC: dict[str, dict[str, float]] = {
    "M1.6": dict(close=1.7, normal=1.8, loose=2.0, tap=1.25, pitch=0.35, head_d=3.0, head_h=1.6, cb_d=3.5, cs_d=3.3, nut_af=3.2, nut_h=1.3, washer_od=4.0, washer_t=0.3, insert_d=2.0, insert_depth=3.0),
    "M2": dict(close=2.2, normal=2.4, loose=2.6, tap=1.6, pitch=0.4, head_d=3.8, head_h=2.0, cb_d=4.4, cs_d=4.4, nut_af=4.0, nut_h=1.6, washer_od=5.0, washer_t=0.3, insert_d=3.2, insert_depth=4.0),
    "M2.5": dict(close=2.7, normal=2.9, loose=3.1, tap=2.05, pitch=0.45, head_d=4.5, head_h=2.5, cb_d=5.0, cs_d=5.5, nut_af=5.0, nut_h=2.0, washer_od=6.0, washer_t=0.5, insert_d=3.6, insert_depth=5.7),
    "M3": dict(close=3.2, normal=3.4, loose=3.6, tap=2.5, pitch=0.5, head_d=5.5, head_h=3.0, cb_d=6.5, cs_d=6.3, nut_af=5.5, nut_h=2.4, washer_od=7.0, washer_t=0.5, insert_d=4.0, insert_depth=5.7),
    "M4": dict(close=4.3, normal=4.5, loose=4.8, tap=3.3, pitch=0.7, head_d=7.0, head_h=4.0, cb_d=8.0, cs_d=9.4, nut_af=7.0, nut_h=3.2, washer_od=9.0, washer_t=0.8, insert_d=5.6, insert_depth=8.1),
    "M5": dict(close=5.3, normal=5.5, loose=5.8, tap=4.2, pitch=0.8, head_d=8.5, head_h=5.0, cb_d=10.0, cs_d=10.4, nut_af=8.0, nut_h=4.7, washer_od=10.0, washer_t=1.0, insert_d=6.4, insert_depth=9.5),
    "M6": dict(close=6.4, normal=6.6, loose=7.0, tap=5.0, pitch=1.0, head_d=10.0, head_h=6.0, cb_d=11.0, cs_d=12.6, nut_af=10.0, nut_h=5.2, washer_od=12.0, washer_t=1.6, insert_d=8.0, insert_depth=12.7),
    "M8": dict(close=8.4, normal=9.0, loose=10.0, tap=6.8, pitch=1.25, head_d=13.0, head_h=8.0, cb_d=15.0, cs_d=17.3, nut_af=13.0, nut_h=6.8, washer_od=16.0, washer_t=1.6, insert_d=10.0, insert_depth=12.7),
    "M10": dict(close=10.5, normal=11.0, loose=12.0, tap=8.5, pitch=1.5, head_d=16.0, head_h=10.0, cb_d=18.0, cs_d=20.0, nut_af=16.0, nut_h=8.4, washer_od=20.0, washer_t=2.0, insert_d=12.0, insert_depth=15.0),
    "M12": dict(close=13.0, normal=13.5, loose=14.5, tap=10.2, pitch=1.75, head_d=18.0, head_h=12.0, cb_d=20.0, cs_d=24.4, nut_af=18.0, nut_h=10.8, washer_od=24.0, washer_t=2.5, insert_d=14.0, insert_depth=18.0),
}


def _row(size: str) -> dict[str, float]:
    key = size.upper().replace(" ", "")
    if key not in METRIC:
        raise KeyError(f"unknown screw size {size!r}; known: {', '.join(METRIC)}")
    return METRIC[key]


def screw_clearance(size: str, fit: str = "normal") -> float:
    """Clearance hole diameter for a metric screw (ISO 273): fit "close", "normal" or "loose"."""
    row = _row(size)
    if fit not in ("close", "normal", "loose"):
        raise ValueError("fit must be close, normal or loose")
    return row[fit]


def tap_drill(size: str) -> float:
    """Tap-drill diameter for a coarse metric thread (also the hole to thread-form into plastic)."""
    return _row(size)["tap"]


def screw_pitch(size: str) -> float:
    """Coarse thread pitch of a metric size."""
    return _row(size)["pitch"]


def counterbore_dims(size: str, extra_depth: float = 0.5) -> tuple[float, float]:
    """(diameter, depth) of a counterbore that hides a socket head cap screw head (ISO 4762)."""
    row = _row(size)
    return row["cb_d"], row["head_h"] + extra_depth


def countersink_dims(size: str) -> tuple[float, float]:
    """(top diameter, included angle in degrees) of a 90 deg countersink for a flat head screw (ISO 10642)."""
    return _row(size)["cs_d"], 90.0


def nut_dims(size: str) -> tuple[float, float]:
    """(across flats, height) of a standard hex nut (ISO 4032)."""
    row = _row(size)
    return row["nut_af"], row["nut_h"]


def washer_dims(size: str) -> tuple[float, float]:
    """(outer diameter, thickness) of a plain washer (ISO 7089)."""
    row = _row(size)
    return row["washer_od"], row["washer_t"]


def heat_set_insert_dims(size: str) -> tuple[float, float]:
    """(hole diameter, hole depth) for a brass heat-set threaded insert in a 3D print."""
    row = _row(size)
    return row["insert_d"], row["insert_depth"]
