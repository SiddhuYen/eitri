from eitri.harness import *

# lead lengths from the CAD layout (bus block at the base's back edge, servos up the arm) + slack for the arm's motion
PARAMS = dict(
    trunk_mm=150,                                         # PSU screw terminal -> bus terminal block
    yaw_mm=180, shoulder_mm=250, elbow_mm=350, grip_mm=450,   # bus -> each servo plug
    sig_extra_mm=50,                                      # Uno header -> servo is a little longer than bus -> servo
    uno_gnd_mm=120,
    servo_stall_A=2.5,                                    # MG996R stall at 6 V (catalog), worst case per servo
)

SERVOS = ["YAW", "SHOULDER", "ELBOW", "GRIP"]
PINS = {"YAW": "D9", "SHOULDER": "D10", "ELBOW": "D11", "GRIP": "D6"}


def build(p):
    h = Harness("Desk arm wiring", "5 V supply -> bus -> 4x MG996R; signals from the Uno; shared ground")
    lens = {"YAW": p.yaw_mm, "SHOULDER": p.shoulder_mm, "ELBOW": p.elbow_mm, "GRIP": p.grip_mm}
    n = len(SERVOS)
    total = n * p.servo_stall_A

    psu = h.add(Connector("PSU", "BARE", pins=["+", "-"], part_number="PSU-5V-4A via barrel-jack screw terminal",
                          notes="5 V supply, screwed into a 5.5x2.1 barrel jack adapter"))
    uno = h.add(Connector("UNO", "ARDUINO-UNO", pins=["D9", "D10", "D11", "D6", "GND"], gender="male"))
    plugs = {s: h.add(Connector(f"SERVO_{s}", "DUPONT-2.54", pins={"1": "GND", "2": "V+", "3": "SIG"}, gender="female",
                                notes="MG996R lead (JR/Futaba order: brown GND, red V+, orange SIG)"))
             for s in SERVOS}
    bus_p = h.splice("BUS_5V", kind="terminal")
    bus_n = h.splice("BUS_GND", kind="terminal")

    # worst case: every servo stalled at once; MG996R works down to 4.8 V -> 4 % of 5 V
    h.net("5V_SERVO", current_A=total, voltage_V=5, max_drop_pct=4)
    h.net("GND", current_A=total, voltage_V=5, round_trip=False)
    for s in SERVOS:
        h.net(f"SIG_{s}", current_A=0.01, voltage_V=5)

    # supply: PSU -> bus -> servos
    h.connect(psu["+"], bus_p, Wire(16, "RD", p.trunk_mm), net="5V_SERVO")
    for s in SERVOS:
        h.connect(bus_p, plugs[s]["V+"], Wire(22, "RD", lens[s]), net="5V_SERVO", current_A=p.servo_stall_A)
    # return: servos -> bus -> PSU; the Uno's ground joins the bus (shared reference for the signals)
    for s in SERVOS:
        h.connect(plugs[s]["GND"], bus_n, Wire(22, "BK", lens[s]), net="GND", current_A=p.servo_stall_A)
    h.connect(uno["GND"], bus_n, Wire(22, "BK", p.uno_gnd_mm), net="GND", current_A=0.05)
    h.connect(bus_n, psu["-"], Wire(16, "BK", p.trunk_mm), net="GND")
    # signals: Uno -> servos
    for s in SERVOS:
        h.connect(uno[PINS[s]], plugs[s]["SIG"], Wire(22, "OG", lens[s] + p.sig_extra_mm), net=f"SIG_{s}")
    return h


def verify(h, p, c):
    st = net_stats(h)
    c.at_most("servo supply drop % (all stalled)", st["5V_SERVO"].drop_pct, 4.0)
    c.at_least("servo supply volts at the far servo", 5.0 * (1 - st["5V_SERVO"].drop_pct / 100), 4.8)
    names = [str(getattr(x, "name", x)) for x in h.connectors]
    c.count("servo plugs", [n for n in names if n.startswith("SERVO_")], 4)
