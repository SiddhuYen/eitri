"""The arm's firmware on an Arduino Uno: three servos on D9, D10, D11. COUPLING maps each pin to the harness connector pin that carries it."""
from eitri.firmware import *

PARAMS = dict(step_ms=15)

COUPLING = dict(harness="uno_wiring.py", pins={"YAW": "UNO.D9", "SHOULDER": "UNO.D10", "ELBOW": "UNO.D11"},
                mechanism="mech.py", joints={"YAW": "yaw", "SHOULDER": "shoulder", "ELBOW": "elbow"})     # servo -> joint (90 deg = the joint's zero)

# the pick-and-place cycle the sketch runs: the servo angles it writes, step by step (checked against the servos and the joints' limits)
SEQUENCE = [("home", dict(YAW=90, SHOULDER=90, ELBOW=90)), ("above pick", dict(YAW=60, SHOULDER=120, ELBOW=140)),
            ("pick", dict(YAW=60, SHOULDER=135, ELBOW=150)), ("lift", dict(YAW=60, SHOULDER=110, ELBOW=130)),
            ("above place", dict(YAW=120, SHOULDER=120, ELBOW=140)), ("place", dict(YAW=120, SHOULDER=135, ELBOW=150)),
            ("home", dict(YAW=90, SHOULDER=90, ELBOW=90))]


def build(p):
    fw = Firmware("Arm servos", board="uno", description="sweep three servos")
    fw.servo("YAW", "D9")
    fw.servo("SHOULDER", "D10")
    fw.servo("ELBOW", "D11")
    fw.source("arm.ino", f"""#include <Servo.h>
#include "eitri_pins.h"
Servo yaw, shoulder, elbow;

void setup() {{
  yaw.attach(PIN_YAW);
  shoulder.attach(PIN_SHOULDER);
  elbow.attach(PIN_ELBOW);
}}

void loop() {{
  for (int a = 60; a <= 120; a++) {{
    yaw.write(a); shoulder.write(a); elbow.write(a);
    delay({int(p.step_ms)});
  }}
}}
""")
    return fw
