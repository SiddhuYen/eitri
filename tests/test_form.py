"""Form bodies: quad cages (box, sphere, cylinder, torus), Catmull-Clark subdivision, smooth solids from
per-quad patches, cage edits (extrude, edge loop, crease, symmetry) and the form / form_edit ops."""
from __future__ import annotations

import math

import pytest

from eitri import form as F
from eitri.edits import apply_edits, describe
from eitri.model import Parts


def test_primitives_subdivision_and_smooth_solids():
    c = F.box(20, 20, 20)
    assert c.is_closed() and len(c.faces) == 6 and len(c.edges()) == 12
    s1 = F.subdivide(c)
    assert len(s1.faces) == 24 and len(s1.vertices) == 8 + 12 + 6 and s1.is_closed()
    sol = F.to_solid(c, 2)
    assert sol.is_valid and 2000 < sol.volume < 8000 and len(sol.faces()) == 6                # a rounded cube, one patch per cage quad
    bb = sol.bounding_box()
    assert bb.size.X < 20 and bb.size.X > 12 and abs(bb.center().Z) < 1e-6
    sp = F.sphere(10, 2)
    assert sp.is_closed() and len(sp.faces) == 24
    s = F.to_solid(sp, 2)
    assert s.is_valid and abs(s.bounding_box().size.X - s.bounding_box().size.Z) < 0.5 and 0.55 * 4 / 3 * math.pi * 1000 < s.volume < 4 / 3 * math.pi * 1000
    cy = F.to_solid(F.cylinder(8, 20, 2, 2), 2)
    assert cy.is_valid and 0.4 * math.pi * 64 * 20 < cy.volume < math.pi * 64 * 20
    to = F.to_solid(F.torus(15, 5, 12, 6), 2)
    assert to.is_valid and 0.4 * 2 * math.pi ** 2 * 15 * 25 < to.volume < 2 * math.pi ** 2 * 15 * 25     # subdivision pulls the surface inside its cage
    # creases keep edges sharp: the creased box is bigger than the smooth one
    c4 = F.box(20, 20, 20)
    F.crease(c4, c4.edges())
    sharp = F.to_solid(c4, 2)
    assert sharp.volume > sol.volume and sharp.volume > 7000


def test_cage_edits_and_ops():
    c = F.box(20, 20, 20, 2, 2, 2)
    top = [i for i, f in enumerate(c.faces) if abs(c.face_normal(i).Z - 1) < 1e-6]
    assert len(top) == 4
    new = F.extrude_face(c, top[0], 10, scale=0.6)
    assert len(new) == 4 and c.is_closed() and len(c.faces) == 24 + 4
    sol = F.to_solid(c, 2)
    assert sol.is_valid and sol.bounding_box().max.Z > 12
    c3 = F.box(20, 20, 20)
    n = F.insert_loop(c3, c3.edges()[0], 0.5)
    assert n == 4 and c3.is_closed() and len(c3.faces) == 10 and len(c3.vertices) == 12
    assert F.to_solid(c3, 2).is_valid
    c5 = F.box(20, 20, 20, 2, 1, 1)
    c5.symmetry = "x"
    F.move_vertices(c5, [i for i, v in enumerate(c5.vertices) if v[0] > 1 and v[2] > 1], (0, 0, 5))
    assert {round(v[2], 3) for v in c5.vertices if v[0] < -1} == {-10.0, 15.0}                   # mirrored onto the -x side
    # the ops: a form part, then edits that rebuild it
    out, notes = apply_edits(Parts([]), [
        {"op": "form", "kind": "box", "size": {"w": 20, "d": 20, "h": 20, "nx": 2, "ny": 2, "nz": 2}, "levels": 2, "new_part": "blob"},
        {"op": "form_edit", "part": "blob", "action": "extrude_face", "face": 1, "distance": 8, "scale": 0.5},
        {"op": "form_edit", "part": "blob", "action": "crease", "edges": [[0, 1]]},
        {"op": "form_edit", "part": "blob", "action": "levels", "levels": 3},
        {"op": "form_edit", "part": "blob", "action": "symmetry", "axis": "x"},
    ])
    assert all(n.ok for n in notes), [n.detail for n in notes]
    assert out.names == ["blob"] and out["blob"].is_valid and out.forms["blob"]["levels"] == 3 and out.forms["blob"]["cage"]["symmetry"] == "x"
    assert len(out.forms["blob"]["cage"]["faces"]) == 28 and out.forms["blob"]["cage"]["creases"] == [[0, 1]]
    assert "form blob: box cage, 2 level(s)" == describe({"op": "form", "kind": "box", "levels": 2, "new_part": "blob"})
    assert "extrude face 1" in describe({"op": "form_edit", "part": "blob", "action": "extrude_face", "face": 1})
    bad, notes = apply_edits(Parts([]), [{"op": "form", "cage": {"vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], "faces": [[0, 1, 2, 3]]}, "new_part": "open"}])
    assert not notes[0].ok and "not closed" in notes[0].detail
