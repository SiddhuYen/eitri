"""The 2D sketch kernel: profiles with holes, the constraint/dimension solver, editing ops,
round-trips from edges, decomposition of the old simple profiles, SVG import, code emitter."""
import math
import textwrap

import build123d as bd
import pytest

from eitri import sketch as S


def rect_sketch(w=20.0, h=10.0):
    return S.from_simple({"type": "rect", "center": [0, 0], "w": w, "h": h})


def test_profile_lines_and_arcs_with_a_hole():
    sk = {"type": "sketch", "entities": [
        {"id": "l1", "type": "line", "a": [0, 0], "b": [20, 0]},
        {"id": "a1", "type": "arc", "c": [20, 5], "r": 5, "start": -90, "end": 90},
        {"id": "l2", "type": "line", "a": [20, 10], "b": [0, 10]},
        {"id": "l3", "type": "line", "a": [0, 10], "b": [0, 0]},
        {"id": "c1", "type": "circle", "c": [10, 5], "r": 2},
        {"id": "x1", "type": "line", "a": [-5, -5], "b": [30, 15], "construction": True},
    ], "constraints": [], "dims": []}
    faces, chains = S.profile_faces(sk)
    assert len(faces) == 1 and not chains
    assert abs(faces[0].area - (200 + math.pi * 25 / 2 - math.pi * 4)) < 1e-3     # rect + half disc - hole
    assert len(faces[0].inner_wires()) == 1
    prof = S.profile(sk)
    assert abs(prof.area - faces[0].area) < 1e-6
    # an open chain is reported, not a face
    open_sk = {"type": "sketch", "entities": [{"id": "l1", "type": "line", "a": [0, 0], "b": [10, 0]}, {"id": "l2", "type": "line", "a": [10, 0], "b": [10, 5]}]}
    faces, chains = S.profile_faces(open_sk)
    assert not faces and len(chains) == 1
    with pytest.raises(ValueError):
        S.profile(open_sk)


def test_solver_dimensions_constraints_dof_and_conflicts():
    sk = rect_sketch(20, 10)
    st = S.solve(sk)
    assert st["ok"] and st["residual"] < 1e-6
    # a rectangle with 4 lines has 16 coords; coincidences (8) + h/v (4) + 2 dims leave 2 (its position)
    assert st["dof"] == 2
    sk["dims"][0]["value"] = 30                                              # the width: distance between the vertical sides
    st = S.solve(sk)
    assert st["ok"] and abs(S.measure(sk, {"type": "length", "a": "l1"}) - 30) < 1e-6 and abs(S.measure(sk, {"type": "length", "a": "l2"}) - 10) < 1e-6
    assert abs(S.profile(sk).area - 300) < 1e-4
    # fix a corner: fully constrained
    sk["constraints"].append({"type": "fix", "a": "l1.a"})
    st = S.solve(sk)
    assert st["ok"] and st["dof"] == 0
    # a conflicting dimension is reported, not silently absorbed
    sk["dims"].append({"type": "length", "a": "l3", "value": 99})
    st = S.solve(sk)
    assert not st["ok"] and st["conflicts"]
    # tangent + equal + concentric on arcs
    slot = S.from_simple({"type": "slot", "center": [0, 0], "length": 30, "w": 8})
    st = S.solve(slot)
    assert st["ok"]
    assert abs(S.profile(slot).area - (22 * 8 + math.pi * 16)) < 1e-3
    slot["dims"][1]["value"] = 12                                           # wider slot: centres stay 22 apart, the arcs and lines follow
    st = S.solve(slot)
    assert st["ok"] and abs(S.profile(slot).area - (22 * 12 + math.pi * 36)) < 1e-2


def test_editing_split_trim_extend_offset_mirror_pattern():
    sk = {"type": "sketch", "entities": [
        {"id": "l1", "type": "line", "a": [0, 0], "b": [20, 0]},
        {"id": "l2", "type": "line", "a": [10, -5], "b": [10, 5]},
        {"id": "c1", "type": "circle", "c": [30, 0], "r": 3},
    ], "constraints": [], "dims": []}
    ids = S.split(sk, "l1", [5, 0])
    near = lambda p, q: abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6
    assert len(ids) == 2 and near(S.entity(sk, ids[0])["b"], [5, 0]) and near(S.entity(sk, ids[1])["a"], [5, 0])
    # trim the right half of l1's remainder at its crossing with l2: the piece right of x=10 goes
    kept = S.trim(sk, ids[1], [15, 0])
    assert kept == [ids[1]] and near(S.entity(sk, ids[1])["b"], [10, 0])
    # extend l2's top end: nothing to meet; extend the piece to the circle
    assert S.extend(sk, "l2", "b") is False
    e = S.entity(sk, ids[1]); e["b"] = [12, 0]
    assert S.extend(sk, ids[1], "b") is True and abs(e["b"][0] - 27) < 1e-6
    # offset a closed loop outward
    rect = rect_sketch(20, 10)
    new = S.offset(rect, [x["id"] for x in rect["entities"]], 2)
    assert len(new) >= 4
    outer = {"type": "sketch", "entities": [x for x in rect["entities"] if x["id"] in new]}
    assert abs(S.profile(outer).area - (24 * 14 - (4 - math.pi) * 4)) < 1e-3     # rounded corners (Kind.ARC)
    # mirror and patterns
    base = {"type": "sketch", "entities": [{"id": "c1", "type": "circle", "c": [5, 0], "r": 1}], "constraints": [], "dims": []}
    m = S.mirror(base, ["c1"], [0, -1], [0, 1])
    assert S.entity(base, m[0])["c"] == [-5, 0]
    arc_sk = {"type": "sketch", "entities": [{"id": "a1", "type": "arc", "c": [0, 0], "r": 5, "start": 0, "end": 90}], "constraints": [], "dims": []}
    m = S.mirror(arc_sk, ["a1"], [0, 0], [0, 1])                                  # across the v axis: 90..180
    a = S.entity(arc_sk, m[0])
    assert abs(a["start"] - 90) < 1e-9 and abs(a["end"] - 180) < 1e-9
    pr = S.pattern_rect(base, ["c1"], 3, 2, 10, 10)
    assert len(pr) == 5 and S.entity(base, pr[-1])["c"] == [25, 10]
    pc = S.pattern_circular(base, ["c1"], 4, [0, 0])
    assert len(pc) == 3 and abs(S.entity(base, pc[0])["c"][1] - 5) < 1e-9


def test_corner_fillet_chamfer_and_code_roundtrip():
    sk = rect_sketch(20, 10)
    arc = S.fillet_corner(sk, "l1", "l2", 3)
    st = S.solve(sk)
    assert st["ok"]
    assert abs(S.profile(sk).area - (200 - (1 - math.pi / 4) * 9)) < 1e-2          # one r=3 corner rounded off
    assert S.entity(sk, arc)["type"] == "arc"
    sk2 = rect_sketch(20, 10)
    S.chamfer_corner(sk2, "l1", "l2", 2)
    assert abs(S.profile(sk2).area - (200 - 2)) < 1e-6
    # the emitted code rebuilds the same profile
    src = S.code(sk2)
    ns = {}
    exec("from build123d import *\n" + src, ns)
    assert abs(ns["profile"].area - S.profile(sk2).area) < 1e-6


def test_edges_roundtrip_simple_profiles_svg_and_projection(tmp_path):
    # from build123d edges (lines, arcs, circles, splines) back to entities and back to the same area
    rr = bd.RectangleRounded(30, 20, 4)
    ents = S.entities_from_edges(list(rr.edges()))
    assert sorted(e["type"] for e in ents) == ["arc"] * 4 + ["line"] * 4
    sk = {"type": "sketch", "entities": [dict(e, id=f"e{i}") for i, e in enumerate(ents)]}
    assert abs(S.profile(sk).area - rr.area) < 1e-6
    for p in ({"type": "circle", "center": [1, 2], "r": 4}, {"type": "polygon", "points": [[0, 0], [10, 0], [5, 8]]}, {"type": "text", "text": "A", "size": 6, "center": [0, 0]}):
        sk = S.from_simple(p)
        assert S.profile(sk).area > 0 and S.solve(sk)["ok"]
    assert "4 lines" in S.summary(rect_sketch())
    svg = tmp_path / "shape.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect x="10" y="10" width="40" height="20"/><circle cx="70" cy="50" r="8"/></svg>', encoding="utf-8")
    sk = {"type": "sketch", "entities": []}
    ids = S.import_svg(sk, str(svg))
    assert len(ids) >= 5 and S.profile(sk).area > 0
    # project the top edges of a box into the XY plane at z = 10
    box = bd.Box(20, 10, 10, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    top_edges = box.faces().sort_by(bd.Axis.Z)[-1].edges()
    sk = {"type": "sketch", "entities": []}
    ids = S.project_edges(sk, list(top_edges), bd.Plane(origin=(0, 0, 10), z_dir=(0, 0, 1)), construction=False)
    assert len(ids) == 4 and abs(S.profile(sk).area - 200) < 1e-6
