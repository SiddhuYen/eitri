"""Catalogue pneumatic parts: what a part number gives a model (bore, rod, stroke limits, pressure range, port, ISO 6358 flow per
passage, coil, sensor electrics), every figure with its source.

    c.cylinder("C1", part="SMC CDQ2B32-20DZ", load_N=300, ...)       # bore 32, rod 16, stroke 20, 0.5-10 bar, Rc1/8 ...
    c.valve("V1", part="SMC SY3120-5LZ-C6")                           # 5/2 single solenoid, C 0.76 b 0.30 P->A/B, 24 VDC 0.4 W
    c.tube("T3", v["A"], cyl["CAP"], part="SMC TU0604", length_m=1)  # 6 x 4 mm polyurethane, 8 bar at 20 C
    c.sensor("B1", "C1", "extended", part="SMC D-M9B")               # a cylinder position switch (an I/O-list input)

A part number is matched against the series patterns below (stroke in the number is taken from it and checked against the
series' stroke range). A figure the model also gives explicitly must agree with the part's (a mismatch is a modelling error).

Where sources disagree the conservative figure is used: the lower for a limit (a pressure rating, a speed or energy limit), the
higher for a draw (a coil's power). Every figure names its source id in SOURCES; `sources_table()` prints them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FETCHED = "2026-11-12"


@dataclass(frozen=True)
class Source:
    id: str
    doc: str                   # the document, revision and date
    url: str
    where: str                 # page / table and what it says
    fetched: str = FETCHED


_S = [
    Source("festo-dsnu-20-50-p-a", "Festo data sheet DSNU-20-50-P-A, part number 19210 (dated 10/8/26)",
           "https://ftp.festo.com/public/PNEUMATIC/SOFTWARE_SERVICE/DataSheet/EN_GB/19210.pdf",
           'p.1: "Stroke 50 mm", "Piston diameter 20 mm", "Piston rod thread M8", "Cushioning Elastic cushioning rings/plates at both ends", '
           '"ISO 6432", "Position detection Via proximity switch", "Operating pressure 0.1 MPa...1 MPa", "Mode of operation Double-acting", '
           '"Impact energy in end positions 0.2 J", "Theoretical force at 0.6 MPa ... return stroke 158.3 N", "... advance stroke 188.5 N", '
           '"Moving mass for 0 mm stroke 44 g", "Additional moving mass per 10 mm stroke 4 g", "Pneumatic connection G1/8"'),
    Source("festo-dsnu-cat", "Festo Round cylinders DSNU catalogue, 2023/08",
           "https://ftp.festo.com/public/pneumatic/SOFTWARE_SERVICE/Documentation/2023/US/DSNU_ENUS.PDF",
           'p.9 General technical data: piston 20 mm "Stroke 1 ... 320" mm, "Pneumatic connection G1/8", "Piston rod thread M8"; rod '
           'diameter 8 mm (p.15 dimensions MM; the data sheet\'s 158.3/188.5 N force ratio gives the same 8.0 mm)'),
    Source("smc-cq2-z", "SMC Europe, Compact Cylinder Series CQ2-Z (CQ2-Z_EU.pdf, 1st printing OX)",
           "https://static.smc.eu/pdf/CQ2-Z_EU.pdf",
           'p.7 Specifications, pneumatic type: "Maximum operating pressure 1.0 MPa"; "Minimum operating pressure, Double acting type" 0.05 MPa '
           '(ø16 to ø200); "Piston speed 50 to 500 mm/s" (ø12 to ø100); "Allowable kinetic energy (J)" ø32: standard 0.15, with rubber bumper 0.29; '
           '"With rubber bumper type is standard form ø25 to ø200 bore"; p.1 How to Order: CDQ2 = with built-in magnet, B = through-hole, Z = '
           'auto switch mounting groove, standard strokes ø32: 5-50 (steps of 5), 75, 100'),
    Source("smc-cq2", "SMC Compact Cylinder Series CQ2 catalogue (SMC USA, CQ2.pdf, server date 2011-03-23)",
           "https://content2.smcetech.com/pdf/CQ2.pdf",
           'p.606 Theoretical Output (N), ø32: OUT 241 / 402 / 563 and IN 181 / 302 / 422 at 0.3 / 0.5 / 0.7 MPa; p.616 Dimensions '
           'ø32, with auto switch: D (rod) 16, P (port) Rc1/8'),
    Source("smc-sy", "SMC Europe catalogue CAT.EUS11-83, SY series 5 port solenoid valve (SY_EU.pdf, server date 2026-11-06)",
           "https://static.smc.eu/pdf/SY_EU.pdf",
           'p.2 Specifications: internal pilot operating pressure range "2 position single 0.15 to 0.7", "2 position double 0.1 to 0.7" MPa; max operating frequency SY3000 10 Hz, '
           'SY5000 5 Hz; Solenoid specifications: coil rated voltage DC 24, 12, 6, 5, 3 V; "Allowable voltage fluctuation 10% of rated voltage"; '
           'power consumption DC (per coil) "Standard 0.35 (With indicator light: 0.4 DIN terminal with indicator light: 0.45)", "With power saving circuit '
           '0.1 (With indicator light only)"; electrical entry L plug connector (L); p.3 Flow characteristics (ISO 6358), 2 position single: '
           'SY3[ ]20-[ ]-C6 (one row for single and double) 1->4/2 (P->A/B) C 0.76 b 0.30 Cv 0.19, 4/2->5/3 (A/B->EA/EB) C 0.65 b 0.39 Cv 0.17, ports 1, 5, 3 M5 x 0.8; '
           'SY5[ ]20-[ ]-01 P->A/B C 1.9 b 0.35 Cv 0.49, A/B->EA/EB C 2.4 b 0.39 Cv 0.61, all ports 1/8'),
    Source("smc-d-m9", "SMC Europe, Solid State Auto Switch D-M9 series (D-M9_EU.pdf, KV printing)",
           "https://static.smc.eu/pdf/D-M9_EU.pdf",
           'D-M9B: "In-line", "2-wire", applicable load "24 VDC relay, PLC", load voltage "24 VDC (10 to 28 VDC)", load current "2.5 to 40 mA", '
           'internal voltage drop "4 V or less", leakage current "0.8 mA or less", "Red LED illuminates when ON"; lead wires "D-M9B(V) 0.15 mm2 x '
           '2 cores", 0.5 m standard'),
    Source("festo-smt-8m-a", "Festo data sheet SMT-8M-A-PS-24V-E-0,3-M8D, part number 574334 (dated 10/8/26)",
           "https://ftp.festo.com/public/PNEUMATIC/SOFTWARE_SERVICE/DataSheet/EN_GB/574334.pdf",
           '"Design For T-slot"; "Switching output PNP"; "N/O contact"; "Max. output current 100 mA"; "Voltage drop 1.5 V"; "Rated operational '
           'voltage DC 24 V"; "Operational voltage range DC 5 V...30 V"; "Electrical connection 1, connector system M8x1, A-coded, to EN 61076-2-104"; '
           '"number of connections/cores 3"; "Cable length 0.3 m". (On a DSNU it needs the mounting kit SMBR-8-20.)'),
    Source("smc-tu", "SMC Europe, Polyurethane Tubing Series TU, TIUB (TU_TIUB_EU.pdf)", "https://static.smc.eu/pdf/TU_TIUB_EU.pdf",
           'Specifications: "Max. operating pressure 0.8MPa at 20 C" (lower when hotter: pressure characteristics curve); operating temperature '
           '-20 to +60 C; TU0604: O.D. 6, I.D. 4 mm'),
    Source("smc-kq2", "SMC Europe, One-touch Fittings Series KQ2 (KQ2_EU.pdf)", "https://static.smc.eu/pdf/KQ2_EU.pdf",
           'p.5 Male connector KQ2H: KQ2H06-01S (tube 6 mm, R1/8) effective area 13.1 mm2 nylon, 10.4 mm2 urethane; max operating pressure '
           '1.0 MPa (p.2 Specifications)'),
    Source("smc-as", "SMC Europe, Speed Controller Series AS (AS_EU.pdf)", "https://static.smc.eu/pdf/AS_EU.pdf",
           'AS2201F-01-06 (R1/8, tube 6 mm): controlled-flow effective area 3.5 mm2 needle fully open; max operating pressure 1 MPa. SMC gives no '
           'C: C = S / 5 = 0.7 L/(s.bar) (SMC: S [mm2] = 5 x C); b not published (0.30 is an ESTIMATE)', fetched="2026-11-07"),
    Source("smc-an", "SMC Silencer Series AN (AN.pdf)", "https://content2.smcetech.com/pdf/AN.pdf",
           'AN05-M5: sonic conductance C 1 dm3/(s.bar), max operating pressure 1.0 MPa; b not published (0.30 is an ESTIMATE)', fetched="2026-11-07"),
    Source("smc-best-tech", "SMC Best Pneumatics technical data, Air Cylinders Model Selection (6-2-1-m21-43-tech_en.pdf)",
           "https://ca01.smcworld.com/catalog/BEST-technical-data-en/pdf/6-2-1-m21-43-tech_en.pdf",
           'Data 2 (p.1574): worked example "Operating pressure 0.5 MPa -> Piping length 2 m -> Bore 6 mm -> Air consumption = 0.56 L (ANR)" per '
           'cycle (two 2 m lines filled from atmosphere: 2 x 56.5 cm3 x 0.5 / 0.1013 = 0.56 L); "a required air volume 350 L/min (ANR)" for '
           'ø50 at 0.5 MPa and 500 mm/s; kinetic energy "E = 1/2 m V^2", "V: Max. piston speed [m/s]", against the cylinder\'s allowable '
           'kinetic energy table'),
]
SOURCES: dict[str, Source] = {s.id: s for s in _S}


def cite(sid: str) -> str:
    s = SOURCES[sid]
    return f"{s.doc}, {s.url}"


@dataclass(frozen=True)
class Series:
    """A family of part numbers sharing figures; `pattern` captures `stroke` when the stroke is in the number."""
    kind: str                         # cylinder | valve | tube | sensor | flow_control | silencer
    pattern: str
    fields: dict
    sources: tuple[str, ...]
    strokes: tuple | None = None      # allowed strokes (a set of standard ones) or None
    stroke_range: tuple[float, float] | None = None
    figures: dict = field(default_factory=dict)        # catalogue figures the build compares with (forces at a pressure ...)


SERIES: list[Series] = [
    # ---------------------------------------------------------------- cylinders
    Series("cylinder", r"(?:FESTO\s+)?DSNU-20-(?P<stroke>\d+)-P-A",
           dict(bore_mm=20.0, rod_mm=8.0, acting="double", min_bar=1.0, max_bar=10.0, port="G1/8", magnet=True, cushion="elastic rings",
                allowable_ke_J=0.2, piston_mass_kg=0.044, piston_mass_per_mm_kg=0.0004, manufacturer="Festo",
                description="Festo DSNU round cylinder ISO 6432, Ø20, elastic cushioning, magnetic piston"),
           ("festo-dsnu-20-50-p-a", "festo-dsnu-cat"), stroke_range=(1, 320),
           figures={"force_extend_N": (188.5, 6.0), "force_retract_N": (158.3, 6.0)}),
    Series("cylinder", r"(?:SMC\s+)?C(?P<magnet>D?)Q2B32-(?P<stroke>\d+)DZ",
           dict(bore_mm=32.0, rod_mm=16.0, acting="double", min_bar=0.5, max_bar=10.0, port="Rc1/8", cushion="rubber bumper",
                allowable_ke_J=0.29, speed_min_mm_s=50.0, speed_max_mm_s=500.0, manufacturer="SMC",
                description="SMC CQ2 compact cylinder, Ø32, through-hole, rubber bumper"),
           ("smc-cq2-z", "smc-cq2"), strokes=(5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 75, 100),
           figures={"force_extend_N": (402.0, 5.0), "force_retract_N": (302.0, 5.0)}),
    # ---------------------------------------------------------------- valves
    Series("valve", r"(?:SMC\s+)?SY3120-5L(?P<light>Z?)-C6",
           dict(cls="5/2", actuation="solenoid", returns="spring", C=0.76, b=0.30, C_exhaust=0.65, b_exhaust=0.39, min_bar=1.5, max_bar=7.0,
                coil_V=24.0, coil_W=0.4, connector="L plug connector (lead wire 300 mm)", port="P, EA, EB M5 x 0.8; A, B one-touch 6 mm",
                max_hz=10.0, manufacturer="SMC", description="SMC SY3000 5/2 single-solenoid valve, body ported, 24 VDC"),
           ("smc-sy",)),
    Series("valve", r"(?:SMC\s+)?SY3220-5L(?P<light>Z?)-C6",
           dict(cls="5/2", actuation="solenoid", returns="solenoid", C=0.76, b=0.30, C_exhaust=0.65, b_exhaust=0.39, min_bar=1.0, max_bar=7.0,
                coil_V=24.0, coil_W=0.4, connector="L plug connector x 2 (lead wire 300 mm)", port="P, EA, EB M5 x 0.8; A, B one-touch 6 mm",
                max_hz=10.0, manufacturer="SMC", description="SMC SY3000 5/2 double-solenoid valve (holds its position unpowered), 24 VDC"),
           ("smc-sy",)),
    Series("valve", r"(?:SMC\s+)?SY5120-5L(?P<light>Z?)-01",
           dict(cls="5/2", actuation="solenoid", returns="spring", C=1.9, b=0.35, C_exhaust=2.4, b_exhaust=0.39, min_bar=1.5, max_bar=7.0,
                coil_V=24.0, coil_W=0.4, connector="L plug connector (lead wire 300 mm)", port="all ports Rc1/8", max_hz=5.0,
                manufacturer="SMC", description="SMC SY5000 5/2 single-solenoid valve, body ported, 24 VDC"),
           ("smc-sy",)),
    # ---------------------------------------------------------------- tube, flow controls, silencers
    Series("tube", r"(?:SMC\s+)?TU0604", dict(id_mm=4.0, od_mm=6.0, max_bar=8.0, material="polyurethane",
                                               description="SMC TU polyurethane tube 6 x 4 mm"), ("smc-tu",)),
    Series("flow_control", r"(?:SMC\s+)?AS2201F-01-06", dict(C=0.7, b=0.30, max_bar=10.0, port="R1/8, tube 6 mm",
                                                         description="SMC AS speed controller, elbow, R1/8 x 6 mm"), ("smc-as",)),
    Series("silencer", r"(?:SMC\s+)?AN05-M5", dict(C=1.0, b=0.30, max_bar=10.0, port="M5", description="SMC AN silencer, M5"), ("smc-an",)),
    # ---------------------------------------------------------------- position sensors
    Series("sensor", r"(?:SMC\s+)?D-M9B", dict(output="2-wire", V_min=10.0, V_max=28.0, load_mA_min=2.5, load_mA_max=40.0, drop_V=4.0,
                                                leakage_mA=0.8, connector="grommet, 2 cores 0.15 mm2 (brown +, blue -), 0.5 m lead",
                                                fits=("CQ2", "CDQ2"), description="SMC D-M9B solid-state auto switch, 2-wire"), ("smc-d-m9",)),
    Series("sensor", r"(?:FESTO\s+)?SMT-8M-A-PS-24V-E-0,3-M8D", dict(output="PNP", V_min=5.0, V_max=30.0, load_mA_max=100.0, drop_V=1.5,
                                                                    connector="M8x1 A-coded 3-pin plug on 0.3 m cable (1 +, 3 -, 4 signal)",
                                                                    fits=("DSNU",), mounting_kit="SMBR-8-20",
                                                                    description="Festo SMT-8M-A proximity sensor, PNP N/O, T-slot"),
           ("festo-smt-8m-a",)),
]


class CatalogError(ValueError):
    pass


def _norm(part: str) -> str:
    return re.sub(r"\s+", " ", str(part).strip().upper())


def lookup(kind: str, part: str) -> tuple[dict, Series]:
    """(fields for the model, the series) of a catalogue part number of this kind; CatalogError when unknown."""
    p = _norm(part)
    for s in SERIES:
        if s.kind != kind:
            continue
        m = re.fullmatch(s.pattern, p, flags=re.IGNORECASE)
        if not m:
            continue
        f = dict(s.fields)
        g = m.groupdict()
        if "stroke" in g and g["stroke"]:
            st = float(g["stroke"])
            if s.strokes is not None and int(st) not in s.strokes:
                raise CatalogError(f"{part}: {st:g} mm is not a standard stroke of this series ({', '.join(str(x) for x in s.strokes)})")
            if s.stroke_range is not None and not s.stroke_range[0] <= st <= s.stroke_range[1]:
                raise CatalogError(f"{part}: stroke {st:g} mm is outside the series' {s.stroke_range[0]:g}-{s.stroke_range[1]:g} mm")
            f["stroke_mm"] = st
            if "piston_mass_per_mm_kg" in f:
                f["piston_mass_kg"] = round(f["piston_mass_kg"] + f.pop("piston_mass_per_mm_kg") * st, 4)
        if "magnet" in g:
            f["magnet"] = bool(g["magnet"])
        if "light" in g and not g["light"]:
            f["coil_W"] = 0.35            # without indicator light / surge suppressor: SMC "Standard 0.35"
        f.pop("piston_mass_per_mm_kg", None)
        return f, s
    known = [s.pattern for s in SERIES if s.kind == kind]
    raise CatalogError(f"{part!r} is not a known {kind} part; known {kind} series: {', '.join(known) or 'none'}. Give the figures from its "
                       "datasheet instead (and say where they come from in part_number/notes).")


def source_text(series: Series) -> str:
    return "; ".join(cite(s) for s in series.sources)


def parts_table() -> str:
    """The catalogue as Markdown (for the agent's reference and the report)."""
    rows = ["| kind | part number pattern | figures | sources |", "|---|---|---|---|"]
    for s in SERIES:
        figs = ", ".join(f"{k} {v}" for k, v in s.fields.items() if k not in ("description", "manufacturer"))
        rows.append(f"| {s.kind} | `{s.pattern}` | {figs} | {', '.join(s.sources)} |")
    return "\n".join(rows)


def sources_table() -> str:
    rows = ["| id | document | url | what it says | fetched |", "|---|---|---|---|---|"]
    for s in _S:
        rows.append(f"| {s.id} | {s.doc} | {s.url} | {s.where.replace('|', '/')} | {s.fetched} |")
    return "\n".join(rows)
