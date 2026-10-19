# Benchmark results

Headline: **baseline**: 20/22 tasks solved (mean score 0.99), 228 s and $0.44 per task on average; follow-ups 2/2 solved; **eitri**: 22/22 tasks solved (mean score 1.00), 89 s and $0.24 per task on average; follow-ups 2/2 solved; **eitri_v1**: 21/22 tasks solved (mean score 0.99), 53 s and $0.77 per task on average.

Scored by the geometry checkers in `bench/tasks.py` on the produced STEP. Score = fraction of checks passed; ✓ = all passed. Times and costs are per task as reported by the CLI. Both systems ran the same model (Claude Opus 5.5 via Claude Code, effort high) on the same prompts; the baseline is headless Claude Code with the text-to-cad plugin.

These runs were made when eitri was still called cadcode (the `name_tag` task keeps its original text, CADCODE); only the name has changed since.

| task | baseline | eitri | eitri_v1 |
|---|---|---|---|
| plate | 1.00 ✓ (53s, $0.28) | 1.00 ✓ (15s, $0.18) | 1.00 ✓ (18s, $0.39) |
| l_bracket | 1.00 ✓ (45s, $0.31) | 1.00 ✓ (122s, $0.18) | 1.00 ✓ (39s, $0.45) |
| enclosure | 1.00 ✓ (402s, $0.70) | 1.00 ✓ (100s, $0.29) | 1.00 ✓ (112s, $1.31) |
| flange | 1.00 ✓ (31s, $0.29) | 1.00 ✓ (32s, $0.10) | 1.00 ✓ (25s, $0.71) |
| gear | 1.00 ✓ (52s, $0.35) | 1.00 ✓ (33s, $0.09) | 1.00 ✓ (18s, $0.44) |
| shaft | 1.00 ✓ (48s, $0.37) | 1.00 ✓ (64s, $0.23) | 1.00 ✓ (33s, $0.42) |
| phone_stand | 0.88 (240s, $1.00) | 1.00 ✓ (377s, $1.10) | 0.88 (126s, $0.61) |
| cable_clip | 0.86 (46s, $0.32) | 1.00 ✓ (68s, $0.20) | 1.00 ✓ (115s, $1.28) |
| hinge | 1.00 ✓ (127s, $0.57) | 1.00 ✓ (123s, $0.40) | 1.00 ✓ (57s, $0.52) |
| knob | 1.00 ✓ (57s, $0.40) | 1.00 ✓ (62s, $0.16) | 1.00 ✓ (74s, $1.41) |
| bottle_cap | 1.00 ✓ (116s, $0.58) | 1.00 ✓ (403s, $0.57) | 1.00 ✓ (97s, $1.58) |
| heatsink | 1.00 ✓ (38s, $0.31) | 1.00 ✓ (20s, $0.08) | 1.00 ✓ (31s, $0.52) |
| pulley | 1.00 ✓ (97s, $0.45) | 1.00 ✓ (32s, $0.11) | 1.00 ✓ (34s, $0.56) |
| tray | 1.00 ✓ (42s, $0.32) | 1.00 ✓ (25s, $0.10) | 1.00 ✓ (29s, $0.31) |
| rpi_case | 1.00 ✓ (44s, $0.33) | 1.00 ✓ (52s, $0.18) | 1.00 ✓ (100s, $1.87) |
| nema17_mount | 1.00 ✓ (77s, $0.39) | 1.00 ✓ (36s, $0.12) | 1.00 ✓ (49s, $0.40) |
| arduino_plate | 1.00 ✓ (44s, $0.34) | 1.00 ✓ (18s, $0.07) | 1.00 ✓ (28s, $0.29) |
| name_tag | 1.00 ✓ (42s, $0.30) | 1.00 ✓ (107s, $0.37) | 1.00 ✓ (25s, $0.40) |
| hex_bolt | 1.00 ✓ (2647s, $0.88) | 1.00 ✓ (94s, $0.23) | 1.00 ✓ (69s, $2.32) |
| slotted_bracket | 1.00 ✓ (40s, $0.32) | 1.00 ✓ (20s, $0.08) | 1.00 ✓ (25s, $0.33) |
| bearing_housing | 1.00 ✓ (170s, $0.34) | 1.00 ✓ (33s, $0.11) | 1.00 ✓ (36s, $0.38) |
| gear_pair | 1.00 ✓ (561s, $0.45) | 1.00 ✓ (113s, $0.21) | 1.00 ✓ (31s, $0.53) |
| **total** | **0.99 avg, 20/22 solved** (228s, $0.44 avg) | **1.00 avg, 22/22 solved** (89s, $0.24 avg) | **0.99 avg, 21/22 solved** (53s, $0.77 avg) |

## Follow-up edits (same session, second request)

| task | baseline | eitri | eitri_v1 |
|---|---|---|---|
| plate | 1.00 ✓ (248s, $0.53) | 1.00 ✓ (34s, $0.31) | - |
| enclosure | 1.00 ✓ (50s, $0.93) | 1.00 ✓ (176s, $0.68) | - |

## Method notes

- Times are the Claude Code CLI's own per-turn duration (`duration_ms`) for runs that recorded it, else wall time around the session. Wall time additionally includes CLI/MCP start-up and, during this run, long network retries on a flaky connection; both systems were affected the same way.
- Costs are the CLI's list-price equivalents (tokens x API prices); the runs used a subscription login, so they consumed plan usage rather than money.
- `eitri` is the final configuration. Four of its tasks (enclosure, name_tag, knob, rpi_case) were re-run once after product fixes made during the pass: a STEP-writer bug (offset-curve faces dropped on export, which also broke the enclosure follow-up) and a thin-wall warning raised from 0.8 to 1.5 mm. Their first attempts under the final configuration scored 1.00/0.75(follow-up), 0.62, 0.89 and 0.91. `eitri_v1` is the first configuration (inline MCP images, see bench/README.md), untouched.
- The baseline ran once per task with no re-runs; its two misses are wall-thickness checks (1.84 mm and 1.32 mm where >= 2 and 1.6 mm were requested) that its agent never measured.
- Checkers were validated against library-built reference solutions (`tests/test_bench_checkers.py`); checker fixes made during the run were applied to both systems by re-scoring the saved STEP files.

## Blind pairwise judge (Claude, vision)

Both STEP files rendered with the same renderer, shown in random A/B order with the request; scores 1-10 for fidelity / engineering / overall.

| task | baseline fid/eng/overall | eitri fid/eng/overall | preferred |
|---|---|---|---|
| phone_stand | 8/8/8 | 8/7/7 | baseline |
| plate | 10/9/9 | 10/9/9 | tie |
| l_bracket | 9/8/8 | 8/8/8 | baseline |
| flange | 9/7/8 | 9/9/9 | eitri |
| gear | 8/6/6 | 8/6/6 | tie |
| shaft | 8/8/8 | 8/8/8 | tie |
| cable_clip | 8/7/7 | 8/8/8 | eitri |
| hinge | 8/7/7 | 8/8/8 | eitri |
| bottle_cap | 5/6/5 | 8/7/7 | eitri |
| heatsink | 10/8/9 | 10/8/9 | tie |
| pulley | 8/7/7 | 8/7/7 | tie |
| tray | 9/8/8 | 9/8/8 | tie |
| nema17_mount | 9/7/8 | 8/8/8 | eitri |
| arduino_plate | 9/8/9 | 9/8/9 | tie |
| hex_bolt | 9/8/8 | 9/8/8 | tie |
| slotted_bracket | 9/8/8 | 9/9/9 | eitri |
| bearing_housing | 9/8/8 | 9/8/8 | tie |
| gear_pair | 9/7/8 | 9/8/8 | eitri |
| enclosure | 8/7/7 | 7/6/6 | baseline |
| name_tag | 9/7/7 | 9/9/9 | eitri |
| knob | 7/7/7 | 7/8/8 | eitri |
| rpi_case | 9/8/8 | 9/8/8 | tie |
| **mean** | 8.5/7.5/7.6 | 8.5/7.9/8.0 | baseline 3, eitri 9, tie 10 |

## plate

> Make a 60 x 40 x 5 mm mounting plate with four M4 clearance holes 8 mm in from each edge and 5 mm rounded corners.

**baseline** - 1.00, 53s, $0.28, 6 tool calls
- PASS produced a STEP file (first.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 60 x 40 x 5 envelope (60.00 x 40.00 x 5.00)
- PASS four M4 clearance through holes (4.3-4.7) (4 found: 4.50, 4.50, 4.50, 4.50)
- PASS holes 8 mm from edges ((-22.0,-12.0), (-22.0,12.0), (22.0,-12.0), (22.0,12.0))
- PASS 5 mm corner radius (radii [5.0])

**eitri** - 1.00, 15s, $0.18, 2 tool calls, 1 builds
- PASS produced a STEP file (first.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 60 x 40 x 5 envelope (60.00 x 40.00 x 5.00)
- PASS four M4 clearance through holes (4.3-4.7) (4 found: 4.50, 4.50, 4.50, 4.50)
- PASS holes 8 mm from edges ((-22.0,-12.0), (-22.0,12.0), (22.0,-12.0), (22.0,12.0))
- PASS 5 mm corner radius (radii [5.0])

**eitri_v1** - 1.00, 18s, $0.39, 1 tool calls, 1 builds
- PASS produced a STEP file (plate.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 60 x 40 x 5 envelope (60.00 x 40.00 x 5.00)
- PASS four M4 clearance through holes (4.3-4.7) (4 found: 4.50, 4.50, 4.50, 4.50)
- PASS holes 8 mm from edges ((-22.0,-12.0), (-22.0,12.0), (22.0,-12.0), (22.0,12.0))
- PASS 5 mm corner radius (radii [5.0])

## l_bracket

> Design an L-bracket: two 50 x 50 mm legs at 90 degrees, 4 mm thick, 30 mm wide, with two 5.5 mm through holes in each leg and a 3 mm inside fillet for strength. Mount with M5 screws.

**baseline** - 1.00, 45s, $0.31, 5 tool calls
- PASS produced a STEP file (l_bracket.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS envelope 50 x 50 x 30 (sorted) ((30.0, 50.0, 50.0))
- PASS four 5.5 mm through holes (4: 5.50, 5.50, 5.50, 5.50)
- PASS holes on both legs (two axes) ({(0.0, 0.0, 1.0), (1.0, 0.0, 2.22044604925e-16)})
- PASS thickness 4 mm (3.9999999999999996)
- PASS 3 mm inside fillet present (concave radii [3.0])

**eitri** - 1.00, 122s, $0.18, 5 tool calls, 3 builds
- PASS produced a STEP file (l_bracket.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS envelope 50 x 50 x 30 (sorted) ((30.0, 50.0, 50.0))
- PASS four 5.5 mm through holes (4: 5.50, 5.50, 5.50, 5.50)
- PASS holes on both legs (two axes) ({(0.0, 0.0, 1.0), (1.0, 0.0, 2.22044604925e-16)})
- PASS thickness 4 mm (3.999999999999998)
- PASS 3 mm inside fillet present (concave radii [3.0])

**eitri_v1** - 1.00, 39s, $0.45, 1 tool calls, 1 builds
- PASS produced a STEP file (l_bracket.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS envelope 50 x 50 x 30 (sorted) ((30.0, 50.0, 50.0))
- PASS four 5.5 mm through holes (4: 5.50, 5.50, 5.50, 5.50)
- PASS holes on both legs (two axes) ({(0.0, 0.0, 1.0), (1.0, 0.0, 2.22044604925e-16)})
- PASS thickness 4 mm (3.999999999999998)
- PASS 3 mm inside fillet present (concave radii [3.0])

## enclosure

> Design a two-part electronics enclosure, 100 x 60 x 40 mm outside when assembled, 2 mm walls, with a removable lid that locates on the base and is held by four M3 screws into heat-set inserts in corner bosses. Rounded corners. Give me base and lid as separate parts in their assembled position.

**baseline** - 1.00, 402s, $0.70, 15 tool calls
- PASS produced a STEP file (first.step)
- PASS 2 separate parts (2 parts: base(1), lid(1))
- PASS valid geometry
- PASS parts do not interfere (max overlap 0.0000 mm^3)
- PASS outer 100 x 60 x 40 assembled (100.00 x 60.00 x 40.00)
- PASS nothing thinner than 1.4 mm (2.0)
- PASS side walls about 2 mm (material 1 mm in, empty 3 mm in)
- PASS base is hollow
- PASS four M3 heat-set insert holes (3.8-4.2) in base (4)
- PASS four M3 clearance holes in lid (4)
- PASS lid seated on base (<=0.3 mm) (0.000)

**eitri** - 1.00, 100s, $0.29, 8 tool calls, 2 builds
- PASS produced a STEP file (first.step)
- PASS 2 separate parts (2 parts: base(1), lid(1))
- PASS valid geometry
- PASS parts do not interfere (max overlap 0.0000 mm^3)
- PASS outer 100 x 60 x 40 assembled (100.00 x 60.00 x 40.00)
- PASS nothing thinner than 1.4 mm (1.9999999999998863)
- PASS side walls about 2 mm (material 1 mm in, empty 3 mm in)
- PASS base is hollow
- PASS four M3 heat-set insert holes (3.8-4.2) in base (4)
- PASS four M3 clearance holes in lid (4)
- PASS lid seated on base (<=0.3 mm) (0.000)

**eitri_v1** - 1.00, 112s, $1.31, 6 tool calls, 2 builds
- PASS produced a STEP file (enclosure.step)
- PASS 2 separate parts (2 parts: base(1), lid(1))
- PASS valid geometry
- PASS parts do not interfere (max overlap 0.0000 mm^3)
- PASS outer 100 x 60 x 40 assembled (100.00 x 60.00 x 40.00)
- PASS nothing thinner than 1.4 mm (1.9999999999998856)
- PASS side walls about 2 mm (material 1 mm in, empty 3 mm in)
- PASS base is hollow
- PASS four M3 heat-set insert holes (3.8-4.2) in base (4)
- PASS four M3 clearance holes in lid (4)
- PASS lid seated on base (<=0.3 mm) (0.000)

## flange

> Make a pipe flange: 80 mm outer diameter, 25 mm total height, with a 40 mm diameter hub, a 20 mm through bore, and six 6.6 mm bolt holes on a 60 mm bolt circle.

**baseline** - 1.00, 31s, $0.29, 6 tool calls
- PASS produced a STEP file (flange.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS OD 80 mm (80.00 x 80.00)
- PASS height 25 mm (25.00)
- PASS 20 mm through bore (1)
- PASS 6 bolt holes 6.4-6.9 mm (6)
- PASS bolt circle 60 mm (radii [30.0, 30.0, 30.0, 30.0, 30.0, 30.0])
- PASS 40 mm hub (1)

**eitri** - 1.00, 32s, $0.10, 2 tool calls, 1 builds
- PASS produced a STEP file (flange.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS OD 80 mm (80.00 x 80.00)
- PASS height 25 mm (25.00)
- PASS 20 mm through bore (1)
- PASS 6 bolt holes 6.4-6.9 mm (6)
- PASS bolt circle 60 mm (radii [30.0, 30.0, 30.0, 30.0, 30.0, 30.0])
- PASS 40 mm hub (1)

**eitri_v1** - 1.00, 25s, $0.71, 1 tool calls, 1 builds
- PASS produced a STEP file (flange.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS OD 80 mm (80.00 x 80.00)
- PASS height 25 mm (25.00)
- PASS 20 mm through bore (1)
- PASS 6 bolt holes 6.4-6.9 mm (6)
- PASS bolt circle 60 mm (radii [30.0, 30.0, 30.0, 30.0, 30.0, 30.0])
- PASS 40 mm hub (1)

## gear

> Make a spur gear: module 2, 20 teeth, 20 degree pressure angle, 8 mm thick, with an 8 mm bore.

**baseline** - 1.00, 52s, $0.35, 8 tool calls
- PASS produced a STEP file (spur_gear.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS outer diameter 44 mm (m2, 20t) (44.00 x 44.00)
- PASS thickness 8 mm (8.00)
- PASS 8 mm bore (1)
- PASS volume consistent with 20 teeth (±12%) (9407 vs ~9651)
- PASS has tooth geometry (>= 60 faces) (123 faces)

**eitri** - 1.00, 33s, $0.09, 4 tool calls, 1 builds
- PASS produced a STEP file (gear.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS outer diameter 44 mm (m2, 20t) (44.00 x 44.00)
- PASS thickness 8 mm (8.00)
- PASS 8 mm bore (1)
- PASS volume consistent with 20 teeth (±12%) (9407 vs ~9651)
- PASS has tooth geometry (>= 60 faces) (123 faces)

**eitri_v1** - 1.00, 18s, $0.44, 1 tool calls, 1 builds
- PASS produced a STEP file (gear.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS outer diameter 44 mm (m2, 20t) (44.00 x 44.00)
- PASS thickness 8 mm (8.00)
- PASS 8 mm bore (1)
- PASS volume consistent with 20 teeth (±12%) (9407 vs ~9651)
- PASS has tooth geometry (>= 60 faces) (123 faces)

## shaft

> Make a stepped shaft 80 mm long: 20 mm diameter for the middle 30 mm, stepping down to 16 mm for 30 mm on one end and 12 mm for 20 mm on the other. Add a 5 mm wide x 3 mm deep keyway on the 16 mm section and 1 mm chamfers on both ends.

**baseline** - 1.00, 48s, $0.37, 9 tool calls
- PASS produced a STEP file (shaft.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS length 80 mm ((20.0, 20.0, 80.0))
- PASS max diameter 20 mm ((20.0, 20.0, 80.0))
- PASS steps of 12, 16 and 20 mm ([12.0, 16.0, 20.0])
- PASS keyway removes material (17405 vs 17719)
- PASS chamfered ends (chamfer faces)

**eitri** - 1.00, 64s, $0.23, 8 tool calls, 5 builds
- PASS produced a STEP file (shaft.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS length 80 mm ((20.0, 20.0, 80.0))
- PASS max diameter 20 mm ((20.0, 20.0, 80.0))
- PASS steps of 12, 16 and 20 mm ([12.0, 16.0, 20.0])
- PASS keyway removes material (17403 vs 17719)
- PASS chamfered ends (chamfer faces)

**eitri_v1** - 1.00, 33s, $0.42, 1 tool calls, 1 builds
- PASS produced a STEP file (shaft.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS length 80 mm ((20.0, 20.0, 80.0))
- PASS max diameter 20 mm ((20.0, 20.0, 80.0))
- PASS steps of 12, 16 and 20 mm ([12.0, 16.0, 20.0])
- PASS keyway removes material (17405 vs 17719)
- PASS chamfered ends (chamfer faces)

## phone_stand

> Design a desk phone stand for a 75 mm wide, 9 mm thick phone, holding it at about 65 degrees, printable in PLA without supports, at least 2 mm walls, with a cable slot at the bottom.

**baseline** - 0.88, 240s, $1.00, 16 tool calls
- PASS produced a STEP file (phone_stand.step)
- PASS valid geometry
- PASS at most 2 bodies (1)
- PASS fits 100 x 100 x 100 footprint (108 x 80 x 110)
- PASS at least 60 mm tall (110.4)
- PASS at least 60 mm wide (107.7 / 80.0)
- FAIL no wall under 2 mm (0.16051188375040676)
- PASS sits on Z=0 (-0.000)

**eitri** - 1.00, 377s, $1.10, 16 tool calls, 7 builds
- PASS produced a STEP file (phone_stand.step)
- PASS valid geometry
- PASS at most 2 bodies (1)
- PASS fits 100 x 100 x 100 footprint (80 x 107 x 97)
- PASS at least 60 mm tall (96.8)
- PASS at least 60 mm wide (80.0 / 107.1)
- PASS no wall under 2 mm (3.1732050807568037)
- PASS sits on Z=0 (0.000)

**eitri_v1** - 0.88, 126s, $0.61, 1 tool calls, 1 builds
- PASS produced a STEP file (phone_stand.step)
- PASS valid geometry
- PASS at most 2 bodies (1)
- PASS fits 100 x 100 x 100 footprint (80 x 109 x 97)
- PASS at least 60 mm tall (96.8)
- PASS at least 60 mm wide (80.0 / 108.8)
- FAIL no wall under 2 mm (0.04503884912678875)
- PASS sits on Z=0 (-0.000)

## cable_clip

> Make a small screw-mount cable clip for a 6 mm round cable: a C-shaped clip with 1.6 mm walls, a base with one M3 clearance hole, and a snap opening so the cable pushes in. Fit in 30 mm.

**baseline** - 0.86, 46s, $0.32, 5 tool calls
- PASS produced a STEP file (cable_clip.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS fits 30 mm (10.0 x 21.0 x 8.6)
- FAIL walls >= 1.5 mm (1.2403015761423077)
- PASS one M3 clearance hole (1)
- PASS 6 mm cable channel (concave, 5.8-7.0) ([6.2])

**eitri** - 1.00, 68s, $0.20, 5 tool calls, 3 builds
- PASS produced a STEP file (cable_clip.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS fits 30 mm (26.0 x 8.0 x 7.9)
- PASS walls >= 1.5 mm (1.5999999999999932)
- PASS one M3 clearance hole (1)
- PASS 6 mm cable channel (concave, 5.8-7.0) ([6.2])

**eitri_v1** - 1.00, 115s, $1.28, 8 tool calls, 7 builds
- PASS produced a STEP file (cable_clip.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS fits 30 mm (26.0 x 10.0 x 8.2)
- PASS walls >= 1.5 mm (1.5999999999999943)
- PASS one M3 clearance hole (1)
- PASS 6 mm cable channel (concave, 5.8-7.0) ([6.0])

## hinge

> Make a print-in-place hinge, 40 mm long along the pin, two leaves each with two M4 mounting holes, with 0.4 mm clearance between the pin and knuckles so it prints as one piece and moves after printing. Model it flat on the bed, as two parts.

**baseline** - 1.00, 127s, $0.57, 12 tool calls
- PASS produced a STEP file (hinge.step)
- PASS 2 separate parts (2 parts: leaf_a_pin(1), leaf_b(1))
- PASS valid geometry
- PASS parts do not interfere (max overlap 0.0000 mm^3)
- PASS print-in-place clearance 0.25-0.6 mm (0.400)
- PASS 40 mm long along the pin (40.0 (bbox 40.0 x 44.0 x 8.0))
- PASS lies flat on Z=0 (-0.000)
- PASS pin and bore present (pins [4.0] bores [4.5, 4.5, 4.5, 4.5, 4.8])
- PASS four M4 mounting holes (4)

**eitri** - 1.00, 123s, $0.40, 12 tool calls, 3 builds
- PASS produced a STEP file (hinge.step)
- PASS 2 separate parts (2 parts: leaf_a(1), leaf_b(1))
- PASS valid geometry
- PASS parts do not interfere (max overlap 0.0000 mm^3)
- PASS print-in-place clearance 0.25-0.6 mm (0.400)
- PASS 40 mm long along the pin (40.0 (bbox 40.0 x 50.0 x 10.0))
- PASS lies flat on Z=0 (0.000)
- PASS pin and bore present (pins [5.0] bores [4.5, 4.5, 4.5, 4.5, 5.8])
- PASS four M4 mounting holes (4)

**eitri_v1** - 1.00, 57s, $0.52, 1 tool calls, 1 builds
- PASS produced a STEP file (hinge.step)
- PASS 2 separate parts (2 parts: leaf_a(1), leaf_b(1))
- PASS valid geometry
- PASS parts do not interfere (max overlap 0.0000 mm^3)
- PASS print-in-place clearance 0.25-0.6 mm (0.400)
- PASS 40 mm long along the pin (40.0 (bbox 40.0 x 44.0 x 10.0))
- PASS lies flat on Z=0 (0.000)
- PASS pin and bore present (pins [5.0] bores [4.5, 4.5, 4.5, 4.5, 5.8])
- PASS four M4 mounting holes (4)

## knob

> Make a control knob 30 mm diameter, 15 mm tall, for a 6 mm D-shaft (flat 0.5 mm deep), with grip ridges around the outside and a pointer mark on top.

**baseline** - 1.00, 57s, $0.40, 10 tool calls
- PASS produced a STEP file (knob.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 30 mm diameter (30.00 x 30.00)
- PASS 15 mm tall (15.00)
- PASS 6 mm shaft hole (D-shaft: partial cylinder) ([6.0])
- PASS D flat face in the bore (2)
- PASS grip ridges/knurl (>= 24 faces) (82 faces)
- PASS no wall under 1.5 mm (1.5711193584598415)

**eitri** - 1.00, 62s, $0.16, 5 tool calls, 2 builds
- PASS produced a STEP file (knob.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 30 mm diameter (30.00 x 30.00)
- PASS 15 mm tall (15.00)
- PASS 6 mm shaft hole (D-shaft: partial cylinder) ([6.15])
- PASS D flat face in the bore (1)
- PASS grip ridges/knurl (>= 24 faces) (86 faces)
- PASS no wall under 1.5 mm (2.1356638493015865)

**eitri_v1** - 1.00, 74s, $1.41, 3 tool calls, 3 builds
- PASS produced a STEP file (knob.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 30 mm diameter (30.00 x 30.00)
- PASS 15 mm tall (15.00)
- PASS 6 mm shaft hole (D-shaft: partial cylinder) ([6.2])
- PASS D flat face in the bore (1)
- PASS grip ridges/knurl (>= 24 faces) (63 faces)
- PASS no wall under 1.5 mm (2.1868543123872155)

## bottle_cap

> Make a screw-on bottle cap for a standard 28 mm PCO 1881 soda bottle neck (internal thread ~ 27.4 mm major diameter, pitch 2.7 mm), 15 mm tall, about 34 mm outside, with grip ridges.

**baseline** - 1.00, 116s, $0.58, 13 tool calls
- PASS produced a STEP file (bottle_cap.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS outer diameter 32-36 mm (34.00 x 34.00)
- PASS height 15 mm (15.00)
- PASS open on one end (hollow cup)
- PASS inner diameter ~28-30 mm (M28 thread) ([27.8])
- PASS has thread geometry (>= 20 faces) (259)

**eitri** - 1.00, 403s, $0.57, 22 tool calls, 11 builds
- PASS produced a STEP file (bottle_cap.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS outer diameter 32-36 mm (34.00 x 34.00)
- PASS height 15 mm (15.00)
- PASS open on one end (hollow cup)
- PASS inner diameter ~28-30 mm (M28 thread) ([28.03])
- PASS has thread geometry (>= 20 faces) (106)

**eitri_v1** - 1.00, 97s, $1.58, 3 tool calls, 1 builds
- PASS produced a STEP file (bottle_cap.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS outer diameter 32-36 mm (33.97 x 33.97)
- PASS height 15 mm (15.00)
- PASS open on one end (hollow cup)
- PASS inner diameter ~28-30 mm (M28 thread) ([28.0])
- PASS has thread geometry (>= 20 faces) (212)

## heatsink

> Make a heatsink: 40 x 40 mm base, 4 mm thick, with 10 straight fins 2 mm thick evenly spaced across the base, total height 20 mm.

**baseline** - 1.00, 38s, $0.31, 7 tool calls
- PASS produced a STEP file (heatsink.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 40 x 40 footprint (40.00 x 40.00)
- PASS 20 mm tall (20.00)
- PASS 10 fins (20 fin side faces along one axis) (X-normal faces 20, Y-normal 2)
- PASS fin thickness 2 mm (±0.2) (1.9999999999940012)

**eitri** - 1.00, 20s, $0.08, 2 tool calls, 1 builds
- PASS produced a STEP file (heatsink.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 40 x 40 footprint (40.00 x 40.00)
- PASS 20 mm tall (20.00)
- PASS 10 fins (20 fin side faces along one axis) (X-normal faces 20, Y-normal 2)
- PASS fin thickness 2 mm (±0.2) (1.9999999999940012)

**eitri_v1** - 1.00, 31s, $0.52, 2 tool calls, 2 builds
- PASS produced a STEP file (heatsink.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 40 x 40 footprint (40.00 x 40.00)
- PASS 20 mm tall (20.00)
- PASS 10 fins (20 fin side faces along one axis) (X-normal faces 20, Y-normal 2)
- PASS fin thickness 2 mm (±0.2) (1.9999999999940012)

## pulley

> Make a V-belt pulley: 50 mm outer diameter, 12 mm wide, an 8 mm bore, a V groove for a 6 mm belt, and a radial M3 set-screw tap hole (2.5 mm) into the bore.

**baseline** - 1.00, 97s, $0.45, 11 tool calls
- PASS produced a STEP file (pulley.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS OD 50 mm (50.00 x 50.00)
- PASS width 12 mm (12.00)
- PASS 8 mm bore (1)
- PASS V/round groove at the rim (mid-height radius < 24)
- PASS rim intact at edges (radius 25 at the faces)
- PASS M3 set-screw tap hole (2.5) radial (1)

**eitri** - 1.00, 32s, $0.11, 2 tool calls, 1 builds
- PASS produced a STEP file (pulley.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS OD 50 mm (50.00 x 50.00)
- PASS width 12 mm (12.00)
- PASS 8 mm bore (1)
- PASS V/round groove at the rim (mid-height radius < 24)
- PASS rim intact at edges (radius 25 at the faces)
- PASS M3 set-screw tap hole (2.5) radial (1)

**eitri_v1** - 1.00, 34s, $0.56, 1 tool calls, 1 builds
- PASS produced a STEP file (pulley.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS OD 50 mm (50.00 x 50.00)
- PASS width 12 mm (12.00)
- PASS 8 mm bore (1)
- PASS V/round groove at the rim (mid-height radius < 24)
- PASS rim intact at edges (radius 25 at the faces)
- PASS M3 set-screw tap hole (2.5) radial (1)

## tray

> Make an open storage tray 120 x 80 x 25 mm outside with 1.6 mm walls and floor, rounded vertical corners, divided into 3 equal compartments along the long axis by two dividers.

**baseline** - 1.00, 42s, $0.32, 7 tool calls
- PASS produced a STEP file (tray.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 120 x 80 x 25 outer (120.0 x 80.0 x 25.0)
- PASS 1.6 mm walls (±0.2) (1.5999999999989996)
- PASS two dividers making 3 equal compartments (dividers True,True; compartments empty True)
- PASS rounded vertical corners ([5.0])

**eitri** - 1.00, 25s, $0.10, 2 tool calls, 1 builds
- PASS produced a STEP file (tray.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 120 x 80 x 25 outer (120.0 x 80.0 x 25.0)
- PASS 1.6 mm walls (±0.2) (1.5999999999999321)
- PASS two dividers making 3 equal compartments (dividers True,True; compartments empty True)
- PASS rounded vertical corners ([6.0])

**eitri_v1** - 1.00, 29s, $0.31, 1 tool calls, 1 builds
- PASS produced a STEP file (tray.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 120 x 80 x 25 outer (120.0 x 80.0 x 25.0)
- PASS 1.6 mm walls (±0.2) (1.5999999999999321)
- PASS two dividers making 3 equal compartments (dividers True,True; compartments empty True)
- PASS rounded vertical corners ([6.0])

## rpi_case

> Design a bottom case for a Raspberry Pi 4 Model B. The board is 85 x 56 mm with four 2.7 mm mounting holes at (3.5, 3.5), (61.5, 3.5), (3.5, 52.5) and (61.5, 52.5) mm from the board's corner. The case needs 2 mm walls and floor, 1.5 mm clearance around the board, four standoffs 5 mm tall with M2.5 heat-set insert holes under the mounting holes, an open top and 25 mm inside height. One printable part.

**baseline** - 1.00, 44s, $0.33, 5 tool calls
- PASS produced a STEP file (rpi4_case.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS outer 92 x 63 (board + 1.5 clearance + 2 walls, ±0.6) (92.00 x 63.00)
- PASS total height 26.5-30 (25 inside + floor) (27.00)
- PASS open top, hollow inside
- PASS four M2.5 insert holes (3.35-3.85) (4: [3.6, 3.6, 3.6, 3.6])
- PASS insert holes on the Pi pattern (58 x 49) ([(7.0, 7.0), (7.0, 56.0), (65.0, 7.0), (65.0, 56.0)])
- PASS standoffs 5 mm above the floor (tops at [7.0, 7.0, 7.0, 7.0])
- PASS nothing thinner than 1.4 mm (1.4499999999995568)
- PASS side walls about 2 mm (material at 1 mm in, empty at 3 mm in)

**eitri** - 1.00, 52s, $0.18, 4 tool calls, 3 builds
- PASS produced a STEP file (rpi_case.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS outer 92 x 63 (board + 1.5 clearance + 2 walls, ±0.6) (92.00 x 63.00)
- PASS total height 26.5-30 (25 inside + floor) (27.00)
- PASS open top, hollow inside
- PASS four M2.5 insert holes (3.35-3.85) (4: [3.6, 3.6, 3.6, 3.6])
- PASS insert holes on the Pi pattern (58 x 49) ([(7.0, 7.0), (7.0, 56.0), (65.0, 7.0), (65.0, 56.0)])
- PASS standoffs 5 mm above the floor (tops at [7.0, 7.0, 7.0, 7.0])
- PASS nothing thinner than 1.4 mm (1.699999999999808)
- PASS side walls about 2 mm (material at 1 mm in, empty at 3 mm in)

**eitri_v1** - 1.00, 100s, $1.87, 9 tool calls, 6 builds
- PASS produced a STEP file (rpi_case.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS outer 92 x 63 (board + 1.5 clearance + 2 walls, ±0.6) (92.00 x 63.00)
- PASS total height 26.5-30 (25 inside + floor) (27.00)
- PASS open top, hollow inside
- PASS four M2.5 insert holes (3.35-3.85) (4: [3.6, 3.6, 3.6, 3.6])
- PASS insert holes on the Pi pattern (58 x 49) ([(3.5, 3.5), (3.5, 52.5), (61.5, 3.5), (61.5, 52.5)])
- PASS standoffs 5 mm above the floor (tops at [7.0, 7.0, 7.0, 7.0])
- PASS nothing thinner than 1.4 mm (1.7000000000000004)
- PASS side walls about 2 mm (material at 1 mm in, empty at 3 mm in)

## nema17_mount

> Make an L-shaped NEMA 17 stepper motor mounting bracket, 4 mm thick: a vertical motor plate 44 x 44 mm with the NEMA 17 pattern (four M3 clearance holes on a 31 x 31 mm square and a 22.5 mm centre hole for the pilot boss), and a horizontal base leg 44 x 40 mm with two M4 clearance holes for mounting it down. One part.

**baseline** - 1.00, 77s, $0.39, 7 tool calls
- PASS produced a STEP file (nema17_mount.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS four M3 clearance holes (4)
- PASS holes on a 31 x 31 square ([(-15.5, -6.5), (-15.5, -37.5), (15.5, -6.5), (15.5, -37.5)])
- PASS 22.5 mm pilot hole (1: [22.5])
- PASS pilot hole centred in the pattern (offset 0.00)
- PASS two M4 base holes (2)
- PASS base holes perpendicular to motor holes
- PASS 4 mm thick (4.0)

**eitri** - 1.00, 36s, $0.12, 2 tool calls, 1 builds
- PASS produced a STEP file (nema17_mount.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS four M3 clearance holes (4)
- PASS holes on a 31 x 31 square ([(-15.5, -10.5), (-15.5, -41.5), (15.5, -10.5), (15.5, -41.5)])
- PASS 22.5 mm pilot hole (1: [22.5])
- PASS pilot hole centred in the pattern (offset 0.00)
- PASS two M4 base holes (2)
- PASS base holes perpendicular to motor holes
- PASS 4 mm thick (3.9999999999999996)

**eitri_v1** - 1.00, 49s, $0.40, 1 tool calls, 1 builds
- PASS produced a STEP file (nema17_mount.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS four M3 clearance holes (4)
- PASS holes on a 31 x 31 square ([(-15.5, -10.5), (-15.5, -41.5), (15.5, -10.5), (15.5, -41.5)])
- PASS 22.5 mm pilot hole (1: [22.5])
- PASS pilot hole centred in the pattern (offset 0.00)
- PASS two M4 base holes (2)
- PASS base holes perpendicular to motor holes
- PASS 4 mm thick (3.9999999999999996)

## arduino_plate

> Make a 4 mm thick mounting plate for an Arduino Uno with rounded corners, at least 10 mm larger than the 68.6 x 53.3 mm board in each direction, with M3 clearance holes at the Uno's mounting positions (14.0, 2.5), (15.3, 50.7), (66.1, 7.6) and (66.1, 35.5) mm measured from the board's lower-left corner, board centred on the plate.

**baseline** - 1.00, 44s, $0.34, 9 tool calls
- PASS produced a STEP file (arduino_plate.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS plate at least 78 x 63, 4 thick (88.6 x 73.3 x 4.00)
- PASS four M3 clearance holes (4)
- PASS holes on the Uno pattern (pairwise distances ±0.3) ([(-20.3, -24.1), (-19.0, 24.1), (31.8, -19.1), (31.8, 8.8)])
- PASS rounded corners ([5.0])

**eitri** - 1.00, 18s, $0.07, 2 tool calls, 1 builds
- PASS produced a STEP file (arduino_plate.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS plate at least 78 x 63, 4 thick (88.6 x 73.3 x 4.00)
- PASS four M3 clearance holes (4)
- PASS holes on the Uno pattern (pairwise distances ±0.3) ([(-20.3, -24.1), (-19.0, 24.1), (31.8, -19.1), (31.8, 8.8)])
- PASS rounded corners ([5.0])

**eitri_v1** - 1.00, 28s, $0.29, 1 tool calls, 1 builds
- PASS produced a STEP file (arduino_plate.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS plate at least 78 x 63, 4 thick (88.6 x 73.3 x 4.00)
- PASS four M3 clearance holes (4)
- PASS holes on the Uno pattern (pairwise distances ±0.3) ([(-20.3, -24.1), (-19.0, 24.1), (31.8, -19.1), (31.8, 8.8)])
- PASS rounded corners ([5.0])

## name_tag

> Make a name tag: a 70 x 25 x 3 mm plate with rounded corners, the text CADCODE raised 1 mm on top in about 8 mm tall letters, and a 4 mm keyring hole near the left end.

**baseline** - 1.00, 42s, $0.30, 6 tool calls
- PASS produced a STEP file (name_tag.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 70 x 25 plate, 3 + 1 mm tall (70.00 x 25.00 x 4.00)
- PASS 4 mm keyring hole (1)
- PASS hole near the left end (x=-29.0)
- PASS raised text present (>= 50 faces) (155)
- PASS text adds material on top (volume > plate - hole) (5308)
- PASS rounded corners ([4.0])

**eitri** - 1.00, 107s, $0.37, 16 tool calls, 10 builds
- PASS produced a STEP file (name_tag.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 70 x 25 plate, 3 + 1 mm tall (70.00 x 25.00 x 4.00)
- PASS 4 mm keyring hole (1)
- PASS hole near the left end (x=-29.0)
- PASS raised text present (>= 50 faces) (182)
- PASS text adds material on top (volume > plate - hole) (5365)
- PASS rounded corners ([0.4, 4.0])

**eitri_v1** - 1.00, 25s, $0.40, 1 tool calls, 1 builds
- PASS produced a STEP file (name_tag.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 70 x 25 plate, 3 + 1 mm tall (70.00 x 25.00 x 4.00)
- PASS 4 mm keyring hole (1)
- PASS hole near the left end (x=-29.0)
- PASS raised text present (>= 50 faces) (143)
- PASS text adds material on top (volume > plate - hole) (5365)
- PASS rounded corners ([4.0])

## hex_bolt

> Model an M8 x 30 hex bolt: ISO 4017 hex head 13 mm across flats and 5.3 mm tall, with a real M8 thread (1.25 mm pitch) modelled along the full 30 mm shank.

**baseline** - 1.00, 2647s, $0.88, 21 tool calls
- PASS produced a STEP file (m8x30_hex_bolt.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS length 35.3 (30 shank + 5.3 head, ±0.6) ((13.000000200000002, 15.011107198933704, 35.300000100001995))
- PASS 13 mm across flats (bbox 13.0 x 15.0 ±0.4) ((13.000000200000002, 15.011107198933704, 35.300000100001995))
- PASS thread geometry present (>= 12 faces) (39)
- PASS volume below a plain cylinder+head (thread cut) (1996 vs plain 2284)

**eitri** - 1.00, 94s, $0.23, 10 tool calls, 5 builds
- PASS produced a STEP file (hex_bolt.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS length 35.3 (30 shank + 5.3 head, ±0.6) ((13.000000000000002, 15.011106998931908, 35.3))
- PASS 13 mm across flats (bbox 13.0 x 15.0 ±0.4) ((13.000000000000002, 15.011106998931908, 35.3))
- PASS thread geometry present (>= 12 faces) (116)
- PASS volume below a plain cylinder+head (thread cut) (2002 vs plain 2284)

**eitri_v1** - 1.00, 69s, $2.32, 6 tool calls, 4 builds
- PASS produced a STEP file (hex_bolt.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS length 35.3 (30 shank + 5.3 head, ±0.6) ((13.000000200000002, 15.011107198933704, 35.3000001))
- PASS 13 mm across flats (bbox 13.0 x 15.0 ±0.4) ((13.000000200000002, 15.011107198933704, 35.3000001))
- PASS thread geometry present (>= 12 faces) (115)
- PASS volume below a plain cylinder+head (thread cut) (2006 vs plain 2284)

## slotted_bracket

> Make a flat mounting bracket 100 x 30 x 5 mm with two adjustment slots 20 mm long and 6.5 mm wide (for M6), each centred 30 mm from its end along the long axis, and a 10 mm hole in the middle.

**baseline** - 1.00, 40s, $0.32, 7 tool calls
- PASS produced a STEP file (slotted_bracket.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 100 x 30 x 5 (100.0 x 30.0 x 5.00)
- PASS 10 mm centre hole (1)
- PASS two 6.5 mm slots (4 half-round ends) (4 ends: [6.5, 6.5, 6.5, 6.5])
- PASS slots 20 mm long (ends 13.5 apart) ([-26.8, -13.2, 13.2, 26.8])
- PASS slots centred 30 mm from each end

**eitri** - 1.00, 20s, $0.08, 2 tool calls, 1 builds
- PASS produced a STEP file (slotted_bracket.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 100 x 30 x 5 (100.0 x 30.0 x 5.00)
- PASS 10 mm centre hole (1)
- PASS two 6.5 mm slots (4 half-round ends) (4 ends: [6.5, 6.5, 6.5, 6.5])
- PASS slots 20 mm long (ends 13.5 apart) ([-26.8, -13.2, 13.2, 26.8])
- PASS slots centred 30 mm from each end

**eitri_v1** - 1.00, 25s, $0.33, 1 tool calls, 1 builds
- PASS produced a STEP file (slotted_bracket.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 100 x 30 x 5 (100.0 x 30.0 x 5.00)
- PASS 10 mm centre hole (1)
- PASS two 6.5 mm slots (4 half-round ends) (4 ends: [6.5, 6.5, 6.5, 6.5])
- PASS slots 20 mm long (ends 13.5 apart) ([-26.8, -13.2, 13.2, 26.8])
- PASS slots centred 30 mm from each end

## bearing_housing

> Make a bearing housing block for a 608 bearing (8 x 22 x 7 mm): a 40 mm wide, 30 mm tall, 12 mm thick block with a 22.1 mm diameter pocket 7 mm deep for the bearing from one face, a 9 mm through hole for the shaft, the bearing centre 18 mm above the bottom, and two M4 clearance holes through the thickness, 30 mm apart, near the bottom corners for mounting.

**baseline** - 1.00, 170s, $0.34, 7 tool calls
- PASS produced a STEP file (bearing_housing.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 40 wide x 30 tall x 12 thick (40.0 x 12.0 x 30.0)
- PASS 22.1 mm pocket, 7 deep, blind ([(22.1, 7.0, False)])
- PASS 9 mm through hole (1)
- PASS pocket and bore coaxial
- PASS bearing centre 18 mm above the base (18.00)
- PASS two M4 mounting holes 30 apart (2)

**eitri** - 1.00, 33s, $0.11, 3 tool calls, 2 builds
- PASS produced a STEP file (bearing_housing.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 40 wide x 30 tall x 12 thick (40.0 x 12.0 x 30.0)
- PASS 22.1 mm pocket, 7 deep, blind ([(22.1, 7.0, False)])
- PASS 9 mm through hole (1)
- PASS pocket and bore coaxial
- PASS bearing centre 18 mm above the base (18.00)
- PASS two M4 mounting holes 30 apart (2)

**eitri_v1** - 1.00, 36s, $0.38, 1 tool calls, 1 builds
- PASS produced a STEP file (bearing_housing.step)
- PASS one solid body (1 solids in 1 part(s))
- PASS valid geometry
- PASS 40 wide x 30 tall x 12 thick (40.0 x 12.0 x 30.0)
- PASS 22.1 mm pocket, 7 deep, blind ([(22.1, 7.0, False)])
- PASS 9 mm through hole (1)
- PASS pocket and bore coaxial
- PASS bearing centre 18 mm above the base (18.00)
- PASS two M4 mounting holes 30 apart (2)

## gear_pair

> Make a pair of meshing spur gears as two separate parts placed in their correct meshed position: module 1.5, 15 teeth and 30 teeth, both 6 mm thick with 5 mm bores, lying flat on Z=0 side by side.

**baseline** - 1.00, 561s, $0.45, 12 tool calls
- PASS produced a STEP file (gear_pair.step)
- PASS 2 separate parts (2 parts: pinion_15T(1), gear_30T(1))
- PASS valid geometry
- PASS ODs 25.5 and 48 (m1.5: 15t, 30t) (25.31, 47.84)
- PASS 6 mm thick, both on Z=0 (6.00, 6.00, z0 -0.00)
- PASS 5 mm bores in both (1, 1)
- PASS centre distance 33.75 (±0.15) (33.750)
- PASS teeth mesh without interference (< 0.5 mm^3) (0.000)
- PASS teeth engage (min distance < 0.3 mm)
- PASS involute tooth geometry (>= 60 faces each) (93, 183)

**eitri** - 1.00, 113s, $0.21, 9 tool calls, 3 builds
- PASS produced a STEP file (gear_pair.step)
- PASS 2 separate parts (2 parts: pinion(1), gear(1))
- PASS valid geometry
- PASS ODs 25.5 and 48 (m1.5: 15t, 30t) (25.23, 47.84)
- PASS 6 mm thick, both on Z=0 (6.00, 6.00, z0 -0.00)
- PASS 5 mm bores in both (1, 1)
- PASS centre distance 33.75 (±0.15) (33.750)
- PASS teeth mesh without interference (< 0.5 mm^3) (0.000)
- PASS teeth engage (min distance < 0.3 mm)
- PASS involute tooth geometry (>= 60 faces each) (93, 183)

**eitri_v1** - 1.00, 31s, $0.53, 2 tool calls, 1 builds
- PASS produced a STEP file (gear_pair.step)
- PASS 2 separate parts (2 parts: pinion(1), gear(1))
- PASS valid geometry
- PASS ODs 25.5 and 48 (m1.5: 15t, 30t) (25.31, 47.84)
- PASS 6 mm thick, both on Z=0 (6.00, 6.00, z0 0.00)
- PASS 5 mm bores in both (1, 1)
- PASS centre distance 33.75 (±0.15) (33.750)
- PASS teeth mesh without interference (< 0.5 mm^3) (0.000)
- PASS teeth engage (min distance < 0.3 mm)
- PASS involute tooth geometry (>= 60 faces each) (93, 183)
