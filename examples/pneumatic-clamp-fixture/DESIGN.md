# Clamp fixture (pneumatic, PLC-controlled)

## Requirement
Locate a 2 kg machined block against a fixed stop, clamp it down with at least 250 N for machining, release it.
One part every 15 s (4 cycles/min). Shop air 7 bar, this station may draw 150 L/min. Control: 24 V DC PLC.

## Design
- C1 pusher, Festo DSNU-20-50-P-A (ISO 6432 round cylinder, magnetic piston): pushes the part 50 mm against the stop, 60 N.
- C2 clamp, SMC CDQ2B32-20DZ (CQ2 compact, magnet): clamps down 20 mm stroke, 250 N needed, 434 N usable at 6 bar.
- V1 SMC SY3120-5LZ-C6, 5/2 single solenoid, spring return: the pusher retracts when unpowered.
- V2 SMC SY3220-5LZ-C6, 5/2 double solenoid: the clamp HOLDS (stays closed or open) if power drops.
- 4 x SMC AS2201F-01-06 meter-out speed controllers in the cylinder ports; 4 x SMC AN05-M5 silencers.
- SMC TU0604 6x4 polyurethane tube (8 bar max at 20 C); an SMC AW20 filter-regulator set to 6 bar.
- Sensors: Festo SMT-8M-A (PNP, M8) on C1 at both ends (mounting kit SMBR-8-20), SMC D-M9B (2-wire) on C2 at both ends.

## Sequence (for the PLC programmer; signal names as in the I/O list)
1. Wait for PUSHER_HOME and CLAMP_OPEN_POS; operator loads the part.
2. PUSHER_EXTEND on; wait PART_AT_STOP.
3. CLAMP_CLOSE pulse (V2 is double solenoid: a pulse is enough, it stays); wait CLAMP_CLOSED_POS.
4. PUSHER_EXTEND off (the clamp holds the part); wait PUSHER_HOME. Machine.
5. CLAMP_OPEN pulse; wait CLAMP_OPEN_POS. Unload.
Never energise CLAMP_CLOSE and CLAMP_OPEN together.

## Assumptions (not from a datasheet)
- 10 % friction allowance on cylinder force; load ratio limits from SMC's sizing guide (moving 0.5, static clamping 0.7).
- Tube and FRL flow conductances are estimates (SMC publishes no C for TU tube or a single AW20 figure); the build warns.
- Moving masses: part 2 kg + pusher head 0.15 kg; clamp pad 0.3 kg. Fittings are counted generically in the BOM.
