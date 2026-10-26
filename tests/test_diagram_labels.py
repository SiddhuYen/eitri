"""Readable diagrams in every domain: no label overlaps another label, a line, or a component, and a wire's label sits next to its
own wire (no other wire between them). The detector (eitri.diagram.collisions / tether_crossings) on synthetic scenes first, then
every domain's diagram on realistic projects: the ones from the first production review included."""
from __future__ import annotations

from pathlib import Path

import pytest

from eitri.diagram import Scene, collisions, place_labels, tether_crossings, text_box, text_w

DEMOS = Path(__file__).resolve().parents[1] / "eitri" / "demos"


def test_text_width_is_measured():
    assert text_w("iiii", 11) < text_w("MMMM", 11) / 2                  # proportional, not a flat 0.58 em
    assert 40 < text_w("AIR · 7 bar", 11) < 80


def test_the_detector_and_the_placer():
    sc = Scene(400, 200)
    sc.items = [{"t": "line", "x1": 20, "y1": 100, "x2": 380, "y2": 100, "stroke": "#000", "sw": 2, "g": "wire:W1"},
                {"t": "rect", "x": 300, "y": 20, "w": 60, "h": 40, "fill": "#fff", "stroke": "#000", "sw": 1, "g": "conn:A"},
                {"t": "text", "x": 200, "y": 104, "s": "W1 on its wire", "size": 11, "fill": "#000", "g": "wire:W1"},
                {"t": "text", "x": 290, "y": 44, "s": "over A's edge", "size": 11, "fill": "#000", "g": "wire:W1"},
                {"t": "text", "x": 320, "y": 44, "s": "pin", "size": 9, "fill": "#000", "g": "conn:A"}]
    found = {(a["s"], b.get("s") or b["t"]) for a, b in collisions(sc)}
    assert ("W1 on its wire", "line") in found and ("over A's edge", "rect") in found and ("over A's edge", "pin") in found
    assert not any(a["s"] == "pin" and b["t"] == "rect" for a, b in collisions(sc))      # content inside its own box is fine
    # movable: the label goes to the first free candidate, off the wire
    lab = sc.items[2]
    lab["cands"] = [(200, 104, "middle"), (200, 88, "middle")]
    sc.items = [it for it in sc.items if it["s" if it["t"] == "text" else "t"] != "over A's edge"]
    assert not place_labels(sc) and lab["y"] == 88 and not collisions(sc)
    assert text_box(lab)[1] + text_box(lab)[3] < 100 - 1                # clear of the wire's stroke


def _scene(model):
    from eitri.domains import kind_of_object
    obj = model() if callable(model) else model
    kind = kind_of_object(obj)
    diagram = __import__(f"eitri.{kind}.diagram", fromlist=["x"])
    report = __import__(f"eitri.{kind}.report", fromlist=["x"])
    rep = report.analyze(obj)
    if kind == "harness":
        return diagram.build_scene(obj, diagram.status_from(rep)), rep
    return diagram.build_scene(obj, rep, diagram.status_from(rep)), rep


def _demo(path):
    from eitri.model import load_model
    return lambda: load_model(path).build()


def _uno_servo():
    from eitri.harness import Connector, Harness, Wire
    h = Harness("MG996R on its own supply")
    uno = h.add(Connector("UNO", "ARDUINO-UNO", pins=["GND", "D9", "D10"]))
    psu = h.add(Connector("PSU", "BARE", pins=["+6V", "GND"]))
    s1 = h.add(Connector("S1", "SOLDER", pins=["GND", "V+", "SIG"]))
    s2 = h.add(Connector("S2", "SOLDER", pins=["GND", "V+", "SIG"]))
    vp, gn = h.splice("V6"), h.splice("GND_J")
    h.net("SERVO_6V", current_A=5.0, voltage_V=6)
    h.net("GND", current_A=5.0, voltage_V=6, round_trip=False)
    h.net("SIG1", current_A=0.001, voltage_V=5)
    h.net("SIG2", current_A=0.001, voltage_V=5)
    h.connect(psu["+6V"], vp, Wire(18, "RD", 200), net="SERVO_6V")
    h.connect(vp, s1["V+"], Wire(20, "RD", 250), net="SERVO_6V", current_A=2.5)
    h.connect(vp, s2["V+"], Wire(20, "RD", 250), net="SERVO_6V", current_A=2.5)
    h.connect(s1["GND"], gn, Wire(20, "BK", 250), net="GND", current_A=2.5)
    h.connect(s2["GND"], gn, Wire(20, "BK", 250), net="GND", current_A=2.5)
    h.connect(gn, psu["GND"], Wire(18, "BK", 200), net="GND")
    h.connect(gn, uno["GND"], Wire(24, "BK", 150), net="GND", current_A=0.01)
    h.connect(uno["D9"], s1["SIG"], Wire(26, "OG", 300), net="SIG1")
    h.connect(uno["D10"], s2["SIG"], Wire(26, "YE", 300), net="SIG2")
    return h


def _planar_arm():
    from eitri.mechanism import Actuator, Mechanism
    m = Mechanism("Two-link planar arm")
    m.link("base", fixed=True)
    m.link("upper", length_mm=120, mass_g=40)
    m.link("fore", length_mm=100, mass_g=30)
    servo = Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=400, travel_deg=180, mass_g=55)
    m.joint("shoulder", "base", "upper", axis=(0, 1, 0), at=(0, 0, 0), limit_deg=(-10, 120), actuator=servo)
    m.joint("elbow", "upper", "fore", axis=(0, 1, 0), limit_deg=(-90, 90), actuator=servo)
    m.payload(50)
    m.tip("fore")
    m.target("reach", (150, 0, 80))
    return m


def _lift():
    from eitri.mechanism import Actuator, Mechanism
    m = Mechanism("Vertical lift")
    m.link("base", fixed=True)
    m.link("carriage", length_mm=60, mass_g=500, direction=(1, 0, 0))
    screw = Actuator("lead screw", stall_N=150, rated_N=80, speed_mm_s=40, travel_mm=100, mass_g=350)
    m.joint("lift", "base", "carriage", type="prismatic", axis=(0, 0, 1), at=(0, 0, 0), limit_mm=(0, 100), actuator=screw)
    m.payload(1000)
    m.tip("carriage")
    m.target("top", (60, 0, 90))
    return m


def _cyl25():
    from test_pneumatics_diagram import cyl25
    return cyl25()


def _sweep_pause():
    from eitri.firmware import Firmware
    fw = Firmware("Servo sweep with pause", board="uno")
    fw.servo("ARM", pin="D9", power="external", current_A=2.5)
    fw.button("PAUSE", pin="D2")
    fw.potentiometer("SPEED", pin="A0")
    fw.pwm_motor("FAN", pwm="D5", dir="D4")
    fw.load("OLED", "3V3", 0.02)
    fw.source("s.ino", "void setup(){}\nvoid loop(){}\n")
    return fw


CASES = {
    "quad-power harness": _demo(DEMOS / "quad-power" / "quad_power.py"),
    "two servos on a separate supply": _uno_servo,
    "pan-tilt": _demo(DEMOS / "pan-tilt" / "pan_tilt.py"),
    "two-link planar arm": _planar_arm,
    "vertical lift": _lift,
    "air clamp": _demo(DEMOS / "air-clamp" / "air_clamp.py"),
    "25 x 50 cylinder circuit": _cyl25,
    "servo sweep": _demo(DEMOS / "servo-sweep" / "servo_sweep.py"),
    "sweep with pause and pot": _sweep_pause,
}


@pytest.mark.parametrize("name", list(CASES))
def test_no_label_overlaps_anything_and_wire_labels_stay_by_their_wire(name):
    sc, _rep = _scene(CASES[name])
    hits = collisions(sc)
    assert not hits, [(a["s"], b.get("s") or f"{b['t']}:{b.get('g')}") for a, b in hits[:8]]
    stray = tether_crossings(sc)
    assert not stray, [(a["s"], b.get("g")) for a, b in stray[:8]]


def test_planar_mechanisms_draw_one_view():
    sc, _rep = _scene(_planar_arm)
    names = [it["s"] for it in sc.items if it["t"] == "text" and it.get("g") == "frame" and "view" in it["s"]]
    assert len(names) == 1 and names[0].startswith("side view") and "plan view adds nothing" in names[0]
    sc, _rep = _scene(CASES["pan-tilt"])                                 # a pan joint about Z: the plan view says something
    names = [it["s"] for it in sc.items if it["t"] == "text" and it.get("g") == "frame" and "view" in it["s"]]
    assert [n.split()[0] for n in names] == ["side", "plan"]


def test_a_slide_shows_its_stroke_along_its_axis():
    sc, _rep = _scene(_lift)
    rails = [it for it in sc.items if it.get("cls") == "rail" and it.get("dash")]
    assert len(rails) == 1
    r = rails[0]
    # 0..100 mm up Z: a vertical rail in the side view, as long as the stroke at the view's scale, starting at the joint
    joint = sc.nodes["lift"]
    assert abs(r["x1"] - r["x2"]) < 0.5 and abs(r["x1"] - joint["x"]) < 0.5 and abs(r["y1"] - joint["y"]) < 0.5 and r["y2"] < r["y1"] - 50


def test_firmware_inputs_are_named_as_asked():
    sc, _rep = _scene(_sweep_pause)
    titles = {it["s"] for it in sc.items if it["t"] == "text" and str(it.get("g", "")).startswith("peripheral:")}
    assert "PAUSE · push button" in titles and "SPEED · potentiometer" in titles and "FAN · PWM motor driver" in titles
    assert "power: own supply, GND shared, 2.5 A" in titles
    sc, _rep = _scene(CASES["servo sweep"])
    assert any(it["t"] == "text" and it["s"] == "START · push button" for it in sc.items)
