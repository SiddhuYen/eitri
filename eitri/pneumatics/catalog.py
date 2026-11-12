"""Catalogue pneumatic parts: what a part number gives a model (bore, rod, stroke limits, pressure range, port, ISO 6358 flow per
passage, coil, sensor electrics), every figure with its source.

    c.cylinder("C1", part="SMC CDQ2B32-20DZ", load_N=300, ...)       # bore 32, rod 16, stroke 20, 0.5-10 bar, Rc1/8 ...
    c.valve("V1", part="SMC SY3120-5LZ-C6")                           # 5/2 single solenoid, C 0.76 b 0.30 P->A/B, 24 VDC 0.4 W
    c.tube("T3", v["4"], cyl["CAP"], part="SMC TU0604", length_m=1)  # 6 x 4 mm polyurethane, 8 bar at 20 C (SMC marks 4 "A")
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
           'p.9 General technical data (piston 16/20/25/32/40/50/63): pneumatic connection M5, G1/8, G1/8, G1/8, G1/4, G1/4, G3/8; stroke '
           '16: 1...200, 20: 1...320, 25-63: 1...500 mm; "Elastic cushioning rings/pads at both ends" (P); p.10 operating pressure DSNU-... '
           '16-63: 1 ... 10 bar; weights: moving mass with 0 mm stroke 23, 44, 71, 121, 230, 413, 459 g, per 10 mm 2, 4, 6, 9, 16, 25, 25 g; '
           'p.11 theoretical force at 6 bar advancing 121, 189, 295, 483, 753, 1178, 1870 N, retracting 104, 158, 247, 415, 633, 990, '
           '1682 N, impact energy in the end positions for P cushioning 0.15, 0.20, 0.30, 0.40, 0.70, 1.00, 1.30 J ("reduced by approx. 50% '
           'at 80 C"); rod diameter MM p.15 (8-25: 6, 8, 10) and p.18 (32-63: 12, 16, 20, 20)'),
    Source("smc-cq2-dims", "SMC Compact Cylinder Series CQ2 catalogue (SMC USA, CQ2.pdf, server date 2011-03-23), dimensions",
           "https://content2.smcetech.com/pdf/CQ2.pdf",
           'p.614 (dia 12-25, with auto switch): D (rod) 6, 8, 10, 12, port 2 x M5 x 0.8; p.616 (32-50): D 16, 16, 20, P Rc1/8, Rc1/8, '
           'Rc1/4; p.618 (63-100): D 20, 25, 30, P 1/4, 3/8, 3/8; p.606 Theoretical Output at 0.5 MPa OUT/IN: 12: 57/42, 16: 101/75, '
           '20: 157/118, 25: 245/189, 32: 402/302, 40: 628/528, 50: 982/825, 63: 1560/1400, 80: 2510/2270, 100: 3930/3570 N'),
    Source("festo-dsbc", "Festo Standards-based cylinders DSBC, ISO 15552 catalogue 2023/08 and DSBC-<bore>-50-PPVA-N3 data sheets",
           "https://ftp.festo.com/public/pneumatic/SOFTWARE_SERVICE/Documentation/2023/US/DSBC_ENUS.PDF",
           'catalogue p.11 piston 32-100, stroke 1 ... 2800 mm; p.13 moving mass with 0 mm stroke 110, 205, 365, 430, 810, 1000 g, per 10 mm 9, '
           '16, 25, 25, 39, 39 g; p.21 rod MM 12, 16, 20, 20, 25, 25; data sheets 1376424 (32), 1376658 (40), 1366950 (50), 1383580 (63), '
           '1383335 (80), 1384808 (100) at ftp.festo.com/.../DataSheet/EN_GB/<part>.pdf: operating pressure 0.6-12 bar (32, 40), 0.4-12 bar '
           '(50-100); connection G1/8, G1/4, G1/4, G3/8, G3/8, G1/2; theoretical force at 6 bar 483/415, 754/633, 1178/990, 1870/1682, '
           '3016/2721, 4712/4418 N; impact energy in the end positions 0.4, 0.7, 1.0, 1.3, 1.8, 2.5 J. (Figures from the data/components '
           'records of 2026-11-06/07, page references as given there; not re-opened in this thread.)', fetched="2026-11-07"),
    Source("smc-mgp-z", "SMC Europe, Compact Guide Cylinder Series MGP-Z (MGP-Z_EU.pdf)", "https://static.smc.eu/pdf/MGP-Z_EU.pdf",
           'printed p.3 How to Order: MGP{M slide bearing} <bore> - <stroke> Z, "Without auto switch (Built-in magnet)", port thread "-" '
           'M5 x 0.8 (dia 12, 16) / Rc; printed p.4 Specifications: max operating pressure 1.0 MPa, minimum 0.12 MPa (12) / 0.1 MPa '
           '(16-100), piston speed 50 to 500 mm/s (12-63) "Maximum speed with no load", cushion "Rubber bumper on both ends"; standard '
           'strokes 20, 25: 20, 30, 40, 50, 75, 100, 125, 150, 175, 200, 250, 300, 350, 400; 32-100: 25, 50, 75, 100, 125, 150, 175, 200, '
           '250, 300, 350, 400; Theoretical Output (rod size, OUT/IN at 0.5 MPa): 20 (rod 10) 157/118, 25 (rod 10) 245/206, 32 (rod 14) '
           '402/325 N. No allowable kinetic energy is printed (MGP is selected from load-speed graphs, pp.8-14: not modelled)'),
    Source("smc-aw", "SMC Europe, Filter Regulator Series AW10 to 40 (AW_EU.pdf)", "https://static.smc.eu/pdf/AW_EU.pdf",
           'Standard specifications, AW20 column: max operating pressure 1.0 MPa, set pressure range 0.05-0.85 MPa, filtration 5 um. '
           '(data/components record smc/aw20, fetched 2026-11-07)', fetched="2026-11-07"),
    Source("festo-ms4-lfr", "Festo data sheet MS4-LFR-1/4-D6-CRM-AS, part 529144",
           "https://ftp.festo.com/public/PNEUMATIC/SOFTWARE_SERVICE/DataSheet/EN_GB/529144.pdf",
           'Operating pressure 0.8-14 bar; pressure regulation range 0.3-7 bar; standard nominal flow rate 1500 L/min (DIN 1343); grade of '
           'filtration 5 um. (data/components record festo/ms4-lfr-1-4-d6-crm-as, fetched 2026-11-06)', fetched="2026-11-06"),
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
           'PDF p.14 (printed 5) Construction, JIS symbols: 2 position single rest (spring) box 1->2, 4->5, 3 blocked, solenoid box 1->4, '
           '2->3; ports "4 (A) 2 (B)", "5 (EA) 1 (P) 3 (EB)"; 3 position closed / exhaust (4->5, 2->3) / pressure (1->4, 1->2) centre; '
           'SY5[ ]20-[ ]-01 P->A/B C 1.9 b 0.35 Cv 0.49, A/B->EA/EB C 2.4 b 0.39 Cv 0.61, all ports 1/8; p.2 Response Time (ms, at 0.5 MPa, '
           'JIS B 8375-1981): SY3000 2 position single 12 or less without light/surge suppressor, 15 Type S, Z, 12 Type R, U; 2 position double '
           '10 / 13 / 10; SY5000 single 19 / 26 / 19, double 18 / 22 / 18'),
    Source("smc-d-m9", "SMC Europe, Solid State Auto Switch D-M9 series (D-M9_EU.pdf, KV printing)",
           "https://static.smc.eu/pdf/D-M9_EU.pdf",
           'D-M9B: "In-line", "2-wire", applicable load "24 VDC relay, PLC", load voltage "24 VDC (10 to 28 VDC)", load current "2.5 to 40 mA", '
           'internal voltage drop "4 V or less", leakage current "0.8 mA or less", "Red LED illuminates when ON"; lead wires "D-M9B(V) 0.15 mm2 x '
           '2 cores", 0.5 m standard'),
    Source("smc-sy-manifold", "SMC Europe catalogue CAT.EUS11-83 SY series (SY_EU.pdf), Type 20 body ported bar stock manifold",
           "https://static.smc.eu/pdf/SY_EU.pdf",
           'PDF p.46 (printed 37) How to Order Manifold: SS5Y{3,5,7}-20-{02..20 stations}{-: Rc, 00F: G, 00N: NPT, 00T: NPTF}-Q, '
           '"This also includes the number of blanking plate assemblies"; PDF p.48 (printed 39) Manifold Specifications: SS5Y3-20 / SS5Y5-20 '
           '"Common SUP, Common EXH", "2 to 20 stations", P, EA, EB port 1/8 (SS5Y3) / 1/4 (SS5Y5); "Note 1) For more than 10 stations ... '
           'supply pressure to P port on both sides and exhaust from EA/EB port on both sides"; Flow Characteristics (manifold base with 5 '
           'stations, individually operated 2 position): SS5Y3-20 C6 P->A/B C 0.72 b 0.29 Cv 0.18, A/B->EA/EB C 0.80 b 0.36 Cv 0.21; '
           'SS5Y5-20 C8 P->A/B C 1.9 b 0.28 Cv 0.48, A/B->EA/EB C 2.2 b 0.20 Cv 0.53; PDF p.75 Manifold Option, Type 20 blanking plate '
           'assembly SY3000-26-19A-Q, SY5000-26-1A-Q'),
    Source("festo-smt-8m-a", "Festo data sheet SMT-8M-A-PS-24V-E-0,3-M8D, part number 574334 (dated 10/8/26)",
           "https://ftp.festo.com/public/PNEUMATIC/SOFTWARE_SERVICE/DataSheet/EN_GB/574334.pdf",
           '"Design For T-slot"; "Switching output PNP"; "N/O contact"; "Switch-on time 1.3 ms"; "Switch-off time 7.3 ms"; "Max. output current 100 mA"; "Voltage drop 1.5 V"; "Rated operational '
           'voltage DC 24 V"; "Operational voltage range DC 5 V...30 V"; "Electrical connection 1, connector system M8x1, A-coded, to EN 61076-2-104"; '
           '"number of connections/cores 3"; "Cable length 0.3 m". (On a DSNU it needs the mounting kit SMBR-8-20.)'),
    Source("smc-tu", "SMC Europe, Polyurethane Tubing Series TU, TIUB (TU_TIUB_EU.pdf)", "https://static.smc.eu/pdf/TU_TIUB_EU.pdf",
           'Specifications: "Max. operating pressure 0.8MPa at 20 C" (lower when hotter: pressure characteristics curve); operating temperature '
           '-20 to +60 C; TU0425: O.D. 4, I.D. 2.5; TU0604: 6 / 4; TU0805: 8 / 5 mm'),
    Source("smc-kq2", "SMC Europe, One-touch Fittings Series KQ2 (KQ2_EU.pdf)", "https://static.smc.eu/pdf/KQ2_EU.pdf",
           'p.5 Male connector KQ2H: KQ2H06-01S (tube 6 mm, R1/8) effective area 13.1 mm2 nylon, 10.4 mm2 urethane; max operating pressure '
           '1.0 MPa (p.2 Specifications)'),
    Source("smc-as", "SMC Europe, Speed Controller Series AS (AS_EU.pdf)", "https://static.smc.eu/pdf/AS_EU.pdf",
           'p.1 (printed 2) Air Flow/Effective Area table (supply 0.5 MPa): AS1201F-M5-04 (M5, tube 4 mm) 1.5 mm2, AS2201F-01-06 (R1/8, '
           'tube 6 mm) 3.5 mm2, AS2201F-02-08 (R1/4, tube 8 mm) 7 mm2, needle fully open; max operating pressure 1 MPa. SMC gives no C: '
           'C = S / 5 (SMC: S [mm2] = 5 x C) = 0.3, 0.7, 1.4 L/(s.bar); b not published (0.30 is an ESTIMATE)', fetched="2026-11-07"),
    Source("smc-an", "SMC Silencer Series AN (AN.pdf)", "https://content2.smcetech.com/pdf/AN.pdf",
           'Specifications: max operating pressure 1.0 MPa; performance table, sonic conductance C [dm3/(s.bar)]: AN05-M5 1, AN10-01 2 (R1/8); b not '
           'published (0.30 is an ESTIMATE)', fetched="2026-11-07"),
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
    stations_range: tuple[int, int] | None = None
    figures: dict = field(default_factory=dict)        # catalogue figures the build compares with (forces at a pressure ...)


# SMC's printed letters for the ISO 5599 numbers (SY_EU.pdf PDF p.14, printed p.5, JIS symbols: "4 (A) 2 (B)", "5 (EA) 1 (P) 3 (EB)";
# PDF p.48 manifold "P, EA, EB port"): a model may write v["A"] on an SMC valve and gets SMC's A, port 4 (pressurised by coil 14).
SMC_VALVE_LETTERS = {"P": "1", "A": "4", "B": "2", "EA": "5", "EB": "3"}
SMC_MANIFOLD_LETTERS = {"P": "1", "EA": "5", "EB": "3"}


def _dsnu() -> list:
    """Festo DSNU round cylinders, P cushioning, magnetic piston (-A): catalogue 2023/08 pp.9-11, 15, 18 (festo-dsnu-cat); dia 20 also
    its own data sheet 19210 (the more precise 188.5 / 158.3 N)."""
    # bore: rod, port, max stroke, force ext / ret at 6 bar, impact J, moving mass g at 0 mm, g per 10 mm
    T = {16: (6, "M5", 200, 121, 104, 0.15, 23, 2), 20: (8, "G1/8", 320, 188.5, 158.3, 0.20, 44, 4), 25: (10, "G1/8", 500, 295, 247, 0.30, 71, 6),
         32: (12, "G1/8", 500, 483, 415, 0.40, 121, 9), 40: (16, "G1/4", 500, 753, 633, 0.70, 230, 16), 50: (20, "G1/4", 500, 1178, 990, 1.00, 413, 25),
         63: (20, "G3/8", 500, 1870, 1682, 1.30, 459, 25)}
    out = []
    for bore, (rod, port, smax, fe, fr, ke, mm0, mm10) in T.items():
        src = ("festo-dsnu-20-50-p-a", "festo-dsnu-cat") if bore == 20 else ("festo-dsnu-cat",)
        out.append(Series("cylinder", rf"(?:FESTO\s+)?DSNU-{bore}-(?P<stroke>\d+)-P-A",
                          dict(bore_mm=float(bore), rod_mm=float(rod), acting="double", min_bar=1.0, max_bar=10.0, port=port,
                               connections={"CAP": port, "ROD": port}, magnet=True, cushion="elastic rings", allowable_ke_J=ke,
                               piston_mass_kg=mm0 / 1000.0, piston_mass_per_mm_kg=mm10 / 10000.0, manufacturer="Festo",
                               description=f"Festo DSNU round cylinder{' ISO 6432' if bore <= 25 else ''}, Ø{bore}, elastic cushioning, magnetic piston"),
                          src, stroke_range=(1, smax), figures={"force_extend_N": (fe, 6.0), "force_retract_N": (fr, 6.0)}))
    return out


def _cq2() -> list:
    """SMC CQ2 compact cylinders (CDQ2B: magnet; CQ2B: none), double acting: CQ2-Z_EU p.7, CQ2.pdf pp.606, 614, 616, 618."""
    # bore: rod, port, standard strokes, out / in at 0.5 MPa, allowable KE J (rubber bumper standard from dia 25; none below), min MPa
    s30, s50, s100, s10_100 = tuple(range(5, 31, 5)), tuple(range(5, 51, 5)), tuple(range(5, 51, 5)) + (75, 100), tuple(range(10, 51, 5)) + (75, 100)
    T = {12: (6, "M5", s30, 57, 42, 0.022, 0.07), 16: (8, "M5", s30, 101, 75, 0.038, 0.05), 20: (10, "M5", s50, 157, 118, 0.055, 0.05),
         25: (12, "M5", s50, 245, 189, 0.18, 0.05), 32: (16, "Rc1/8", s100, 402, 302, 0.29, 0.05), 40: (16, "Rc1/8", s100, 628, 528, 0.52, 0.05),
         50: (20, "Rc1/4", s10_100, 982, 825, 0.91, 0.05), 63: (20, "Rc1/4", s10_100, 1560, 1400, 1.54, 0.05),
         80: (25, "Rc3/8", s10_100, 2510, 2270, 2.71, 0.05), 100: (30, "Rc3/8", s10_100, 3930, 3570, 4.54, 0.05)}
    out = []
    for bore, (rod, port, strokes, fo, fi, ke, pmin) in T.items():
        out.append(Series("cylinder", rf"(?:SMC\s+)?C(?P<magnet>D?)Q2B{bore}-(?P<stroke>\d+)DZ",
                          dict(bore_mm=float(bore), rod_mm=float(rod), acting="double", min_bar=pmin * 10, max_bar=10.0, port=port,
                               connections={"CAP": port, "ROD": port}, cushion="rubber bumper" if bore >= 25 else "none (standard type)",
                               allowable_ke_J=ke, speed_min_mm_s=50.0, speed_max_mm_s=500.0, manufacturer="SMC",
                               description=f"SMC CQ2 compact cylinder, Ø{bore}, through-hole" + (", rubber bumper" if bore >= 25 else "")),
                          ("smc-cq2-z", "smc-cq2", "smc-cq2-dims"), strokes=strokes,
                          figures={"force_extend_N": (float(fo), 5.0), "force_retract_N": (float(fi), 5.0)}))
    return out


def _dsbc() -> list:
    """Festo DSBC ISO 15552 profile cylinders, PPVA self-adjusting pneumatic cushioning, magnetic piston (-N3) (festo-dsbc)."""
    T = {32: (12, "G1/8", 0.6, 483, 415, 0.4, 110, 9), 40: (16, "G1/4", 0.6, 754, 633, 0.7, 205, 16), 50: (20, "G1/4", 0.4, 1178, 990, 1.0, 365, 25),
         63: (20, "G3/8", 0.4, 1870, 1682, 1.3, 430, 25), 80: (25, "G3/8", 0.4, 3016, 2721, 1.8, 810, 39), 100: (25, "G1/2", 0.4, 4712, 4418, 2.5, 1000, 39)}
    out = []
    for bore, (rod, port, pmin, fe, fr, ke, mm0, mm10) in T.items():
        out.append(Series("cylinder", rf"(?:FESTO\s+)?DSBC-{bore}-(?P<stroke>\d+)-PPVA-N3",
                          dict(bore_mm=float(bore), rod_mm=float(rod), acting="double", min_bar=pmin, max_bar=12.0, port=port,
                               connections={"CAP": port, "ROD": port}, magnet=True, cushion="pneumatic, self-adjusting (PPVA)", allowable_ke_J=ke,
                               piston_mass_kg=mm0 / 1000.0, piston_mass_per_mm_kg=mm10 / 10000.0, manufacturer="Festo",
                               description=f"Festo DSBC ISO 15552 profile cylinder, Ø{bore}, PPVA cushioning, magnetic piston"),
                          ("festo-dsbc",), stroke_range=(1, 2800), figures={"force_extend_N": (float(fe), 6.0), "force_retract_N": (float(fr), 6.0)}))
    return out


def _mgp() -> list:
    """SMC MGPM compact guide cylinders (slide bearing), built-in magnet (smc-mgp-z). No allowable kinetic energy is printed."""
    s20 = (20, 30, 40, 50, 75, 100, 125, 150, 175, 200, 250, 300, 350, 400)
    s32 = (25, 50, 75, 100, 125, 150, 175, 200, 250, 300, 350, 400)
    T = {20: (10, s20, 157, 118), 25: (10, s20, 245, 206), 32: (14, s32, 402, 325)}
    return [Series("cylinder", rf"(?:SMC\s+)?MGPM{bore}-(?P<stroke>\d+)Z",
                   dict(bore_mm=float(bore), rod_mm=float(rod), acting="double", min_bar=1.0, max_bar=10.0, port="Rc (size: dimensions page)",
                        magnet=True, cushion="rubber bumper", speed_min_mm_s=50.0, speed_max_mm_s=500.0, manufacturer="SMC", guided=True,
                        description=f"SMC MGPM compact guide cylinder, Ø{bore}, slide bearing, built-in magnet"),
                   ("smc-mgp-z",), strokes=st, figures={"force_extend_N": (float(fo), 5.0), "force_retract_N": (float(fi), 5.0)})
            for bore, (rod, st, fo, fi) in T.items()]


_CYLINDERS = _dsnu() + _cq2() + _dsbc() + _mgp()

SERIES: list[Series] = [
    # ---------------------------------------------------------------- cylinders
    *_CYLINDERS,
    # ---------------------------------------------------------------- valves
    Series("valve", r"(?:SMC\s+)?SY3120-5L(?P<light>Z?)-C6",
           dict(cls="5/2", actuation="solenoid", returns="spring", C=0.76, b=0.30, C_exhaust=0.65, b_exhaust=0.39, min_bar=1.5, max_bar=7.0,
                coil_V=24.0, coil_W=0.4, connector="L plug connector (lead wire 300 mm)", port="P, EA, EB M5 x 0.8; A, B one-touch 6 mm",
                connections={"1": "M5", "3": "M5", "5": "M5", "4": "push-in 6", "2": "push-in 6"}, letters=SMC_VALVE_LETTERS,
                max_hz=10.0, series="SY3000", response_ms=15.0, manufacturer="SMC", description="SMC SY3000 5/2 single-solenoid valve, body ported, 24 VDC"),
           ("smc-sy",)),
    Series("valve", r"(?:SMC\s+)?SY3220-5L(?P<light>Z?)-C6",
           dict(cls="5/2", actuation="solenoid", returns="solenoid", C=0.76, b=0.30, C_exhaust=0.65, b_exhaust=0.39, min_bar=1.0, max_bar=7.0,
                coil_V=24.0, coil_W=0.4, connector="L plug connector x 2 (lead wire 300 mm)", port="P, EA, EB M5 x 0.8; A, B one-touch 6 mm",
                connections={"1": "M5", "3": "M5", "5": "M5", "4": "push-in 6", "2": "push-in 6"}, letters=SMC_VALVE_LETTERS,
                max_hz=10.0, series="SY3000", response_ms=13.0, manufacturer="SMC", description="SMC SY3000 5/2 double-solenoid valve (holds its position unpowered), 24 VDC"),
           ("smc-sy",)),
    Series("valve", r"(?:SMC\s+)?SY5120-5L(?P<light>Z?)-01",
           dict(cls="5/2", actuation="solenoid", returns="spring", C=1.9, b=0.35, C_exhaust=2.4, b_exhaust=0.39, min_bar=1.5, max_bar=7.0,
                coil_V=24.0, coil_W=0.4, connector="L plug connector (lead wire 300 mm)", port="all ports Rc1/8", connections={k: "Rc1/8" for k in ("1", "2", "3", "4", "5")}, letters=SMC_VALVE_LETTERS, max_hz=5.0,
                series="SY5000", response_ms=26.0, manufacturer="SMC", description="SMC SY5000 5/2 single-solenoid valve, body ported, 24 VDC"),
           ("smc-sy",)),
    # ---------------------------------------------------------------- valve manifolds (Type 20, bar stock, individual wiring)
    Series("manifold", r"(?:SMC\s+)?SS5Y3-20-(?P<stations>\d\d)(?P<thread>00F|00N|00T)?(?:-Q)?",
           dict(series="SY3000", C=0.72, b=0.29, C_exhaust=0.80, b_exhaust=0.36, max_stations=20, both_sides_above=10, port="P, EA, EB 1/8",
                blanking_plate="SMC SY3000-26-19A-Q", connections={"1": "Rc1/8", "3": "Rc1/8", "5": "Rc1/8"}, letters=SMC_MANIFOLD_LETTERS,
                description="SMC SS5Y3-20 manifold base for SY3000, common SUP/EXH, 1/8 ports"),
           ("smc-sy-manifold",), stations_range=(2, 20)),
    Series("manifold", r"(?:SMC\s+)?SS5Y5-20-(?P<stations>\d\d)(?P<thread>00F|00N|00T)?(?:-Q)?",
           dict(series="SY5000", C=1.9, b=0.28, C_exhaust=2.2, b_exhaust=0.20, max_stations=20, both_sides_above=10, port="P, EA, EB 1/4",
                blanking_plate="SMC SY5000-26-1A-Q", connections={"1": "Rc1/4", "3": "Rc1/4", "5": "Rc1/4"}, letters=SMC_MANIFOLD_LETTERS,
                description="SMC SS5Y5-20 manifold base for SY5000, common SUP/EXH, 1/4 ports"),
           ("smc-sy-manifold",), stations_range=(2, 20)),
    # ---------------------------------------------------------------- tube, flow controls, silencers
    Series("tube", r"(?:SMC\s+)?TU0604", dict(id_mm=4.0, od_mm=6.0, max_bar=8.0, material="polyurethane",
                                               description="SMC TU polyurethane tube 6 x 4 mm"), ("smc-tu",)),
    Series("frl", r"(?:SMC\s+)?AW20-0[12][A-Z]*(?:-[A-Z0-9]+)?", dict(max_bar=10.0, set_min_bar=0.5, set_max_bar=8.5, port="Rc1/8 or Rc1/4",
                                                                   description="SMC AW20 filter regulator, 5 um"), ("smc-aw",)),
    Series("frl", r"(?:FESTO\s+)?MS4-LFR-1/4-D6-CRM-AS", dict(max_bar=14.0, set_min_bar=0.3, set_max_bar=7.0, port="G1/4", nominal_flow_l_min=1500.0,
                                                            connections={"IN": "G1/4", "OUT": "G1/4"},
                                                            description="Festo MS4-LFR filter regulator, 5 um, G1/4"), ("festo-ms4-lfr",)),
    Series("tube", r"(?:SMC\s+)?TU0425", dict(id_mm=2.5, od_mm=4.0, max_bar=8.0, material="polyurethane", description="SMC TU polyurethane tube 4 x 2.5 mm"), ("smc-tu",)),
    Series("tube", r"(?:SMC\s+)?TU0805", dict(id_mm=5.0, od_mm=8.0, max_bar=8.0, material="polyurethane", description="SMC TU polyurethane tube 8 x 5 mm"), ("smc-tu",)),
    Series("flow_control", r"(?:SMC\s+)?AS1201F-M5-04", dict(C=0.3, b=0.30, max_bar=10.0, port="M5, tube 4 mm", connections={"A": "push-in 4", "B": "M5 male"},
                                                         description="SMC AS speed controller, elbow, M5 x 4 mm"), ("smc-as",)),
    Series("flow_control", r"(?:SMC\s+)?AS2201F-02-08", dict(C=1.4, b=0.30, max_bar=10.0, port="R1/4, tube 8 mm", connections={"A": "push-in 8", "B": "R1/4 male"},
                                                         description="SMC AS speed controller, elbow, R1/4 x 8 mm"), ("smc-as",)),
    Series("flow_control", r"(?:SMC\s+)?AS2201F-01-06", dict(C=0.7, b=0.30, max_bar=10.0, port="R1/8, tube 6 mm", connections={"A": "push-in 6", "B": "R1/8 male"},
                                                         description="SMC AS speed controller, elbow, R1/8 x 6 mm"), ("smc-as",)),
    Series("silencer", r"(?:SMC\s+)?AN10-01", dict(C=2.0, b=0.30, max_bar=10.0, port="R1/8", connections={"IN": "R1/8 male"},
                                                  description="SMC AN silencer, R1/8"), ("smc-an",)),
    Series("silencer", r"(?:SMC\s+)?AN05-M5", dict(C=1.0, b=0.30, max_bar=10.0, port="M5", connections={"IN": "M5 male"}, description="SMC AN silencer, M5"), ("smc-an",)),
    # ---------------------------------------------------------------- position sensors (see also MALE_CONNECTORS below)
    Series("sensor", r"(?:SMC\s+)?D-M9B", dict(output="2-wire", V_min=10.0, V_max=28.0, load_mA_min=2.5, load_mA_max=40.0, drop_V=4.0,
                                                leakage_mA=0.8, connector="grommet, 2 cores 0.15 mm2 (brown +, blue -), 0.5 m lead",
                                                fits=("CQ2", "CDQ2"), description="SMC D-M9B solid-state auto switch, 2-wire"), ("smc-d-m9",)),
    Series("sensor", r"(?:FESTO\s+)?SMT-8M-A-PS-24V-E-0,3-M8D", dict(output="PNP", V_min=5.0, V_max=30.0, load_mA_max=100.0, drop_V=1.5,
                                                                    connector="M8x1 A-coded 3-pin plug on 0.3 m cable (1 +, 3 -, 4 signal)", response_ms=1.3,
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
        if g.get("stations"):
            n = int(g["stations"])
            if s.stations_range and not s.stations_range[0] <= n <= s.stations_range[1]:
                raise CatalogError(f"{part}: {n} stations is outside the series' {s.stations_range[0]}-{s.stations_range[1]}")
            f["stations"] = n
        if g.get("thread"):
            t = {"00F": "G", "00N": "NPT", "00T": "NPTF"}[g["thread"].upper()]
            f["connections"] = {k: v.replace("Rc", t) for k, v in f.get("connections", {}).items()}
        if "magnet" in g:
            f["magnet"] = bool(g["magnet"])
        if "light" in g and not g["light"]:
            f["coil_W"] = 0.35            # without indicator light / surge suppressor: SMC "Standard 0.35"
            if "response_ms" in f:        # and the faster "without light/surge suppressor" response time (SY catalogue p.2)
                f["response_ms"] = {15.0: 12.0, 13.0: 10.0, 26.0: 19.0}.get(f["response_ms"], f["response_ms"])
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


# SMC KQ2H male connectors (R taper thread, fits an Rc or M female port) by (tube OD mm, thread): KQ2 catalogue
# https://static.smc.eu/pdf/KQ2_EU.pdf p.5 "Male connector: KQ2H" table (fetched 2026-11-12). A G (parallel) port takes a G-thread
# fitting, which is not in this table: the BOM names the size and leaves the part number blank.
MALE_CONNECTORS = {
    (4, "M5"): "KQ2H04-M5", (4, "1/8"): "KQ2H04-01S", (4, "1/4"): "KQ2H04-02S",
    (6, "M5"): "KQ2H06-M5", (6, "1/8"): "KQ2H06-01S", (6, "1/4"): "KQ2H06-02S", (6, "3/8"): "KQ2H06-03S",
    (8, "1/8"): "KQ2H08-01S", (8, "1/4"): "KQ2H08-02S", (8, "3/8"): "KQ2H08-03S",
    (10, "1/8"): "KQ2H10-01S", (10, "1/4"): "KQ2H10-02S", (10, "3/8"): "KQ2H10-03S", (10, "1/2"): "KQ2H10-04S",
    (12, "1/4"): "KQ2H12-02S", (12, "3/8"): "KQ2H12-03S", (12, "1/2"): "KQ2H12-04S",
}
UNION_TEES = {4: "KQ2T04-00", 6: "KQ2T06-00", 8: "KQ2T08-00", 10: "KQ2T10-00", 12: "KQ2T12-00"}   # KQ2_EU.pdf union tee table


def male_connector(od_mm: float | None, female: str) -> str:
    """The KQ2H part for a tube OD into a female port ("M5", "Rc1/8"; "" for G ports or an unknown size)."""
    if od_mm is None or not female:
        return ""
    f = female.strip()
    if f.upper().startswith("G"):
        return ""
    size = "M5" if f.upper().startswith("M5") else f.upper().lstrip("RC").lstrip("R").strip()
    return MALE_CONNECTORS.get((int(round(od_mm)), size), "")
