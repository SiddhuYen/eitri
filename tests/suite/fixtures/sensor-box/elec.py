from eitri.electronics import *

PARAMS = dict()

COUPLING = dict(
    harness="wiring.py", firmware="fw.py", cad="model.py",
    mounts={"NANO": dict(anchor="NANO", stand_in="controller", carrier=["base"], screw="M1.6", clearance_mm=1.0),
            "OLED": dict(anchor="OLED", stand_in="oled", carrier=["lid"], screw="M2.5", clearance_mm=1.0),
            "BME280": dict(anchor="BME280", stand_in="bme280", carrier=["base"], screw="M2.5", clearance_mm=0.5)},
)


def build(p):
    c = Circuit("Climate box", "Nano reads a BME280 and drives an SSD1306 OLED over a 3.3 V I2C bus")
    nano = c.board("NANO", "nano", powered="usb")
    oled = c.module("OLED", "SSD1306", address=0x3C)
    bme = c.module("BME280", "BME280-2652", address=0x77)   # Adafruit 2652 (STEMMA QT)
    c.net("3V3", nano["3V3"], oled["VIN"], bme["VIN"])      # OLED on 3.3 V: its pull-ups then sit at 3.3 V too
    c.net("GND", nano["GND"], oled["GND"], bme["GND"])
    c.net("SDA", nano["A4"], oled["SDA"], bme["SDI"])
    c.net("SCL", nano["A5"], oled["SCL"], bme["SCK"])
    c.i2c("SDA", internal_pullups=False)                   # the sketch turns Wire's 5 V pull-ups off after Wire.begin()
    return c


def verify(c, p, chk):
    chk.at_most("3V3 rail draw (A)", 0.020 + 0.0012, 0.050)
