"""The desk arm's wiring on an Arduino Uno: five servo signals (D9-D13 side: D9, D10, D11, D6, D5); the servos' power from a 5 V supply
through two bus blocks and a fuse per servo, the Uno's ground on the same bus. Every connector, splice and fuse has an anchor in model.py.

Overcurrent protection (core eitri.harness.protection): the supply is a Mean Well LRS-100-5 (the BOM's PSU-5V-18A). Its overload
protection is taken as the LRS series' "110 ~ 150% rated output power", hiccup mode (the LRS-50/75 datasheets; the LRS-100 sheet was
not re-read, and 150 % is the higher, conservative end): set as low as 4.5 V it can push 150 % x 90 W / 4.5 V = 30 A into a fault.
No single fuse can both stay under the 22 AWG leads' 7 A and carry the five servos, so each lead has its own Littelfuse MINI 5 A
fuse beside the bus. The 18 AWG trunk and the 22 AWG fuse feeds keep the unprotected run at 165 mm (ABYC E-11: 175 mm)."""
from eitri.harness import *

PARAMS = dict(servo_a=1.0, small_a=0.4)
PSU_LIMIT_A = 30.0         # LRS series OVER LOAD 110 ~ 150 % rated output power: 150 % x 90 W / 4.5 V (LRS-100-5, ADJ. RANGE 4.5 ~ 5.5 V)
# ASSUMPTION: Mean Well publishes no short-circuit current for the LRS series (hiccup mode); taken as the overload figure. The MINI
# fuses interrupt 1000 A (Littelfuse 297 datasheet), 33 x that.
PSU_ISC_A = 30.0


def build(p):
    h = Harness("Desk arm wiring", "Arduino Uno, three MG996R and two SG90 servos, fused 5 V bus", ambient_C=40)
    uno = h.add(Connector("UNO", "DUPONT-2.54", pins={"D9": "D9", "D10": "D10", "D11": "D11", "D6": "D6", "D5": "D5", "GND": "GND"}, gender="female"))
    psu = h.add(Connector("PSU", "BARE", pins={"+": "5V", "-": "GND"}, part_number="LRS-100-5"))
    h.source(psu["+"], 5.5, max_current_A=PSU_LIMIT_A, short_circuit_A=PSU_ISC_A, kind="psu", part="Mean Well LRS-100-5")
    names = ("SERVO_YAW", "SERVO_SH", "SERVO_EL", "SERVO_WR", "SERVO_GRIP")
    servos = [h.add(Connector(n, "DUPONT-2.54", pins={"GND": "GND", "V+": "V+", "SIG": "PWM"}, gender="female")) for n in names]
    v5 = h.splice("BUS_5V", kind="terminal")
    gnd = h.splice("BUS_GND", kind="terminal")
    if hasattr(h, "mcu_pin"):          # core 0.8.0 (wires-plc): the signal nets are graded against the Uno's I/O pin (Arduino pinout)
        for pin in ("D9", "D10", "D11", "D6", "D5"):
            h.mcu_pin(uno[pin], "uno")
    total = 3 * p.servo_a + 2 * p.small_a
    h.net("V5", current_A=total, voltage_V=5.0)
    h.net("GND_PWR", current_A=total, voltage_V=5.0, round_trip=False)
    h.connect(psu["+"], v5, Wire(18, "RD", 125), net="V5", name="PWR_V")
    h.connect(gnd, psu["-"], Wire(18, "BK", 125), net="GND_PWR", name="PWR_GND")
    h.connect(gnd, uno["GND"], Wire(22, "BK", 150), net="GND_PWR", current_A=0.05, name="UNO_GND")
    for tag, pin, sv, length, amps in (("YAW", "D9", servos[0], 200, p.servo_a), ("SH", "D10", servos[1], 260, p.servo_a), ("EL", "D11", servos[2], 380, p.servo_a),
                                       ("WR", "D6", servos[3], 480, p.small_a), ("GRIP", "D5", servos[4], 540, p.small_a)):
        h.net(f"SIG_{tag}", current_A=0.001, voltage_V=5.0, signal=True)       # driven by the Uno's pins
        fz = h.add(Fuse(f"F_{tag}", "MINI", 5))
        h.connect(v5, fz["LINE"], Wire(22, "RD", 40), net="V5", current_A=amps, name=f"{tag}_F")      # the fuse block feed
        h.connect(fz["LOAD"], sv["V+"], Wire(22, "RD", length), net="V5", current_A=amps, name=f"{tag}_V")
        h.connect(sv["GND"], gnd, Wire(22, "BK", length), net="GND_PWR", current_A=amps, name=f"{tag}_GND")
        h.connect(uno[pin], sv["SIG"], Wire(22, "OG", length), net=f"SIG_{tag}", name=f"{tag}_SIG")
    return h
