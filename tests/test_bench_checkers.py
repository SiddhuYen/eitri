"""The benchmark checkers must accept known-good reference solutions built with the library.

(Keeps the checkers honest: a checker that rejects a correct part would penalise
both systems for nothing.)
"""
from __future__ import annotations

import pytest

from eitri import *  # noqa: F403
from eitri.model import normalize_result

from bench import tasks as T


def _run(name, result):
    parts = normalize_result(result, name)
    checks = T.get(name).check(parts)
    bad = [c for c in checks if not c[1]]
    assert not bad, bad


def test_plate_and_followup():
    plate = rounded_box(60, 40, 5, 5)
    plate = cut_holes(plate, [(x, y) for x in (-22, 22) for y in (-12, 12)], size="M4")
    _run("plate", plate)
    plate2 = rounded_box(60, 40, 5, 5)
    plate2 = cut_holes(plate2, [(x, y) for x in (-22, 22) for y in (-12, 12)], size="M5", head="counterbore")
    plate2 = cut_holes(plate2, [(0, 0)], d=20)
    parts = normalize_result(plate2, "plate")
    bad = [c for c in T.t_plate_followup(parts) if not c[1]]
    assert not bad, bad


def test_rpi_case_and_nema17():
    L, W, wall, cl, floor_t, inside_h = 85, 56, 2.0, 1.5, 2.0, 25.0
    outer = rounded_box(L + 2 * cl + 2 * wall, W + 2 * cl + 2 * wall, floor_t + inside_h, 3)
    cav = Pos(0, 0, floor_t) * extrude(RectangleRounded(L + 2 * cl, W + 2 * cl, 1.5), inside_h + 1)
    case = outer - cav
    hd, hdep = heat_set_insert_dims("M2.5")
    for x, y in [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]:
        case = case + Pos(x - L / 2, y - W / 2, floor_t) * boss(hd + 3.2, 5, hole_d=hd, hole_depth=hdep)
    _run("rpi_case", case)

    plate = Box(44, 4, 44, align=(Align.CENTER, Align.MAX, Align.MIN)).moved(Location((0, 0, 4)))
    base = Box(44, 40, 4, align=(Align.CENTER, Align.MAX, Align.MIN))
    br = plate + base
    face = Plane(origin=(0, -4, 26), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
    br = cut_holes(br, [(x, y) for x in (-15.5, 15.5) for y in (-15.5, 15.5)], size="M3", plane=face)
    br = cut_holes(br, [(0, 0)], d=22.5, plane=face)
    br = cut_holes(br, [(-15, -20), (15, -20)], size="M4")
    _run("nema17_mount", br)


def test_plates_text_bolt_slots():
    pl = rounded_box(88.6, 73.3, 4, 5)
    ref = [(14.0, 2.5), (15.3, 50.7), (66.1, 7.6), (66.1, 35.5)]
    _run("arduino_plate", cut_holes(pl, [(x - 68.6 / 2, y - 53.3 / 2) for x, y in ref], size="M3"))
    tag = cut_holes(emboss_text(rounded_box(70, 25, 3, 4), "CADCODE", size=8, height=1, at=(4, 0)), [(-30, 0)], d=4)
    _run("name_tag", tag)
    bolt = hex_prism(13, 5.3) + threaded_rod("M8", 30).moved(Location((0, 0, -30)))
    _run("hex_bolt", bolt)
    sb = Box(100, 30, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    sb = sb - [Pos(-20, 0, 5) * slot_cutter(20, 6.5), Pos(20, 0, 5) * slot_cutter(20, 6.5)]
    _run("slotted_bracket", cut_holes(sb, [(0, 0)], d=10))


def test_bearing_housing_and_gear_pair():
    bh = Box(40, 12, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))
    front = Plane(origin=(0, -6, 18), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
    bh = bh - front * bearing_pocket_cutter("608", fit=0.1, through_bore_clearance=0.5)
    bh = cut_holes(bh, [(-15, 5), (15, 5)], size="M4", plane=front)
    _run("bearing_housing", bh)
    g1 = spur_gear(1.5, 15, 6, bore=5)
    g2 = spur_gear(1.5, 30, 6, bore=5).rotate(Axis.Z, 360 / 30 / 2).moved(Location((gear_center_distance(1.5, 15, 30), 0, 0)))
    _run("gear_pair", {"pinion": g1, "gear": g2})


def test_enclosure_tray_heatsink_flange():
    e = enclosure(100, 60, 40, wall=2, screw_size="M3")
    _run("enclosure", e)
    tray = shell_box(120, 80, 25, wall=1.6, floor=1.6, corner_radius=4)
    tray = tray + [Pos(x, 0, 0) * Box(1.6, 80 - 3.2, 25, align=(Align.CENTER, Align.CENTER, Align.MIN)) for x in (-20, 20)]
    _run("tray", tray)
    base = Box(40, 40, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
    pitch = (40 - 2) / 9
    fins = [Pos(-19 + i * pitch, 0, 4) * Box(2, 40, 16, align=(Align.CENTER, Align.CENTER, Align.MIN)) for i in range(10)]
    _run("heatsink", base + fins)
    fl = Cylinder(40, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)) + Pos(0, 0, 10) * Cylinder(20, 15, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fl = fl - Cylinder(10, 100)
    fl = fl - [loc * Cylinder(3.3, 100) for loc in PolarLocations(30, 6)]
    _run("flange", fl)
