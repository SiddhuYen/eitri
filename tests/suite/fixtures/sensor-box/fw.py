from eitri.firmware import *

# The room climate box's firmware, cut down for the tests: the same board, pins and loads (eitri never uploads).
PARAMS = dict(update_ms=2000)

COUPLING = dict(harness="wiring.py", pins={"SDA": "NANO.A4", "SCL": "NANO.A5"})

SKETCH = r'''
#include <Wire.h>
#include "eitri_pins.h"

void setup() {
  Wire.begin();
  digitalWrite(SDA, LOW);   // the AVR's internal 5 V pull-ups off: the bus is pulled to 3.3 V by the OLED module
  digitalWrite(SCL, LOW);
}

void loop() {
  delay({update_ms});
}
'''


def build(p):
    fw = Firmware("Climate box", board="nano")
    fw.pin("A4", "inout", "i2c_sda", net="SDA")
    fw.pin("A5", "out", "i2c_scl", net="SCL")
    fw.load("OLED", supply="3V3", current_A=0.020)
    fw.load("BME280", supply="3V3", current_A=0.0012)
    fw.source("climate.ino", SKETCH.replace("{update_ms}", str(int(p.update_ms))))
    return fw
