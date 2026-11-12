"""Servo arm wiring with overcurrent protection: an Arduino Uno drives three servos (D9, D10, D11); the servos run from a 5 V
supply through one blade fuse at the supply and two bus blocks (terminal splices), with the Uno's ground on the same bus.

The harness of the eitri.io robot-arm demo (eitri_suite/demos/uno-arm/wiring.py), made complete: the supply is declared (a
Mean Well LRS-50-5: its overload protection trips at 110-150 % of the rated 50 W, so set as low as 4.5 V it can push up to
75 W / 4.5 V = 16.7 A into a fault, more than a 22 AWG branch's 7 A), so the 5 V rail needs a fuse. One ATO 5 A fuse within 175 mm of the supply protects every wire on the rail: its
5 A is under the thinnest wire's 7 A, and the rail's 3 A steady load is within the fuse's continuous-load derating.

The supply's datasheet gives no short-circuit current, so the fuse's interrupting-rating check reports "not run" with that
reason: the one warning, intended. Put the supply's measured or published short-circuit current in `PSU_ISC_A` to run it.
"""
from eitri.harness import *

PARAMS = dict(
    servo_a=1.0,           # each servo's running current, A (a design figure, ESTIMATE; an MG996R stalls at 2.5 A)
    lead_mm=320,           # bus to the shoulder servo; the elbow is 60 mm further
    psu_limit_a=16.7,      # Mean Well LRS-50-5 (LRS-50-SPEC 2025-04-07): OVER LOAD "110 ~ 150% rated output power", hiccup mode:
                           #   150 % of 50 W at the lowest setting, 4.5 V (ADJ. RANGE 4.5 ~ 5.5V): 16.7 A, the higher figure for a draw
    psu_max_v=5.5,         # the highest the supply can be set to (ADJ. RANGE 4.5 ~ 5.5V)
    fuse_a=5,              # ATO blade fuse at the supply
    fuse_lead_mm=60,       # supply + terminal to the fuse holder: within 175 mm (ABYC E-11, as published by Blue Sea Systems)
    ambient_c=40,          # air around the fuse inside the arm's base, C
)
PSU_ISC_A = None           # the supply's short-circuit current, A: the datasheet lists "Short circuit" protection but no current, so the
                           # interrupting check is not run (put a measured or published figure here to run it)


def build(p):
    h = Harness("Servo arm wiring", "Arduino Uno, three servos, fused 5 V bus", ambient_C=p.ambient_c)
    uno = h.add(Connector("UNO", "DUPONT-2.54", pins={"D9": "D9", "D10": "D10", "D11": "D11", "GND": "GND"}, gender="female"))
    psu = h.add(Connector("PSU", "BARE", pins={"+": "5V", "-": "GND"}, part_number="LRS-50-5"))
    servos = [h.add(Connector(n, "DUPONT-2.54", pins={"GND": "GND", "V+": "V+", "SIG": "PWM"}, gender="female"))
              for n in ("SERVO_YAW", "SERVO_SH", "SERVO_EL")]
    fuse = h.add(Fuse("F1", "ATO", p.fuse_a))
    v5 = h.splice("BUS_5V", kind="terminal")
    gnd = h.splice("BUS_GND", kind="terminal")

    h.source(psu["+"], p.psu_max_v, max_current_A=p.psu_limit_a, short_circuit_A=PSU_ISC_A, kind="psu", part="Mean Well LRS-50-5")
    h.net("V5", current_A=3 * p.servo_a, voltage_V=5.0)
    h.net("GND_PWR", current_A=3 * p.servo_a, voltage_V=5.0, round_trip=False)
    for tag in ("YAW", "SH", "EL"):
        h.net(f"SIG_{tag}", current_A=0.001, voltage_V=5.0, signal=True)       # driven by the Uno's pins

    h.connect(psu["+"], fuse["LINE"], Wire(18, "RD", p.fuse_lead_mm), net="V5", name="PWR_F")
    h.connect(fuse["LOAD"], v5, Wire(18, "RD", 150), net="V5", name="PWR_V")
    h.connect(gnd, psu["-"], Wire(18, "BK", 150 + p.fuse_lead_mm), net="GND_PWR", name="PWR_GND")
    h.connect(gnd, uno["GND"], Wire(22, "BK", 120), net="GND_PWR", current_A=0.05, name="UNO_GND")      # the common ground
    for tag, pin, sv, length in (("YAW", "D9", servos[0], 150), ("SH", "D10", servos[1], p.lead_mm),
                                 ("EL", "D11", servos[2], p.lead_mm + 60)):
        h.connect(v5, sv["V+"], Wire(22, "RD", length), net="V5", current_A=p.servo_a, name=f"{tag}_V")
        h.connect(sv["GND"], gnd, Wire(22, "BK", length), net="GND_PWR", current_A=p.servo_a, name=f"{tag}_GND")
        h.connect(uno[pin], sv["SIG"], Wire(22, "OG", length), net=f"SIG_{tag}", name=f"{tag}_SIG")
    return h


def verify(h, p, c):
    c.at_most("5 V rail drop to the elbow servo (%)", net_stats(h)["V5"].drop_pct, 3.0)
    c.at_most("fuse lead from the supply (mm)", p.fuse_lead_mm, 175)
