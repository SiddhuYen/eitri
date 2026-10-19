"""Engineering drawings: third-angle views with hidden lines, dimensions, hole callouts, DXF."""
from pathlib import Path

from eitri.drawing import drawing_dxf, drawing_svg, project
from eitri.model import load_model

BRACKET = Path(__file__).resolve().parent.parent / "examples" / "bracket.py"


def test_bracket_drawing_svg_and_dxf(tmp_path):
    parts = load_model(BRACKET).build()
    front = project(parts, "front")
    assert front.visible and front.hidden                                   # the holes behind the wall are hidden lines
    assert abs((front.bbox[2] - front.bbox[0]) - 60) < 1e-3 and abs((front.bbox[3] - front.bbox[1]) - 30) < 1e-3
    top = project(parts, "top")
    assert any(h["text"] == "2x Ø5.5 THRU" for h in top.holes)             # the two plate holes point at the viewer
    assert any(h["text"] == "2x Ø5.5 THRU" for h in front.holes)           # the two wall holes
    svg = drawing_svg(parts, "bracket", material="pla", mass_g=19.3)
    for needle in ('id="view-front"', 'id="view-top"', 'id="view-right"', 'id="view-iso"', ">60<", ">40<", ">30<", "A: 2x Ø5.5 THRU", "B: 2x Ø5.5 THRU", ">HOLE<", "TOP: (", "SCALE", "pla, 19.3 g", "stroke-dasharray"):
        assert needle in svg, needle
    (tmp_path / "bracket.svg").write_text(svg, encoding="utf-8")
    dxf = drawing_dxf(parts, "bracket", tmp_path / "bracket.dxf")
    import ezdxf
    doc = ezdxf.readfile(str(dxf))
    layers = {l.dxf.name for l in doc.layers}
    assert {"VISIBLE", "HIDDEN", "TEXT"} <= layers
    msp = doc.modelspace()
    assert len(list(msp.query("CIRCLE"))) >= 2 and len(list(msp.query("LINE LWPOLYLINE"))) > 10 and any(t.dxf.text.startswith("2x Ø5.5") for t in msp.query("TEXT"))


def test_section_view(tmp_path):
    parts = load_model(BRACKET).build()
    sec = project(parts, "section:XZ@20")                    # cut through the plate holes, seen from the front
    assert sec.section and sec.section["plane"] == "XZ" and sec.hatch
    vs = [v for seg in sec.hatch for v in seg[:, 1]]
    assert min(vs) >= sec.bbox[1] - 1e-6 and max(vs) <= sec.bbox[1] + 4 + 1e-6          # hatch covers the 4 mm plate strip at the bottom
    assert abs((sec.bbox[2] - sec.bbox[0]) - 60) < 1e-3 and abs((sec.bbox[3] - sec.bbox[1]) - 30) < 1e-3
    svg = drawing_svg(parts, "bracket", views=("front", "top", "right", "section:XZ@20"))
    assert "SECTION A-A (XZ at 20)" in svg and 'id="view-section:XZ@20"' in svg and "stroke-dasharray=\"4 1.5 1 1.5\"" in svg
    (tmp_path / "section.svg").write_text(svg, encoding="utf-8")
    dxf = drawing_dxf(parts, "bracket", tmp_path / "section.dxf", views=("front", "section:XZ@20"))
    import ezdxf
    assert len(list(ezdxf.readfile(str(dxf)).modelspace().query('LINE[layer=="HATCH"]'))) > 10


def test_parts_list_for_assemblies():
    from build123d import Align, Box, Pos
    from eitri.model import normalize_result
    base = Box(40, 40, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    base.material = "aluminum"
    parts = normalize_result({"base": base, "lid": Pos(0, 0, 10) * Box(40, 40, 3, align=(Align.CENTER, Align.CENTER, Align.MIN))}, "m")
    svg = drawing_svg(parts, "box", views=("front", "top"))
    assert ">PART<" in svg and ">base<" in svg and ">lid<" in svg and ">aluminium<" in svg and ">43.2<" in svg     # 16000 mm³ x 2.7 g/cm³
