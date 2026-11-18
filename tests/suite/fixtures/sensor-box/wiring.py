from eitri.harness import *

PARAMS = dict(gauge=24, lead_mm=110, branch_mm=130, bus_mm=110)


def build(p):
    h = Harness("Climate box leads", "Nano to the OLED and the BME280 over one 3.3 V I2C bus")
    nano = h.add(Connector("NANO", "ARDUINO-NANO", pins=["3V3", "GND", "A4", "A5"]))
    oled = h.add(Connector("OLED", "DUPONT-2.54", pins=["GND", "VIN", "SCL", "SDA"], gender="female"))
    bme = h.add(Connector("BME280", "DUPONT-2.54", pins=["VIN", "GND", "SCK", "SDI"], gender="female"))
    b33, bgnd, bsda, bscl = (h.splice(n, kind="solder") for n in ("BUS_3V3", "BUS_GND", "BUS_SDA", "BUS_SCL"))

    h.source(nano["3V3"], 3.3, max_current_A=0.05, kind="board", part="Arduino Nano 3V3 pin, 50 mA (Arduino Nano pinout)")
    i_oled, i_bme = 0.020, 0.0012
    h.net("3V3", current_A=i_oled + i_bme, voltage_V=3.3)
    h.net("GND", current_A=i_oled + i_bme, voltage_V=3.3, round_trip=False)
    h.net("SDA", current_A=0.003, voltage_V=3.3, signal=True)
    h.net("SCL", current_A=0.003, voltage_V=3.3, signal=True)

    w = lambda col, mm: Wire(p.gauge, col, mm)
    h.connect(nano["3V3"], b33, w("RD", p.bus_mm), net="3V3")
    h.connect(b33, oled["VIN"], w("RD", p.branch_mm), net="3V3", current_A=i_oled)
    h.connect(b33, bme["VIN"], w("RD", p.branch_mm), net="3V3", current_A=i_bme)
    h.connect(oled["GND"], bgnd, w("BK", p.branch_mm), net="GND", current_A=i_oled)
    h.connect(bme["GND"], bgnd, w("BK", p.branch_mm), net="GND", current_A=i_bme)
    h.connect(bgnd, nano["GND"], w("BK", p.bus_mm), net="GND")
    h.connect(nano["A4"], bsda, w("BU", p.bus_mm), net="SDA")
    h.connect(bsda, oled["SDA"], w("BU", p.branch_mm), net="SDA")
    h.connect(bsda, bme["SDI"], w("BU", p.branch_mm), net="SDA")
    h.connect(nano["A5"], bscl, w("YE", p.bus_mm), net="SCL")
    h.connect(bscl, oled["SCL"], w("YE", p.branch_mm), net="SCL")
    h.connect(bscl, bme["SCK"], w("YE", p.branch_mm), net="SCL")
    h.mcu_pin(nano["A4"], board="nano")
    h.mcu_pin(nano["A5"], board="nano")
    return h


def verify(h, p, c):
    c.at_most("3V3 drop %", net_stats(h)["3V3"].drop_pct, 1.0)
