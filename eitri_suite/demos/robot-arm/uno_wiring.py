"""The Arduino Uno's servo wiring: three signal wires from D9, D10 and D11 to the servo connectors, each with its ground reference from
the Uno's GND (a signal needs the board's ground at the far end: the core's shared-ground rule). Power is the arm's other harness."""
from eitri.harness import *

PARAMS = dict(lead_mm=250)


def build(p):
    h = Harness("Uno servo signals", "D9, D10, D11 to three servo connectors")
    uno = h.add(Connector("UNO", "DUPONT-2.54", pins={"D9": "D9", "D10": "D10", "D11": "D11", "GND": "GND"}, gender="female"))
    servos = [h.add(Connector(n, "DUPONT-2.54", pins={"GND": "GND", "SIG": "PWM"}, gender="female")) for n in ("YAW", "SHOULDER", "ELBOW")]
    if hasattr(h, "mcu_pin"):          # core 0.8.0 (wires-plc): the signal nets are graded against the Uno's I/O pin (Arduino pinout)
        for pin in ("D9", "D10", "D11"):
            h.mcu_pin(uno[pin], "uno")
    gnd = h.splice("GND_REF")                                   # the Uno's ground, split to the three servo connectors
    h.net("GND_REF", current_A=0.003, voltage_V=5.0, round_trip=False)        # the signals' return only: the servos' power returns in wiring.py
    h.connect(uno["GND"], gnd, Wire(24, "BK", 60), net="GND_REF", name="UNO_GND")
    for pin, sv in (("D9", servos[0]), ("D10", servos[1]), ("D11", servos[2])):
        h.net(f"SIG_{sv.name}", current_A=0.001, voltage_V=5.0, signal=True)      # driven by the Uno's pins
        h.connect(uno[pin], sv["SIG"], Wire(24, "OG", p.lead_mm), net=f"SIG_{sv.name}", name=f"{sv.name}_SIG")
        h.connect(gnd, sv["GND"], Wire(24, "BK", p.lead_mm), net="GND_REF", current_A=0.001, name=f"{sv.name}_GND")
    return h
