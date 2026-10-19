"""Drawings, the complete set: detail, auxiliary, half-section and broken views, centre marks and
centrelines, radius and angle dimensions, notes, leaders, GD&T frames and datums, ISO/ANSI text, first
and third angle, per-view scales, PDF output and the DXF centre layer."""
from __future__ import annotations

from pathlib import Path

import build123d as bd
import pytest

from eitri.drawing import drawing_dxf, drawing_pdf, drawing_svg, parse_view, project
from eitri.model import Part, Parts, load_model

BRACKET = Path(__file__).resolve().parent.parent / "examples" / "bracket.py"


def wedge_block():
    """A block with a chamfered front-top edge, a side hole and a rounded corner: sloped lines, a side-on
    hole and arcs to dimension."""
    body = bd.Box(60, 30, 20, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    body = bd.chamfer(body.edges().filter_by(bd.Axis.X).sort_by(bd.Axis.Z)[-1:], 6)         # one chamfer along X at the top
    body = body - bd.Pos(20, 0, 10) * bd.Rot(90, 0, 0) * bd.Cylinder(3, 40)                   # a hole through Y
    body = bd.fillet(body.edges().filter_by(bd.Axis.Z).sort_by(bd.Axis.X)[:2], 5)             # the two -X vertical edges rounded
    return Parts([Part("block", body, "#999", "aluminum")])


def test_parse_and_project_new_view_kinds():
    assert parse_view("detail:front@10,5:8:3")[1]["detail"] == {"parent": "front", "x": 10.0, "y": 5.0, "r": 8.0, "scale": 3.0, "label": "B"}
    assert parse_view("halfsection:XZ@0|YZ@0")[1]["half"] == {"plane": "YZ", "offset": 0.0}
    assert parse_view("aux:1,0,1")[1]["aux"]["dir"] == [1.0, 0.0, 1.0] and parse_view("broken:front@-10..10")[1]["broken"]["parent"] == "front"
    parts = wedge_block()
    front = project(parts, "front")
    right = project(parts, "right")
    assert right.angles and any(abs(a[4] - 45) < 1e-3 for a in right.angles)                 # the 6 mm chamfer (along X) shows its 45 degrees from the side
    assert any(abs(a[2] - 5) < 1e-6 for a in project(parts, "top").arcs)                       # the R5 corners seen from above
    assert not front.centerlines and front.circles                                             # the Y hole is a circle from the front...
    assert right.centerlines and abs(right.centerlines[0][1] - right.centerlines[0][3]) < 1e-9 # ...and a horizontal centreline from the side
    det = project(parts, "detail:top@-25,10:8:2")
    assert det.detail and det.scale_mult == 2 and det.visible and all(((p[0] + 25) ** 2 + (p[1] - 10) ** 2) <= 64.5 for pl in det.visible for p in pl)
    aux = project(parts, "aux:1,0,1")
    assert aux.visible and aux.name == "aux:1,0,1"
    half = project(parts, "halfsection:XZ@0|YZ@0")
    assert half.section and half.section["half"]["plane"] == "YZ" and half.hatch and all(p[0] <= 1e-6 for pl in half.hatch for p in pl)   # hatched left of x = 0 only
    assert any(p[0] > 1 for pl in half.visible for p in pl)                                    # the exterior on the right
    brk = project(parts, "broken:front@-10..10")
    assert brk.broken and (brk.bbox[2] - brk.bbox[0]) == pytest.approx(60 - 20 + 6, abs=1e-6) # 20 mm removed, a 6 mm gap left


def test_sheet_with_everything_iso_third(tmp_path):
    parts = wedge_block()
    svg = drawing_svg(parts, "wedge", views=("front", "top", "right", "iso", "detail:top@-25,10:8:2", "halfsection:XZ@0|YZ@0", "aux:1,0,1", "broken:front@-10..10"),
                      notes=["break sharp edges 0.5", "anodise black"],
                      annotations=[{"view": "front", "at": [-20, 10], "text": "powder coat this face"}],
                      gdt=[{"view": "top", "at": [0, 0], "symbol": "flatness", "tolerance": "0.05"}, {"view": "front", "at": [20, 10], "symbol": "position", "tolerance": "0.2", "datums": ["A", "B"]}],
                      datums=[{"view": "front", "at": [-30, 0], "letter": "A"}], scales={"iso": 0.5})
    for needle in ('id="view-detail:top@-25,10:8:2"', "DETAIL B (", 'class="detail-mark"', "HALF SECTION A-A", "AUXILIARY VIEW 1,0,1", "BROKEN, 20 mm removed", 'class="break"',
                   'class="centerlines"', 'class="rdim"', ">R5<", 'class="adim"', ">45°<", 'class="note"', "powder coat this face", 'class="gdt"', "⏤", "⌖", ">A<", ">B<", 'class="datum"',
                   'class="notes"', "1. break sharp edges 0.5", "2. anodise black", 'class="projection-third"', "third angle · ISO", "ISO (", "clipPath"):
        assert needle in svg, needle
    assert "ISO (" in svg and svg.count("transform=\"rotate(-90") >= 2                            # aligned (rotated) vertical dimension text
    (tmp_path / "wedge.svg").write_text(svg, encoding="utf-8")
    pdf = drawing_pdf(svg, tmp_path / "wedge.pdf")
    assert pdf.exists() and pdf.read_bytes()[:4] == b"%PDF" and pdf.stat().st_size > 5000
    dxf = drawing_dxf(parts, "wedge", tmp_path / "wedge.dxf", views=("front", "top", "right"))
    import ezdxf
    doc = ezdxf.readfile(str(dxf))
    assert "CENTER" in {l.dxf.name for l in doc.layers} and len(list(doc.modelspace().query('LINE[layer=="CENTER"]'))) >= 2


def test_ansi_first_angle_layout():
    parts = load_model(BRACKET).build()
    svg = drawing_svg(parts, "bracket", style="ansi", projection="first")
    assert 'class="projection-first"' in svg and "first angle · ANSI" in svg and "transform=\"rotate(-90" not in svg   # unidirectional text
    # first angle: the top view sits below the front view, the right-side view to its left
    import re
    def origin(view):
        m = re.search(rf'<text x="([\d.]+)" y="([\d.]+)" fill="#444" font-size="2.8">{view}</text>', svg)
        return float(m.group(1)), float(m.group(2))
    fx, fy = origin("FRONT"); tx, ty = origin("TOP"); rx, ry = origin("RIGHT")
    assert ty > fy and rx < fx
    svg3 = drawing_svg(parts, "bracket")
    def origin3(view):
        m = re.search(rf'<text x="([\d.]+)" y="([\d.]+)" fill="#444" font-size="2.8">{view}</text>', svg3)
        return float(m.group(1)), float(m.group(2))
    fx, fy = origin3("FRONT"); tx, ty = origin3("TOP"); rx, ry = origin3("RIGHT")
    assert ty < fy and rx > fx


def test_user_placed_dimensions(tmp_path):
    """Your own dimensions: horizontal and vertical between view points, an aligned one along a slope,
    a chain and a baseline group - placed in view mm relative to the look-at point."""
    from eitri.drawing import drawing_svg
    parts = wedge_block()
    dims = [{"view": "front", "from": [-30, 0], "to": [30, 0], "offset": -14},                       # 60 wide, below the view
            {"view": "front", "from": [30, 0], "to": [30, 20], "kind": "vertical", "offset": 10, "text": "20 ±0.1"},
            {"view": "front", "from": [-30, 0], "to": [-10, 20], "kind": "aligned", "offset": 6},    # 28.28 along the slope
            {"view": "front", "points": [[-30, 0], [-10, 0], [30, 0]], "axis": "x", "offset": 24},    # chain: 20 + 40
            {"view": "top", "points": [[-30, 0], [-20, 0], [10, 0]], "axis": "x", "offset": -10, "baseline": True}]   # baseline: 10, 40
    svg = drawing_svg(parts, "block", views=("front", "top"), sheet="A4", dims=dims)
    grp = svg[svg.index('<g class="userdims"'):]
    grp = grp[:grp.index("</g>")]
    for text in (">60<", ">20 ±0.1<", ">28.28<", ">20<", ">40<", ">10<"):
        assert text in grp, text
    assert grp.count('marker-end="url(#a1)"') == 7 and "rotate(" in grp                          # 7 dimension lines, the aligned one rotated
    assert 'transform="rotate(-45.0' in grp                                                          # the slope runs up at 45 degrees
    ansi = drawing_svg(parts, "block", views=("front",), sheet="A4", style="ansi", dims=dims[:1])
    assert ansi.count('marker-end="url(#a1)"') >= 1 and ">60<" in ansi
    pytest.importorskip("playwright.sync_api")
    from PIL import Image
    from eitri.drawing import drawing_png
    png = drawing_png(svg, tmp_path / "sheet.png", "A4", width_px=900)
    im = Image.open(png)
    assert im.size == (900, 636) and im.getpixel((5, 5))[:3] == (255, 255, 255)                  # A4 landscape, white sheet
