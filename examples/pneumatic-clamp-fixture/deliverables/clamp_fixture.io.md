# I/O list: Clamp fixture

Control voltage: 24 V DC.

| tag | I/O | signal | device | function | part number | V | mA | connector | location | terminal | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DI01 | DI | CLAMP_CLOSED_POS | B4 | C2 extended | SMC D-M9B | 24 | 2.5-40 | grommet, 2 cores 0.15 mm2 (brown +, blue -), 0.5 m lead | on C2 |  | 2-wire: in series with the PLC input, which must draw 2.5-40 mA when on and stay off at 0.8 mA leakage; the switch drops up to 4 V; sinking (current-sourcing switch to +) input |
| DI02 | DI | CLAMP_OPEN_POS | B3 | C2 retracted | SMC D-M9B | 24 | 2.5-40 | grommet, 2 cores 0.15 mm2 (brown +, blue -), 0.5 m lead | on C2 |  | 2-wire: in series with the PLC input, which must draw 2.5-40 mA when on and stay off at 0.8 mA leakage; the switch drops up to 4 V; sinking (current-sourcing switch to +) input |
| DI03 | DI | PART_AT_STOP | B2 | C1 extended | FESTO SMT-8M-A-PS-24V-E-0,3-M8D | 24 | <= 100 | M8x1 A-coded 3-pin plug on 0.3 m cable (1 +, 3 -, 4 signal) | on C1 |  | PNP output to a sinking PLC input; max 100 mA; drop 1.5 V |
| DI04 | DI | PUSHER_HOME | B1 | C1 retracted | FESTO SMT-8M-A-PS-24V-E-0,3-M8D | 24 | <= 100 | M8x1 A-coded 3-pin plug on 0.3 m cable (1 +, 3 -, 4 signal) | on C1 |  | PNP output to a sinking PLC input; max 100 mA; drop 1.5 V |
| DO01 | DO | CLAMP_CLOSE | V2 coil 14 | 5/2 valve V2 shifts to position 2 (P-B, A-R) | SMC SY3220-5LZ-C6 | 24 | 16.7 | L plug connector x 2 (lead wire 300 mm) | M1 station 2 |  | 0.4 W at 24 V DC (rated); solenoid return |
| DO02 | DO | CLAMP_OPEN | V2 coil 12 | 5/2 valve V2 shifts back to position 1 (P-A, B-S) | SMC SY3220-5LZ-C6 | 24 | 16.7 | L plug connector x 2 (lead wire 300 mm) | M1 station 2 |  | 0.4 W at 24 V DC (rated); solenoid return |
| DO03 | DO | PUSHER_EXTEND | V1 coil 14 | 5/2 valve V1 shifts to position 2 (P-B, A-R) | SMC SY3120-5LZ-C6 | 24 | 16.7 | L plug connector (lead wire 300 mm) | M1 station 1 |  | 0.4 W at 24 V DC (rated); spring return: de-energised = position 1 |

All coils on at once: 50 mA from the control supply (sensors' own supply current not included).

The terminal column is filled from the model (terminal=); blank means the panel builder assigns it.
