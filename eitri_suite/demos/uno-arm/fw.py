"""The arm's firmware on an Arduino Uno: three servos on D9, D10, D11, and the pick-and-place cycle the sketch runs (SEQUENCE).
COUPLING maps each pin to the harness connector pin that carries it, and each servo to the mechanism joint it drives."""
from eitri.firmware import *

PARAMS = dict(step_ms=12)

COUPLING = dict(harness="wiring.py", pins={"YAW": "UNO.D9", "SHOULDER": "UNO.D10", "ELBOW": "UNO.D11"},
                mechanism="mech.py", joints={"YAW": "yaw", "SHOULDER": "shoulder", "ELBOW": "elbow"},
                servo_zero={"ELBOW": 45})           # servo 90 deg = the joint's zero, except the elbow: its horn is set 45 deg back so it folds to 120

# the pick-and-place cycle: the servo angles the sketch writes, step by step (checked against the servos and the joints' limits)
SEQUENCE = [("home", dict(YAW=90, SHOULDER=90, ELBOW=45)), ("above pick", dict(YAW=60, SHOULDER=75, ELBOW=79)),
            ("pick", dict(YAW=60, SHOULDER=60, ELBOW=79)), ("lift", dict(YAW=60, SHOULDER=75, ELBOW=79)),
            ("above place", dict(YAW=85, SHOULDER=75, ELBOW=79)), ("place", dict(YAW=85, SHOULDER=60, ELBOW=79)),
            ("home", dict(YAW=90, SHOULDER=90, ELBOW=45))]


def build(p):
    fw = Firmware("Arm pick and place", board="uno", description="three servos run a pick-and-place cycle", powered="usb")
    fw.servo("YAW", "D9", power="external")
    fw.servo("SHOULDER", "D10", power="external")
    fw.servo("ELBOW", "D11", power="external")
    steps = ",\n".join("  {%d, %d, %d}" % (s["YAW"], s["SHOULDER"], s["ELBOW"]) for _, s in SEQUENCE)
    fw.source("arm.ino", f"""#include <Servo.h>
#include "eitri_pins.h"
Servo yaw, shoulder, elbow;
const uint8_t SEQ[][3] = {{
{steps}
}};
const uint8_t N = sizeof(SEQ) / sizeof(SEQ[0]);
uint8_t cur[3] = {{90, 90, 45}};

void moveTo(const uint8_t *to) {{
  for (int k = 1; k <= 60; k++) {{                      // eased, 60 sub-steps per move
    float e = k / 60.0; e = e < 0.5 ? 4 * e * e * e : 1 - pow(-2 * e + 2, 3) / 2;
    yaw.write(cur[0] + (to[0] - cur[0]) * e);
    shoulder.write(cur[1] + (to[1] - cur[1]) * e);
    elbow.write(cur[2] + (to[2] - cur[2]) * e);
    delay({int(p.step_ms)});
  }}
  for (int i = 0; i < 3; i++) cur[i] = to[i];
}}

void setup() {{
  yaw.attach(PIN_YAW); shoulder.attach(PIN_SHOULDER); elbow.attach(PIN_ELBOW);
  moveTo(SEQ[0]);
}}

void loop() {{
  for (uint8_t i = 0; i < N; i++) {{ moveTo(SEQ[i]); delay(400); }}
}}
""")
    return fw
