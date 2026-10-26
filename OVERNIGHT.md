# Overnight log: 2026-10-26 (branch `overnight/2026-10-26`)

Resume from here: each round below says what was looked at, what was found, what was fixed (commit), what was skipped and why.
Nothing here is pushed, merged, tagged or released.

## Step 0: starting point

- `git fetch --tags origin`, `git checkout main`, `git pull origin main`: already up to date.
- `git describe --tags` = `v0.5.0`; `git merge-base --is-ancestor v0.5.0 HEAD` succeeds; `pyproject.toml` and `eitri/__init__.py` say 0.5.0.
- Working tree clean: nothing stashed.
- Branch `overnight/2026-10-26` created from main at v0.5.0 (021f0ce).
- Baseline suite on v0.5.0 (clean export, fresh venv from pyproject `.[dev]`, arduino-cli + arduino:avr installed): **370 passed, 0 failed, 0 skipped**.
  (A first baseline run was discarded: it ran on the live working tree while code was being edited.)

## Live agent runs: not possible tonight (fixtures and scripted sessions instead)

eitri's Claude backend authenticates with an Anthropic API key only: it runs Claude Code with `--bare`, which never reads OAuth or
the keychain, so a logged-in Claude subscription is never used (README "API key only"; `eitri/agent/claude_cli.py`). Running the
agent through the Claude Code login would need a change to how eitri authenticates, which is a product decision (it touches BYOK),
so it is not made tonight. Every project below is a fixture model file, edited by hand in the editor and by scripted edits; chat is
exercised with the recorded demo replays and the suite's scripted session. No API key is used.

## How each fix is verified

Every fix is committed only after the full suite passes on a snapshot of HEAD plus that fix's files, run from a fresh venv
(`pip install -e '.[dev]'`) with arduino-cli 1.5.1 and arduino:avr 1.8.8, so later edits never leak into a run. Load from other
work running alongside made two CAD editor browser tests time out once (`test_viewer_edit::test_edit_tool`,
`test_viewer_form::test_form_tool_and_cage_editing`). Both passed when rerun alone, and the whole suite was then rerun with
nothing else going on before committing.

## Round 1: backlog 1, the power budget (wires + firmware)

Looked at: an MG996R on the Uno's 5V pin in both domains; the same servo on its own 6 V supply.

Found: neither domain knew what a board supply pin can give, so a 2.5 A servo on the Uno's 5V pin passed every check; nothing
checked that a load taking a board signal shares the board's ground.

Fixed (sources recorded in `eitri/power.py`, fetched 2026-10-26):
- Uno and Nano 3V3 pin: 50 mA ("MAXIMUM current per +3.3V pin is 50mA", Arduino's official full-pinout PDFs A000066 / A000005).
- Uno 5V on USB: 500 mA (the MF-MSMF050-2 500 mA polyfuse in the official UNO R3 schematic; Arduino's store page: "If more than
  500 mA is applied to the USB port, the fuse will automatically break the connection").
- Nano 5V on USB: 500 mA (USB 2.0 specification 7.2.1: five 100 mA unit loads). The Nano's VIN regulator has no published
  rating: reported ungraded, never guessed.
- Uno 5V on VIN: the regulator named in the UNO R3 datasheet (SPX1117M3-L-5; the schematic shows an NCP1117 rated 1.0 A, the
  lower rating is used): 0.8 A, or less as it heats: (125 C - ambient) / (46 C/W x (VIN - 5 V)), MaxLinear SPX1117 datasheet.
- MG996R stall 2.5 A at 6 V (TowerPro MG996R datasheet), used in tests and fixtures.
- Harness: `ARDUINO-UNO` / `ARDUINO-NANO` connector types (`powered="usb" | "vin"`, `vin_V`); a check per supply pin (sum of the
  wire currents leaving it; FAIL above, WARN within 20 %, the fix in the message); a shared-ground check for every connector that
  takes a signal from a board I/O pin. Firmware: `Firmware(powered=, vin_V=)`, `servo(power="5V" | "3V3" | "external",
  current_A=)`, `load(name, supply, current_A)`, the same grading. Agent references and the firmware prompt explain it.
- Tests: tests/test_power.py (the backlog's must-fail and must-pass examples in both domains, every limit and its source, the
  VIN thermal limit, ungraded cases, the shared-ground failure).

Decision on your behalf: a pin whose loads give no current is reported as a WARN ("not graded"), not a FAIL.
- Commit: f7625bf.

## Round 2: backlog 2, pneumatics (the merged CAP / ROD lines)

Looked at: the 25 x 50 cylinder at 6 bar (FRL -> 5/2 solenoid -> two meter-out flow controls -> cylinder); then a single-acting
clamp, a two-cylinder sequence with a manifold tee, the air-clamp demo.

Found: the circuit model kept the lines apart (the checks traced CAP through T3/FC1/T4 and ROD through T5/FC2/T6); only the
drawing merged them. Tubes were drawn as two-bend elbows that ignored the symbols and each other, so the A and B lines (14 px
apart at the valve) shared their middle run, the supply line came up through the FRL's body instead of into IN, and FRL OUT left
from the middle. Labels sat on tubes and on each other ("FC1 · meter-out" ran into "FC2", "AIR · 7 bar" over "OUT"). The
"flow data estimated" notice named nearly every part, so the whole schematic was drawn amber. A manifold tee's tubes all left it
upward and looped around it.

Fixed:
- `eitri/route.py`: orthogonal routing (A* on a grid of the ports' own coordinates plus 8 px lines): around every symbol, into each
  component at its named port, never along another tube (crossing at right angles only), never turning on another tube, parallel
  runs kept 10 px apart; a tee's tubes each leave on their own side (the assignment closest to where they go).
- Layout: a supply feeding a side port stands to that side; row spacing makes room for the titles; valve positions are 44 px wide
  so P sits clear between R and S; silencers hang to the outside of R and S on a short leader, the P tube runs past them; 40 px
  more room under the valve row.
- Labels (shared, `eitri/diagram.py`): measured text widths (the wider of Plex Sans and the PNG font, per glyph), a label halo,
  candidate positions, a placement pass with a repair round, and the collision test every domain is now held to.
- The flow-data notice no longer colours the drawing (it stays in the checks, the footer and the click-to-highlight).
- New check: "C1.CAP and C1.ROD on separate lines" fails when a cylinder's two ports are joined by tubes (directly or through a
  tee): both chambers would always see the same pressure.
- Tests: tests/test_pneumatics_diagram.py (orthogonal, never along each other, every tube ends at its ports, the supply enters IN
  from the left, OUT leaves to the right, no label collides, the tee case, the new check both ways).
- Commit: ee9d499.

## Round 3: backlog 3, labels in the wires, motion and firmware diagrams

Looked at: the quad-power demo, a 3-servo harness on a separate supply, a Nano sensor loom, the Uno-to-MG996R lead; the pan-tilt,
a two-link planar arm, a 3-joint arm, a vertical lift; the servo-sweep demo, a sweep with a pause button and a potentiometer.

Found (the detector, `eitri.diagram.collisions`, then by eye): every harness wire label was a card centred ON its wire; when a
spot was taken it was pushed straight down in 30 px steps, so one ended far below the diagram; labels covered other wires and
each other in the fan out of a splice; splice names sat on the wires leaving them. Motion: plan-view joint names sat on the
links and on each other (the pan-tilt's two joints share a plan position), labels sat on targets. Firmware: every pin-to-
peripheral wire turned at the same lane, so a motor driver's PWM and DIR wires ran on top of each other and all ended at the
middle of the box.

Fixed:
- Harness: a wire's label sits beside its own wire, never on it: first where the wire runs straight into a connector (a 36 px
  lead out of every pin), then along it; a label may not have another wire between it and its own (a tether test). Pin rows
  are 32 px, room for a two-line label between neighbouring leads. Column gaps grow with the wires that cross them; wires leave
  a splice in a fan and arrive flat at the pins. Splice names find a free spot around the dot.
- The editor (assets/harness.js) draws and drags the new 6-point paths (lead, curve, lead); 4-point paths still work.
- Motion and pneumatics labels: candidates around each symbol; a joint's limits travel with its name.
- Firmware: each pin's wire has its own lane and ends at its own row in the peripheral box; boxes size to their text.
- Tests: tests/test_diagram_labels.py: the detector and the placer on synthetic scenes; every domain's diagram on eight
  realistic projects with no collision and no label away from its wire.

## Round 4: backlog 4, firmware input names and planar mechanisms

Found: a push button was drawn and reported as a "limit switch" (the only input constructor); the motion diagram always drew a
plan view, a flat line for a planar arm.

Fixed:
- Firmware: `fw.button()` (a user's push button) and `fw.potentiometer()` (a wiper on an analog pin) beside `fw.limit_switch()`
  (a switch the mechanism trips); the diagram names them ("PAUSE · push button"), and shows a peripheral's supply ("power: own
  supply, GND shared, 2.5 A"). The servo-sweep demo and the reference example use `button`; the firmware prompt tells the agent
  to name inputs as asked and never to power a servo or motor from a board pin.
- Motion: a mechanism whose points all lie in the X-Z plane with Y revolute axes (or flat in X-Y with Z axes) draws one view, at
  double width, captioned "planar: the plan view adds nothing". The pan-tilt keeps both.
- Commit (rounds 3 and 4 together, they touch the same diagram files): 85a82b6. Full suite 396 passed, 0 failed, 0 skipped.

## Round 5: sources for every number the wire checks rely on

Looked at: every connector rating in `eitri/harness/catalog.py`, the AWG ampacity table and the voltage-drop constant, against
the manufacturers' pages and datasheets.

Found:
- **The XT connectors were graded on their burst ratings, not their continuous ones**: XT30 30 A, XT60 60 A, XT90 90 A. Amass
  publishes XT30 20 A (16 AWG), XT60 30 A rated / 60 A instantaneous (datasheet V1.2), XT90 45 A (10 AWG). A steady 50 A
  through an XT60 passed: a check passing when it should not.
- No rating had a recorded source; the AWG table and `K_COPPER` neither.

Fixed:
- Every graded catalog type has its source in `RATING_SOURCES` (a test holds every future type to that): JST XH 3 A at AWG 22 and
  PH 2 A at AWG 24 (JST's series pages), Micro-Fit 3.0 8.5 A (Molex 43030 terminals), XT30 / XT60 / XT90 as above, the 6.3 mm
  spade 20 A (TE/AMP FASTON catalog). Dupont 2.54 (2 A) and the 2.8 / 4.8 mm spades (6 / 12 A) have no published per-size
  figure: kept, recorded as deratings below the nearest published one (FCI Mini-PV 3 A; FASTON family 20 A with 2.5 mm^2 wire).
- A connector between its continuous and its published short-time rating is a WARN that says so ("fine for peaks under a
  minute, not for a steady load"); past the short-time rating, a FAIL. A rating given in the model gets no short-time allowance.
- The AWG chassis table cites its source (Handbook of Electronic Tables and Formulas for AWG, via PowerStream's table).
  `K_COPPER = 10.75` is copper near 29 C (NBS Handbook 100: 10.371 at 20 C, +0.393 %/C): recorded as such, value unchanged
  (see open issues).
- The quad-power demo now reports 27 pass, 5 warn, 0 FAIL instead of 32 / 0 / 0: its 44.5 A battery peak is past the XT60's
  30 A continuous rating, and each XT30 carries its ESC's full 20 A. Both are true, so the demo is left as designed and its
  test now asserts exactly those five warnings; AGENTS.md says so.

Decision on your behalf: where a manufacturer's own figures disagree (XT60: 30 A in the datasheet, 35 A on the XT60H product
page), the lower one is used.
- Commit: 4c861fa (full suite 397 passed, 0 failed, 0 skipped).

## Round 6: every domain in the editor, dark and light (19 projects, 57 screenshots)

Looked at: three new CAD projects (the racket-holder demo, an Arduino Uno case with lid, a 2020 L bracket) beside the 16 from
rounds 1-4; each project's editor at 1440 x 900, dark and light, the first failing check clicked, the Export menu.

Checks that did their job: the L bracket's first draft put its slots 0.75 mm from the edge, and the thinnest-wall check failed it
(fixed in the fixture: 2.25 mm). The Uno case's 1.2 mm lid lip is flagged by the geometry report.

Found and fixed (each its own commit, full suite green before each):
- Editor: "All 32 checks pass · 5 warnings" and "32 checks pass, 5 warn" for a build the CLI reports as 27 pass, 5 warn: a
  warning was counted as a pass. Now "27 of 32 checks pass, 5 warn" in the card and the status bar. Commit 1aed9c4.
- Wires: the Dupont rating (2 A) and the 2.8 / 4.8 mm spade ratings (6 / 12 A) were my round-5 "deratings", i.e. numbers with
  no source. TE's FASTON spec 108-106496 rates spades by the wire crimped in (22 AWG 3 A ... 12 AWG 20 A), so spades are now
  graded per pin against the wire on it; Dupont uses the 3 A of the Amphenol FCI Mini-PV contact. (Uno -> MG996R: the servo
  connector now warns at 83 % instead of failing; the 5V pin's power-budget FAIL is unchanged and is the real problem.)
- Motion: the mass rollup read "fore 80 g; payload 50 g; total 230 g", as if the payload were extra; now "fore 80 g (the 50 g
  payload counted on fore)". Hand check of the two-link arm: shoulder 40 g x 6 cm + 55 g servo x 12 + 30 g x 17 + 50 g x 22 =
  2.51 kg.cm, elbow 30 x 5 + 50 x 10 = 0.65 kg.cm: both match.
- Motion: a vertical lift was drawn as a horizontal link with nothing showing it slides or which way. A prismatic joint now
  draws its stroke as a dashed rail with end stops along its axis, and its symbol's bar lies along the slide.
- Firmware: a TMP36's 50 uA read "5e-05 A". Currents below 1 mA now read in uA everywhere (eitri.power.amps).

Found, next: a servo declared without power= is never checked (an MG996R on the 5V pin passes if the agent leaves power out);
the servo-sweep demo is such a servo. CAD's review image of an assembly with a lid shows only the closed box (standoffs and
port openings hidden): logged, see open issues.

## Round 7: BOMs, cut lists and exports, read as a buyer would

Looked at: every project's BOM / cut list / schedule as exported (wires, pneumatics, motion, firmware).

Found:
- Wires: the quad-power demo crimps 22 AWG into a JST PH contact; PH takes AWG 24-32 (JST). Nothing checked that a wire fits
  the contact it goes into. -> new check "wires fit the <connector> contacts" (family ranges with sources); demo lead 24 AWG.
- Wires: Micro-Fit housings in the BOM were "43025-0300", a dual-row family with no 3-circuit size, with female terminals
  for a connector declared male. Its 8.5 A rating is the 18 AWG terminal's; the 20-24 AWG terminal is listed at 5-8.5 A.
  -> rated and specified by the wire crimped in (like spades); single-row receptacle 43645-0n00; male terminal 43031-0001.
  The harness reference example (a 6 A pump on 18 AWG Micro-Fit) stays green: 6 A of 8.5 A.
- Firmware: a servo with no power= was never graded -> "servo supply declared" warns; the demo, the reference example and the
  test project declare power="external".
- Pneumatics: the schematic always drew a lubricator in an FRL (model default lubricator=False; the BOM said filter-
  regulator) -> drawn only when fitted, titled FR / FRL. Tubes are modelled by bore but labelled "4 mm" and the BOM said "push-in
  fitting for 4 mm tube" (fittings are ordered by OD) -> labels say "mm ID", the BOM says fittings go by the tube's OD.

Environment: this Mac's idle sleep is 1 minute; it slept during suites (12-minute runs took over an hour of wall clock, and
browser tests timed out after waking). `caffeinate -i -m` runs for the rest of the night (stopped at the end). A suite with
timeouts is rerun in full before anything is committed; the four tests that timed out passed alone.

## State of each domain (after round 7)

- **CAD.** Solid: build, checks, geometry report (thin walls, slivers), exports (STEP/STL/3MF/GLB/OBJ/IGES, drawings), the
  editor. Three fixtures built clean, and the thin-wall check caught a real slot-to-edge sliver. Weak: the default review
  image of an assembly with a lid shows only the closed box (the agent has section and exploded views, but must ask for them);
  no check for fastener-head clearance (an L-bracket's slots next to its gusset leave no room for an M5 head).
- **Wires.** Solid now: every rating and wire range has a recorded source; continuous vs short-time ratings; spades and Micro-
  Fit rated by the wire crimped in; terminal fit; power budget on board supply pins; shared ground; labels beside their wires.
  Weak: K_COPPER = 10.75 is copper near 29 C (a hot wire drops ~17 % more); the BOM picks one terminal per gauge but not per
  plating or brand; ring terminals are unrated (wire-limited by design).
- **Motion.** Solid: statics by hand-checked numbers, limits vs actuator travel, targets (IK), planar one-view drawings,
  prismatic strokes drawn. Weak: RATED_FRACTION = 0.5 of stall is an estimate (ROBOTIS recommends 1/5 of stall for the XL320);
  no check that the arm stays above its base plane / the table (needs your decision, below); statics only (no acceleration).
- **Pneumatics.** Solid: routed schematic, CAP/ROD separation check, force with friction allowance, ISO 6358 flow, supply
  consumption, drop. Weak: most default C, b values are estimates (the valve's now cites SMC SY3000); the lubricator symbol is
  a plain circle, not the ISO 1219 drip symbol; tubes have no OD, so the BOM cannot name the fitting size.
- **Firmware.** Solid: pin capability checks, arduino-cli compile, power budget, named inputs, undeclared servo supply
  warned, small currents readable. Weak: no per-I/O-pin current check (an LED without a resistor, 20 mA recommended / 40 mA
  absolute per ATmega328P pin) because the model has no LED / resistor objects; pwm_motor drivers carry no supply data.

Commits from rounds 6-7 (each its own full-suite run on HEAD + that fix's files only):
- cd67eb4 spades by wire, Dupont 3 A (399 passed) · ff9cd12 mass rollup wording (400) · 071ceaf prismatic rail (402) ·
  808fbc4 microamps (403) · d2d93d4 terminal fit + Micro-Fit by terminal (406).
- Flaky, pre-existing, not touched by any fix: `test_web_browser::test_live_building_in_the_browser` failed once on a streaming
  timing assertion ("Claude is thinking..." not yet replaced) and passed 3/3 alone; `test_viewer_solid::test_solid_tools`,
  `test_viewer_sketch`, `test_viewer_inspect`, `test_web::test_projects_chat_stop...` timed out while the display was asleep
  (WebGL fell back to software rendering at 500 % CPU) and passed alone. A red run is always rerun in full before committing.
- 02d4de5 servo supply declared (406) · ff57f8d FRL symbol + tube ID (407) · f87debc payload() error (408) ·
  3e501d7 valve C source (408).

## Round 8: the fixes, seen in the editor

Re-screenshotted all 19 projects (dark, light, Export). Confirmed by eye: the quad's old 22 AWG flight-controller lead now fails
"wires fit the FC contacts" with both wires highlighted (the shipped demo uses 24 AWG); the status bar reads "1 of 33 checks
failing"; the lift draws its 100 mm stroke as a vertical rail and the rollup counts the payload once; the TMP36 reads "50 µA";
the FRL draws filter + regulator titled "FR 6 bar"; tubes read "4 mm ID"; "21 of 22 checks pass, 1 warn". BOM: 26 AWG signal
wires on a male Micro-Fit get "crimp contact (match the housing)" (no verified male terminal for that size) instead of a wrong number.

## Round 9: probing checks with deliberate mistakes

Firmware: Serial pins reused, analog-only pins driven, two inputs on one pin, PWM on a non-PWM pin and literal pins were all caught.
Not caught well:
- a PIN_ constant the pinout does not define was only a warning calling it "a pin held in another variable" -> FAIL "not in the
  pinout"; a declared pin written as a number (pinMode(9)) -> WARN "write PIN_X" (the number does not follow the pinout).
- nothing graded what an output pin drives itself -> fw.pin(..., current_A=) graded against Arduino's 20 mA per I/O pin.
Pneumatics: a valve with nothing declared on R and S fails both chambers as "dead-headed" (right: an undeclared exhaust port is
plugged) but did not say why -> the failure names the bare ports and the call that fixes it.
Motion: a weak servo, an unreachable target, limits wider than the servo's travel, a joint loop and a missing actuator were all
reported correctly.
Not fixed (needs data I could not source): no check of component pressure ratings (a 12 bar supply straight into a valve and
cylinder raises nothing; ISO 15552 cylinders are 10 bar, SMC SY valves 0.15-0.7 MPa, but there is no per-component field).
- 40ec528 pin references (410) · 2b31ddf undeclared exhaust ports named (411) · 23efddc per-pin current (412).

## Round 10: final pass

All 19 projects re-screenshotted on 23efddc, dark and light, with no console errors. The quad demo reads "28 of 33 checks pass, 5 warn"; the
servo sweep reads 12 pass with "power: own supply, GND shared". Stopped here: what is left needs a decision from you or data I could not source.

# Summary

## Commits (17 on `overnight/2026-10-26`, none pushed; each passed the full suite as committed)

f7625bf power budget · ee9d499 pneumatic routing + labels · 85a82b6 labels in every domain, named inputs, planar one view ·
4c861fa connector ratings continuous + sourced · 1aed9c4 warnings not counted as passes · cd67eb4 spades by wire, Dupont 3 A ·
ff9cd12 mass rollup wording · 071ceaf prismatic stroke · 808fbc4 microamps · d2d93d4 terminal fit, Micro-Fit by terminal ·
02d4de5 servo supply declared · ff57f8d FRL symbol, tube ID · f87debc payload() error · 3e501d7 valve C source ·
40ec528 pin references · 2b31ddf undeclared exhaust ports · 23efddc per-pin current.
Suite: 370 tests at v0.5.0 -> 412, all passing, no skips.

## Defects fixed, per domain

- Wires (8): no power budget on board pins; no shared-ground check; XT ratings were burst figures (a steady 50 A on an XT60
  passed); no rating had a source; spade and Micro-Fit ratings ignored the wire; no wire-fits-terminal check (the demo could not
  be built); Micro-Fit part numbers wrong (nonexistent housing, wrong gender); labels on top of wires.
- Firmware (7): power budget; push button drawn as "limit switch"; overlapping peripheral wires; "5e-05 A"; undeclared servo
  supply silent; undefined PIN_ constant misreported, literal pins not flagged; no per-pin current check.
- Pneumatics (6): CAP/ROD lines drawn joined (model was right) + new check that a cylinder's ports never share a line; tubes
  through symbols and along each other; supply entering the FRL from below; phantom lubricator; ambiguous tube sizes / fittings;
  "dead-headed" with no reason.
- Motion (5): plan view of a planar arm; labels on links; a slide not drawn as a slide; misleading mass rollup; unreadable
  payload() error.
- Editor (1): warnings counted as passes.
- CAD (0): fixtures behaved; the thin-wall check caught a real defect in my own fixture.

## Open issues, by impact

1. Motion: no check that an arm stays above its base plane / the table (the two-link arm can reach z < 0). Needs your call (below).
2. Motion: RATED_FRACTION = 0.5 x stall is an estimate; ROBOTIS recommends 1/5 of stall for the XL320. Changing it re-grades
   every arm (below).
3. Wires: K_COPPER = 10.75 is copper near 29 C; at a 75 C conductor (NEC practice, 12.9) drops are ~20 % higher (below).
4. Pneumatics: no component pressure ratings (a 12 bar supply into a 10 bar cylinder passes). Needs a max_bar field and data.
5. Pneumatics: the regulator, FRL, flow-control and silencer default C, b values are ESTIMATEs (the valve's now cites SMC).
6. CAD: the default review image of an assembly with a lid shows only the closed box; the agent must ask for a section.
7. Pneumatics: tubes carry no OD, so the BOM cannot name the fitting size; the lubricator symbol is not the ISO 1219 one.
8. Wires: the BOM has no male Micro-Fit terminal for 26-30 AWG and no verified single-row plug housing number (left blank).
9. Tests: `test_web_browser::test_live_building_in_the_browser` (streaming timing) and the WebGL viewer tests are timing-
   sensitive under load or with the display asleep; they pass alone. Pre-existing.
10. v0.5.0's annotated tag message lost its comment lines to `git tag -F` (from before tonight); retag only if you want to.

## Decisions I made for you

- Live agent runs were impossible without an API key (the app runs Claude Code with --bare); every project is a fixture with
  scripted edits. No key was used.
- A supply pin whose loads give no current is a WARN ("not graded"), not a FAIL.
- When a manufacturer's own figures disagree, the lower one is used (XT60 30 A, not 35 A; Micro-Fit 5 A on the 20-24 AWG terminal).
- Between an XT60/XT90's continuous and short-time rating the check warns ("fine for peaks under a minute"); the quad demo now
  shows 5 true warnings rather than being redesigned, and AGENTS.md says so.
- The quad demo's flight-controller lead changed from 22 to 24 AWG (22 does not fit a JST PH contact). The servo-sweep demo declares
  power="external".
- A literal pin number for a declared pin is a WARN, not a FAIL; an undefined PIN_ constant is a FAIL.
- Kept this Mac awake with caffeinate during the runs (idle sleep is 1 minute and the display going to sleep turned WebGL tests
  into software rendering). Stopped at the end. No dev servers are left running.

## Needs your decision

1. Should motion check a ground / base plane (WARN when the tip or any link goes below z = 0, with an opt-out such as
   `m.floor(z_mm)`)? I left it out because some arms are meant to reach below their mount.
2. RATED_FRACTION: keep 0.5 x stall, or move to a more conservative figure (ROBOTIS XL320: 1/5)? It changes every arm's grade.
3. K_COPPER: keep 10.75 (copper near 29 C) or use 12.9 (75 C, NEC practice)? Drops go up ~20 % in every harness.
4. Whether to add live-run support through a Claude Code login (it would change how eitri authenticates).

# Your decisions, carried out (2026-10-26)

1. Ground plane: on by default. "stays above its base plane" warns when any part reaches below z = 0 of the base;
   `m.mount("edge" | "ceiling" | "wall")` opts a project out (the reference 3-DOF arm is declared clamped at a bench edge, since
   its pick target needs it to fold below its base). Tested both ways. Commit 491f0a1.
2. Servo continuous torque: the datasheet's rated torque when the actuator catalog has one (Feetech STS3215: 6.5 kg·cm continuous of
   19.5 at 6 V, source recorded); otherwise ASSUMED_RATED_FRACTION = 1/3 of stall, named in the message as "assumed, not a spec".
   Demos re-graded (unchanged results); AGENTS.md updated. Commit 07a3883.
3. Voltage drop: K = 12.9, copper at 75 C (NEC Chapter 9 Table 8), tests and the README / reference updated. Commit 173bd0c.
4. Claude Code login: no. eitri stays API-key only (Claude Code runs with --bare and never reads OAuth or the keychain). Decided
   2026-10-26; not to be revisited without a new decision.

Then: pneumatic pressure ratings (vendor defaults per kind, each sourced; FAIL naming the lowest-rated component reached), and the
0.5.1 release.
