"""Protective devices for low-voltage DC harnesses: blade fuses, 5x20 mm cartridge fuses, PTC resettable fuses, breakers.

Plain data, every figure with its source (FUSE_SOURCES, quoted; fetched 2026-11-12). Where sources disagree, the
conservative figure is used: the lower one for a limit (a voltage or interrupting rating, a continuous-load fraction).

    resolve(type, rating_A, ...) -> Device     what Fuse(name, type, rating_A, ...) stands for
    suggest(family, max_A) -> float | None     the largest catalog rating at or under max_A (a fix hint)

A part not in the catalog ("FUSE", "PTC", "BREAKER", or any other name) takes its figures from the model file; what is
left unset makes the checks that need it report "not run" (eitri.harness.protection), never a silent pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------- blade fuses (ISO 8820-3) and 5x20 mm cartridges

SRC_ATOF = ("Littelfuse 'ATOF Blade Fuses, Rated 32V' (287 series, replaces the obsolete ATO 257 series), Revised 12/06/2022 "
            "(Littelfuse PDF as hosted at store.comet.bg/download-file.php?id=27669; littelfuse.com refuses automated fetches; the "
            "08/18/2022 copy at static.chipdip.ru/lib/614/DOC034614307.pdf has the same numbers). p.1: ratings 1-40 A, 'Voltage Rating: "
            "32 V dc', 'Interrupting Rating: 1000 A @ 32 V dc', 'Comply With: SAE J1284 and ISO 8820-3'; p.3 Time-Current "
            "Characteristics (3-30 A): 110 % 360,000 s min, 135 % 0.75-600 s, 200 % 0.15-5 s, 350 % 0.08-0.5 s")
SRC_ATOF_TEMP = ("Littelfuse ATOF 287 Revised 12/06/2022, p.3 'Temperature Table: Max. allowed current load (A) at ambient temperature "
                 "(typical derating)', -40/0/20/65/85/110/125 C; 'Temperature Security Margin is 20%'")
SRC_MINI = ("Littelfuse 'MINI Blade Fuses, Rated 32V' (297 series), Revised 05/26/2023 (Littelfuse PDF as hosted at "
            "store.comet.bg/download-file.php?id=27670). p.1: ratings 2-30 A, 'Voltage Rating: 32 VDC', 'Interrupting Rating: "
            "1000A @ 32 VDC', SAE J2077 / ISO 8820-3; p.3 Time-Current Characteristics: 110 % 360,000 s min, 135 % 0.75-600 s, "
            "200 % 0.15-5 s, 350 % 0.08-0.5 s, 600 % 0.03-0.1 s")
SRC_MINI_TEMP = ("Littelfuse MINI 297 Revised 05/26/2023, p.3 'Temperature Table ... (typical derating)', -40 to 100 C; "
                 "'Temperature Security Margin is 20%'")
SRC_LF_75 = ("Littelfuse 'Circuit Protection Products Catalog & Design Guide' (Revised Jan 12, 2009; docs.rs-online.com/c6d2/"
             "0900766b81388d9c.pdf), Fuse Selection Guide p.9: 'The current rating of a fuse is typically derated 25% for operation "
             "at 25C to avoid nuisance blowing ... Catalog Fuse Rating = Normal Operating Current / 0.75'; p.6 RERATING: 'operated at "
             "no more than 75% of the nominal current rating' (UL 248-14). For ATO blades the same factor: Littelfuse FKS ATO Style "
             "Blade Fuse 32V (Automotive catalog 2006, p.2): 'max. operating current: 0.75 x Irat at 23C'")
SRC_487 = ("Littelfuse 487 Series 5x20 mm fast-acting, Revised 08/22/19 (Littelfuse PDF as hosted at static.chipdip.ru/lib/881/"
           "DOC046881352.pdf). p.2: 8, 10, 12.5, 15, 16, 20 A, all '420VDC 420VAC', interrupting '300A@420VDC'; p.1: 100 % 60 min "
           "min, 210 % 30 min max, 275 % 0.04-20 s, 400 % 0.01-1.0 s; UL 248-14, EN 60127-7; p.2: 'Rerating depicted in this curve "
           "is in addition to the standard derating of 25% for continuous operation'")
SRC_477 = ("Littelfuse 477 Series 5x20 mm time-lag, Revised 09/05/18 (Littelfuse PDF as hosted at static.chipdip.ru/lib/840/"
           "DOC012840973.pdf). p.1: '400Vdc/500Vac rated ... time-lag', IEC 60127-2 Sheet 5; p.2 ratings 0.5, 0.8, 1, 2, 3.15, 4, 5, "
           "6.3, 8, 10, 12, 16 A; interrupting at 400 VDC: 0.5-3.15 A 1500 A, 4-12 A 500 A, 16 A 400 A; footnote: 'Semko approval "
           "for 100A@500Vac and 200A@400Vdc', '300A@400Vdc for 16A': the lower figures (200 A; 300 A for 16 A) are used")
SRC_5X20_TEMP = ("Littelfuse 487 / 477 datasheets, temperature rerating curve, read off the graph (not printed numbers): about 110 % at "
                 "-55 C, 100 % at 25 C, about 91 % at 125 C")

# ATOF (287) and MINI (297): the maker's 'max. allowed current load' (A) at each ambient (p.3 tables); eitri uses each as a
# rerating factor relative to 25 C (interpolated) on top of the 75 % continuous-load rule: the conservative combination
ATOF_TEMP_C = (-40, 0, 20, 65, 85, 110, 125)
ATOF_LOAD = {1: (1, 1, 1, 1, 1, 1, 1), 2: (2, 2, 2, 2, 2, 1, 1), 3: (3, 3, 3, 3, 2, 2, 2), 4: (4, 4, 4, 3, 3, 3, 2),
             5: (5, 5, 5, 4, 4, 3, 3), 7.5: (8, 7, 7, 6, 5, 5, 4), 10: (10, 10, 10, 8, 7, 6, 5), 15: (15, 15, 14, 12, 11, 9, 8),
             20: (20, 19, 18, 15, 14, 12, 10), 25: (25, 25, 23, 19, 18, 15, 13), 30: (30, 29, 27, 23, 21, 18, 15),
             35: (35, 35, 35, 29, 27, 22, 19), 40: (40, 39, 37, 31, 28, 24, 20)}
MINI_TEMP_C = (-40, -20, 0, 20, 40, 60, 80, 100)
MINI_LOAD = {2: (2.0, 2.0, 2.0, 1.9, 1.7, 1.6, 1.4, 1.3), 3: (3.0, 2.8, 2.7, 2.5, 2.4, 2.2, 2.0, 1.8),
             4: (4.0, 3.8, 3.6, 3.4, 3.2, 2.9, 2.7, 2.4), 5: (5.0, 4.8, 4.5, 4.3, 4.0, 3.7, 3.4, 3.0),
             7.5: (7.5, 7.1, 6.8, 6.4, 6.0, 5.5, 5.1, 4.5), 10: (10, 10, 9.8, 9.2, 8.6, 8.0, 7.3, 6.5),
             15: (15, 15, 14, 13, 12, 12, 11, 9.0), 20: (20, 19, 18, 17, 16, 15, 14, 12), 25: (25, 24, 22, 21, 20, 18, 17, 15),
             30: (30, 29, 27, 26, 24, 22, 20, 18)}
RERATE_5X20 = {-55: 1.10, 25: 1.00, 125: 0.91}        # graph-read (SRC_5X20_TEMP)

# --------------------------------------------------------------------------- PTC resettable fuses: Bourns MF-R (radial leaded)

SRC_MFR = ("Bourns MF-R Series PTC Resettable Fuses datasheet, Rev. AR 09/26 (bourns.com/docs/Product-Datasheets/mfr.pdf, read "
           "2026-11-12), p.1 Electrical Characteristic table at 23 C: Vmax, Imax, Ihold, Itrip per model; p.2 test 'Hold Current: "
           "30 min. at Ihold: No trip'")
SRC_MFR_DERATE = ("Bourns MF-R Rev. AR 09/26, p.4 'Thermal Derating Table - Ihold / Itrip (Amps)', -40 to 85 C (operating range "
                  "-40 C to +85 C): the hold current at each ambient")
SRC_MFR_VMAX = ("Bourns MF-R Rev. AR 09/26, p.1: Vmax (V) as published; the datasheet does not mark it AC or DC, and eitri grades "
                "it against the DC system voltage")
#            model:      Vmax  Imax   Ihold  Itrip    (p.1, 23 C)
MFR_PARTS = {
    "MF-R005": (60, 40, 0.05, 0.10), "MF-R010": (60, 40, 0.10, 0.20), "MF-R017": (60, 40, 0.17, 0.34),
    "MF-R020": (60, 40, 0.20, 0.40), "MF-R025": (60, 40, 0.25, 0.50), "MF-R030": (60, 40, 0.30, 0.60),
    "MF-R040": (60, 40, 0.40, 0.80), "MF-R050": (60, 40, 0.50, 1.00), "MF-R065": (60, 40, 0.65, 1.30),
    "MF-R075": (60, 40, 0.75, 1.50), "MF-R090": (60, 40, 0.90, 1.80), "MF-R090-0-9": (30, 40, 0.90, 1.80),
    "MF-R110": (30, 40, 1.10, 2.20), "MF-R135": (30, 40, 1.35, 2.70), "MF-R160": (30, 40, 1.60, 3.20),
    "MF-R185": (30, 40, 1.85, 3.70), "MF-R250": (30, 40, 2.50, 5.00), "MF-R250-0-10": (30, 40, 2.50, 5.00),
    "MF-R300": (30, 40, 3.00, 6.00), "MF-R400": (30, 40, 4.00, 8.00), "MF-R500": (30, 40, 5.00, 10.00),
    "MF-R600": (30, 40, 6.00, 12.00), "MF-R700": (30, 40, 7.00, 14.00), "MF-R800": (30, 40, 8.00, 16.00),
    "MF-R900": (30, 40, 9.00, 18.00), "MF-R1100": (16, 100, 11.00, 22.00),
}
MFR_DERATE_C = (-40, -20, 0, 23, 40, 50, 60, 70, 85)
#            Ihold (A) at each ambient above (p.4)
MFR_IHOLD_BY_C = {
    "MF-R005": (.08, .07, .06, .05, .04, .04, .03, .03, .02), "MF-R010": (.16, .14, .12, .10, .08, .07, .06, .05, .04),
    "MF-R017": (.26, .23, .20, .17, .14, .12, .11, .09, .07), "MF-R020": (.31, .27, .24, .20, .16, .14, .13, .11, .08),
    "MF-R025": (.39, .34, .30, .25, .20, .18, .16, .14, .10), "MF-R030": (.47, .41, .36, .30, .24, .22, .19, .16, .12),
    "MF-R040": (.62, .54, .48, .40, .32, .29, .25, .22, .16), "MF-R050": (.78, .68, .60, .50, .41, .36, .32, .27, .20),
    "MF-R065": (1.01, .88, .77, .65, .53, .47, .41, .35, .26), "MF-R075": (1.16, 1.02, .89, .75, .61, .54, .47, .41, .30),
    "MF-R090": (1.40, 1.22, 1.07, .90, .73, .65, .57, .49, .36), "MF-R090-0-9": (1.40, 1.22, 1.07, .90, .73, .65, .57, .49, .36),
    "MF-R110": (1.60, 1.43, 1.27, 1.10, .91, .85, .75, .67, .57), "MF-R135": (1.96, 1.76, 1.55, 1.35, 1.12, 1.04, .92, .82, .70),
    "MF-R160": (2.32, 2.08, 1.84, 1.60, 1.33, 1.23, 1.09, .98, .83), "MF-R185": (2.68, 2.41, 2.13, 1.85, 1.54, 1.42, 1.26, 1.13, .96),
    "MF-R250": (3.63, 3.25, 2.88, 2.50, 2.08, 1.93, 1.70, 1.53, 1.30), "MF-R250-0-10": (3.63, 3.25, 2.88, 2.50, 2.08, 1.93, 1.70, 1.53, 1.30),
    "MF-R300": (4.35, 3.90, 3.45, 3.00, 2.49, 2.31, 2.04, 1.83, 1.56), "MF-R400": (5.80, 5.20, 4.60, 4.00, 3.32, 3.08, 2.72, 2.44, 2.08),
    "MF-R500": (7.25, 6.50, 5.75, 5.00, 4.15, 3.85, 3.40, 3.05, 2.60), "MF-R600": (8.70, 7.80, 6.90, 6.00, 4.98, 4.62, 4.08, 3.66, 3.12),
    "MF-R700": (10.1, 9.10, 8.05, 7.00, 5.81, 5.39, 4.76, 4.27, 3.64), "MF-R800": (11.6, 10.4, 9.20, 8.00, 6.64, 6.16, 5.44, 4.88, 4.16),
    "MF-R900": (13.0, 11.7, 10.3, 9.00, 7.47, 6.93, 6.12, 5.49, 4.68), "MF-R1100": (16.1, 14.6, 13.1, 11.0, 9.40, 8.80, 7.80, 6.90, 5.20),
}

# --------------------------------------------------------------------------- placement, signal nets

# The short cite each check line carries -> the full source (quoted). The reference doc lists them all.
FUSE_SOURCES: dict[str, str] = {}


def cite(short: str, full: str) -> str:
    FUSE_SOURCES[short] = full
    return short


CITE_ATOF = cite("Littelfuse ATOF 287 datasheet rev. 12/06/2022, p.1", SRC_ATOF)
CITE_ATOF_TEMP = cite("Littelfuse ATOF 287 rev. 12/06/2022, p.3 temperature table", SRC_ATOF_TEMP)
CITE_MINI = cite("Littelfuse MINI 297 datasheet rev. 05/26/2023, p.1", SRC_MINI)
CITE_MINI_TEMP = cite("Littelfuse MINI 297 rev. 05/26/2023, p.3 temperature table", SRC_MINI_TEMP)
CITE_LF_75 = cite("Littelfuse Fuse Selection Guide (2009 catalog p.9): 75 % at 25 C", SRC_LF_75)
CITE_487 = cite("Littelfuse 487 datasheet rev. 08/22/19, p.2", SRC_487)
CITE_477 = cite("Littelfuse 477 datasheet rev. 09/05/18, p.2", SRC_477)
CITE_5X20_TEMP = cite("Littelfuse 487/477 rerating curve (graph-read)", SRC_5X20_TEMP)
# Part numbers: the tin-plated ordering codes as printed, 0287xxx.PXCN (ATOF, Rev 08/18/2022 ordering table) and 0297xxx.WXNV
# (MINI, 05/26/2023), xxx the amp code (005 = 5 A). 7.5 A is coded "07.5" there with no full example printed: left blank.
CITE_MFR = cite("Bourns MF-R datasheet Rev. AR 09/26, p.1 (23 C)", SRC_MFR)
CITE_MFR_DERATE = cite("Bourns MF-R Rev. AR 09/26, p.4 thermal derating table", SRC_MFR_DERATE)
CITE_MFR_VMAX = cite("Bourns MF-R Rev. AR 09/26, p.1 Vmax", SRC_MFR_VMAX)

# The overcurrent device sits at the source end: ABYC E-11 as published by Blue Sea Systems. The 40 in / 72 in exceptions
# (a conductor in a sheath or enclosure all the way) are not modelled: the shortest figure is used for every conductor.
PLACEMENT_MAX_MM = 175.0
PLACEMENT_SOURCE_FULL = ("Blue Sea Systems, 'DC Circuit Protection' (bluesea.com/support/articles/Circuit_Protection/98, dated "
                    "23 Dec 2005, read via the Wayback Machine 2026-11-12): 'ABYC E-11 recommends that each ungrounded conductor "
                    "connected to a battery ... shall be provided with overcurrent protection within a distance of seven inches "
                    "(175mm) of the point of connection'; the 40 in / 72 in sheathed-conductor exceptions are not used")
SIZING_SOURCE = ("Blue Sea Systems, 'DC Circuit Protection' (23 Dec 2005): 'choose an amperage rating that is equal to or less than "
                 "the current rating, ampacity of the wire'")
PLACEMENT_SOURCE = cite("ABYC E-11 7 in (175 mm), per Blue Sea Systems 'DC Circuit Protection' 2005", PLACEMENT_SOURCE_FULL)
SIZING_SOURCE = cite("Blue Sea Systems 'DC Circuit Protection' 2005: rating <= wire ampacity", SIZING_SOURCE)
# Until 0.8.0 every signal=True net was graded at this MCU-pin figure; since 0.8.1 the limit comes from the device declared at
# the net's ends (eitri.harness.signals). Kept for the quote in the reference doc.
SIGNAL_MAX_A = 0.020
SIGNAL_SOURCE_FULL = ("Arduino UNO R3 full pinout (docs.arduino.cc/resources/pinouts/A000066-full-pinout.pdf): 'MAXIMUM current per "
                 "I/O pin is 20mA' (eitri.power.SRC_UNO_IO)")
SIGNAL_SOURCE = cite("Arduino UNO R3 pinout: 20 mA per I/O pin", SIGNAL_SOURCE_FULL)


# --------------------------------------------------------------------------- the resolved device


@dataclass
class Device:
    type: str                          # the catalog key or the model's own type ("ATO", "MF-R110", "BREAKER")
    kind: str                          # fuse | ptc | breaker
    family: str                        # the catalog family a fix hint picks from ("" for a part not in the catalog)
    rating_A: float | None             # fuse / breaker: rated current; PTC: hold current (Ihold at 23 C)
    trip_A: float | None = None        # PTC: trip current (Itrip at 23 C): the least it is sure to open at
    voltage_rating_V: float | None = None        # DC voltage rating
    interrupt_A: float | None = None   # interrupting rating at that voltage (PTC: Imax, the most it survives)
    continuous_fraction: float | None = None     # the steady load as a fraction of the rating the maker allows (at 25 C)
    rerate: dict | None = None         # {ambient C: the load allowed as a fraction of the rating} from the maker's temperature table
    rerate_ref_C: float = 25.0         # the ambient the continuous fraction is given at
    part_number: str = ""
    termination: str = "bare"          # how its leads join the harness (cut list / BOM): bare (an inline holder's leads) | solder
    holder: str = ""                   # a BOM row for the holder ("" when the part is its own body)
    catalog: bool = False
    sources: dict = field(default_factory=dict)  # figure -> source text ("rating", "voltage", "interrupt", "continuous", "rerate")

    @property
    def title(self) -> str:
        r = "" if self.rating_A is None else f" {self.rating_A:g} A"
        what = {"ptc": "PTC", "breaker": "breaker", "fuse": "fuse"}[self.kind]
        if self.kind == "ptc" and self.catalog:
            return f"{self.type} PTC, hold {self.rating_A:g} A"
        return f"{self.type}{r} {what}" if self.type not in ("FUSE", "PTC", "BREAKER") else f"{what}{r}"

    @property
    def bom_description(self) -> str:
        bits = [FAMILY_TEXT.get(self.family, self.title)]
        if self.rating_A is not None:
            bits.append(f"{'hold ' if self.kind == 'ptc' else ''}{self.rating_A:g} A")
        if self.trip_A is not None:
            bits.append(f"trip {self.trip_A:g} A")
        if self.voltage_rating_V is not None:
            bits.append(f"{self.voltage_rating_V:g} V DC")
        if self.interrupt_A is not None:
            bits.append(f"{'Imax' if self.kind == 'ptc' else 'interrupting'} {self.interrupt_A:g} A")
        return ", ".join(bits)

    def continuous(self, ambient_C: float | None) -> tuple[float | None, str, str]:
        """(fraction of the rating allowed as a steady load at this ambient, how it was found, its source)."""
        if self.continuous_fraction is None:
            return None, "no continuous-load derating known for this part", ""
        amb = DEFAULT_AMBIENT_C if ambient_C is None else float(ambient_C)
        said = f"{amb:g} C ambient" + (" (ASSUMPTION: Harness(ambient_C=...) not given)" if ambient_C is None else "")
        src = "; ".join(s for s in (self.sources.get("continuous"), self.sources.get("rerate")) if s)
        if not self.rerate:
            return self.continuous_fraction, f"{self.continuous_fraction:g} of the rating at {self.rerate_ref_C:g} C, no rerating table: {said}", src
        factor = _interp(self.rerate, amb)
        if factor is None:
            return 0.0, f"{said} is outside the rerating table ({min(self.rerate):g} to {max(self.rerate):g} C): not usable there", src
        frac = self.continuous_fraction * factor
        return frac, f"{self.continuous_fraction:g} at {self.rerate_ref_C:g} C x temperature rerating {factor:.3g} at {said}", src

    def to_json(self) -> dict:
        return {"type": self.type, "kind": self.kind, "title": self.title, "rating_A": self.rating_A, "trip_A": self.trip_A,
                "voltage_rating_V": self.voltage_rating_V, "interrupt_A": self.interrupt_A,
                "continuous_fraction": self.continuous_fraction, "part_number": self.part_number, "catalog": self.catalog}


def _interp(table: dict, x: float) -> float | None:
    pts = sorted(table.items())
    if x < pts[0][0] or x > pts[-1][0]:
        return None
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 != x0 else y0
    return pts[-1][1]


# --------------------------------------------------------------------------- families and lookup


@dataclass(frozen=True)
class Family:
    key: str                           # what Fuse(name, type=...) takes ("ATO")
    kind: str                          # fuse | ptc | breaker
    text: str                          # the BOM description
    ratings: tuple = ()                # the ampere ratings made
    voltage_V: float | None = None     # DC voltage rating
    interrupt_A: float | dict | None = None      # DC interrupting rating (a dict: per ampere rating)
    continuous_fraction: float | None = None
    rerate: dict | None = None
    rerate_ref_C: float = 25.0
    part_number: str = ""              # a format with {r} (the rating) when the maker's numbers follow one
    pn_by_rating: dict | None = None
    holder: str = ""
    termination: str = "bare"
    sources: dict = field(default_factory=dict)
    aliases: tuple = ()


FAMILY_TEXT: dict[str, str] = {}
FUSES: dict[str, Family] = {}
_ALIASES: dict[str, str] = {}
GENERIC = {"FUSE": "fuse", "PTC": "ptc", "BREAKER": "breaker"}


def _register(*fams: Family) -> None:
    for f in fams:
        FUSES[f.key] = f
        FAMILY_TEXT[f.key] = f.text
        for a in f.aliases:
            _ALIASES[a] = f.key


def _norm(text: str) -> str:
    t = " ".join(str(text).strip().upper().split())
    return _ALIASES.get(t, _ALIASES.get(t.replace(" ", "-"), t.replace(" ", "-")))


def _num(name: str, v, positive: bool = True) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (positive and v <= 0):
        from .model import HarnessError
        raise HarnessError(f"{name} must be a positive number, got {v!r}")
    return float(v)


def resolve(type: str, rating_A=None, *, kind=None, voltage_rating_V=None, interrupt_A=None, trip_A=None,
            continuous_fraction=None, part_number: str = "") -> Device:
    """The device a Fuse(...) stands for: a catalog family and rating, a Bourns MF-R part, or a part described by the model
    (every figure it gives is used; a catalog part's figures come from its datasheet and cannot be overridden)."""
    from .model import HarnessError
    key = _norm(type)
    rating_A, voltage_rating_V, interrupt_A, trip_A = (_num("rating_A", rating_A), _num("voltage_rating_V", voltage_rating_V),
                                                       _num("interrupt_A", interrupt_A), _num("trip_A", trip_A))
    continuous_fraction = _num("continuous_fraction", continuous_fraction)
    if continuous_fraction is not None and continuous_fraction > 1:
        raise HarnessError(f"continuous_fraction is a fraction of the rating (0 to 1), got {continuous_fraction!r}")
    given = {k: v for k, v in (("voltage_rating_V", voltage_rating_V), ("interrupt_A", interrupt_A), ("trip_A", trip_A),
                               ("continuous_fraction", continuous_fraction)) if v is not None}
    if key in MFR_PARTS:
        if given or (rating_A is not None and abs(rating_A - MFR_PARTS[key][2]) > 1e-9):
            raise HarnessError(f"{key} is a catalog PTC: its figures come from the Bourns datasheet (hold {MFR_PARTS[key][2]:g} A); "
                               f"give only Fuse(name, {key!r})")
        vmax, imax, ihold, itrip = MFR_PARTS[key]
        ih = MFR_IHOLD_BY_C[key]
        return Device(key, "ptc", "MF-R", ihold, itrip, float(vmax), float(imax), 1.0,
                      {c: v / ih[MFR_DERATE_C.index(23)] for c, v in zip(MFR_DERATE_C, ih)}, 23.0, part_number or key,
                      "solder", "", True,
                      {"rating": CITE_MFR, "voltage": CITE_MFR_VMAX, "interrupt": CITE_MFR, "continuous": CITE_MFR, "rerate": CITE_MFR_DERATE})
    if key in FUSES:
        fam = FUSES[key]
        if given:
            raise HarnessError(f"{key} is a catalog family: its figures come from {fam.text}'s datasheet; "
                               f"give only Fuse(name, {key!r}, rating_A)")
        if rating_A is None or not any(abs(rating_A - r) < 1e-9 for r in fam.ratings):
            raise HarnessError(f"{key} fuses are made in {', '.join(f'{r:g}' for r in fam.ratings)} A; "
                               f"give one of them: Fuse(name, {key!r}, <A>)")
        intr = fam.interrupt_A.get(rating_A) if isinstance(fam.interrupt_A, dict) else fam.interrupt_A
        rr = fam.rerate
        if rr and isinstance(next(iter(rr.values())), dict):          # a table per ampere rating
            rr = rr[rating_A]
        pn = part_number or (fam.pn_by_rating or {}).get(rating_A) or (fam.part_number.format(r=rating_A) if fam.part_number else "")
        return Device(key, fam.kind, key, rating_A, None, fam.voltage_V, intr, fam.continuous_fraction, rr,
                      fam.rerate_ref_C, pn, fam.termination, fam.holder, True, dict(fam.sources))
    k = (kind or GENERIC.get(key) or "fuse").lower()
    if k not in ("fuse", "ptc", "breaker"):
        raise HarnessError(f"kind must be fuse, ptc or breaker, not {kind!r}")
    if rating_A is None:
        raise HarnessError(f"a {k} needs its rating: Fuse(name, {type!r}, rating_A, ...) "
                           f"(catalog types: {', '.join(FUSES)}, Bourns MF-R005 ... MF-R1100)")
    given_src = "as given in the model file (not from eitri's catalog)"
    return Device(key, k, "", rating_A, trip_A, voltage_rating_V, interrupt_A, continuous_fraction, None, 25.0, part_number,
                  "solder" if k == "ptc" else "bare", "", False,
                  {n: given_src for n in ("voltage", "interrupt", "continuous") if {"voltage": voltage_rating_V,
                   "interrupt": interrupt_A, "continuous": continuous_fraction}[n] is not None})


def suggest(family: str, max_A: float) -> str | None:
    """A fix hint: the largest part of the family whose rating (a PTC: trip current) is at most max_A."""
    if family == "MF-R":
        ok = [(v[3], k) for k, v in MFR_PARTS.items() if v[3] <= max_A + 1e-9 and "-0-" not in k]
        return f'Fuse(name, "{max(ok)[1]}")' if ok else None
    fam = FUSES.get(family)
    if fam is None:
        return None
    ok = [r for r in fam.ratings if r <= max_A + 1e-9]
    return f'Fuse(name, "{family}", {max(ok):g})' if ok else None


def fuse_table() -> str:
    """The catalog as a markdown table (the agent's harness reference)."""
    rows = ["| type | what | ratings | DC voltage | interrupting | steady load allowed |", "|---|---|---|---|---|---|"]
    for f in FUSES.values():
        intr = (f"{min(f.interrupt_A.values()):g}-{max(f.interrupt_A.values()):g} A" if isinstance(f.interrupt_A, dict)
                else f"{f.interrupt_A:g} A" if f.interrupt_A else "-")
        rows.append(f"| {f.key} | {f.text} | {', '.join(f'{r:g}' for r in f.ratings)} A | {f.voltage_V:g} V | {intr} | "
                    f"{f.continuous_fraction:g} x rating at {f.rerate_ref_C:g} C, rerated with ambient |")
    rows.append("| MF-R005 ... MF-R1100 | Bourns MF-R radial PTC (type = part number) | hold 0.05-11 A, trip 2 x hold | "
                "60 / 30 / 16 V | Imax 40 A (MF-R1100: 100 A) | hold current at the ambient (Bourns derating table) |")
    return "\n".join(rows)


DEFAULT_AMBIENT_C = 40.0               # ASSUMPTION when Harness(ambient_C=...) is not given: a warm machine enclosure, not a lab bench


def _table(temps, row, rating) -> dict:
    """A maker's 'max. allowed current load at ambient' row as fractions of the nominal rating (a 7.5 A ATOF: 7 A at 20 C)."""
    return {float(t): float(v) / float(rating) for t, v in zip(temps, row)}


_register(
    Family("ATO", "fuse", "Littelfuse ATOF blade fuse (287 series, ISO 8820-3 / SAE J1284)", tuple(float(r) for r in ATOF_LOAD), 32.0, 1000.0, 0.75,
           {float(r): _table(ATOF_TEMP_C, row, r) for r, row in ATOF_LOAD.items()}, 25.0,
           pn_by_rating={float(r): f"0287{int(r):03d}.PXCN" for r in ATOF_LOAD if float(r).is_integer()},
           holder="inline ATO / ATC blade fuse holder, rated for the fuse and the wire size",
           sources={"rating": CITE_ATOF, "voltage": CITE_ATOF, "interrupt": CITE_ATOF, "continuous": CITE_LF_75, "rerate": CITE_ATOF_TEMP},
           aliases=("ATOF", "ATC", "ATO-287", "287", "BLADE", "REGULAR-BLADE")),
    Family("MINI", "fuse", "Littelfuse MINI blade fuse (297 series, ISO 8820-3 / SAE J2077)", tuple(float(r) for r in MINI_LOAD), 32.0, 1000.0, 0.75,
           {float(r): _table(MINI_TEMP_C, row, r) for r, row in MINI_LOAD.items()}, 25.0,
           pn_by_rating={float(r): f"0297{int(r):03d}.WXNV" for r in MINI_LOAD if float(r).is_integer()},
           holder="inline MINI (ATM) blade fuse holder, rated for the fuse and the wire size",
           sources={"rating": CITE_MINI, "voltage": CITE_MINI, "interrupt": CITE_MINI, "continuous": CITE_LF_75, "rerate": CITE_MINI_TEMP},
           aliases=("ATM", "MINI-BLADE", "297")),
    Family("5X20-F", "fuse", "Littelfuse 487 series 5x20 mm fast-acting cartridge fuse (UL 248-14, EN 60127-7)",
           (8.0, 10.0, 12.5, 15.0, 16.0, 20.0), 420.0, 300.0, 0.75, RERATE_5X20, 25.0,
           holder="5x20 mm inline fuse holder, rated for the fuse and the wire size", termination="bare",
           sources={"rating": CITE_487, "voltage": CITE_487, "interrupt": CITE_487, "continuous": CITE_487, "rerate": CITE_5X20_TEMP},
           aliases=("487", "5X20-FAST", "5X20F")),
    Family("5X20-T", "fuse", "Littelfuse 477 series 5x20 mm time-lag cartridge fuse (IEC 60127-2 sheet 5)",
           (0.5, 0.8, 1.0, 2.0, 3.15, 4.0, 5.0, 6.3, 8.0, 10.0, 12.0, 16.0), 400.0,
           {**{r: 200.0 for r in (0.5, 0.8, 1.0, 2.0, 3.15, 4.0, 5.0, 6.3, 8.0, 10.0, 12.0)}, 16.0: 300.0}, 0.75, RERATE_5X20, 25.0,
           holder="5x20 mm inline fuse holder, rated for the fuse and the wire size",
           sources={"rating": CITE_477, "voltage": CITE_477, "interrupt": CITE_477, "continuous": CITE_LF_75, "rerate": CITE_5X20_TEMP},
           aliases=("477", "5X20-SLOW", "5X20T", "5X20-TIME-LAG")),
)
