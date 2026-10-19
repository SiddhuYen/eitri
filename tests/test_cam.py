"""2.5-D CAM: contour with tabs, pocket detection and rings, drilling, facing, chamfer, G-code for GRBL
and LinuxCNC, the time estimate and preview; cut files with kerf; additive orientation and bed arrangement."""
from __future__ import annotations

import math

import build123d as bd
import pytest

from eitri import additive, cam
from eitri.model import Part, Parts


def part():
    body = bd.Box(60, 40, 10, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    body = body - bd.Pos(0, 0, 10) * bd.Box(30, 20, 6, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MAX))   # a 30x20 pocket, 6 deep
    body = body - bd.Pos(22, 12, 0) * bd.Cylinder(2.5, 40) - bd.Pos(-22, -12, 0) * bd.Cylinder(2.5, 40)           # two Ø5 through holes
    return body


def test_contour_pocket_drill_face_chamfer_and_gcode(tmp_path):
    shape = part()
    tool = cam._tool("endmill_6")
    tp = cam.contour(shape, tool, "aluminum", tabs=4)
    zs = sorted({m.z for m in tp.moves if m.kind != "rapid"})
    assert zs[0] == 0.0 and zs[-1] <= 10 and len([z for z in zs if z < 10]) >= 4                 # depth passes down to z = 0 (3 mm stepdown)
    xy = [(m.x, m.y) for m in tp.moves if m.kind == "cut" and m.z == 0.0]
    assert min(x for x, _ in xy) <= -33 + 1e-6 and max(x for x, _ in xy) >= 33 - 1e-6              # offset outward by the 3 mm radius
    assert any(m.z == 2.0 for m in tp.moves if m.kind == "cut") and "4 tab(s)" in tp.notes             # tabs raise the cutter 2 mm on the last pass
    regions = cam.pocket_regions(shape)
    assert len(regions) == 1 and regions[0][1] == 10 and abs(regions[0][2] - 4) < 0.1                  # the pocket, floor at z = 4 (not the through holes)
    pk = cam.pocket(shape, tool, "aluminum")
    cut_pts = [(m.x, m.y, m.z) for m in pk.moves if m.kind == "cut"]
    assert cut_pts and min(z for _, _, z in cut_pts) >= 4 - 0.11 and max(abs(x) for x, _, _ in cut_pts) <= 12 + 1e-6 and max(abs(y) for _, y, _ in cut_pts) <= 7 + 1e-6
    dr = cam.drill(shape, cam._tool("drill_5"), "aluminum")
    assert "2 hole(s)" in dr.notes and min(m.z for m in dr.moves) < 0 and len({(m.x, m.y) for m in dr.moves if m.kind == "plunge"}) == 2
    fc = cam.face(shape, cam._tool("facemill_20"), "aluminum", depth=0.5)
    assert all(abs(m.z - 9.5) < 1e-9 for m in fc.moves if m.kind == "cut")
    ch = cam.chamfer(shape, cam._tool("vbit_90"), "aluminum", size=1.0)
    assert all(abs(m.z - 9.0) < 1e-9 for m in ch.moves if m.kind == "cut") and max(m.x for m in ch.moves if m.kind == "cut") >= 31 - 1e-6
    with pytest.raises(ValueError):
        cam.chamfer(shape, tool)
    job = cam.plan(shape, "bracket", ["face", {"op": "contour", "tabs": 3}, {"op": "pocket", "tool": "endmill_3"}, {"op": "drill", "tool": "drill_5"}, {"op": "chamfer", "size": 0.8}], post="grbl", material="aluminum")
    assert len(job.toolpaths) == 5 and job.seconds > 60 and "~" in job.summary()
    g = cam.gcode(job)
    assert g.startswith("; eitri 2.5-D job") and "G21 G90 G17" in g and "M30" in g and g.count("M3") >= 4 and "G0 Z15.000" in g and "G1 X" in g and " F" in g
    g2 = cam.gcode(cam.plan(shape, "bracket", ["contour"], post="linuxcnc"))
    assert g2.startswith("%") and "G64 P0.01" in g2 and "T2 M6" in g2 and g2.rstrip().endswith("%")
    js = cam.toolpath_json(job)
    assert len(js["paths"]) == 5 and js["paths"][1]["points"][0][3] == 0 and js["seconds"] == round(job.seconds, 1)
    png = cam.preview_png(job, shape, tmp_path / "paths.png")
    from PIL import Image
    assert Image.open(png).size == (900, 700)
    # a pocket too narrow for the tool is reported, not silently skipped
    narrow = bd.Box(40, 40, 10, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)) - bd.Pos(0, 0, 10) * bd.Box(4, 20, 3, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MAX))
    assert "too narrow" in cam.pocket(narrow, cam._tool("endmill_6")).notes


def test_cut_files_with_kerf(tmp_path):
    plate = bd.Box(60, 40, 3, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)) - bd.Pos(10, 0, 0) * bd.Cylinder(5, 10)
    outers, inners = cam.cut_outlines(plate, kerf=0.4)
    assert len(outers) == 1 and len(inners) == 1
    assert abs(outers[0].length - (2 * (60.4 + 40.4) - (8 - 2 * math.pi) * 0.2)) < 0.05                   # outward by 0.2 (rounded corners)
    assert abs(inners[0].length - 2 * math.pi * 4.8) < 1e-3                                              # the hole shrinks by the kerf
    dxf = cam.cut_file(plate, tmp_path / "plate.dxf", kerf=0.4)
    import ezdxf
    doc = ezdxf.readfile(dxf)
    assert {"CUT_OUTER", "CUT_INNER"} <= {l.dxf.name for l in doc.layers}
    assert len(list(doc.modelspace().query('*[layer=="CUT_INNER"]'))) >= 1 and len(list(doc.modelspace().query('*[layer=="CUT_OUTER"]'))) >= 4   # the offset hole (a polyline or circle), the rounded outline
    svg = cam.cut_file(plate, tmp_path / "plate.svg", kerf=0.4, fmt="svg")
    text = (tmp_path / "plate.svg").read_text(encoding="utf-8")
    assert text.startswith("<svg") and text.count("<path") == 2 and 'width="70.40mm"' in text   # 60 + 2 x 5 pad + kerf


def test_additive_orientation_and_bed():
    # a T: a flat plate with a tall post - best printed plate down (no overhang); on its side the plate overhangs
    t_shape = bd.Box(40, 40, 4, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)) + bd.Pos(0, 0, 4) * bd.Cylinder(5, 30, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    best, allo = additive.best_orientation(t_shape)
    assert best.name == "as is" and best.overhang_area == 0 and best.height == 34
    upside = next(o for o in allo if o.name == "upside down")
    assert upside.overhang_area > 1000 and upside.support_volume > upside.overhang_area * 20          # the plate hangs 30 mm up
    # a mushroom: better upside down
    mush = bd.Cylinder(4, 20, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)) + bd.Pos(0, 0, 20) * bd.Cylinder(15, 5, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    best2, _ = additive.best_orientation(mush)
    assert best2.name == "upside down" and best2.overhang_area == 0
    parts = Parts([Part("plate", t_shape, "#999"), Part("mush", mush, "#999"), Part("big", bd.Box(300, 300, 5), "#999")])
    placed, placements, skipped = additive.arrange(parts, bed=(120, 120), spacing=5)
    assert skipped == ["big"] and {p.name for p in placements} == {"plate", "mush"}
    for a in placements:
        for b in placements:
            if a is not b:
                assert abs(a.x - b.x) >= (a.w + b.w) / 2 + 4.9 or abs(a.y - b.y) >= (a.d + b.d) / 2 + 4.9   # no overlap on the bed
        assert abs(a.x) + a.w / 2 <= 60 + 1e-6 and abs(a.y) + a.d / 2 <= 60 + 1e-6
    rep = additive.print_report(parts, bed=(120, 120))
    assert "best orientation 'as is'" in rep and "upside down" in rep and "did not fit: big" in rep and "layers" in rep
