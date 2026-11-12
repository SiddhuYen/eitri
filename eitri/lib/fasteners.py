"""Metric fastener tables (ISO) used by the hole and boss helpers.

All dimensions in mm. Sources: ISO 273 (clearance holes), ISO 4762 (socket head
cap screws), ISO 10642 (countersunk), ISO 4032 (hex nuts), ISO 7089 (washers),
common brass heat-set insert catalogues (hole sizes for FDM prints).

Sourced figures (re-checked 2026-11-07):
- cs_d is the ISO 10642 head diameter dk max (= nominal, sharp edge), so a 90 deg countersink of that top diameter
  takes the head flush: DIN EN ISO 10642:2020-02, dimensions table, columns "dk max=nom" (fasten.it norm sheet,
  https://www.fasten.it/en/norms/norm/din_en_iso_10642). M2 4.7, M2.5 5.88, M3 6.72, M4 8.96, M5 11.20, M6 13.44,
  M8 17.92, M10 22.4, M12 26.88. M1.6 is not in ISO 10642; its 3.3 is kept from the earlier table (unsourced).
- insert_d is ruthex's hole diameter: column D3 of the ruthex insert tables (3DJake listings of ruthex data,
  e.g. https://www.3djake.com/ruthex/threaded-insert-m25-70-pieces: "M2.5 | 4.6 | 3.9 | 4,0 | 5.7 | 1.6";
  .../threaded-inserts-m8-20-pieces: "M8 | 10.1 | 9.5 | 9.6 | 12.7 | 4.5"). D3 equals ruthex's own drill set
  for M2-M6 (ruthex.de: "M2, M2.5, M3, M4, M5, M6 ... 3.2 + 4 + 5.6 + 6.4 + 8mm"); for M8 the same column
  meaning is assumed (ruthex publishes no M8 drill). insert_depth is the insert length L; ruthex gives no
  hole-depth figure (a deeper hole is usual for melt overflow).
- close/normal/loose are the ISO 273 fine/medium/coarse clearance-hole series (ISO_273 below, re-checked 2026-11-12).
  Source: Albany County Fasteners (Raw Products Corp), "Clearance Hole Chart", page 2 "Metric Series Clearance Holes
  (ISO 273)", columns Close Fit (H12) / Normal Fit (H13) / Loose Fit (H14), (c) 2025,
  https://www.albanycountyfasteners.com/media/6b/b8/g0/1764944339/clearance-hole-chart.pdf (M1.6-M30). Cross-check:
  Optimas OE Solutions, "Recommended Tapping Drill Sizes and Clearance Holes" (page stamp 2026-07-08; it does not
  name ISO 273), https://optimas.com/technical-resources/tapping-sizes/ : the same figures for M1.6-M10, M20, M24,
  but M12 13/14/15, M14 15/16/17, M16 17/18/19 where the ISO-273-labelled chart has 13/13.5/14.5, 15/15.5/16.5,
  17/17.5/18.5. The ISO-labelled figures are used (Optimas does not claim to be ISO 273). ISO 273:1979 itself is
  paywalled and was not read; EN 20273:1991 is its identical European adoption.
"""
from __future__ import annotations

__all__ = ["METRIC", "ISO_273", "iso273_clearance", "screw_clearance", "tap_drill", "counterbore_dims", "countersink_dims", "nut_dims",
           "heat_set_insert_dims", "washer_dims", "screw_pitch"]

# size: clearance (close, normal, loose), tap drill (coarse pitch), pitch,
#       SHCS head dia / height, counterbore dia (SHCS), countersink dia (flat head, 90 deg),
#       hex nut across-flats / height, washer od / thickness, heat-set insert hole dia / depth
METRIC: dict[str, dict[str, float]] = {
    "M1.6": dict(close=1.7, normal=1.8, loose=2.0, tap=1.25, pitch=0.35, head_d=3.0, head_h=1.6, cb_d=3.5, cs_d=3.3, nut_af=3.2, nut_h=1.3, washer_od=4.0, washer_t=0.3, insert_d=2.0, insert_depth=3.0),
    "M2": dict(close=2.2, normal=2.4, loose=2.6, tap=1.6, pitch=0.4, head_d=3.8, head_h=2.0, cb_d=4.4, cs_d=4.7, nut_af=4.0, nut_h=1.6, washer_od=5.0, washer_t=0.3, insert_d=3.2, insert_depth=4.0),
    "M2.5": dict(close=2.7, normal=2.9, loose=3.1, tap=2.05, pitch=0.45, head_d=4.5, head_h=2.5, cb_d=5.0, cs_d=5.88, nut_af=5.0, nut_h=2.0, washer_od=6.0, washer_t=0.5, insert_d=4.0, insert_depth=5.7),
    "M3": dict(close=3.2, normal=3.4, loose=3.6, tap=2.5, pitch=0.5, head_d=5.5, head_h=3.0, cb_d=6.5, cs_d=6.72, nut_af=5.5, nut_h=2.4, washer_od=7.0, washer_t=0.5, insert_d=4.0, insert_depth=5.7),
    "M4": dict(close=4.3, normal=4.5, loose=4.8, tap=3.3, pitch=0.7, head_d=7.0, head_h=4.0, cb_d=8.0, cs_d=8.96, nut_af=7.0, nut_h=3.2, washer_od=9.0, washer_t=0.8, insert_d=5.6, insert_depth=8.1),
    "M5": dict(close=5.3, normal=5.5, loose=5.8, tap=4.2, pitch=0.8, head_d=8.5, head_h=5.0, cb_d=10.0, cs_d=11.2, nut_af=8.0, nut_h=4.7, washer_od=10.0, washer_t=1.0, insert_d=6.4, insert_depth=9.5),
    "M6": dict(close=6.4, normal=6.6, loose=7.0, tap=5.0, pitch=1.0, head_d=10.0, head_h=6.0, cb_d=11.0, cs_d=13.44, nut_af=10.0, nut_h=5.2, washer_od=12.0, washer_t=1.6, insert_d=8.0, insert_depth=12.7),
    "M8": dict(close=8.4, normal=9.0, loose=10.0, tap=6.8, pitch=1.25, head_d=13.0, head_h=8.0, cb_d=15.0, cs_d=17.92, nut_af=13.0, nut_h=6.8, washer_od=16.0, washer_t=1.6, insert_d=9.6, insert_depth=12.7),
    "M10": dict(close=10.5, normal=11.0, loose=12.0, tap=8.5, pitch=1.5, head_d=16.0, head_h=10.0, cb_d=18.0, cs_d=22.4, nut_af=16.0, nut_h=8.4, washer_od=20.0, washer_t=2.0, insert_d=12.0, insert_depth=15.0),
    "M12": dict(close=13.0, normal=13.5, loose=14.5, tap=10.2, pitch=1.75, head_d=18.0, head_h=12.0, cb_d=20.0, cs_d=26.88, nut_af=18.0, nut_h=10.8, washer_od=24.0, washer_t=2.5, insert_d=14.0, insert_depth=18.0),
}


# ISO 273 clearance holes for bolts and screws, mm: size -> (fine, medium, coarse). Source: see the module docstring.
ISO_273: dict[str, tuple[float, float, float]] = {
    "M1.6": (1.7, 1.8, 2.0), "M2": (2.2, 2.4, 2.6), "M2.5": (2.7, 2.9, 3.1), "M3": (3.2, 3.4, 3.6),
    "M4": (4.3, 4.5, 4.8), "M5": (5.3, 5.5, 5.8), "M6": (6.4, 6.6, 7.0), "M8": (8.4, 9.0, 10.0),
    "M10": (10.5, 11.0, 12.0), "M12": (13.0, 13.5, 14.5), "M14": (15.0, 15.5, 16.5), "M16": (17.0, 17.5, 18.5),
    "M20": (21.0, 22.0, 24.0), "M24": (25.0, 26.0, 28.0), "M30": (31.0, 33.0, 35.0),
}
ISO_273_SOURCE = ("ISO 273 clearance holes as published by Albany County Fasteners (Clearance Hole Chart p.2, (c) 2025; the only "
                  "ISO-labelled table read: ISO 273 itself is paywalled)")
ISO_273_PUBLISHER = "Albany County Fasteners"
ISO_273_URL = "https://www.albanycountyfasteners.com/media/6b/b8/g0/1764944339/clearance-hole-chart.pdf"
_SERIES = {"fine": 0, "close": 0, "medium": 1, "normal": 1, "coarse": 2, "loose": 2}


def iso273_clearance(size: str, series: str = "medium") -> float:
    """ISO 273 clearance hole diameter (mm) for a metric screw: series "fine" | "medium" | "coarse"
    (also "close" | "normal" | "loose")."""
    key = str(size).upper().replace(" ", "")
    if key not in ISO_273:
        raise KeyError(f"unknown screw size {size!r} for ISO 273; known: {', '.join(ISO_273)}")
    s = str(series).lower()
    if s not in _SERIES:
        raise ValueError("series must be fine, medium or coarse (close, normal or loose)")
    return ISO_273[key][_SERIES[s]]


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
