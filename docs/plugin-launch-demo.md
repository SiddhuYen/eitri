# Launch demo: blank to every check passing, in Claude Code

A screen recording of about 90 s (cut from a real run of about 10 to 20 minutes). It shows the plugin's default: the
combined workspace, which runs on the recording machine (eitri 0.9.0 or newer): no account, no token.

## Before recording
- Install on a clean user account: `curl -fsSL https://eitri.io/install | sh -s -- --plugin`. Record this part
  separately (step 0). Run `eitri setup firmware` once beforehand, so the firmware's compile check runs.
- Use an empty folder: `mkdir ~/demo-arm && cd ~/demo-arm && claude`.
- Lay out the screen: Claude Code on the left half, the browser on the right (the local viewer at 127.0.0.1:879x, which
  opens at the first build: a tab per model file).
- Turn off notifications. Use a big terminal font (18 pt or more).

## Shots
0. **Install (10 s, sped up).** The one curl line. Cut to the `ok  Claude Code: plugin eitri@eitri` line and
   `Done. Now run  claude`.
1. **Ask (5 s).** Type:
   > Design a small desktop robot arm that picks up a 20 mm block, with an Arduino Uno, servos and wiring.

   This is the exact prompt of the plugin end-to-end run (reports/2026-11-06-plugin-e2e.md). Adding "and its circuit" brings in the electronics file too (checked against the harness, the firmware and the CAD).
2. **Routing (5 s).** Claude loads the eitri `design` skill and says one line: *"Using eitri's combined workspace:
   this arm needs its servos, wiring and power checked together."* Keep it on screen.
3. **Project (5 s).** `combined_reference`, then `combined_new_project`. The viewer link it prints opens in the browser at
   the first build.
4. **First build (15 s).** `combined_put_files` (the structure and motion files), then `combined_build`. Zoom in on the
   result: a red `torque joint shoulder` (needed vs stall, with the datasheet source) and the next steps (*"wiring not
   built yet"*, *"firmware not built yet"*).
5. **The loop (30 s, sped up 8-16x).** Claude swaps the servo, adds the harness and the firmware, and rebuilds. The
   check list fills in and turns from red to green. Freeze on one cross-domain check passing, for example servo
   stall current against the supply rail, or wire gauge against the servo current.
6. **Done (10 s).** Every check passes and the next steps list is empty. Claude replies with the tightest margins and
   the view link. Click through the file tabs in the browser.
7. **End card (5 s).** "eitri: the checks your machine has to pass, inside the agent you already use.
   `curl -fsSL https://eitri.io/install | sh -s -- --plugin`". Then: everything on your computer, no account,
   open source.

## B-roll: one local domain (no account)
Ask *"A wall bracket for a 25 mm pipe, 40 mm off the wall, two M4 countersunk holes, PLA"* in an empty folder. The
`design` skill picks local CAD. The viewer opens on the first build, a verify() check fails, Claude fixes it, and the
PROJECT STATUS line reads *every check passes*. The recorded run of this task is in
`reports/2026-11-06-plugin-core.md`.

## If something goes wrong on camera
- If the `combined_*` tools are missing, eitri is older than 0.9.0 or the server did not connect: `eitri doctor`, then
  `/mcp` → eitri-combined → Reconnect.
- If a build runs long (the result says it goes on in the background), cut. Don't wait on camera.
- Don't edit the transcript or the check results. Retake instead. The demo's claim is that the checks are real.
