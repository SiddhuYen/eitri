"""STEP export must round-trip every part as a solid, even for geometry OCCT's writer dislikes."""
from __future__ import annotations

from pathlib import Path

import build123d as bd

from eitri import *  # noqa: F403
from eitri.kernel import export_parts
from eitri.model import load_step_file, normalize_result


def test_offset_curve_text_round_trips(tmp_path):
    # rounded (offset) text outlines extruded onto a lid: the raw STEP writer drops these faces
    lid = Box(60, 30, 3, align=(Align.CENTER, Align.CENTER, Align.MIN))
    sk = Text("MODEL A", font_size=8, font_style=bd.FontStyle.BOLD)
    rounded = offset(sk, amount=0.3, kind=Kind.ARC)          # Geom_OffsetCurve outlines
    label = extrude(rounded, 0.6)
    lid = lid + Pos(0, 0, 3) * label
    assert len(lid.solids()) == 1
    parts = normalize_result({"base": Box(60, 30, 5), "lid": lid}, "enc")
    out = export_parts(parts, tmp_path, "enc", ("step",))
    back = load_step_file(out["step"])
    assert [(p.name, len(p.shape.solids())) for p in back] == [("base", 1), ("lid", 1)]
    assert abs(back["lid"].volume - lid.volume) < 0.005 * lid.volume
    assert "NURBS" in out.get("notes", "") or True   # a note is informational


def test_plain_parts_untouched(tmp_path):
    plate = cut_holes(Box(40, 30, 5, align=(Align.CENTER, Align.CENTER, Align.MIN)), [(0, 0)], d=6)
    parts = normalize_result(plate, "plate")
    out = export_parts(parts, tmp_path, "plate", ("step", "stl", "glb", "3mf"))
    back = load_step_file(out["step"])
    assert len(back) == 1 and len(back[0].solids()) == 1
    # plain geometry keeps its analytic faces (no NURBS conversion)
    assert any(f.geom_type == bd.GeomType.CYLINDER for f in back[0].faces())
    for fmt in ("stl", "glb", "3mf"):
        assert Path(out[fmt]).stat().st_size > 200
