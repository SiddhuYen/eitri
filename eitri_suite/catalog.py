"""The parts catalog: purchased hardware a design is built from (servos, controller boards, power supplies,
fasteners, bearings, filament), with part number, source link, unit price and key specs.

It generalises the harness connector catalog (eitri/harness/catalog.py): the same pattern, frozen entries looked up
by a forgiving key. Connectors of that catalog are reachable through `lookup()` too, so a BOM can name either.

HONESTY RULES. Every spec carries `verified`. A spec is verified only when someone compared it with the maker's
datasheet; nothing in this file has been: the values below are from the maker's published figures as remembered when
the entry was written, so they are all `verified=False`, and the BOM and the checks that use a spec say so. Prices are
rough hobby-retail estimates (USD, not quotes) and always flagged. Source links point at the maker's or a main
retailer's site (a product page where one is known, else the site's root) and were not fetched. To verify a spec, check it and set `verified=True` (with the datasheet
in `note`); nothing else needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Spec:
    """One specification. value: a number, a string, a tuple, or a curve {voltage: value} (a servo's torque at
    its supply voltages)."""
    value: Any
    unit: str = ""
    verified: bool = False
    note: str = ""
    # where a verified figure was read: the document, its revision / date as printed, the page, the URL
    doc: str = ""
    rev: str = ""
    page: str = ""
    url: str = ""

    @property
    def is_curve(self) -> bool:
        return isinstance(self.value, dict)

    @property
    def cite(self) -> str:
        """The citation a check prints: the document, revision, page and URL; or the ASSUMPTION label for an unverified figure."""
        if not self.verified:
            return "ASSUMPTION: unverified catalog figure (not compared with a datasheet)"
        return ", ".join(x for x in (self.doc, self.rev, f"p.{self.page}" if self.page else "", self.url) if x)


@dataclass(frozen=True)
class CatalogPart:
    id: str
    category: str                       # servo | controller | power | fastener | bearing | material | connector | other
    name: str
    part_number: str
    manufacturer: str
    source: str                         # product page (not fetched)
    price_usd: float | None             # per `unit`
    unit: str = "pc"
    specs: dict[str, Spec] = field(default_factory=dict)
    price_note: str = "estimate, typical hobby retail; not a quote"
    aliases: tuple[str, ...] = ()

    def spec(self, key: str) -> Spec | None:
        return self.specs.get(key)

    def value(self, key: str, default: Any = None) -> Any:
        s = self.specs.get(key)
        return default if s is None else s.value

    @property
    def mass_g(self) -> float | None:
        s = self.specs.get("mass_g")
        return None if s is None else float(s.value)

    @property
    def unverified(self) -> list[str]:
        """Names of the specs not compared with a datasheet, and 'price' (a price is never a datasheet figure)."""
        out = [k for k, s in self.specs.items() if not s.verified]
        if self.price_usd is not None:
            out.append("price")
        return out

    def at_voltage(self, key: str, volts: float) -> tuple[float, bool] | None:
        """A curve spec at a supply voltage, linearly interpolated between the published points:
        (value, verified), or None when the voltage is outside the published range (never extrapolated)."""
        s = self.specs.get(key)
        if s is None or not s.is_curve:
            return None
        pts = sorted((float(v), float(x)) for v, x in s.value.items())
        if volts < pts[0][0] - 1e-9 or volts > pts[-1][0] + 1e-9:
            return None
        for (v0, x0), (v1, x1) in zip(pts, pts[1:]):
            if v0 - 1e-9 <= volts <= v1 + 1e-9:
                t = 0.0 if v1 == v0 else (volts - v0) / (v1 - v0)
                return x0 + t * (x1 - x0), s.verified
        return pts[0][1], s.verified

    def to_dict(self) -> dict:
        return {"id": self.id, "category": self.category, "name": self.name, "part_number": self.part_number,
                "manufacturer": self.manufacturer, "source": self.source, "unit_price_usd": self.price_usd, "unit": self.unit,
                "price_note": self.price_note, "unverified": self.unverified,
                "specs": {k: {"value": ({str(v): x for v, x in s.value.items()} if s.is_curve else (list(s.value) if isinstance(s.value, tuple) else s.value)),
                              "unit": s.unit, "verified": s.verified, **({"note": s.note} if s.note else {}),
                              **({"source": s.cite} if s.verified else {})} for k, s in self.specs.items()}}


def _s(value, unit="", note="") -> Spec:
    return Spec(value, unit, False, note)


def _v(value, unit, note, doc, rev, page, url) -> Spec:
    """A figure read from the maker's document (fetched 2026-11-12 unless the revision says otherwise)."""
    return Spec(value, unit, True, note, doc, rev, page, url)


_TP_MG = ("TowerPro MG996R product page", "no revision printed (fetched 2026-11-12)", "", "https://towerpro.com.tw/product/mg996r/")
_AF_815 = ("Adafruit 16-Channel 12-bit PWM/Servo Driver - I2C interface [PCA9685], product 815", "fetched 2026-11-12", "",
           "https://www.adafruit.com/product/815")
_AF_1466 = ("Adafruit 5V 4A (4000mA) switching power supply - UL Listed, product 1466", "revision history as of July 24, 2025; fetched 2026-11-12",
            "", "https://www.adafruit.com/product/1466")


def _mw(series: str) -> tuple[str, str, str, str]:
    return (f"MEAN WELL {series} series datasheet (File Name: {series}-SPEC)", "2025-04-07", "2",
            f"https://www.meanwell.com/Upload/PDF/{series}/{series}-SPEC.PDF")


_PARTS = [
    # ---- servo: the actuator of the 3-DOF arm.
    # The maker's published figures for the MG996R (TowerPro). Mass and stall current are read from the maker's page (verified, cited);
    # torque, speed and dimensions are not compared with a datasheet here (unverified).
    CatalogPart(
        "MG996R", "servo", "MG996R metal-gear hobby servo, 180 deg", "MG996R", "TowerPro",
        "https://www.towerpro.com.tw/product/mg996r/", 5.5, "pc",
        {"stall_torque_kgcm": _s({4.8: 9.4, 6.0: 11.0}, "kgf.cm", "datasheet figures at 4.8 V and 6 V; interpolated between, never extrapolated"),
         "continuous_fraction": _s(0.5, "", "ESTIMATE, not a datasheet figure: hobby servos are usually held at 1/3 to 1/2 of stall torque for continuous duty"),
         "voltage_range_v": _s((4.8, 7.2), "V"),
         "stall_current_a": _v({6.0: 2.5}, "A", "TowerPro prints 'Stall current draw 1400mA'; the Electronicos Caldas MG996R datasheet (2014-08-19) "
                               "'Stall Current 2.5 A (6V)': the higher draw is used (the core's electronics source towerpro-mg996r)",
                               "TowerPro MG996R product page and Electronicos Caldas MG996R datasheet", "2014-08-19 / fetched 2026-11-05", "",
                               "https://towerpro.com.tw/product/mg996r/"),
         "speed_s_per_60deg": _s({4.8: 0.19, 6.0: 0.15}, "s/60deg"),
         "mass_g": _v(80.0, "g", "TowerPro prints two weights: 'Weight: 55g' (spec list) and 'Weight 0.080 kg' (additional information, "
                                   "likely packed); the higher is used for a mass (conservative)", *_TP_MG),
         "mass_g_low": _v(55.0, "g", "the lower of TowerPro's two weights: used where a lower mass is the conservative figure (a body on a "
                                     "free-standing base)", *_TP_MG), "dims_mm": _s((40.7, 19.7, 42.9), "mm", "body without the mounting ears: length x width x height"),
         "ear_span_mm": _s(53.0, "mm", "length over the mounting ears (approximate)"), "horn_spline": _s("25T", "", "output spline, 25 teeth"),
         "control": _s("50 Hz PWM, about 500-2500 us pulse", "", "typical hobby servo signal; the endpoints vary by unit"),
         "rotation_deg": _s(180.0, "deg", "about 180 degrees of travel; individual units differ")},
        aliases=("MG996", "MG-996R", "TOWERPRO MG996R")),
    # ---- controller boards
    CatalogPart(
        "PCA9685", "controller", "PCA9685 16-channel 12-bit PWM / servo driver breakout (I2C)", "PCA9685", "NXP (chip); Adafruit 815 and clones (board)",
        "https://www.adafruit.com/product/815", 6.0, "pc",
        {"channels": _s(16), "interface": _s("I2C", "", "default address 0x40, 6 address-select jumpers"),
         "logic_voltage_v": _s((2.3, 5.5), "V", "chip supply range (breakouts usually 3.3-5 V logic)"), "servo_rail_max_v": _s(6.0, "V", "V+ rail, board dependent"),
         "dims_mm": _s((62.5, 25.4, 3.0), "mm", "Adafruit board 2.5 x 1 in; clones differ"), "mass_g": _v(9.0, "g", "Adafruit: 'Weight (with 3x4 headers & terminal block): 9grams' (the higher, conservative for a mass)", *_AF_815),
         "mass_g_low": _v(5.5, "g", "Adafruit: 'Weight (no headers or terminal block): 5.5grams'", *_AF_815), "header": _s("3-pin servo headers (GND, V+, signal), 2.54 mm", "")},
        price_note="generic clone price; the Adafruit board is about 3x", aliases=("PCA9685-BREAKOUT", "ADAFRUIT-815")),
    CatalogPart(
        "ESP32-DEVKIT", "controller", "ESP32 DevKit development board (ESP32-WROOM-32, 30/38 pin)", "ESP32-DevKitC", "Espressif (module); many board makers",
        "https://docs.espressif.com/projects/esp-idf/en/latest/esp32/hw-reference/esp32/get-started-devkitc.html", 9.0, "pc",
        {"logic_voltage_v": _s(3.3, "V", "GPIO are 3.3 V and not 5 V tolerant"), "supply_voltage_v": _s((4.5, 5.5), "V", "USB or the 5V pin"),
         "gpio_max_ma": _s(12.0, "mA", "per pin, about; the datasheet gives drive-strength settings"),
         "i2c_default_pins": _s("SDA GPIO21, SCL GPIO22", "", "Arduino-ESP32 defaults"), "dims_mm": _s((54.4, 28.3, 13.0), "mm", "varies by maker"),
         "mass_g": _s(10.0, "g", "Espressif's ESP32-DevKitC V4 user guide publishes no weight (read 2026-11-12): unverified")},
        aliases=("ESP32", "ESP32-DEVKITC", "ESP32 DEVKIT", "ESP32-DEVKIT-V1")),
    # ---- power
    CatalogPart(
        "PSU-5V-4A", "power", "Adafruit 1466: 5 V 4 A switching wall supply, UL listed, 2.1 mm barrel plug", "1466", "Adafruit",
        "https://www.adafruit.com/product/1466", 15.0, "pc",
        {"output_voltage_v": _v(5.0, "V", "'Output: ~5V DC up to 4A'", *_AF_1466),
         "output_current_a": _v(4.0, "A", "'Output: ~5V DC up to 4A'; the label reads '.4A', Adafruit: 'It is really a 4A supply'", *_AF_1466),
         "connector": _s("2.1 mm barrel, centre positive", ""), "mass_g": _s(150.0, "g", "no weight printed by Adafruit: unverified")},
        aliases=("5V4A", "PSU 5V 4A", "5V-4A-PSU", "ADAFRUIT-1466")),
    CatalogPart(
        "PSU-5V-10A", "power", "MEAN WELL LRS-50-5: 5 V 10 A enclosed supply (50 W), screw terminals", "LRS-50-5", "MEAN WELL",
        "https://www.meanwell.com/Upload/PDF/LRS-50/LRS-50-SPEC.PDF", 18.0, "pc",
        {"output_voltage_v": _v(5.0, "V", "DC VOLTAGE 5V (ADJ. RANGE 4.5 ~ 5.5V)", *_mw("LRS-50")),
         "output_current_a": _v(10.0, "A", "RATED CURRENT 10A, CURRENT RANGE 0 ~ 10A, RATED POWER 50W", *_mw("LRS-50")),
         "connector": _s("screw terminals (+V, -V)", ""), "mass_g": _v(210.0, "g", "PACKING 0.21Kg per unit (packed: at least the bare unit, the conservative figure for a mass)", *_mw("LRS-50"))},
        aliases=("5V10A", "PSU 5V 10A", "5V-10A-PSU", "LRS-50-5", "MEANWELL LRS-50-5", "MEAN WELL LRS-50-5")),
    CatalogPart(
        "PSU-5V-14A", "power", "MEAN WELL LRS-75-5: 5 V 14 A enclosed supply (70 W), screw terminals", "LRS-75-5", "MEAN WELL",
        "https://www.meanwell.com/Upload/PDF/LRS-75/LRS-75-SPEC.PDF", 22.0, "pc",
        {"output_voltage_v": _v(5.0, "V", "DC VOLTAGE 5V (ADJ. RANGE 4.5 ~ 5.5V)", *_mw("LRS-75")),
         "output_current_a": _v(14.0, "A", "RATED CURRENT 14A, CURRENT RANGE 0 ~ 14A, RATED POWER 70W", *_mw("LRS-75")),
         "connector": _s("screw terminals (+V, -V)", ""), "mass_g": _v(230.0, "g", "PACKING 0.23Kg per unit (packed: at least the bare unit, the conservative figure for a mass)", *_mw("LRS-75"))},
        aliases=("5V14A", "PSU 5V 14A", "LRS-75-5", "MEANWELL LRS-75-5", "MEAN WELL LRS-75-5")),
    CatalogPart(
        "PSU-5V-18A", "power", "MEAN WELL LRS-100-5: 5 V 18 A enclosed supply (90 W), screw terminals", "LRS-100-5", "MEAN WELL",
        "https://www.meanwell.com/Upload/PDF/LRS-100/LRS-100-SPEC.PDF", 26.0, "pc",
        {"output_voltage_v": _v(5.0, "V", "DC VOLTAGE 5V (ADJ. RANGE 4.5 ~ 5.5V)", *_mw("LRS-100")),
         "output_current_a": _v(18.0, "A", "RATED CURRENT 18A, CURRENT RANGE 0 ~ 18A, RATED POWER 90W", *_mw("LRS-100")),
         "connector": _s("screw terminals (+V, -V)", ""), "mass_g": _v(330.0, "g", "PACKING 0.33Kg per unit (packed: at least the bare unit, the conservative figure for a mass)", *_mw("LRS-100"))},
        aliases=("5V18A", "PSU 5V 18A", "LRS-100-5", "MEANWELL LRS-100-5", "MEAN WELL LRS-100-5")),
    # ---- fasteners (ISO 4762 socket head cap screws, ISO 4032 nuts, ISO 7089 washers); dimensions from the standards, none re-checked here
    CatalogPart("M3x8-SHCS", "fastener", "M3 x 8 socket head cap screw, ISO 4762, A2 stainless", "ISO 4762 M3x8", "generic",
                "https://www.mcmaster.com/", 0.06, "pc",
                {"thread": _s("M3", ""), "length_mm": _s(8.0, "mm"), "head_diameter_mm": _s(5.5, "mm"), "head_height_mm": _s(3.0, "mm"), "hex_key_mm": _s(2.5, "mm"),
                 "mass_g": _s(0.9, "g")}, aliases=("M3X8", "M3-8", "M3X8-SCREW")),
    CatalogPart("M3x12-SHCS", "fastener", "M3 x 12 socket head cap screw, ISO 4762, A2 stainless", "ISO 4762 M3x12", "generic",
                "https://www.mcmaster.com/", 0.07, "pc",
                {"thread": _s("M3", ""), "length_mm": _s(12.0, "mm"), "head_diameter_mm": _s(5.5, "mm"), "head_height_mm": _s(3.0, "mm"), "hex_key_mm": _s(2.5, "mm"),
                 "mass_g": _s(1.2, "g")}, aliases=("M3X12", "M3-12", "M3X12-SCREW")),
    CatalogPart("M3x16-SHCS", "fastener", "M3 x 16 socket head cap screw, ISO 4762, A2 stainless", "ISO 4762 M3x16", "generic",
                "https://www.mcmaster.com/", 0.08, "pc",
                {"thread": _s("M3", ""), "length_mm": _s(16.0, "mm"), "head_diameter_mm": _s(5.5, "mm"), "head_height_mm": _s(3.0, "mm"), "hex_key_mm": _s(2.5, "mm"),
                 "mass_g": _s(1.5, "g")}, aliases=("M3X16", "M3-16", "M3X16-SCREW")),
    CatalogPart("M3-NUT", "fastener", "M3 hex nut, ISO 4032, A2 stainless", "ISO 4032 M3", "generic",
                "https://www.mcmaster.com/", 0.04, "pc",
                {"thread": _s("M3", ""), "across_flats_mm": _s(5.5, "mm"), "thickness_mm": _s(2.4, "mm"), "mass_g": _s(0.3, "g")}, aliases=("M3 NUT", "M3-HEX-NUT")),
    CatalogPart("M3-WASHER", "fastener", "M3 flat washer, ISO 7089, A2 stainless", "ISO 7089 M3", "generic",
                "https://www.mcmaster.com/", 0.03, "pc",
                {"thread": _s("M3", ""), "outer_diameter_mm": _s(7.0, "mm"), "thickness_mm": _s(0.5, "mm"), "mass_g": _s(0.1, "g")}, aliases=("M3 WASHER",)),
    # ---- bearings
    CatalogPart("625ZZ", "bearing", "625ZZ deep groove ball bearing, 5 x 16 x 5 mm, double shielded", "625ZZ", "generic (NSK, SKF, ...)",
                "https://www.skf.com/", 0.6, "pc",
                {"bore_mm": _s(5.0, "mm"), "outer_diameter_mm": _s(16.0, "mm"), "width_mm": _s(5.0, "mm"), "mass_g": _s(5.0, "g"),
                 "dynamic_load_n": _s(1730.0, "N", "approximate; varies by maker")}, aliases=("625", "625-2RS", "625Z", "625 ZZ")),
    # ---- material
    CatalogPart("PLA-1KG", "material", "PLA filament, 1.75 mm, 1 kg spool", "generic", "generic",
                "https://www.prusa3d.com/", 22.0, "kg",
                {"density_g_cm3": _s(1.24, "g/cm3", "same value as eitri.measure.DENSITY['pla']"), "diameter_mm": _s(1.75, "mm")}, aliases=("PLA", "PLA FILAMENT")),
]

PARTS: dict[str, CatalogPart] = {p.id: p for p in _PARTS}
_ALIASES: dict[str, str] = {}
for _p in _PARTS:
    _ALIASES[_p.id.upper().replace("_", "-")] = _p.id
    for _a in _p.aliases:
        _ALIASES[_a.upper().replace("_", "-")] = _p.id


def normalize_id(text: str) -> str:
    t = " ".join(str(text).strip().upper().replace("_", "-").split())
    if t in _ALIASES:
        return _ALIASES[t]
    squashed = t.replace(" ", "-")
    if squashed in _ALIASES:
        return _ALIASES[squashed]
    flat = t.replace("-", "").replace(" ", "")
    for k, v in _ALIASES.items():
        if k.replace("-", "").replace(" ", "") == flat:
            return v
    return str(text).strip()


def lookup(part_id: str) -> CatalogPart | None:
    """A catalog part by id or alias; a harness connector type ('JST-XH', 'XT60') comes back as a connector part."""
    key = normalize_id(part_id)
    if key in PARTS:
        return PARTS[key]
    from eitri.harness.catalog import spec_for
    spec = spec_for(part_id)
    if spec is not None:
        specs = {"termination": _s(spec.termination)}
        if spec.pitch_mm is not None:
            specs["pitch_mm"] = _s(spec.pitch_mm, "mm")
        if spec.rating_a is not None:
            specs["rating_a_per_pin"] = _s(spec.rating_a, "A")
        return CatalogPart(spec.key, "connector", spec.family, spec.terminal_pn or spec.housing_pn or spec.key, "various",
                           "", None, "pc", specs, price_note="no price in the catalog")
    return None


def require(part_id: str) -> CatalogPart:
    p = lookup(part_id)
    if p is None:
        raise KeyError(f"unknown catalog part {part_id!r}; known: {', '.join(PARTS)}")
    return p


def catalog_table() -> str:
    rows = ["| id | category | part number | unit price (est.) | key specs | unverified |", "|---|---|---|---|---|---|"]
    for p in PARTS.values():
        key = []
        for k, s in p.specs.items():
            if k in ("stall_torque_kgcm",):
                key.append("stall " + ", ".join(f"{v:g} kgf.cm @ {x:g} V" for x, v in sorted(s.value.items())))
            elif k in ("mass_g", "dims_mm", "output_current_a", "channels", "bore_mm", "length_mm") and not s.is_curve:
                key.append(f"{k.replace('_', ' ')} {s.value if not isinstance(s.value, tuple) else 'x'.join(f'{x:g}' for x in s.value)}")
        price = "" if p.price_usd is None else f"${p.price_usd:g}/{p.unit}"
        rows.append(f"| {p.id} | {p.category} | {p.part_number} | {price} | {'; '.join(key[:4])} | {len(p.unverified)} of {len(p.specs) + (p.price_usd is not None)} |")
    return "\n".join(rows)
