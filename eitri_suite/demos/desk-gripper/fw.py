"""The desk arm's firmware on an Arduino Uno: five servos, and the pick-and-place cycle that moves a bottle cap (SEQUENCE).
COUPLING maps each pin to the harness pin that carries it and each servo to the mechanism joint it drives."""
from eitri.firmware import *

PARAMS = dict(step_ms=12)

COUPLING = dict(harness="wiring.py", pins={"YAW": "UNO.D9", "SHOULDER": "UNO.D10", "ELBOW": "UNO.D11", "WRIST": "UNO.D6", "GRIP": "UNO.D5"},
                mechanism="mech.py", joints={"YAW": "yaw", "SHOULDER": "shoulder", "ELBOW": "elbow", "WRIST": "wrist", "GRIP": "jaw"},
                servo_zero={"GRIP": 60})        # the jaw: servo 60 = closed on the cap, 80 = 20 mm open

OPEN, SHUT = 80, 60
SEQUENCE = [("home", dict(YAW=90, SHOULDER=135, ELBOW=45, WRIST=0, GRIP=SHUT)),
            ("above pick", dict(YAW=55, SHOULDER=129, ELBOW=21, WRIST=30, GRIP=OPEN)),
            ("lower", dict(YAW=55, SHOULDER=114, ELBOW=21, WRIST=45, GRIP=OPEN)),
            ("grab", dict(YAW=55, SHOULDER=114, ELBOW=21, WRIST=45, GRIP=SHUT)),
            ("lift", dict(YAW=55, SHOULDER=129, ELBOW=21, WRIST=30, GRIP=SHUT)),
            ("above place", dict(YAW=105, SHOULDER=129, ELBOW=21, WRIST=30, GRIP=SHUT)),
            ("descend", dict(YAW=105, SHOULDER=114, ELBOW=21, WRIST=45, GRIP=SHUT)),
            ("release", dict(YAW=105, SHOULDER=114, ELBOW=21, WRIST=45, GRIP=OPEN)),
            ("up", dict(YAW=105, SHOULDER=129, ELBOW=21, WRIST=30, GRIP=OPEN)),
            ("home", dict(YAW=90, SHOULDER=135, ELBOW=45, WRIST=0, GRIP=SHUT))]
NETS = ("YAW", "SHOULDER", "ELBOW", "WRIST", "GRIP")


def build(p):
    fw = Firmware("Desk arm pick and place", board="uno", description="five servos move a bottle cap", powered="usb")
    for net, pin in zip(NETS, ("D9", "D10", "D11", "D6", "D5")):
        fw.servo(net, pin, power="external")
    rows = ",\n".join("  {" + ", ".join(str(s[n]) for n in NETS) + "}" for _, s in SEQUENCE)
    fw.source("arm.ino", f"""#include <Servo.h>
#include "eitri_pins.h"
Servo yaw, shoulder, elbow, wrist, grip;
Servo *sv[5] = {{&yaw, &shoulder, &elbow, &wrist, &grip}};
const uint8_t SEQ[][5] = {{
{rows}
}};
const uint8_t N = sizeof(SEQ) / sizeof(SEQ[0]);
uint8_t cur[5];

void moveTo(const uint8_t *to) {{
  for (int k = 1; k <= 60; k++) {{
    float e = k / 60.0; e = e < 0.5 ? 4 * e * e * e : 1 - pow(-2 * e + 2, 3) / 2;
    for (int i = 0; i < 5; i++) sv[i]->write(cur[i] + (to[i] - cur[i]) * e);
    delay({int(p.step_ms)});
  }}
  for (int i = 0; i < 5; i++) cur[i] = to[i];
}}

void setup() {{
  yaw.attach(PIN_YAW); shoulder.attach(PIN_SHOULDER); elbow.attach(PIN_ELBOW); wrist.attach(PIN_WRIST); grip.attach(PIN_GRIP);
  for (int i = 0; i < 5; i++) {{ cur[i] = SEQ[0][i]; sv[i]->write(cur[i]); }}
}}

void loop() {{
  for (uint8_t i = 0; i < N; i++) {{ moveTo(SEQ[i]); delay(400); }}
}}
""")
    return fw
