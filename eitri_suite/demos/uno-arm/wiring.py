"""The arm's wiring on an Arduino Uno: three servo signals from D9, D10 and D11; the servos' power from a 5 V supply through two bus
blocks (terminal splices) and a fuse per servo, with the Uno's ground on the same bus. Every connector, splice and fuse has an anchor
in model.py.

Power, sized for the three MG996R at their 2.5 A stall (elec.py):
- the servo plugs are re-terminated into Molex Micro-Fit 3.0 (43645-0300 housing; 43030-0039 terminals for the 18 AWG power leads,
  43030-0001 for the 22 AWG signal): 7 A per pin with 2 powered circuits (Molex PS-43045), where a 2.54 mm Dupont contact takes
  22 AWG at most and is rated 3 A (83 % at stall);
- 12 AWG from the supply to the bus blocks, 18 AWG to each servo, so the voltage at the farthest servo (fuse drop included) stays
  above the MG996R's 4.8 V minimum.

Overcurrent protection (core eitri.harness.protection): the supply is a Mean Well LRS-50-5 (the BOM's PSU-5V-10A). Its overload
protection is "110 ~ 150% rated output power", hiccup mode (LRS-50 datasheet), so set as low as 4.5 V it can push 150 % x 50 W /
4.5 V = 16.7 A into a fault: more than an 18 AWG lead's 16 A. One fuse cannot protect the leads and carry three stalled servos
within its continuous-load derating, so each servo lead has its own Littelfuse MINI 5 A fuse in a fuse block beside the bus (5 A
<= 16 A; 2.5 A within the derated continuous load at 40 C). The 12 AWG trunk is rated above 16.7 A, and the 18 AWG fuse feeds keep
the unprotected run from the supply at 150 mm, inside the 175 mm ABYC E-11 limit."""
from eitri.harness import *

PARAMS = dict(servo_a=2.5, lead_mm=320)
FUSE_A = 5                 # Littelfuse MINI 297, one per servo lead
AMBIENT_C = 40             # the air around the fuse block inside the base, C
PSU_LIMIT_A = 16.7         # Mean Well LRS-50-5 (LRS-50 datasheet): OVER LOAD 110 ~ 150 % rated output power, hiccup; 150 % x 50 W / 4.5 V
PSU_MAX_V = 5.5            # the highest the supply can be set to (ADJ. RANGE 4.5 ~ 5.5 V)
# The supply's prospective short-circuit current. ASSUMPTION: Mean Well publishes none for the LRS series (it lists "short circuit"
# protection, hiccup mode, with no current); the hiccup protection is taken to limit a fault to the overload trip figure above. The
# MINI fuse's 1000 A interrupting rating (Littelfuse 297 datasheet) is 60 x that, so the verdict does not hang on the assumption.
PSU_ISC_A = 16.7


def build(p):
    h = Harness("Robot arm wiring", "Arduino Uno, three MG996R servos, fused 5 V bus", ambient_C=AMBIENT_C)
    uno = h.add(Connector("UNO", "DUPONT-2.54", pins={"D9": "D9", "D10": "D10", "D11": "D11", "GND": "GND"}, gender="female"))
    psu = h.add(Connector("PSU", "BARE", pins={"+": "5V", "-": "GND"}, part_number="LRS-50-5"))
    servos = [h.add(Connector(n, "MOLEX-MICROFIT-3.0", pins={"GND": "GND", "V+": "V+", "SIG": "PWM"}, gender="female", part_number="43645-0300"))
              for n in ("SERVO_YAW", "SERVO_SH", "SERVO_EL")]
    v5 = h.splice("BUS_5V", kind="terminal")
    gnd = h.splice("BUS_GND", kind="terminal")
    fuses = [h.add(Fuse(f"F_{t}", "MINI", FUSE_A)) for t in ("YAW", "SH", "EL")]
    if hasattr(h, "mcu_pin"):          # core 0.8.0 (wires-plc): the signal nets are graded against the Uno's I/O pin (Arduino pinout)
        for pin in ("D9", "D10", "D11"):
            h.mcu_pin(uno[pin], "uno")
    h.source(psu["+"], PSU_MAX_V, max_current_A=PSU_LIMIT_A, short_circuit_A=PSU_ISC_A, kind="psu", part="Mean Well LRS-50-5")

    h.net("V5", current_A=3 * p.servo_a, voltage_V=5.0)                 # the servo rail: three servos at their 2.5 A stall (the circuit's figure, elec.py)
    h.net("GND_PWR", current_A=3 * p.servo_a, voltage_V=5.0, round_trip=False)
    for tag in ("YAW", "SH", "EL"):
        h.net(f"SIG_{tag}", current_A=0.001, voltage_V=5.0, signal=True)     # driven by the Uno's pins

    h.connect(psu["+"], v5, Wire(12, "RD", 120), net="V5", name="PWR_V")
    h.connect(gnd, psu["-"], Wire(12, "BK", 120), net="GND_PWR", name="PWR_GND")
    h.connect(gnd, uno["GND"], Wire(22, "BK", 120), net="GND_PWR", current_A=0.05, name="UNO_GND")      # the common ground
    for tag, pin, sv, fz, length in (("YAW", "D9", servos[0], fuses[0], 150), ("SH", "D10", servos[1], fuses[1], p.lead_mm),
                                     ("EL", "D11", servos[2], fuses[2], p.lead_mm + 60)):
        h.connect(v5, fz["LINE"], Wire(18, "RD", 30), net="V5", current_A=p.servo_a, name=f"{tag}_F")      # the fuse block's feed
        h.connect(fz["LOAD"], sv["V+"], Wire(18, "RD", length), net="V5", current_A=p.servo_a, name=f"{tag}_V")
        h.connect(sv["GND"], gnd, Wire(18, "BK", length), net="GND_PWR", current_A=p.servo_a, name=f"{tag}_GND")
        h.connect(uno[pin], sv["SIG"], Wire(22, "OG", length), net=f"SIG_{tag}", name=f"{tag}_SIG")
    return h
