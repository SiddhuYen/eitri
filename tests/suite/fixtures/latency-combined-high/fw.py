from eitri.firmware import *

PARAMS = dict(step_ms=12, dwell_ms=400, pause_ms=3000, min_us=500, max_us=2500)

SERVO_ORDER = ["YAW", "SHOULDER", "ELBOW", "GRIP"]

# servo angles the sketch writes (joint = servo - servo_zero); yaw +40 = pick side, -40 = place side
SEQUENCE = [
    ("home",        dict(YAW=90,  SHOULDER=90, ELBOW=45, GRIP=90)),
    ("open",        dict(YAW=90,  SHOULDER=90, ELBOW=45, GRIP=115)),
    ("over_pick",   dict(YAW=130, SHOULDER=45, ELBOW=27, GRIP=115)),
    ("lower_pick",  dict(YAW=130, SHOULDER=31, ELBOW=27, GRIP=115)),
    ("grab",        dict(YAW=130, SHOULDER=31, ELBOW=27, GRIP=87)),
    ("lift",        dict(YAW=130, SHOULDER=45, ELBOW=27, GRIP=87)),
    ("over_place",  dict(YAW=50,  SHOULDER=45, ELBOW=27, GRIP=87)),
    ("lower_place", dict(YAW=50,  SHOULDER=31, ELBOW=27, GRIP=87)),
    ("release",     dict(YAW=50,  SHOULDER=31, ELBOW=27, GRIP=115)),
    ("retreat",     dict(YAW=50,  SHOULDER=45, ELBOW=27, GRIP=115)),
    ("home",        dict(YAW=90,  SHOULDER=90, ELBOW=45, GRIP=90)),
]

COUPLING = dict(
    harness="wiring.py",
    pins={"YAW": "UNO.D9", "SHOULDER": "UNO.D10", "ELBOW": "UNO.D11", "GRIP": "UNO.D6"},
    mechanism="mech.py",
    joints={"YAW": "yaw", "SHOULDER": "shoulder", "ELBOW": "elbow", "GRIP": "grip"},
    servo_zero={"YAW": 90, "SHOULDER": 0, "ELBOW": 135, "GRIP": 90},
)

SKETCH = r'''
// Desktop arm: pick a 20 mm block on one side and place it on the other.
// Joints move one at a time in small steps so only one MG996R accelerates at once
// (keeps the 5 V / 4 A supply out of current limit).
#include <Servo.h>
#include "eitri_pins.h"

const int STEP_MS = {step_ms};
const int DWELL_MS = {dwell_ms};
const unsigned long PAUSE_MS = {pause_ms};
const int MIN_US = {min_us};
const int MAX_US = {max_us};

const uint8_t N_JOINTS = 4;          // YAW, SHOULDER, ELBOW, GRIP
const uint8_t N_STEPS = {n_steps};
const uint8_t STEPS[N_STEPS][N_JOINTS] = {
{table}
};
const char* const NAMES[N_STEPS] = { {names} };

Servo yawServo, shoulderServo, elbowServo, gripServo;
Servo* const servos[N_JOINTS] = { &yawServo, &shoulderServo, &elbowServo, &gripServo };
uint8_t current[N_JOINTS];

void moveJoint(uint8_t j, uint8_t target) {
  while (current[j] != target) {
    current[j] += (target > current[j]) ? 1 : -1;
    servos[j]->write(current[j]);
    delay(STEP_MS);
  }
}

void goStep(uint8_t k) {
  Serial.print(F("step "));
  Serial.println(NAMES[k]);
  // raise before swinging: the shoulder goes first when it lifts, last when it lowers
  bool lifting = STEPS[k][1] > current[1];
  if (lifting) moveJoint(1, STEPS[k][1]);
  moveJoint(0, STEPS[k][0]);
  moveJoint(2, STEPS[k][2]);
  if (!lifting) moveJoint(1, STEPS[k][1]);
  moveJoint(3, STEPS[k][3]);
  delay(DWELL_MS);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  for (uint8_t j = 0; j < N_JOINTS; j++) {
    current[j] = STEPS[0][j];
    servos[j]->write(current[j]);      // set the first pulse before attaching: no jump at power-up
  }
  // attach one at a time to stagger the servos' start-up current
  yawServo.attach(PIN_YAW, MIN_US, MAX_US);
  delay(300);
  shoulderServo.attach(PIN_SHOULDER, MIN_US, MAX_US);
  delay(300);
  elbowServo.attach(PIN_ELBOW, MIN_US, MAX_US);
  delay(300);
  gripServo.attach(PIN_GRIP, MIN_US, MAX_US);
  Serial.println(F("arm ready"));
}

void loop() {
  for (uint8_t k = 0; k < N_STEPS; k++) goStep(k);
  delay(PAUSE_MS);
}
'''


def sketch(p):
    rows = ",\n".join("  {" + ", ".join(str(a[s]) for s in SERVO_ORDER) + "}" for _, a in SEQUENCE)
    names = ", ".join('"%s"' % n for n, _ in SEQUENCE)
    code = SKETCH
    for k, v in dict(step_ms=p.step_ms, dwell_ms=p.dwell_ms, pause_ms=p.pause_ms, min_us=p.min_us, max_us=p.max_us,
                     n_steps=len(SEQUENCE), table=rows, names=names).items():
        code = code.replace("{%s}" % k, str(v))
    return code


def build(p):
    fw = Firmware("Arm controller", board="uno", description="pick-and-place cycle for the 4-servo desktop arm")
    fw.servo("YAW", pin="D9", min_us=p.min_us, max_us=p.max_us, power="external", current_A=2.5)
    fw.servo("SHOULDER", pin="D10", min_us=p.min_us, max_us=p.max_us, power="external", current_A=2.5)
    fw.servo("ELBOW", pin="D11", min_us=p.min_us, max_us=p.max_us, power="external", current_A=2.5)
    fw.servo("GRIP", pin="D6", min_us=p.min_us, max_us=p.max_us, power="external", current_A=0.7)
    fw.serial(115200)
    fw.source("arm.ino", sketch(p))
    return fw


def verify(fw, p, c):
    c.true("every step sets all four servos", all(set(a) == set(SERVO_ORDER) for _, a in SEQUENCE))
    c.true("cycle starts and ends at home", SEQUENCE[0][1] == SEQUENCE[-1][1])
    c.true("gripper closes on the block before lifting",
           [n for n, _ in SEQUENCE].index("grab") < [n for n, _ in SEQUENCE].index("lift"))
