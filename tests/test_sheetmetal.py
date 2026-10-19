"""Sheet metal: a base flange, edge flanges (up and down, chained), a hem, a tab, a cut, the flat pattern with
bend allowances and bend lines, the DXF of the flat, and the rules."""
from __future__ import annotations

import math

import build123d as bd
import pytest

from eitri import sheetmetal as SM
from eitri.edits import apply_edits, describe, selector_for_edge, selector_for_face
from eitri.model import Parts
from eitri.viewglb import topology


def ok(notes):
    assert all(n.ok for n in notes), [n.detail for n in notes]


BASE = {"op": "base_flange", "part": None, "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [50, 30], "w": 100, "h": 60}, "thickness": 2, "radius": 2, "k": 0.44, "new_part": "sheet"}


def edge_at(parts, part, mid):
    t = topology(parts)
    return next(e for e in t.edges if e.part == part and e.type == "line" and all(abs(e.mid[i] - mid[i]) < 1e-6 for i in range(3)))


def test_base_flange_edge_flanges_and_flat_pattern():
    out, notes = apply_edits(Parts([]), [BASE])
    ok(notes)
    assert out.names == ["sheet"] and abs(out["sheet"].volume - 100 * 60 * 2) < 1e-6 and out.sheets["sheet"]["flat_area"] == 6000
    # a 90 degree flange up on the +X edge of the top face: bend sector + 20 mm leg
    top_edge = edge_at(out, "sheet", [100, 30, 2])
    out2, notes = apply_edits(Parts([]), [BASE, {"op": "flange", "part": "sheet", "edges": [selector_for_edge(top_edge.__dict__)], "height": 20, "angle": 90}])
    ok(notes)
    r, t = 2.0, 2.0
    bend = (math.pi / 4) * ((r + t) ** 2 - r ** 2) * 60
    assert abs(out2["sheet"].volume - (12000 + bend + 60 * 2 * 20)) < 1e-3
    bb = out2["sheet"].bounding_box()
    assert abs(bb.max.Z - (2 + 2 + 20)) < 1e-6 and abs(bb.max.X - 104) < 1e-6              # leg rises above the top face, outer face at x = 104
    ba = math.radians(90) * (r + 0.44 * t)
    sh = out2.sheets["sheet"]
    assert abs(sh["flat_area"] - (6000 + 60 * (ba + 20))) < 1e-3 and len(sh["bends"]) == 1
    b = sh["bends"][0]
    assert abs(b["a"][0] - (100 + ba / 2)) < 1e-6 and abs(b["b"][0] - (100 + ba / 2)) < 1e-6 and {b["a"][1], b["b"][1]} == {0.0, 60.0}
    assert "allowance" in notes[1].detail and "flange 20 mm at 90°" in describe({"op": "flange", "part": "sheet", "edges": [{}], "height": 20})
    # the same edge picked on the bottom face bends down
    bottom_edge = edge_at(out, "sheet", [100, 30, 0])
    out3, notes = apply_edits(Parts([]), [BASE, {"op": "flange", "part": "sheet", "edges": [selector_for_edge(bottom_edge.__dict__)], "height": 20, "angle": 90}])
    ok(notes)
    bb = out3["sheet"].bounding_box()
    assert abs(bb.min.Z + 22) < 1e-6 and abs(bb.max.Z - 2) < 1e-6 and "down" in notes[1].detail
    # a second flange on the leg's end edge (chained), with an inset relief, then the flat pattern as a part
    leg_edge = edge_at(out2, "sheet", [102, 30, 24])                                      # the leg's top end, outer... pick the inner-face edge
    out4, notes = apply_edits(Parts([]), [BASE, {"op": "flange", "part": "sheet", "edges": [selector_for_edge(top_edge.__dict__)], "height": 20, "angle": 90},
                                           {"op": "flange", "part": "sheet", "edges": [selector_for_edge(leg_edge.__dict__)], "height": 10, "angle": 90, "inset": 5},
                                           {"op": "flat_pattern", "part": "sheet"}])
    ok(notes)
    sh = out4.sheets["sheet"]
    assert len(sh["bends"]) == 2 and abs(sh["flat_area"] - (6000 + 60 * (ba + 20) + 50 * (ba + 10))) < 1e-3
    assert out4.names == ["sheet", "sheet_flat"] and abs(out4["sheet_flat"].volume - sh["flat_area"] * 2) < 1e-3
    assert out4.flats["sheet_flat"]["source"] == "sheet" and len(out4.flats["sheet_flat"]["bends"]) == 2
    fb = out4["sheet_flat"].bounding_box()
    assert fb.min.X > out4["sheet"].bounding_box().max.X + 10                                 # laid out beside the folded part
    assert out4["sheet"].volume > out2["sheet"].volume + 50 * 2 * 10 - 1e-3


def test_hem_tab_cut_rules_and_dxf(tmp_path):
    out, notes = apply_edits(Parts([]), [BASE])
    top_edge = edge_at(out, "sheet", [50, 60, 2])                                            # the +Y edge of the top face
    top = {"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [50, 30, 2], "area": 6000}
    out2, notes = apply_edits(Parts([]), [
        BASE,
        {"op": "sheet_rules", "part": "sheet", "radius": 1, "k": 0.4},
        {"op": "hem", "part": "sheet", "edges": [selector_for_edge(top_edge.__dict__)], "length": 8},
        {"op": "tab", "part": "sheet", "face": top, "origin": [50, 30, 2], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [-50, 0], "w": 20, "h": 10}},   # half outside the -X edge
        {"op": "sheet_cut", "part": "sheet", "face": top, "origin": [50, 30, 2], "normal": [0, 0, 1], "profile": {"type": "circle", "center": [20, 0], "r": 5}},
    ])
    ok(notes)
    sh = out2.sheets["sheet"]
    assert sh["radius"] == 1 and sh["k"] == 0.4
    ba = math.pi * (1 + 0.4 * 2)
    assert abs(sh["flat_area"] - (6000 + 100 * (ba + 8) + 10 * 10 - math.pi * 25)) < 1e-3 and sh["bends"][0]["angle"] == 180
    bb = out2["sheet"].bounding_box()
    assert abs(bb.max.Z - (2 + 2 * 1 + 2)) < 1e-6 and abs(bb.max.Y - (60 + 1 + 2)) < 1e-6     # the hem folds back over the sheet (outer radius r + t)
    assert abs(bb.min.X + 10) < 1e-6                                                          # the tab sticks out of the -X side
    v = out2["sheet"].volume
    assert v < 100 * 60 * 2 + 100 * ((math.pi / 2) * ((1 + 2) ** 2 - 1) + 8 * 2) + 20 * 10 * 2 - math.pi * 25 * 2 + 1e-3
    # the DXF of the flat pattern: outline and a bend line on its own layer
    st = SM.base_flange(bd.Plane.XY, bd.Rectangle(100, 60).face(), 2, 2, 0.44)[1]
    e = bd.Edge.make_line((50, -30, 2), (50, 30, 2))
    _, st, info = SM.flange(st, bd.extrude(bd.Rectangle(100, 60), 2), e, 20)
    path = SM.flat_dxf(st, tmp_path / "flat.dxf")
    import ezdxf
    doc = ezdxf.readfile(path)
    layers = {l.dxf.name for l in doc.layers}
    assert {"OUTLINE", "BEND"} <= layers and len(list(doc.modelspace().query('LINE[layer=="BEND"]'))) == 1 and info["allowance"] == round(math.radians(90) * (2 + 0.44 * 2), 3)
    assert "sheet sheet: 100x60 rectangle t2" == describe(BASE)


def test_contour_flange_chains_bends():
    """A contour flange is a chain of bends from one edge: a hat (up, then out), a C return (up, then
    in), a chain that starts towards the other face, and the op with its flat pattern."""
    import build123d as bd
    from eitri import sheetmetal as SM
    from eitri.edits import apply_edits, describe
    from eitri.model import Parts
    def fresh():                                       # flange() extends the sheet state in place: one state per chain
        solid, st = SM.base_flange(bd.Plane.XY, bd.Rectangle(120, 50).face(), 1.5, 1.5, 0.44)
        edge = min([e for e in solid.edges() if e.geom_type == bd.GeomType.LINE and abs(e.position_at(0.5).Y - 25) < 1e-6 and abs(e.position_at(0.5).Z - 1.5) < 1e-6], key=lambda e: e.length)
        return solid, st, edge
    solid, st, edge = fresh()
    hat, st_hat, info = SM.contour_flange(st, solid, edge, [(20, 90), (15, -90)])
    bb = hat.bounding_box()
    assert hat.is_valid and abs(bb.size.Y - (25 + 3 + 1.5 + 15 + 25)) < 0.6 and abs(bb.size.Z - (1.5 + 1.5 + 20 + 1.5 + 1.5)) < 0.6     # out by the bend radii and the 15 mm leg, up by 20
    assert len(st_hat.bends) == 2 and info["bends"] == 2 and abs(info["unfolded"] - (20 + 15 + 2 * 3.393)) < 0.01
    solid, st, edge = fresh()
    cee, st_c, _ = SM.contour_flange(st, solid, edge, [(20, 90), (15, 90)])
    bb = cee.bounding_box()
    assert cee.is_valid and abs(bb.max.Y - (25 + 1.5 + 1.5)) < 0.6 and bb.size.Y < 66                                       # the return comes back over the sheet
    solid, st, edge = fresh()
    down, st_d, _ = SM.contour_flange(st, solid, edge, [(10, -90), (8, 90)])
    bb = down.bounding_box()
    assert down.is_valid and bb.min.Z < -10 and bb.max.Z == 1.5 and len(st_d.bends) == 2                                    # starts downward
    flat, lines = SM.flat_pattern(st_hat)
    fb = flat.bounding_box()
    assert abs(fb.size.X - 120) < 1e-6 and abs(fb.size.Y - (50 + 20 + 15 + 2 * 3.393)) < 0.01 and len(lines) == 2
    # the op
    out, notes = apply_edits(Parts([]), [
        {"op": "base_flange", "origin": [0, 0, 0], "normal": [0, 0, 1], "profile": {"type": "rect", "center": [0, 0], "w": 120, "h": 50}, "thickness": 1.5, "new_part": "hat"},
        {"op": "contour_flange", "part": "hat", "edge": {"kind": "edge", "type": "line", "mid": [0, 25, 1.5], "length": 120}, "legs": [[20, 90], [15, -90]]},
    ])
    assert all(n.ok for n in notes), [n.detail for n in notes]
    assert len(out.sheets["hat"]["bends"]) == 2 and "unfolded 41.786 mm" in notes[1].detail
    assert describe({"op": "contour_flange", "part": "hat", "edge": {"kind": "edge"}, "legs": [[20, 90], [15, -90]]}) == "contour flange 20@90°, 15@-90° on 1 edge(s) of hat"
