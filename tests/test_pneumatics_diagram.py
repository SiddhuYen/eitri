"""The pneumatic schematic as a drawing: orthogonal tubes that enter components at their named ports, never run along each
other, and labels that collide with nothing. The circuit from the first production review: a 25 x 50 cylinder at 6 bar, FRL ->
5/2 solenoid valve -> two meter-out flow controls -> cylinder (its CAP and ROD lines, and the two flow-control outputs, were drawn
merged). Plus the check that a cylinder's two ports are on separate lines."""
from __future__ import annotations

from eitri.diagram import collisions
from eitri.pneumatics import Circuit
from eitri.pneumatics.diagram import _ports, build_scene, layout, status_from
from eitri.pneumatics.report import analyze


def cyl25(joined: bool = False) -> Circuit:
    c = Circuit("25 x 50 cylinder")
    air = c.supply("AIR", pressure_bar=7.0, flow_l_min=300)
    frl = c.frl("FRL", set_bar=6.0)
    v = c.valve("V1", "5/2", actuation="solenoid", returns="spring")
    cyl = c.cylinder("C1", bore_mm=25, rod_mm=10, stroke_mm=50, load_N=120, speed_mm_s=200, cycles_per_min=10)
    c.tube("T1", air["OUT"], frl["IN"], id_mm=6, length_m=2.0)
    c.tube("T2", frl["OUT"], v["P"], id_mm=6, length_m=0.5)
    if joined:                                                  # the CAP and ROD lines tee'd together: a cylinder that cannot move
        t = c.node("TEE")
        c.tube("T3", v["A"], t["N"], id_mm=4, length_m=1.0)
        c.tube("T4", t["N"], cyl["CAP"], id_mm=4, length_m=0.3)
        c.tube("T6", t["N"], cyl["ROD"], id_mm=4, length_m=0.3)
        c.exhaust(v["B"])
    else:
        fc1, fc2 = c.flow_control("FC1", meter="out"), c.flow_control("FC2", meter="out")
        c.tube("T3", v["A"], fc1["A"], id_mm=4, length_m=1.0)
        c.tube("T4", fc1["B"], cyl["CAP"], id_mm=4, length_m=0.3)
        c.tube("T5", v["B"], fc2["A"], id_mm=4, length_m=1.0)
        c.tube("T6", fc2["B"], cyl["ROD"], id_mm=4, length_m=0.3)
    c.silencer(v["R"])
    c.silencer(v["S"])
    return c


def _scene(c):
    rep = analyze(c)
    return build_scene(c, rep, status_from(rep)), rep


def _tubes(sc):
    return {it["g"].split(":", 1)[1]: it["pts"] for it in sc.items if it.get("cls") == "tube"}


def _runs(pts):
    """((axis, fixed coordinate), lo, hi) for every straight run of a polyline."""
    out = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if abs(y1 - y2) < 1e-6:
            out.append((("h", round(y1, 1)), min(x1, x2), max(x1, x2)))
        elif abs(x1 - x2) < 1e-6:
            out.append((("v", round(x1, 1)), min(y1, y2), max(y1, y2)))
        else:
            raise AssertionError(f"a diagonal segment {(x1, y1)} -> {(x2, y2)}")
    return out


def test_cylinder_ports_on_separate_lines():
    ok = {k.name: k for k in analyze(cyl25()).checks}["C1.CAP and C1.ROD on separate lines"]
    assert ok.severity == "pass"
    bad = {k.name: k for k in analyze(cyl25(joined=True)).checks}["C1.CAP and C1.ROD on separate lines"]
    assert bad.severity == "fail" and "same pressure" in bad.measured and bad.refs == ["C1"]


def test_tubes_are_orthogonal_and_never_run_along_each_other():
    sc, _rep = _scene(cyl25())
    tubes = _tubes(sc)
    assert set(tubes) == {"T1", "T2", "T3", "T4", "T5", "T6"}
    runs = {name: _runs(pts) for name, pts in tubes.items()}           # raises on a diagonal
    names = sorted(runs)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for (ka, lo1, hi1) in runs[a]:
                for (kb, lo2, hi2) in runs[b]:
                    assert not (ka == kb and lo1 < hi2 - 0.5 and lo2 < hi1 - 0.5), f"{a} and {b} share a run on {ka}"


def test_tubes_enter_at_their_ports():
    c = cyl25()
    sc, _rep = _scene(c)
    tubes = _tubes(sc)
    pos = layout(c)
    port = lambda ref: _ports(c.components[ref.component], *pos[ref.component])[ref.port]
    for t in c.tubes:
        pts = tubes[t.name]
        for end, ref in ((pts[0], t.a), (pts[-1], t.b)):
            x, y, _d = port(ref)
            assert abs(end[0] - x) < 0.01 and abs(end[1] - y) < 0.01, (t.name, ref, end, (x, y))
    # the supply's tube comes into the FRL's IN from the left, level with it (not up through the body)
    t1 = tubes["T1"]
    (xa, ya), (xb, yb) = t1[-2], t1[-1]
    assert abs(ya - yb) < 0.01 and xa < xb, t1
    # and the FRL's OUT leaves to the right
    t2 = tubes["T2"]
    assert abs(t2[0][1] - t2[1][1]) < 0.01 and t2[1][0] > t2[0][0], t2


def test_no_label_collides_with_anything():
    for c in (cyl25(), cyl25(joined=True)):
        sc, _rep = _scene(c)
        hits = collisions(sc)
        assert not hits, [(a["s"], b.get("s") or f"{b['t']}:{b.get('g')}") for a, b in hits[:8]]


def test_the_flow_data_notice_does_not_paint_the_whole_drawing():
    sc, rep = _scene(cyl25())
    assert any(k.category == "flow data" and k.severity == "warn" for k in rep.checks)       # it is still reported
    assert not status_from(rep)                                                                # but colours nothing


def two_cylinders() -> Circuit:
    """Two cylinders, each on its own 5/2 valve, fed through a manifold tee from one FRL."""
    c = Circuit("Clamp and push sequence")
    air = c.supply("AIR", pressure_bar=7.0, flow_l_min=400)
    frl = c.frl("FRL", set_bar=6.0)
    tee = c.node("MAN")
    v1, v2 = c.valve("V1", "5/2"), c.valve("V2", "5/2")
    c1 = c.cylinder("C1", bore_mm=32, rod_mm=12, stroke_mm=30, load_N=250, speed_mm_s=100, cycles_per_min=10)
    c2 = c.cylinder("C2", bore_mm=25, rod_mm=10, stroke_mm=100, load_N=80, speed_mm_s=250, cycles_per_min=10)
    c.tube("T1", air["OUT"], frl["IN"], id_mm=8, length_m=2.0)
    c.tube("T2", frl["OUT"], tee["N"], id_mm=8, length_m=0.3)
    c.tube("T3", tee["N"], v1["P"], id_mm=6, length_m=0.3)
    c.tube("T4", tee["N"], v2["P"], id_mm=6, length_m=0.3)
    c.tube("T5", v1["A"], c1["CAP"], id_mm=4, length_m=1.0)
    c.tube("T6", v1["B"], c1["ROD"], id_mm=4, length_m=1.0)
    c.tube("T7", v2["A"], c2["CAP"], id_mm=4, length_m=1.2)
    c.tube("T8", v2["B"], c2["ROD"], id_mm=4, length_m=1.2)
    for v in (v1, v2):
        c.silencer(v["R"])
        c.silencer(v["S"])
    return c


def test_a_manifold_tee_and_two_valves(monkeypatch):
    import eitri.pneumatics.diagram as D
    gave_up = []
    route = D.Router.route
    monkeypatch.setattr(D.Router, "route", lambda self, a, b: route(self, a, b) or gave_up.append((a, b)))
    c = two_cylinders()
    sc, _rep = _scene(c)
    assert not gave_up, gave_up                                         # every tube routed around the symbols, none drawn straight
    assert not collisions(sc)
    tubes = _tubes(sc)
    # the tee's three tubes leave it on three different sides
    tee = layout(c)["MAN"]
    first_steps = set()
    for name in ("T2", "T3", "T4"):
        pts = tubes[name]
        near, nxt = (pts[-1], pts[-2]) if abs(pts[-1][0] - tee[0]) < 0.01 and abs(pts[-1][1] - tee[1]) < 0.01 else (pts[0], pts[1])
        first_steps.add((round(nxt[0] - near[0]) > 0) - (round(nxt[0] - near[0]) < 0) + 10 * ((round(nxt[1] - near[1]) > 0) - (round(nxt[1] - near[1]) < 0)))
    assert len(first_steps) == 3
    runs = {name: _runs(pts) for name, pts in tubes.items()}
    names = sorted(runs)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for (ka, lo1, hi1) in runs[a]:
                for (kb, lo2, hi2) in runs[b]:
                    assert not (ka == kb and lo1 < hi2 - 0.5 and lo2 < hi1 - 0.5), f"{a} and {b} share a run on {ka}"
