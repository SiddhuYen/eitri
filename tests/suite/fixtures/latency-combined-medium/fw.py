from eitri.firmware import *

PARAMS = dict(step_ms=20, move_steps=50, dwell_ms=400, cycle_pause_ms=2000)

# servo angles written by the sketch (servo = joint + 90); joint angles from the mechanism's IK for the
# pick (150, -60) and place (60, 130) targets, grasp 4 mm above the block centre. GRIP 115 = open 25 deg,
# 87 = closed with 3 deg of squeeze on the 20 mm block.
SERVOS = ["YAW", "SHOULDER", "ELBOW", "GRIP"]
SEQUENCE = [
    ("home",        dict(YAW=90,  SHOULDER=130, ELBOW=30, GRIP=115)),
    ("above_pick",  dict(YAW=72,  SHOULDER=115, ELBOW=27, GRIP=115)),
    ("descend",     dict(YAW=72,  SHOULDER=99,  ELBOW=35, GRIP=115)),
    ("grab",        dict(YAW=72,  SHOULDER=99,  ELBOW=35, GRIP=87)),
    ("lift",        dict(YAW=72,  SHOULDER=115, ELBOW=27, GRIP=87)),
    ("above_place", dict(YAW=160, SHOULDER=123, ELBOW=8,  GRIP=87)),
    ("lower",       dict(YAW=160, SHOULDER=106, ELBOW=15, GRIP=87)),
    ("release",     dict(YAW=160, SHOULDER=106, ELBOW=15, GRIP=115)),
    ("retreat",     dict(YAW=160, SHOULDER=123, ELBOW=8,  GRIP=115)),
    ("home",        dict(YAW=90,  SHOULDER=130, ELBOW=30, GRIP=115)),
]

COUPLING = dict(
    harness="wiring.py",
    pins={"YAW": "UNO.D9", "SHOULDER": "UNO.D10", "ELBOW": "UNO.D11", "GRIP": "UNO.D6"},
    mechanism="mech.py",
    joints={"YAW": "yaw", "SHOULDER": "shoulder", "ELBOW": "elbow", "GRIP": "grip"},
)

SKETCH = r'''
// Desk arm: pick a 20 mm block and place it, forever. 4x MG996R on an external 5 V supply.
#include <Servo.h>
#include "eitri_pins.h"

const int STEP_MS = {step_ms};
const int MOVE_STEPS = {move_steps};
const int DWELL_MS = {dwell_ms};
const int CYCLE_PAUSE_MS = {cycle_pause_ms};

const uint8_t N_SERVO = 4;               // YAW, SHOULDER, ELBOW, GRIP
const uint8_t N_STEP = {n_step};
const uint8_t SEQ[N_STEP][N_SERVO] = {
{table}
};
const char* const NAMES[N_STEP] = { {names} };

Servo yawServo, shoulderServo, elbowServo, gripServo;
Servo* const servos[N_SERVO] = { &yawServo, &shoulderServo, &elbowServo, &gripServo };
int pos[N_SERVO];

void moveTo(const uint8_t* target) {
  int start[N_SERVO];
  for (uint8_t j = 0; j < N_SERVO; j++) start[j] = pos[j];
  for (int s = 1; s <= MOVE_STEPS; s++) {         // all joints together, linear in time
    for (uint8_t j = 0; j < N_SERVO; j++) {
      int a = start[j] + (int)(((long)(target[j] - start[j]) * s) / MOVE_STEPS);
      servos[j]->write(a);
      pos[j] = a;
    }
    delay(STEP_MS);
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  for (uint8_t j = 0; j < N_SERVO; j++) {
    pos[j] = SEQ[0][j];
    servos[j]->write(pos[j]);                      // set the home angle before the first pulse
  }
  yawServo.attach(PIN_YAW);
  shoulderServo.attach(PIN_SHOULDER);
  elbowServo.attach(PIN_ELBOW);
  gripServo.attach(PIN_GRIP);
  delay(1000);
}

void loop() {
  for (uint8_t i = 0; i < N_STEP; i++) {
    Serial.println(NAMES[i]);
    moveTo(SEQ[i]);
    delay(DWELL_MS);
  }
  delay(CYCLE_PAUSE_MS);
}
'''


def build(p):
    fw = Firmware("Desk arm firmware", board="uno", description="pick-and-place cycle for a 4-servo desk arm")
    for s in SERVOS:
        pin = COUPLING["pins"][s].split(".")[1]
        fw.servo(s, pin=pin, min_us=544, max_us=2400, power="external", current_A=2.5)
    fw.serial(115200)
    table = ",\n".join("  {" + ", ".join(str(a[s]) for s in SERVOS) + "}" for _, a in SEQUENCE)
    names = ", ".join(f'"{n}"' for n, _ in SEQUENCE)
    code = (SKETCH.replace("{step_ms}", str(int(p.step_ms))).replace("{move_steps}", str(int(p.move_steps)))
            .replace("{dwell_ms}", str(int(p.dwell_ms))).replace("{cycle_pause_ms}", str(int(p.cycle_pause_ms)))
            .replace("{n_step}", str(len(SEQUENCE))).replace("{table}", table).replace("{names}", names))
    fw.source("desk_arm.ino", code)
    return fw


def verify(fw, p, c):
    c.count("servos declared", [s for s in SERVOS if fw.get_pin(s)], 4)
    c.true("every step angle within 0..180", all(0 <= a <= 180 for _, d in SEQUENCE for a in d.values()))
    c.at_most("one move takes (s)", p.step_ms * p.move_steps / 1000, 1.5)
