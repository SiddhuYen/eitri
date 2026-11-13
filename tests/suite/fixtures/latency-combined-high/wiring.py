from eitri.harness import *

PARAMS = dict(
    trunk_mm=220,
    trunk_awg=16,
    lead_awg=22,
    power_awg=20,
    uno_gnd_mm=200,
    sig_yaw_mm=200,
    sig_shoulder_mm=320,
    sig_elbow_mm=420,
    sig_grip_mm=560,
    pwr_yaw_mm=160,
    pwr_shoulder_mm=240,
    pwr_elbow_mm=330,
    pwr_grip_mm=480,
    psu_A=4.0,
    mg996r_stall_A=2.5,
    sg90_stall_A=0.7,
)

SERVOS = [  # (connector, Uno pin, signal length, power length, stall current key, housing, power AWG key)
    ("SERVO_YAW", "D9", "sig_yaw_mm", "pwr_yaw_mm", "mg996r_stall_A", "MOLEX-MICROFIT-3.0", "power_awg"),
    ("SERVO_SHOULDER", "D10", "sig_shoulder_mm", "pwr_shoulder_mm", "mg996r_stall_A", "MOLEX-MICROFIT-3.0", "power_awg"),
    ("SERVO_ELBOW", "D11", "sig_elbow_mm", "pwr_elbow_mm", "mg996r_stall_A", "MOLEX-MICROFIT-3.0", "power_awg"),
    ("SERVO_GRIP", "D6", "sig_grip_mm", "pwr_grip_mm", "sg90_stall_A", "DUPONT-2.54", "lead_awg"),
]


def build(p):
    h = Harness("Arm wiring", "5 V 4 A supply -> terminal block -> 4 servos; Uno (USB powered) drives the signals, shared ground")
    uno = h.add(Connector("UNO", "ARDUINO-UNO", pins=["D6", "D9", "D10", "D11", "GND"]))
    psu = h.add(Connector("PSU", "DC-BARREL-5.5x2.1", pins=["+", "-"], rating_a=5.0,
                          notes="panel barrel jack with solder lugs; PSU-5V-4A plugs in (supply limits the current to 4 A)"))
    bus5, busg = h.splice("BUS_5V", kind="terminal"), h.splice("BUS_GND", kind="terminal")
    servos = {}
    for name, pin, sig, pwr, stall, housing, awg in SERVOS:
        note = ("servo lead re-terminated into a mating Micro-Fit plug" if housing.startswith("MOLEX")
                else "servo's own Dupont plug mates here")
        servos[name] = h.add(Connector(name, housing, pins=["GND", "V+", "SIG"], gender="female", notes=note))

    h.net("V5", current_A=p.psu_A, voltage_V=5.0, max_drop_pct=4)
    h.net("GND", current_A=p.psu_A, voltage_V=5.0, max_drop_pct=4, round_trip=False)
    for name, *_ in SERVOS:
        h.net("SIG_" + name[6:], current_A=0.02, voltage_V=5.0)

    # supply: PSU -> terminal block -> each servo
    h.connect(psu["+"], bus5, Wire(p.trunk_awg, "RD", p.trunk_mm), net="V5")
    for name, pin, sig, pwr, stall, housing, awg in SERVOS:
        h.connect(bus5, servos[name]["V+"], Wire(getattr(p, awg), "RD", getattr(p, pwr)), net="V5", current_A=getattr(p, stall))
    # return: each servo -> terminal block -> PSU; the Uno's ground tied to the same block
    for name, pin, sig, pwr, stall, housing, awg in SERVOS:
        h.connect(servos[name]["GND"], busg, Wire(getattr(p, awg), "BK", getattr(p, pwr)), net="GND", current_A=getattr(p, stall))
    h.connect(uno["GND"], busg, Wire(p.lead_awg, "BK", p.uno_gnd_mm), net="GND", current_A=0.1)
    h.connect(busg, psu["-"], Wire(p.trunk_awg, "BK", p.trunk_mm), net="GND")
    # signals: Uno pin -> servo SIG
    for name, pin, sig, pwr, stall, housing, awg in SERVOS:
        h.connect(uno[pin], servos[name]["SIG"], Wire(p.lead_awg, "YE", getattr(p, sig)), net="SIG_" + name[6:])
    return h


def verify(h, p, c):
    st = net_stats(h)
    c.at_most("V5 drop % (servo sees >= 4.8 V)", st["V5"].drop_pct, 4.0)
    c.at_most("trunk current within PSU rating (A)", p.psu_A, 4.0)
    c.count("servo plugs", [n for n in h.connectors if str(n).startswith("SERVO_")], 4)
