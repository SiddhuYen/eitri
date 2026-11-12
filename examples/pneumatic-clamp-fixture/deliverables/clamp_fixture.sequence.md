# Sequence: Clamp fixture

| step | moves | coils (on / pulse) | ends on | est. time (s) | cumulative (s) |
|---|---|---|---|---|---|
| 1 push | C1+ | PUSHER_EXTEND on | B2 | 0.350 | 0.350 |
| 2 clamp | C2+ | PUSHER_EXTEND on, CLAMP_CLOSE pulse | B4 | 0.264 | 0.614 |
| 3 pusher back | C1- | none | B1 | 0.350 | 0.964 |
| 4 machine | wait 8 s | none | time | 8.000 | 8.964 |
| 5 unclamp | C2- | CLAMP_OPEN pulse | B3 | 0.264 | 9.228 |

**Estimated cycle time: 9.23 s** (an ESTIMATE: a lower bound).

Assumptions:
- valve response from the datasheet (SMC SY catalogue p.2, at 0.5 MPa, JIS B 8375-1981)
- mean speed = the set speed where a speed controller throttles the stroke, else the ISO 6358 open-path estimate
- not included: pressure build-up before the piston moves, load acceleration, controller scan and output delays, cushioning
- sensor switch-on time from its datasheet (Festo SMT-8M-A 1.3 ms); a sensor without one: 1.3 ms (ASSUMPTION)

Start: C1 retracted, C2 retracted. End: C1 retracted, C2 retracted.
