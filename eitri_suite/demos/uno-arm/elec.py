"""The arm's electronics: an Arduino Uno (on USB) drives three MG996R servos straight from D9, D10 and D11; the servos run from their
own 5 V 10 A supply, whose minus joins the Uno's ground. Three MG996R at their 2.5 A stall are 7.5 A, 75 % of the supply.

The parts are named as the harness connectors (wiring.py), so each harness pin is a circuit pin; COUPLING says which mechanism joint
each servo drives and where the Uno sits in the CAD model (its anchor, the four standoffs that carry it, the M3 screws, the clearance
the design keeps around the board)."""
from eitri.electronics import *

PARAMS = dict(psu_A=10.0)

COUPLING = dict(
    harness="wiring.py", firmware="fw.py", mechanism="mech.py", cad="model.py",
    connectors={"UNO": "UNO", "PSU": "PSU", "SERVO_YAW": "SERVO_YAW", "SERVO_SH": "SERVO_SH", "SERVO_EL": "SERVO_EL"},
    actuators={"yaw": "SERVO_YAW", "shoulder": "SERVO_SH", "elbow": "SERVO_EL"},
    mounts={"UNO": dict(anchor="UNO", stand_in="controller", carrier=["standoffs"], screw="M3", clearance_mm=1.0)},
)


def build(p):
    c = Circuit("Arm electronics", "Arduino Uno, three MG996R servos on their own 5 V supply")
    uno = c.board("UNO", "uno", powered="usb")
    psu = c.supply("PSU", 5.0, current_A=p.psu_A, part_number="PSU-5V-10A")
    c.net("V5", psu["+"])
    c.net("GND", uno["GND"], psu["-"])                       # a common ground; the + rails stay apart
    for name, pin, tag in (("SERVO_YAW", "D9", "YAW"), ("SERVO_SH", "D10", "SH"), ("SERVO_EL", "D11", "EL")):
        s = c.servo(name, "MG996R")
        c.gpio(uno[pin], "out")
        c.net(f"SIG_{tag}", uno[pin], s["SIG"])
        c.connect(s["V+"], psu["+"])
        c.connect(s["GND"], uno["GND"])
    return c
