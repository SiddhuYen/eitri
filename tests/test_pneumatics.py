"""Pneumatics kernel: the typed circuit, the closed-form sizing against hand-computed values, every check firing on a crafted bad
circuit and passing on the fixed one, direct edits, the schematic, the BOM and tables, the reference examples and the demo."""
from __future__ import annotations

import csv
import io
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from eitri.pneumatics import (P_ATM, Circuit, PneumaticsError, consumption_per_cycle_l, cylinder_forces, drop_for_flow, find_path, flow_l_s,
                              series, tube_conductance)
from eitri.pneumatics import diagram, edits as E, outputs
from eitri.pneumatics.report import analyze, circuit_data, summarize
from eitri.model import Checker


def one(rep, needle):
    hits = [c for c in rep.checks if needle in c.name]
    assert len(hits) == 1, f"{needle!r} matched {[c.name for c in hits]} in {[c.name for c in rep.checks]}"
    return hits[0]


def pusher(load=350.0, speed=200.0, p_bar=6.0, bore=32.0, with_fc=True, supply_flow=600.0, cycles=20.0):
    c = Circuit("pusher")
    air = c.supply("AIR", pressure_bar=7.0, flow_l_min=supply_flow)
    frl = c.frl("FRL", set_bar=p_bar, C=2.5, b=0.35)
    v = c.valve("V1", "5/2", C=1.2, b=0.3)
    cyl = c.cylinder("C1", bore_mm=bore, rod_mm=12, stroke_mm=100, load_N=load, speed_mm_s=speed, cycles_per_min=cycles)
    c.tube("T1", air["OUT"], frl["IN"], id_mm=8, length_m=2.0, C=3.0)
    c.tube("T2", frl["OUT"], v["P"], id_mm=6, length_m=1.0, C=2.0)
    c.tube("T3", v["A"], cyl["CAP"], id_mm=6, length_m=1.5, C=1.8)
    if with_fc:
        fc = c.flow_control("FC1", meter="out", C=0.6, b=0.3)
        c.tube("T4", v["B"], fc["A"], id_mm=6, length_m=1.0, C=2.0)
        c.tube("T5", fc["B"], cyl["ROD"], id_mm=6, length_m=0.5, C=2.5)
    else:
        c.tube("T4", v["B"], cyl["ROD"], id_mm=6, length_m=1.5, C=1.8)
    c.silencer(v["R"], C=3.0, b=0.3)
    c.silencer(v["S"], C=3.0, b=0.3)
    return c


# ---------------------------------------------------------------------------- the API


def test_api_validation():
    c = Circuit()
    v = c.valve("V1", "5/2")
    with pytest.raises(PneumaticsError, match="already defined"):
        c.valve("V1", "3/2")
    with pytest.raises(PneumaticsError, match="not one of"):
        c.valve("V2", "7/4")
    with pytest.raises(PneumaticsError, match="no port"):
        v["X"]
    cyl = c.cylinder("C1", 32, 12, 100)
    assert cyl.ports == ["CAP", "ROD"] and c.cylinder("C2", 25, 10, 25, acting="single").ports == ["CAP"]
    with pytest.raises(PneumaticsError, match="rod_mm must be smaller"):
        c.cylinder("C3", 20, 25, 10)
    c.tube("T1", v["A"], cyl["CAP"])
    with pytest.raises(PneumaticsError, match="already has a connection"):
        c.tube("T2", v["A"], cyl["ROD"])
    with pytest.raises(PneumaticsError, match="already defined"):
        c.tube("T1", v["B"], cyl["ROD"])
    with pytest.raises(PneumaticsError, match="both ends"):
        c.tube("T9", v["B"], v["B"])
    n = c.node("N1")
    c.tube("T3", v["B"], n)                                                 # a node takes many tubes
    c.tube("T4", n, cyl["ROD"])
    assert len(c.connections_at(n.port("N"))) == 2
    with pytest.raises(PneumaticsError, match="b must be"):
        c.valve("V3", "3/2", C=1, b=1.2)
    with pytest.raises(PneumaticsError, match="positive"):
        c.supply("S", pressure_bar=0)
    c.silencer(v["R"])
    with pytest.raises(PneumaticsError, match="already has something"):
        c.exhaust(v["R"])


def test_valve_positions():
    from eitri.pneumatics.model import Valve
    assert Valve("v", cls="5/2").positions == [[("P", "A"), ("B", "S")], [("P", "B"), ("A", "R")]]
    assert Valve("v", cls="3/2").positions == [[("A", "R")], [("P", "A")]] and Valve("v", cls="3/2", normally="open").positions == [[("P", "A")], [("A", "R")]]
    assert Valve("v", cls="5/3", center="exhaust").positions[1] == [("A", "R"), ("B", "S")] and Valve("v", cls="5/3").rest_position == 1


# ---------------------------------------------------------------------------- formulas, hand computed


def test_iso_6358_flow():
    # choked: Q = C p1
    assert flow_l_s(1.0, 0.3, 7.0, 1.0) == pytest.approx(7.0)
    # subsonic: p2/p1 = 0.8, b = 0.3: sqrt(1 - ((0.8-0.3)/0.7)^2) = sqrt(1 - 0.5102) = 0.6999
    assert flow_l_s(1.0, 0.3, 7.0, 5.6) == pytest.approx(7.0 * math.sqrt(1 - ((0.8 - 0.3) / 0.7) ** 2), rel=1e-9)
    assert flow_l_s(1.0, 0.3, 7.0, 7.0) == 0.0 and flow_l_s(1.0, 0.3, 7.0, 8.0) == 0.0
    # the inverse
    q = flow_l_s(1.0, 0.3, 7.0, 5.6)
    assert drop_for_flow(1.0, 0.3, 7.0, q) == pytest.approx(1.4, rel=1e-9)
    assert drop_for_flow(1.0, 0.3, 7.0, 7.5) is None and drop_for_flow(1.0, 0.3, 7.0, 0) == 0.0
    # series
    C, b = series([(2.0, 0.35), (1.0, 0.3), (2.0, 0.5)])
    assert C == pytest.approx(1 / math.sqrt(1 / 4 + 1 + 1 / 4)) and b == 0.3


def test_tube_conductance_estimate():
    # 6 mm x 1 m, 2 fittings: L_eq = 1 + 2 * 30 * 0.006 = 1.36 m; Se = 28.27 / sqrt(1 + 0.025 * 1.36 / 0.006) = 28.27 / sqrt(6.667) = 10.95 mm2; C = 2.19
    C, b = tube_conductance(6.0, 1.0, 2)
    area = math.pi / 4 * 36
    assert C == pytest.approx(area / math.sqrt(1 + 0.025 * 1.36 / 0.006) / 5, rel=1e-9) and C == pytest.approx(2.19, abs=0.01) and b == 0.5
    assert tube_conductance(6.0, 10.0, 2)[0] < C < tube_conductance(8.0, 1.0, 2)[0]


def test_cylinder_force_and_consumption():
    c = Circuit()
    cyl = c.cylinder("C1", 32, 12, 100, load_N=350, cycles_per_min=20)
    f = cylinder_forces(cyl, 6.0)
    a_bore, a_ann = math.pi / 4 * 32 ** 2, math.pi / 4 * (32 ** 2 - 12 ** 2)
    assert f["extend_theoretical_N"] == pytest.approx(6.0 * 0.1 * a_bore) == pytest.approx(482.5, abs=0.1)
    assert f["extend_usable_N"] == pytest.approx(482.5 * 0.9, abs=0.1) and f["retract_theoretical_N"] == pytest.approx(6.0 * 0.1 * a_ann)
    cons = consumption_per_cycle_l(cyl, 6.0)
    ratio = (6.0 + P_ATM) / P_ATM
    assert cons["swept_l"] == pytest.approx((a_bore + a_ann) * 100 / 1e6 * ratio) == pytest.approx(1.035, abs=0.002)
    assert cons["per_minute_l"] == pytest.approx(cons["per_cycle_l"] * 20)
    single = c.cylinder("C2", 25, 10, 25, acting="single", spring_N=30)
    assert cylinder_forces(single, 5.0)["retract_usable_N"] == 30 and consumption_per_cycle_l(single, 5.0)["swept_l"] == pytest.approx(math.pi / 4 * 625 * 25 / 1e6 * (5 + P_ATM) / P_ATM)


# ---------------------------------------------------------------------------- the checks


def test_good_pusher_passes():
    rep = analyze(pusher(load=250))
    assert rep.ok and not rep.warnings, summarize(rep)
    cy = rep.cylinders[0]
    assert cy.pressure_bar == 6.0 and cy.strokes[0].usable_N == pytest.approx(434.3, abs=0.2) and cy.strokes[0].status == "pass"
    fill = cy.strokes[0].fill
    assert fill.names == ["T1", "FRL", "T2", "V1", "T3"] and fill.valve_positions == {"V1": 0} and fill.pressure_bar == 6.0
    assert cy.strokes[0].exhaust.names == ["T5", "FC1", "T4", "V1", "S_V1_S"] and cy.strokes[0].exhaust.valve_positions == {"V1": 0}
    assert cy.strokes[1].fill.names == ["T1", "FRL", "T2", "V1", "T4", "FC1", "T5"] and cy.strokes[1].fill.valve_positions == {"V1": 1}


def test_force_levels():
    assert one(analyze(pusher(load=250)), "force C1 extend").severity == "pass"            # ratio 0.52
    c = one(analyze(pusher(load=350)), "force C1 extend")
    assert c.severity == "warn" and "load ratio 0.73" in c.measured
    c = one(analyze(pusher(load=450)), "force C1 extend")
    assert c.severity == "fail" and "434 N usable" in c.measured and c.refs == ["C1"]
    # fixed by a bigger bore: Ø40 at 6 bar = 754 N theoretical
    assert one(analyze(pusher(load=450, bore=40)), "force C1 extend").severity == "pass"
    # the regulator sets the pressure, not the supply: 4 bar on a 7 bar supply
    c = one(analyze(pusher(load=250, p_bar=4.0)), "force C1 extend")
    assert "4 bar x 804 mm²" in c.measured and c.severity == "warn"


def test_single_acting_retract():
    c = Circuit()
    air = c.supply("AIR", 7.0, 300)
    v = c.valve("V1", "3/2", C=0.8, b=0.3)
    cyl = c.cylinder("C1", 25, 10, 25, acting="single", load_N=150, spring_N=25, load_retract_N=10)
    c.tube("T1", air["OUT"], v["P"], id_mm=6, length_m=1, C=2.0)
    c.tube("T2", v["A"], cyl["CAP"], id_mm=6, length_m=1, C=2.0)
    c.silencer(v["R"], C=3, b=0.3)
    rep = analyze(c)
    assert rep.ok, summarize(rep)
    assert one(rep, "force C1 retract").measured == "spring 25 N returns against 10 N"
    cyl.load_retract_N = 40
    assert one(analyze(c), "force C1 retract").severity == "fail"
    cyl.spring_N = None
    assert one(analyze(c), "force C1 retract").severity == "warn"


def test_speed_sizing_and_levels():
    c = pusher(load=250, speed=200)
    rep = analyze(c)
    s = one(rep, "speed C1 extend")
    assert s.severity == "pass" and "200 mm/s needs" in s.measured and "achievable" in s.measured
    ext = rep.cylinders[0].strokes[0]
    a_bore = math.pi / 4 * 32 ** 2
    assert ext.flow_required_l_s == pytest.approx(a_bore * 200 / 1e6 * (6 + P_ATM) / P_ATM, rel=1e-9)
    # the fill flow: series C of T1, FRL, T2, V1, T3 from 7.013 bar abs to the pressure the load needs
    C, b = series([(3.0, 0.5), (2.5, 0.35), (2.0, 0.5), (1.2, 0.3), (1.8, 0.5)])
    p_load = 250 / (a_bore * 0.1 * 0.9) + P_ATM
    assert ext.flow_fill_l_s == pytest.approx(flow_l_s(C, b, 6 + P_ATM, p_load), rel=1e-9)
    assert one(analyze(pusher(load=250, speed=900)), "speed C1 extend").severity == "fail"
    warn = analyze(pusher(load=250, speed=600))
    assert one(warn, "speed C1 extend").severity in ("warn", "fail")
    # a speed requirement with no supply path is a failure, not a crash
    c2 = pusher(load=250)
    c2.tubes = [t for t in c2.tubes if t.name != "T2"]
    assert one(analyze(c2), "speed C1 extend").severity == "fail" and one(analyze(c2), "C1.CAP fed from supply").severity == "fail"


def test_meter_out_limits_the_exhaust_path():
    with_fc, without = analyze(pusher(load=250)), analyze(pusher(load=250, with_fc=False))
    ext_fc, ext_free = with_fc.cylinders[0].strokes[0], without.cylinders[0].strokes[0]
    assert ext_fc.flow_exhaust_l_s < ext_free.flow_exhaust_l_s and ext_fc.exhaust.C_eff < ext_free.exhaust.C_eff
    assert "FC1" in ext_fc.exhaust.names and "FC1" not in ext_free.exhaust.names


def test_air_and_supply_capacity():
    rep = analyze(pusher(load=250, cycles=20))
    cy = rep.cylinders[0]
    assert cy.air_per_cycle_l == pytest.approx(cy.dead_volume_l + 1.035, abs=0.003) and cy.dead_volume_l > 0 and cy.air_per_minute_l == pytest.approx(cy.air_per_cycle_l * 20)
    assert one(rep, "supply AIR capacity").severity == "pass" and rep.supplies[0].demand_l_min == pytest.approx(cy.air_per_minute_l, abs=0.01)
    assert one(analyze(pusher(load=250, cycles=20, supply_flow=35)), "supply AIR capacity").severity == "warn"
    assert one(analyze(pusher(load=250, cycles=20, supply_flow=20)), "supply AIR capacity").severity == "fail"
    c = pusher(load=250, supply_flow=None)
    assert one(analyze(c), "supply AIR capacity").severity == "warn"


def test_pressure_drop_per_tube():
    c = pusher(load=250, speed=200)
    rep = analyze(c)
    drops = [x for x in rep.checks if x.name.startswith("pressure drop") and "extend" in x.name]
    assert [x.refs[0] for x in drops] == ["T1", "T2", "T3"] and all(x.severity == "pass" for x in drops)
    # a long thin run: the flow cannot pass -> fail; a merely long one warns
    c.get_tube("T3").C = 0.05
    bad = one(analyze(c), "pressure drop T3 (C1 extend)")
    assert bad.severity == "fail" and "cannot pass" in bad.measured
    c.get_tube("T3").C = 0.3
    assert one(analyze(c), "pressure drop T3 (C1 extend)").severity == "warn"


def test_circuit_validity():
    c = pusher(load=250)
    c.tubes = [t for t in c.tubes if t.name != "T3"]                       # the cap side is not fed, and V1's A is open
    rep = analyze(c)
    assert one(rep, "C1.CAP fed from supply").severity == "fail" and one(rep, "C1.CAP can exhaust").severity == "fail"
    assert one(rep, "valve V1 ports").severity == "fail" and "work port A is unconnected" in one(rep, "valve V1 ports").measured
    # a tee with one tube is dead-headed; a regulator connected on one side only too
    c2 = pusher(load=250)
    n = c2.node("N1")
    c2.tube("T9", n, c2.get("FRL")["OUT"]) if False else None
    c2.regulator("R2", set_bar=4.0)
    c2.tube("T8", c2.get("R2")["IN"], n)
    d = one(analyze(c2), "no dead-headed lines")
    assert d.severity == "fail" and "node N1 has 1 connection" in d.measured and "R2 is connected only at IN" in d.measured
    assert one(analyze(pusher(load=250)), "no dead-headed lines").severity == "pass"
    # a 2-position valve whose both positions feed P: the chamber can never vent
    c3 = Circuit()
    air = c3.supply("AIR", 7, 300)
    v = c3.valve("V1", "2/2", C=1, b=0.3)
    cyl = c3.cylinder("C1", 25, 10, 25, acting="single", load_N=50, spring_N=20)
    c3.tube("T1", air["OUT"], v["P"], C=2)
    c3.tube("T2", v["A"], cyl["CAP"], C=2)
    rep3 = analyze(c3)
    assert one(rep3, "C1.CAP fed from supply").severity == "pass" and one(rep3, "C1.CAP can exhaust").severity == "fail"
    empty = analyze(Circuit("e"))
    assert one(empty, "a supply").severity == "fail" and one(empty, "an actuator").severity == "warn"


def test_estimated_flow_data_is_one_warning():
    c = pusher(load=250)
    for t in c.tubes:
        t.C = None
    c.get("V1").C = None
    rep = analyze(c)
    w = one(rep, "flow data estimated")
    assert w.severity == "warn" and {"T1", "T2", "T3", "V1"} <= set(w.refs) and "V1 (" in w.measured
    assert not [x for x in analyze(pusher(load=250)).checks if x.name == "flow data estimated"]


def test_verify_and_summary():
    c = pusher(load=250)
    chk = Checker()
    chk.at_least("margin", 434 / 250, 1.5)
    rep = analyze(c, chk)
    assert one(rep, "margin").category == "verify" and rep.ok
    text = summarize(rep)
    assert text.startswith("Pneumatic circuit 'pusher': 7 components, 5 tubes, 1 cylinder(s)") and "C1: Ø32/12 x 100 mm double-acting at 6 bar; extend 434 N usable vs 250 N" in text
    data = circuit_data(c, rep)
    cyl = next(x for x in data["components"] if x["name"] == "C1")
    assert cyl["strokes"][0]["usable_N"] == pytest.approx(434.3, abs=0.2) and data["tubes"][0]["a"] == "AIR.OUT" and data["attached"]["V1.R"] == "S_V1_R.IN"


# ---------------------------------------------------------------------------- outputs and the schematic


def test_bom_and_tables():
    c = pusher(load=250)
    rows = outputs.bom(c)
    by = {r["description"]: r for r in rows}
    assert any(r["category"] == "cylinder" and r["qty"] == 1 for r in rows) and any(r["category"] == "valve" and "5/2" in r["description"] for r in rows)
    tube6 = next(r for r in rows if r["category"] == "tube" and "6 mm" in r["description"])
    assert tube6["qty"] == pytest.approx((1.0 + 1.5 + 1.0 + 0.5) * 1.1, abs=0.01) and tube6["unit"] == "m"
    fit6 = next(r for r in rows if r["category"] == "fitting" and "6 mm" in r["description"])
    assert fit6["qty"] == 8
    assert any(r["category"] == "silencer" and r["qty"] == 2 for r in rows) and not any(r["category"] == "supply" for r in rows)
    parsed = list(csv.DictReader(io.StringIO(outputs.bom_csv(rows))))
    assert len(parsed) == len(rows) and parsed[0]["item"] == "1"
    assert "# Bill of materials: pusher" in outputs.bom_markdown(c)
    rep = analyze(c)
    crows = outputs.cylinder_rows(rep)
    assert [r["stroke"] for r in crows] == ["extend", "retract"] and crows[0]["usable_N"] == pytest.approx(434.3, abs=0.2)
    assert "| C1 | extend |" in outputs.cylinders_markdown(c, rep) and list(csv.DictReader(io.StringIO(outputs.cylinders_csv(crows))))[0]["status"] == "pass"


def test_schematic_svg_and_png(tmp_path):
    c = pusher(load=450)
    rep = analyze(c)
    sc = diagram.build_scene(c, rep, diagram.status_from(rep))
    svg = diagram.scene_to_svg(sc)
    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    ids = {(g.get("data-kind"), g.get("data-id")) for g in root.iter(ns + "g") if g.get("data-id")}
    assert {("component", "C1"), ("component", "V1"), ("component", "FRL"), ("component", "AIR"), ("component", "FC1"), ("tube", "T3"), ("component", "S_V1_R")} <= ids
    texts = " ".join("".join(t.itertext()) for t in root.iter(ns + "text"))
    assert "C1 · D32x100" in texts and "V1 · 5/2 solenoid" in texts and "T3  6 mm ID · 1.5 m" in texts
    assert sc.nodes["C1"]["kind"] == "component" and sc.nodes["AIR"]["y"] > sc.nodes["C1"]["y"]      # supply below the cylinder
    c.place("V1", 400, 300)
    assert diagram.build_scene(c, rep).nodes["V1"]["x"] == 400
    diagram.scene_to_png(diagram.build_scene(c, rep, diagram.status_from(rep), footer=diagram.footer_from(rep)), tmp_path / "s.png")
    from PIL import Image
    assert Image.open(tmp_path / "s.png").size[0] > 500


# ---------------------------------------------------------------------------- direct edits


def test_edits():
    c = pusher(load=250)
    notes = E.apply_edits(c, [
        {"op": "set_component", "component": "C1", "bore_mm": 40, "load_N": 500},
        {"op": "set_component", "component": "V1", "cls": "4/2", "C": 1.5},
        {"op": "set_component", "component": "FRL", "set_bar": 5.5},
        {"op": "set_tube", "tube": "T3", "id_mm": 8, "length_m": 2.0, "C": None},
        {"op": "add_tube", "name": "T9", "a": "V1.R", "b": "C1.CAP"},
        {"op": "set_component", "component": "ghost", "bore_mm": 1},
        {"op": "remove_tube", "tube": "T5"},
        {"op": "set_vent", "port": "FC1.B"},
    ])
    assert [n.ok for n in notes] == [True, True, True, True, False, False, True, True]
    assert "already has a connection" in notes[4].detail and "ghost" in notes[5].detail
    assert c.get("C1").bore_mm == 40 and c.get("V1").cls == "4/2" and c.get("V1").ports == ["P", "A", "B", "R"] and c.get("FRL").set_bar == 5.5
    assert c.get_tube("T3").id_mm == 8 and c.get_tube("T3").C is None and not any(t.name == "T5" for t in c.tubes)
    assert E.describe({"op": "set_component", "component": "C1", "bore_mm": 40}) == "C1: bore_mm = 40"
    assert E.merge_edit({"op": "set_tube", "tube": "T3", "id_mm": 8}, {"op": "set_tube", "tube": "T3", "length_m": 2}) == {"op": "set_tube", "tube": "T3", "id_mm": 8, "length_m": 2}
    from eitri.edits import describe
    assert describe({"op": "remove_tube", "tube": "T5"}) == "remove tube T5"
    assert E.describe_state(c)["C1"]["bore_mm"] == 40


# ---------------------------------------------------------------------------- the reference examples and the demo


def test_reference_examples_build_green(tmp_path):
    from eitri.agent.prompt import domain_reference_text
    from eitri.model import load_model
    blocks = re.findall(r"```python\n(.*?)```", domain_reference_text("pneumatics"), re.S)
    assert len(blocks) == 2
    for i, code in enumerate(blocks):
        f = tmp_path / f"ex{i}" / "model.py"
        f.parent.mkdir()
        f.write_text(code, encoding="utf-8")
        lm = load_model(f)
        c = lm.build()
        rep = analyze(c, lm.verify(c))
        assert rep.ok, f"example {i + 1}:\n{summarize(rep)}"
        assert all(w.name in ("flow data estimated", "force C1 extend") for w in rep.warnings), [w.line() for w in rep.warnings]
    fast = load_model(tmp_path / "ex1" / "model.py", config="fast")
    cf = fast.build()
    assert one(analyze(cf, fast.verify(cf)), "speed C1 extend").severity == "fail"


def test_air_clamp_demo():
    from eitri.model import load_model
    demo = Path(__file__).resolve().parents[1] / "eitri" / "demos" / "air-clamp" / "air_clamp.py"
    lm = load_model(demo)
    c = lm.build()
    rep = analyze(c, lm.verify(c))
    assert rep.ok and [w.name for w in rep.warnings] == ["flow data estimated"], summarize(rep)
    fast = load_model(demo, config="fast")
    cf = fast.build()
    assert one(analyze(cf), "speed C1 extend").severity == "fail"
