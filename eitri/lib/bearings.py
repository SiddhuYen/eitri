"""Ball bearing dimensions and pockets."""
from __future__ import annotations

import build123d as bd
from build123d import Align, Cylinder, Pos

__all__ = ["BEARINGS", "bearing_dims", "bearing_pocket_cutter", "bearing_placeholder"]

# name: (bore, outer diameter, width) in mm - deep groove ball bearings, OPEN type (no shields or seals).
# Source: NSK Miniature Ball Bearings, CAT. No. E126i (2015), metric series 600/MR tables, catalogue p.32 (bore 1-4 mm)
# and p.34 (bore 5-9 mm), columns d, D, B (open) and B1 (shielded ZZ/ZZ1):
# https://www.nsk.com/content/dam/nsk/am/en_us/documents/bearings-americas/Miniature%20Ball%20Bearings.pdf
# (re-read 2026-11-07). Most miniature bearings are wider shielded than open; those are in SHIELDED_WIDTH.
# MR115 is listed shielded only (B "-", B1 4), so its single width is the shielded one.
BEARINGS: dict[str, tuple[float, float, float]] = {
    "MR63": (3, 6, 2), "MR85": (5, 8, 2), "MR105": (5, 10, 3), "MR115": (5, 11, 4), "MR128": (8, 12, 2.5),
    "623": (3, 10, 4), "624": (4, 13, 5), "625": (5, 16, 5), "626": (6, 19, 6), "627": (7, 22, 7), "628": (8, 24, 8),
    "683": (3, 7, 2), "684": (4, 9, 2.5), "685": (5, 11, 3), "686": (6, 13, 3.5), "687": (7, 14, 3.5), "688": (8, 16, 4),
    "693": (3, 8, 3), "694": (4, 11, 4), "695": (5, 13, 4), "696": (6, 15, 5), "697": (7, 17, 5), "698": (8, 19, 6),
    "608": (8, 22, 7), "609": (9, 24, 7), "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9),
    "6003": (17, 35, 10), "6004": (20, 42, 12), "6005": (25, 47, 12), "6200": (10, 30, 9), "6201": (12, 32, 10),
    "6202": (15, 35, 11), "6203": (17, 40, 12), "6204": (20, 47, 14), "6300": (10, 35, 11), "6301": (12, 37, 12),
    "6700": (10, 15, 3), "6701": (12, 18, 4), "6702": (15, 21, 4), "6703": (17, 23, 4), "6704": (20, 27, 4),
    "6800": (10, 19, 5), "6801": (12, 21, 5), "6802": (15, 24, 5), "6803": (17, 26, 5), "6804": (20, 32, 7),
    "6805": (25, 37, 7), "6806": (30, 42, 7), "6807": (35, 47, 7), "6808": (40, 52, 7), "6900": (10, 22, 6),
    "6901": (12, 24, 6), "6902": (15, 28, 7), "6903": (17, 30, 7), "6904": (20, 37, 9), "6905": (25, 42, 9),
}

# name: width B1 of the shielded (ZZ) / sealed (2RS) type where it differs from the open width above (NSK E126i, as above;
# the sealed width is taken as B1, the shielded one: a pocket for either is then deep enough).
SHIELDED_WIDTH: dict[str, float] = {"MR63": 2.5, "MR85": 2.5, "MR105": 4, "MR128": 3.5, "683": 3, "684": 4, "685": 5, "686": 5, "687": 5, "688": 5, "693": 4}


def bearing_dims(name: str) -> tuple[float, float, float]:
    """(bore, outer diameter, width) of a standard ball bearing, e.g. "608" -> (8, 22, 7).

    A bare name is the open bearing; a ZZ / 2RS / RS suffix gives the shielded or sealed width, which is wider for many
    miniature sizes ("683" -> (3, 7, 2), "683ZZ" -> (3, 7, 3))."""
    upper = name.upper().strip()
    key = upper.replace("ZZ", "").replace("-2RS", "").replace("2RS", "").replace("RS", "").replace("-", "").strip()
    if key not in BEARINGS:
        raise KeyError(f"unknown bearing {name!r}; known: {', '.join(BEARINGS)} (add ZZ or 2RS for the shielded/sealed width)")
    bore, od, w = BEARINGS[key]
    if key != upper.replace("-", "") and key in SHIELDED_WIDTH:
        w = SHIELDED_WIDTH[key]
    return bore, od, w


def bearing_pocket_cutter(name: str, fit: float = 0.1, depth: float | None = None, extra: float = 1.0,
                          through_bore_clearance: float = 1.0) -> bd.Shape:
    """A cutter for a bearing pocket: top at Z=0, pocket diameter = OD + fit (press fit
    ~0.0-0.05, printed parts ~0.1-0.2), depth = bearing width unless given, plus a
    through hole slightly larger than the bore so the inner race and shaft clear.
    """
    bore, od, w = bearing_dims(name)
    depth = w if depth is None else depth
    pocket = Pos(0, 0, extra) * Cylinder((od + fit) / 2, depth + extra, align=(Align.CENTER, Align.CENTER, Align.MAX))
    through = Pos(0, 0, extra) * Cylinder((bore + 2 * through_bore_clearance) / 2, 500, align=(Align.CENTER, Align.CENTER, Align.MAX))
    return pocket + through


def bearing_placeholder(name: str) -> bd.Shape:
    """A simple ring with the bearing's bore/OD/width standing on Z=0, for assemblies."""
    bore, od, w = bearing_dims(name)
    ring = Cylinder(od / 2, w, align=(Align.CENTER, Align.CENTER, Align.MIN)) - \
        Pos(0, 0, -1) * Cylinder(bore / 2, w + 2, align=(Align.CENTER, Align.CENTER, Align.MIN))
    ring.label = f"bearing_{name}"
    return ring
