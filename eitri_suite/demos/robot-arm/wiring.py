"""The arm's wiring: ESP32 -> PCA9685 over I2C, three servos on the PCA9685's channels, 5 V in from the supply.

Overcurrent protection (core eitri.harness.protection): the supply is a Mean Well LRS-50-5 (the BOM's PSU-5V-10A); its overload
protection is "110 ~ 150% rated output power", hiccup mode (LRS-50 datasheet), so set as low as 4.5 V it can push 16.7 A into a
fault, more than the 18 AWG feed's 16 A. A Littelfuse ATO 5 A fuse 60 mm from the supply protects the feed, and through the PCA9685's
V+ bus the 22 AWG servo leads (7 A) too: 5 A <= 7 A, and the 3 A steady load is within its derated continuous load at 40 C.
The 3V3 rail is the ESP32 DevKit's own regulator output (an AMS1117-3.3, "Output Current of 1A" in its datasheet), declared as a board
source; 24 AWG (3.5 A) carries more than that regulator can deliver."""
from eitri.harness import *

PARAMS = dict(servo_a=1.0, lead_mm=260)
PSU_LIMIT_A = 16.7         # Mean Well LRS-50-5: 150 % x 50 W / 4.5 V (OVER LOAD 110 ~ 150 %, ADJ. RANGE 4.5 ~ 5.5 V; LRS-50 datasheet)
# ASSUMPTION: Mean Well publishes no short-circuit current for the LRS series (hiccup-mode short-circuit protection, no figure); the
# hiccup protection is taken to limit a fault to the overload figure. The ATO fuse interrupts 1000 A (Littelfuse 287), 60 x that.
PSU_ISC_A = 16.7
# The ESP32 DevKit's 3V3 regulator (AMS1117-3.3): its datasheet gives "Output Current of 1A" and no current limit; 1.5 A is taken as
# the most it can push into a fault (ASSUMPTION, 1.5 x its rating), still under the 24 AWG wires' 3.5 A.
REG_LIMIT_A = 1.5


def build(p):
    h = Harness("Robot arm wiring", "ESP32 + PCA9685 + 3 servos, fused 5 V", ambient_C=40)
    esp = h.add(Connector("ESP32", "DUPONT-2.54", pins={"3V3": "3V3", "GND": "GND", "21": "SDA", "22": "SCL"}, gender="female"))
    i2c = h.add(Connector("PCA_I2C", "DUPONT-2.54", pins={"VCC": "VCC", "GND": "GND", "SDA": "SDA", "SCL": "SCL"}, gender="male"))
    psu = h.add(Connector("PSU", "BARE", pins={"+": "5V", "-": "GND"}, part_number="LRS-50-5"))
    fuse = h.add(Fuse("F1", "ATO", 5))
    h.source(psu["+"], 5.5, max_current_A=PSU_LIMIT_A, short_circuit_A=PSU_ISC_A, kind="psu", part="Mean Well LRS-50-5")
    h.source(esp["3V3"], 3.3, max_current_A=REG_LIMIT_A, kind="board", part="ESP32 DevKit 3V3 (AMS1117-3.3)")
    rail = h.add(Connector("PCA_PWR", "BARE", pins={"V+": "V+", "GND": "GND"}))
    chans = [h.add(Connector(f"PCA_CH{i}", "DUPONT-2.54", pins={"GND": "GND", "V+": "V+", "SIG": "PWM"}, gender="male")) for i in range(3)]
    servos = [h.add(Connector(n, "DUPONT-2.54", pins={"GND": "GND", "V+": "V+", "SIG": "PWM"}, gender="female")) for n in ("SERVO_YAW", "SERVO_SH", "SERVO_EL")]
    if hasattr(h, "sensor_output"):    # core 0.8.0 (wires-plc): what drives each signal net, with its data sheet's output rating
        for pin in ("21", "22"):       # ESP32 GPIO21/22 (I2C): 28 mA, its low-level sink figure, the lower of its two drive figures
            h.sensor_output(esp[pin], max_output_A=0.028, notes="ESP32 GPIO, Espressif ESP32 Series Datasheet v5.3 Table 5-3")
        for ch in chans:               # PCA9685 LEDn: "totem pole with a 25 mA sink, 10 mA source capability at 5 V": 10 mA, the lower
            h.sensor_output(ch["SIG"], max_output_A=0.010, notes="PCA9685 LEDn output, NXP PCA9685 data sheet Rev. 4")

    h.net("3V3", current_A=0.05, voltage_V=3.3)
    h.net("GND_LOGIC", current_A=0.05, voltage_V=3.3, round_trip=False)
    h.net("SDA", current_A=0.001, voltage_V=3.3, signal=True)
    h.net("SCL", current_A=0.001, voltage_V=3.3, signal=True)
    for tag in ("YAW", "SH", "EL"):
        h.net(f"SIG_{tag}", current_A=0.001, voltage_V=5.0, signal=True)      # driven by the PCA9685's outputs
    h.net("V5", current_A=3 * p.servo_a, voltage_V=5.0)               # the servo rail: three servos, p.servo_a each (a design figure, ESTIMATE)
    h.net("GND_PWR", current_A=3 * p.servo_a, voltage_V=5.0, round_trip=False)

    h.connect(esp["3V3"], i2c["VCC"], Wire(24, "OG", 200), net="3V3", name="I2C_3V3")
    h.connect(esp["GND"], i2c["GND"], Wire(24, "BK", 200), net="GND_LOGIC", name="I2C_GND")
    h.connect(esp["21"], i2c["SDA"], Wire(24, "GN", 200), net="SDA", name="I2C_SDA")
    h.connect(esp["22"], i2c["SCL"], Wire(24, "YE", 200), net="SCL", name="I2C_SCL")
    h.connect(psu["+"], fuse["LINE"], Wire(18, "RD", 60), net="V5", name="PWR_F")         # within 175 mm of the supply (ABYC E-11)
    h.connect(fuse["LOAD"], rail["V+"], Wire(18, "RD", 340), net="V5", name="PWR_V")
    h.connect(rail["GND"], psu["-"], Wire(18, "BK", 400), net="GND_PWR", name="PWR_GND")
    for tag, ch, sv, length in (("YAW", chans[0], servos[0], 120), ("SH", chans[1], servos[1], p.lead_mm), ("EL", chans[2], servos[2], p.lead_mm + 40)):
        h.connect(ch["V+"], sv["V+"], Wire(22, "RD", length), net="V5", current_A=p.servo_a, name=f"{tag}_V")
        h.connect(sv["GND"], ch["GND"], Wire(22, "BK", length), net="GND_PWR", current_A=p.servo_a, name=f"{tag}_GND")
        h.connect(ch["SIG"], sv["SIG"], Wire(22, "OG", length), net=f"SIG_{tag}", name=f"{tag}_SIG")
    return h
