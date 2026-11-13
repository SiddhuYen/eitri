"""The sourced figures the electronics cross-domain checks add to the electronics domain's own (eitri.electronics.sources): board and
module outlines, their mounting holes, and screw sizes. Each record has a URL, the exact figure as printed and the fetch date. A value
that is not printed by any fetched source is marked ASSUMPTION in its record and in every check text that uses it.

Board frames are the Assembly view's (eitri_suite.assembly): the origin at the centre of the PCB's top face, x along the long side with
the USB jack at -x, y towards the side the USB jack is nearer (the Uno's high-y edge in its own board file), z up out of the component
side. Hole positions below are converted from each source's corner origin to that frame.
"""
from __future__ import annotations

import re

FETCHED = "2026-11-12"

SOURCES: dict[str, dict] = {
    "littelfuse-297-drop": dict(
        title="Littelfuse MINI Blade Fuses, Rated 32V (297 series) datasheet, ratings table 'Typ. Voltage Drop (mV)' at rated current, "
              "0.5 mm2 test cable (the same datasheet the core's fuse checks read: store.comet.bg/download-file.php?id=27670)",
        url="https://store.comet.bg/download-file.php?id=27670",
        quote="0297005 (5 A): Typ. Voltage Drop 129 mV, Typ. Cold Resistance 17.75 mOhm (figures as reproduced by distributors' copies of the "
              "table; the drop at rated current is used, scaled to the current: the hot resistance, higher than the cold one)"),
    "arduino-uno-brd": dict(
        title="Arduino UNO R3 (A000066) CAD files, UNO-TH_Rev3e.brd (Eagle board file)",
        url="https://docs.arduino.cc/static/6bb7a3ca51ebee82a252f60c0b418787/A000066-cad-files.zip",
        quote='board outline (layer 20) x 0-68.58 mm, y 0-53.34 mm; <hole x="13.97" y="2.54" drill="3.2"/>, x="15.24" y="50.8", '
              'x="66.04" y="35.56", x="66.04" y="7.62"; USB-B X2 at x="3.81" y="38.1"'),
    "arduino-uno-ds": dict(
        title="Arduino UNO R3 datasheet (A000066), p.11, 5.4 Board Outline & Mounting Holes",
        url="https://docs.arduino.cc/resources/datasheets/A000066-datasheet.pdf",
        quote="4 x Ø126mil / 4 x Ø3.20mm"),
    "arduino-nano-ds": dict(
        title="Arduino Nano datasheet (A000005), p.11, 6 Mechanical Information",
        url="https://docs.arduino.cc/resources/datasheets/A000005-datasheet.pdf",
        quote="PCB 43.2 x 18 mm; holes 40.64 x 15.24 mm apart, 4 x Ø1.8 (hole positions assume the pattern is centred on the PCB: "
              "the drawing does not print the edge offsets)"),
    "adafruit-815-brd": dict(
        title="Adafruit 16-Channel 12-bit PWM/Servo Driver (815), board file Adafruit PCA9685.brd",
        url="https://github.com/adafruit/Adafruit-16-Channel-PWM-Servo-Driver-PCB",
        quote="outline 62.23 x 25.4 mm (2.45 x 1.00 in); four MOUNTINGHOLE_2.5_PLATED drill=\"2.5\" at (3.175, 3.175), (3.175, 22.225), "
              "(59.055, 3.175), (59.055, 22.225) mm from the lower-left corner; learn guide: \"Holes are 2.5mm diameter\""),
    "adafruit-815-vplus": dict(
        title="Adafruit 815 product page and learn guide (pinouts)",
        url="https://www.adafruit.com/product/815",
        quote="\"Reverse polarity protection on the terminal block input.\" No current rating for V+ is published (the guide: \"Some "
              "High-torque servos will draw more than 1A each under load\")"),
    "iso-metric-thread": dict(
        title="Metric tap drill chart (ISO 68-1 basic profile, ISO 2306 drills), AMES",
        url="https://amesweb.info/Screws/metric-tap-drill-chart.aspx",
        quote="M2.5 x 0.45: major diameter 2.5, drill 2.05 mm; M3 x 0.5: major diameter 3, drill 2.50 mm (a metric screw's size "
              "number is its basic major diameter, ISO 68-1)"),
    "pcb-thickness": dict(
        title="ASSUMPTION: 1.6 mm FR-4, the common board thickness",
        url="",
        quote="no fetched source prints these boards' thickness; 1.6 mm is assumed (it only sets how deep the outline box reaches below "
              "the anchor)"),
}


def cite(key: str) -> str:
    s = SOURCES.get(key)
    if s is None:
        return key
    return f"{s['title']}" + (f", {s['url']}" if s["url"] else "") + f", fetched {FETCHED}"


def _from_corner(w: float, h: float, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(round(x - w / 2, 3), round(y - h / 2, 3)) for x, y in pts]


# outline: PCB length x width x thickness (mm, the board frame), holes: centres in the board frame, hole_d: drilled diameter,
# screw: the screw the holes take by default (COUPLING mounts screw= overrides it)
OUTLINES: dict[str, dict] = {
    "uno": dict(title="Arduino Uno R3", size=(68.58, 53.34, 1.6), src="arduino-uno-brd", thickness_src="pcb-thickness",
                holes=_from_corner(68.58, 53.34, [(13.97, 2.54), (15.24, 50.8), (66.04, 35.56), (66.04, 7.62)]),
                hole_d=3.2, hole_src="arduino-uno-ds", screw="M3"),
    "nano": dict(title="Arduino Nano", size=(43.2, 18.0, 1.6), src="arduino-nano-ds", thickness_src="pcb-thickness",
                 holes=[(-20.32, -7.62), (-20.32, 7.62), (20.32, -7.62), (20.32, 7.62)], hole_d=1.8, hole_src="arduino-nano-ds", screw="M1.6"),
    "PCA9685": dict(title="Adafruit 815 PCA9685 breakout", size=(62.23, 25.4, 1.6), src="adafruit-815-brd", thickness_src="pcb-thickness",
                    holes=_from_corner(62.23, 25.4, [(3.175, 3.175), (3.175, 22.225), (59.055, 3.175), (59.055, 22.225)]),
                    hole_d=2.5, hole_src="adafruit-815-brd", screw="M2.5"),
}

# a module that passes servo power through (its V+ terminal) and what its maker says the terminal carries (None: nothing published)
PASS_THROUGH_RATING: dict[str, tuple[float | None, str]] = {"PCA9685": (None, "adafruit-815-vplus")}


def outline_for(catalog: str) -> dict | None:
    k = str(catalog or "")
    return OUTLINES.get(k) or OUTLINES.get(k.lower()) or OUTLINES.get(k.upper())


def screw_diameter(designation) -> float | None:
    """A metric screw's basic major diameter from its designation (M3, M2.5, M3x8): the size number (ISO 68-1)."""
    if isinstance(designation, (int, float)) and not isinstance(designation, bool):
        return float(designation)
    m = re.match(r"^\s*M(\d+(?:\.\d+)?)", str(designation or ""), re.I)
    return float(m.group(1)) if m else None


# a protective device's voltage drop: (family, rating A) -> (drop V at the rated current, source id). A device not listed has no figure,
# and the voltage-at-the-loads check through it is "not run" (never a silent zero).
FUSE_DROP_V = {("MINI", 5.0): (0.129, "littelfuse-297-drop")}


def fuse_drop_V(family: str, rating_A: float, amps: float) -> tuple[float | None, str]:
    """(drop at `amps`, source cite) for a catalog fuse, scaled from its drop at the rated current; (None, why) when no figure."""
    rec = FUSE_DROP_V.get((str(family).upper(), float(rating_A)))
    if rec is None:
        return None, f"no published voltage drop for a {family} {rating_A:g} A fuse in the suite's fuse-drop table"
    v, sid = rec
    return v * amps / float(rating_A), cite(sid)
