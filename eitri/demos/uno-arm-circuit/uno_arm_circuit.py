"""Uno arm electronics: an Arduino Uno drives four MG996R servos through a PCA9685 breakout on I2C, lights a status LED, reads an
end-stop switch, and switches a 12 V gripper solenoid through a relay that a logic-level MOSFET drives.

The servos and the relay coil run from their own 5 V supply (only its ground joins the Uno's); the relay has a flyback diode across
its coil. CONFIGS["bad"] is the classic first attempt: the servos on the Uno's 5V pin and no flyback diode. It fails on purpose
(the 5V pin's budget and the diode), and the default configuration is the fix."""
from eitri.electronics import *

PARAMS = dict(servos=4, servo_supply="psu", flyback=True, psu_A=10.0, led_ohm=330, pullup_ohm=10000, gate_ohm=220, solenoid_A=0.5)
CONFIGS = {"bad": dict(servo_supply="uno", flyback=False)}


def build(p):
    c = Circuit("Uno arm electronics", "Uno + PCA9685 + MG996R servos, status LED, end-stop, relay-switched gripper solenoid")
    uno = c.board("UNO", "uno", powered="usb")
    c.net("5V", uno["5V"])
    c.net("GND", uno["GND"])

    # the servo driver: logic from the Uno, I2C on A4 / A5 (its own 10 kOhm pull-ups to VCC)
    pwm = c.module("PWM", "PCA9685")
    c.connect(pwm["VCC"], "UNO.5V")
    c.connect(pwm["GND"], "UNO.GND")
    c.net("SDA", uno["A4"], pwm["SDA"])
    c.net("SCL", uno["A5"], pwm["SCL"])

    # servo power: a 5 V supply of its own, or (the bad configuration) the Uno's 5V pin
    if p.servo_supply == "psu":
        psu = c.supply("PSU5", 5.0, current_A=p.psu_A, part_number="5 V 10 A adapter")
        c.net("SERVO_5V", psu["+"], pwm["V+"])
        c.connect(psu["-"], "UNO.GND")                       # a common ground; the + rails stay apart
    else:
        c.connect(pwm["V+"], "UNO.5V")
    for i in range(int(p.servos)):
        s = c.servo(f"S{i + 1}", "MG996R")
        c.connect(s["V+"], pwm["V+"])
        c.connect(s["GND"], "UNO.GND")
        c.connect(s["SIG"], pwm[f"PWM{i}"])

    # status LED on D13: (5 V - 1.85 V) / 330 Ohm = 9.5 mA
    c.gpio(uno["D13"], "out")
    r1, led = c.resistor("R1", p.led_ohm), c.led("LED1", "red")
    c.net("LED_A", uno["D13"], r1["1"])
    c.connect(r1["2"], led["A"])
    c.connect(led["K"], "UNO.GND")

    # end-stop: a switch to ground on D2, pulled up to 5 V
    c.gpio(uno["D2"], "in")
    sw, r2 = c.switch("SW1", "limit switch"), c.resistor("R2", p.pullup_ohm)
    c.net("ENDSTOP", uno["D2"], sw["1"], r2["2"])
    c.connect(r2["1"], "UNO.5V")
    c.connect(sw["2"], "UNO.GND")

    # gripper: D7 -> gate resistor -> IRLZ44N, relay coil from the servo rail to the drain, contacts switch a 12 V solenoid
    k1, q1 = c.relay("K1", "SRD-05VDC-SL-C"), c.mosfet("Q1", "IRLZ44N")
    r3, r4 = c.resistor("R3", p.gate_ohm), c.resistor("R4", 10000)
    c.gpio(uno["D7"], "out")
    c.connect(uno["D7"], r3["1"])
    c.net("GATE", r3["2"], q1["G"], r4["1"])
    c.connect(r4["2"], "UNO.GND")                            # holds the MOSFET off while the Uno boots
    c.connect(k1["COIL1"], pwm["V+"])
    c.net("COIL_LOW", k1["COIL2"], q1["D"])
    c.connect(q1["S"], "UNO.GND")
    if p.flyback:
        d1 = c.diode("D1", "1N4148")                         # across the coil, cathode to the supply side
        c.connect(d1["K"], k1["COIL1"])
        c.connect(d1["A"], q1["D"])
    psu12 = c.supply("PSU12", 12.0, current_A=1.0, part_number="12 V 1 A adapter")
    sol = c.solenoid("SOL1", 12.0, p.solenoid_A, part_number="12 V gripper solenoid")
    c.net("12V", psu12["+"], k1["COM"])
    c.connect(k1["NO"], sol["+"])
    c.connect(sol["-"], "UNO.GND")
    c.connect(psu12["-"], "UNO.GND")
    return c


def verify(c, p, chk):
    an = analyze_dc(c)
    chk.between("status LED current (mA)", an.part_current("LED1") * 1000, 5, 15)
