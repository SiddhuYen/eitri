"""Ball bearing dimensions and pockets."""
from __future__ import annotations

import build123d as bd
from build123d import Align, Cylinder, Pos

__all__ = ["BEARINGS", "bearing_dims", "bearing_pocket_cutter", "bearing_placeholder"]

# name: (bore, outer diameter, width) in mm - deep groove ball bearings
BEARINGS: dict[str, tuple[float, float, float]] = {
    "MR63": (3, 6, 2.5), "MR85": (5, 8, 2.5), "MR105": (5, 10, 4), "MR115": (5, 11, 4), "MR128": (8, 12, 3.5),
    "623": (3, 10, 4), "624": (4, 13, 5), "625": (5, 16, 5), "626": (6, 19, 6), "627": (7, 22, 7), "628": (8, 24, 8),
    "683": (3, 7, 3), "684": (4, 9, 4), "685": (5, 11, 5), "686": (6, 13, 5), "687": (7, 14, 5), "688": (8, 16, 5),
    "693": (3, 8, 4), "694": (4, 11, 4), "695": (5, 13, 4), "696": (6, 15, 5), "697": (7, 17, 5), "698": (8, 19, 6),
    "608": (8, 22, 7), "609": (9, 24, 7), "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9),
    "6003": (17, 35, 10), "6004": (20, 42, 12), "6005": (25, 47, 12), "6200": (10, 30, 9), "6201": (12, 32, 10),
    "6202": (15, 35, 11), "6203": (17, 40, 12), "6204": (20, 47, 14), "6300": (10, 35, 11), "6301": (12, 37, 12),
    "6700": (10, 15, 3), "6701": (12, 18, 4), "6702": (15, 21, 4), "6703": (17, 23, 4), "6704": (20, 27, 4),
    "6800": (10, 19, 5), "6801": (12, 21, 5), "6802": (15, 24, 5), "6803": (17, 26, 5), "6804": (20, 32, 7),
    "6805": (25, 37, 7), "6806": (30, 42, 7), "6807": (35, 47, 7), "6808": (40, 52, 7), "6900": (10, 22, 6),
    "6901": (12, 24, 6), "6902": (15, 28, 7), "6903": (17, 30, 7), "6904": (20, 37, 9), "6905": (25, 42, 9),
}


def bearing_dims(name: str) -> tuple[float, float, float]:
    """(bore, outer diameter, width) of a standard ball bearing, e.g. "608" -> (8, 22, 7)."""
    key = name.upper().replace("ZZ", "").replace("-2RS", "").replace("RS", "").strip()
    if key not in BEARINGS:
        raise KeyError(f"unknown bearing {name!r}; known: {', '.join(BEARINGS)}")
    return BEARINGS[key]


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
