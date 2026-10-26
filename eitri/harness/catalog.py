"""Reference data for harness design: the connector catalog, AWG sizes and ampacity, wire colours.

Everything here is plain data. Ratings are the manufacturers' typical continuous per-pin ratings; a model
file can always give its own (`Connector(..., rating_a=...)`), and an unknown connector type is allowed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# --------------------------------------------------------------------------- connectors


@dataclass(frozen=True)
class ConnectorSpec:
    key: str
    family: str                  # what it is, for the BOM
    pitch_mm: float | None       # pin pitch (None: single-pin terminals)
    rating_a: float | None       # per-pin continuous rating (None: limited by the wire, not the terminal)
    termination: str             # crimp | solder | ring | spade | bare
    terminal_pn: str = ""        # the crimp terminal (contact) part number
    housing_pn: str = ""         # housing part number; {n} is the position count
    strip_mm: float = 3.0        # insulation to strip at this termination
    default_pins: int = 1
    gender: str = ""


CATALOG: dict[str, ConnectorSpec] = {s.key: s for s in [
    ConnectorSpec("JST-XH", "JST XH 2.50 mm wire-to-board housing", 2.5, 3.0, "crimp", "SXH-001T-P0.6", "XHP-{n}", 2.5, 2),
    ConnectorSpec("JST-PH", "JST PH 2.00 mm wire-to-board housing", 2.0, 2.0, "crimp", "SPH-002T-P0.5S", "PHR-{n}", 2.0, 2),
    ConnectorSpec("DUPONT-2.54", "Dupont 2.54 mm housing", 2.54, 2.0, "crimp", "Dupont 2.54 crimp contact", "", 2.5, 2),
    ConnectorSpec("MOLEX-MICROFIT-3.0", "Molex Micro-Fit 3.0 housing", 3.0, 8.5, "crimp", "43030-0001", "43025-{n:02d}00", 3.5, 2),
    ConnectorSpec("XT30", "Amass XT30 bullet connector", 5.0, 30.0, "solder", "", "", 6.0, 2),
    ConnectorSpec("XT60", "Amass XT60 bullet connector", 7.2, 60.0, "solder", "", "", 8.0, 2),
    ConnectorSpec("XT90", "Amass XT90 bullet connector", 10.0, 90.0, "solder", "", "", 10.0, 2),
    ConnectorSpec("RING", "ring terminal", None, None, "ring", "ring terminal (size to the stud)", "", 6.0),
    ConnectorSpec("SPADE-2.8", "2.8 mm spade (Faston) terminal", None, 6.0, "spade", "2.8 mm spade terminal", "", 5.0),
    ConnectorSpec("SPADE-4.8", "4.8 mm spade (Faston) terminal", None, 12.0, "spade", "4.8 mm spade terminal", "", 5.5),
    ConnectorSpec("SPADE-6.3", "6.3 mm spade (Faston) terminal", None, 20.0, "spade", "6.3 mm spade terminal", "", 6.0),
    ConnectorSpec("BARE", "bare / tinned end (soldered or screwed by the user)", None, None, "bare", "", "", 6.0),
    ConnectorSpec("SOLDER", "soldered to a pad or pin", None, None, "bare", "", "", 3.0),
    # development boards: wires end in Dupont 2.54 male contacts pushed into the board's female headers. No per-pin rating here:
    # the I/O pins carry signals, and the 5V / 3V3 supply pins are graded by the power-budget check (limits and sources: eitri/power.py)
    ConnectorSpec("ARDUINO-UNO", "Arduino Uno R3 board (female 2.54 mm headers)", 2.54, None, "crimp", "Dupont 2.54 male crimp contact", "", 2.5, 3),
    ConnectorSpec("ARDUINO-NANO", "Arduino Nano board (2.54 mm headers)", 2.54, None, "crimp", "Dupont 2.54 female crimp contact", "", 2.5, 3),
]}
BOARD_TYPES = {"ARDUINO-UNO": "uno", "ARDUINO-NANO": "nano"}

ALIASES = {
    "XH": "JST-XH", "JSTXH": "JST-XH", "JST XH": "JST-XH",
    "PH": "JST-PH", "JSTPH": "JST-PH", "JST PH": "JST-PH",
    "DUPONT": "DUPONT-2.54", "DUPONT2.54": "DUPONT-2.54", "DUPONT 2.54": "DUPONT-2.54", "2.54": "DUPONT-2.54",
    "MICROFIT": "MOLEX-MICROFIT-3.0", "MICRO-FIT": "MOLEX-MICROFIT-3.0", "MOLEX-MICROFIT": "MOLEX-MICROFIT-3.0",
    "MOLEX MICRO-FIT 3.0": "MOLEX-MICROFIT-3.0", "MICROFIT-3.0": "MOLEX-MICROFIT-3.0", "MICRO-FIT 3.0": "MOLEX-MICROFIT-3.0",
    "SPADE": "SPADE-6.3", "FASTON": "SPADE-6.3", "RING TERMINAL": "RING", "RING-TERMINAL": "RING",
    "TINNED": "BARE", "WIRE": "BARE", "SOLDER-PAD": "SOLDER",
    "UNO": "ARDUINO-UNO", "ARDUINO UNO": "ARDUINO-UNO", "ARDUINO-UNO-R3": "ARDUINO-UNO", "ARDUINO UNO R3": "ARDUINO-UNO",
    "NANO": "ARDUINO-NANO", "ARDUINO NANO": "ARDUINO-NANO",
}
for _s in ("M3", "M4", "M5", "M6", "M8", "M10"):
    ALIASES[f"RING-{_s}"] = "RING"


def normalize_type(text: str) -> str:
    """The catalog key for a connector type as typed ("jst xh", "XT-60"), else the text uppercased."""
    t = " ".join(str(text).strip().upper().split())
    if t in CATALOG:
        return t
    if t in ALIASES:
        return ALIASES[t]
    squashed = t.replace("_", "-").replace(" ", "-")
    if squashed in CATALOG:
        return squashed
    if squashed in ALIASES:
        return ALIASES[squashed]
    no_dash = t.replace("-", "").replace(" ", "").replace("_", "")
    for key in CATALOG:
        if key.replace("-", "") == no_dash:
            return key
    return t


def spec_for(type_name: str) -> ConnectorSpec | None:
    return CATALOG.get(normalize_type(type_name))


def catalog_table() -> str:
    rows = ["| type | pitch | rating / pin | termination | notes |", "|---|---|---|---|---|"]
    for s in CATALOG.values():
        rows.append(f"| {s.key} | {'' if s.pitch_mm is None else f'{s.pitch_mm:g} mm'} | "
                    f"{'wire-limited' if s.rating_a is None else f'{s.rating_a:g} A'} | {s.termination} | {s.family} |")
    return "\n".join(rows)


# --------------------------------------------------------------------------- AWG

# Chassis-wiring ampacity (single conductor in free air), amperes, even AWG sizes. Odd sizes are the
# geometric mean of their neighbours.
_CHASSIS_AMPS = {
    -3: 380, -2: 328, -1: 283, 0: 245, 1: 211, 2: 181, 3: 158, 4: 135, 6: 101, 8: 73, 10: 55, 12: 41, 14: 32,
    16: 22, 18: 16, 20: 11, 22: 7, 24: 3.5, 26: 2.2, 28: 1.4, 30: 0.86, 32: 0.53, 34: 0.33, 36: 0.21, 38: 0.13, 40: 0.081,
}
AWG_MIN, AWG_MAX = -3, 40            # -3 is 4/0
K_COPPER = 10.75                     # ohm-circular-mil per foot, the voltage-drop constant for copper
MM2_PER_CM = 0.000506707479          # one circular mil in mm^2
FT_PER_MM = 1 / 304.8


def awg_diameter_mm(n: int) -> float:
    return 0.127 * 92 ** ((36 - n) / 39)


def awg_area_mm2(n: int) -> float:
    return math.pi / 4 * awg_diameter_mm(n) ** 2


def awg_circular_mils(n: int) -> float:
    return (awg_diameter_mm(n) / 0.0254) ** 2          # d in mils, squared


def awg_ampacity(n: int) -> float:
    if n in _CHASSIS_AMPS:
        return float(_CHASSIS_AMPS[n])
    lo, hi = n - 1, n + 1
    return math.sqrt(_CHASSIS_AMPS[lo] * _CHASSIS_AMPS[hi])


def awg_label(n: int) -> str:
    return "4/0 AWG" if n == -3 else "3/0 AWG" if n == -2 else "2/0 AWG" if n == -1 else "1/0 AWG" if n == 0 else f"{n} AWG"


def nearest_awg(area_mm2: float) -> int:
    best = min(range(AWG_MIN, AWG_MAX + 1), key=lambda n: abs(math.log(awg_area_mm2(n) / area_mm2)))
    return best


@dataclass(frozen=True)
class Gauge:
    """A conductor size: an AWG number, or a metric cross-section in mm^2."""
    awg: int | None
    mm2: float

    @staticmethod
    def parse(value) -> "Gauge":
        if isinstance(value, Gauge):
            return value
        if isinstance(value, bool):
            raise ValueError(f"not a wire gauge: {value!r}")
        if isinstance(value, (int, float)):
            if float(value) != int(value):
                raise ValueError(f"AWG sizes are whole numbers, got {value!r}; for a metric size write '0.5mm2'")
            n = int(value)
            if not AWG_MIN <= n <= AWG_MAX:
                raise ValueError(f"AWG {n} is outside the supported range {AWG_MIN if AWG_MIN > 0 else '4/0'}..{AWG_MAX}")
            return Gauge(n, awg_area_mm2(n))
        text = str(value).strip().lower().replace(" ", "").replace("²", "2")
        for suffix in ("mm2", "sqmm", "mmsq"):
            if text.endswith(suffix):
                try:
                    area = float(text[:-len(suffix)])
                except ValueError:
                    break
                if area <= 0:
                    raise ValueError(f"wire cross-section must be positive, got {value!r}")
                return Gauge(None, area)
        for suffix in ("awg",):
            if text.endswith(suffix):
                text = text[:-len(suffix)]
        text = {"4/0": "-3", "3/0": "-2", "2/0": "-1", "1/0": "0", "0000": "-3", "000": "-2", "00": "-1"}.get(text, text)
        try:
            return Gauge.parse(int(text))
        except ValueError:
            raise ValueError(f"not a wire gauge: {value!r} (use an AWG number like 18, or a metric size like '0.5mm2')") from None

    @property
    def equiv_awg(self) -> int:
        return self.awg if self.awg is not None else nearest_awg(self.mm2)

    @property
    def circular_mils(self) -> float:
        return awg_circular_mils(self.awg) if self.awg is not None else self.mm2 / MM2_PER_CM

    @property
    def ampacity(self) -> float:
        return awg_ampacity(self.equiv_awg)

    @property
    def label(self) -> str:
        return awg_label(self.awg) if self.awg is not None else f"{self.mm2:g} mm²"

    @property
    def key(self) -> str:
        return f"{self.awg}" if self.awg is not None else f"{self.mm2:g}mm2"

    def to_json(self):
        return self.awg if self.awg is not None else f"{self.mm2:g}mm2"


def voltage_drop(current_a: float, length_mm: float, gauge: Gauge, round_trip: bool = True) -> float:
    """V = 2 * K * I * L / CM (K = 10.75 for copper, L in feet, CM the conductor's circular mils); the 2 is the
    round trip (the supply conductor and an equal return conductor)."""
    factor = 2.0 if round_trip else 1.0
    return factor * K_COPPER * current_a * (length_mm * FT_PER_MM) / gauge.circular_mils


# --------------------------------------------------------------------------- colours

COLORS: dict[str, tuple[str, str]] = {      # code -> (name, hex)
    "BK": ("black", "#1c1c1c"), "BN": ("brown", "#7b4a2a"), "RD": ("red", "#d62828"), "OG": ("orange", "#f77f00"),
    "YE": ("yellow", "#f4d03f"), "GN": ("green", "#2e9e4f"), "BU": ("blue", "#2b6cd4"), "VT": ("violet", "#7d3fc4"),
    "GY": ("grey", "#8d99ae"), "WH": ("white", "#f7f7f2"), "PK": ("pink", "#f28fb5"), "TQ": ("turquoise", "#1fb5ad"),
    "SR": ("silver", "#c0c4c8"), "GD": ("gold", "#c9a227"),
}
_COLOR_WORDS = {name: code for code, (name, _h) in COLORS.items()}
_COLOR_WORDS.update({"gray": "GY", "purple": "VT", "tan": "BN", "grn": "GN", "blk": "BK", "wht": "WH", "ylw": "YE", "org": "OG",
                     "blu": "BU", "brn": "BN", "gry": "GY", "vio": "VT"})
_CODES = tuple(COLORS)


def _one_color(text: str) -> str:
    t = text.strip()
    if t.upper() in COLORS:
        return t.upper()
    if t.lower() in _COLOR_WORDS:
        return _COLOR_WORDS[t.lower()]
    raise ValueError(f"unknown wire colour {text!r}; use a code ({', '.join(_CODES)}), a name like 'red', or a stripe like 'RD/WH'")


def normalize_color(value: str) -> str:
    """'red' -> 'RD'; 'rd/wh', 'RDWH' -> 'RD/WH'. Raises ValueError for anything else."""
    t = str(value).strip()
    if not t:
        raise ValueError("a wire colour is required")
    for sep in ("/", "-", "+", " "):
        if sep in t:
            a, _, b = t.partition(sep)
            return f"{_one_color(a)}/{_one_color(b)}"
    if t.upper() in COLORS or t.lower() in _COLOR_WORDS:
        return _one_color(t)
    u = t.upper()
    if len(u) == 4 and u[:2] in COLORS and u[2:] in COLORS:
        return f"{u[:2]}/{u[2:]}"
    raise ValueError(f"unknown wire colour {value!r}; use a code ({', '.join(_CODES)}), a name like 'red', or a stripe like 'RD/WH'")


def color_hex(code: str) -> str:
    return COLORS[code.split("/")[0]][1]


def stripe_hex(code: str) -> str | None:
    return COLORS[code.split("/")[1]][1] if "/" in code else None


def color_name(code: str) -> str:
    base, _, stripe = code.partition("/")
    return COLORS[base][0] + (f"/{COLORS[stripe][0]}" if stripe else "")
