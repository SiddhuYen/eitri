"""Servo sweep on an Arduino Uno: a servo on D9 sweeps while a button on D2 is held; the LED on D13 shows it running; Serial
prints each sweep. The pinout is the single source of truth: the sketch uses the PIN_ constants from eitri_pins.h."""
from eitri.firmware import *

PARAMS = dict(step_ms=15, sweep_deg=180)
CONFIGS = {"nano": dict(step_ms=15)}

SKETCH = r'''
#include <Servo.h>
#include "eitri_pins.h"

Servo pan;
const int STEP_MS = {step_ms};
const int SWEEP = {sweep_deg};

void setup() {
  Serial.begin(SERIAL_BAUD);
  pan.attach(PIN_PAN);
  pinMode(PIN_START, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
}

void sweep(int from, int to) {
  int dir = from < to ? 1 : -1;
  for (int a = from; a != to + dir; a += dir) {
    pan.write(a);
    delay(STEP_MS);
  }
}

void loop() {
  if (digitalRead(PIN_START) == LOW) {
    digitalWrite(PIN_LED, HIGH);
    Serial.println("sweep");
    sweep(0, SWEEP);
    sweep(SWEEP, 0);
    digitalWrite(PIN_LED, LOW);
  }
}
'''


def build(p):
    fw = Firmware("Servo sweep", board="uno", description="sweep a servo while a button is held")
    fw.servo("PAN", pin="D9")
    fw.limit_switch("START", pin="D2")
    fw.pin("D13", "out", "digital_out", net="LED")
    fw.serial(115200)
    fw.source("servo_sweep.ino", SKETCH.replace("{step_ms}", str(p.step_ms)).replace("{sweep_deg}", str(p.sweep_deg)))
    return fw


def verify(fw, p, c):
    c.at_most("sweep step (ms)", p.step_ms, 50)
