# Clamp fixture (pneumatic, PLC-controlled)

## Requirement
Locate a 2 kg machined block against a fixed stop, clamp it down with at least 250 N for machining, release it.
One part every 15 s (4 cycles/min). Shop air 7 bar, this station may draw 150 L/min. Control: 24 V DC PLC.

## Design
- C1 pusher, Festo DSNU-20-50-P-A (ISO 6432 round cylinder, magnetic piston): pushes the part 50 mm against the stop, 60 N.
- C2 clamp, SMC CDQ2B32-20DZ (CQ2 compact, magnet): clamps down 20 mm stroke, 250 N needed, 434 N usable at 6 bar.
- M1 SMC SS5Y3-20-02-Q manifold base (2 stations, common supply and exhaust, 1/8 ports; AN10-01 silencers on its R and S).
- V1 SMC SY3120-5LZ-C6 on station 1, 5/2 single solenoid, spring return: the pusher retracts when unpowered.
- V2 SMC SY3220-5LZ-C6 on station 2, 5/2 double solenoid: the clamp HOLDS (stays closed or open) if power drops.
- 4 x SMC AS2201F-01-06 meter-out speed controllers screwed into the cylinder ports.
- SMC TU0604 6x4 polyurethane tube (8 bar max at 20 C); an SMC AW20-02BG filter regulator set to 6 bar (regulates 0.5-8.5 bar).
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

## Package (deliverables/, rebuilt 2026-11-12 with core pneu-0.8.1)
- clamp_fixture.drawing.pdf / .svg: circuit diagram with device tags (-MM, -QM, -BG, -AZ, -XM, -RZ), sensors, coil signals,
  parts table and title block (ticket T-0001, revision A; "approved by" left for the customer).
- clamp_fixture.sequence.pdf / .svg / .md: displacement-step diagram and step table; ESTIMATED cycle time 9.2 s (a lower bound:
  no pressure build-up, acceleration or PLC scan) against 15 s.
- clamp_fixture.io.md / .csv (7 points, coils with manifold station), .bom, .cylinders, schematic.
- Build: 87 pass, 1 warn (flow data estimated for the tubes and the AW20, which publishes no C), 0 fail, 0 not run.

## Port numbers (corrected 2026-11-12)
Valve ports are the ISO 5599 numbers SMC prints on the SY body (SY_EU.pdf PDF p.14): 1 (P), 2 (B), 3 (EB), 4 (A), 5 (EA). At rest
the spring connects 1-2 and 4-5; coil 14 connects 1-4 and 2-3. Plumbing: V1 port 2 (B) to the pusher's rod end (fed at rest: the
pusher stays back on a power cut), port 4 (A) to its cap end (PUSHER_EXTEND); V2 port 4 (A) to the clamp's cap end (CLAMP_CLOSE,
coil 14), port 2 (B) to its rod end (CLAMP_OPEN, coil 12). The deliverables before this date labelled the valve ports "A" (meaning
port 2) and "B" (port 4): read against SMC's marking that is backwards. Use the drawing of 2026-11-12 or later.
