# Launch demo: blank to every check passing, in Claude Code

A screen recording of about 90 s (cut from a real run of about 10 to 20 minutes). It shows the plugin's default: the
combined workspace. It needs eitri.io's remote MCP server (eitri-hosted branch `remote-mcp`) to be live, and a token.

## Before recording
- Install on a clean user account: `curl -fsSL https://eitri.io/install | sh -s -- --plugin`. Record this part
  separately (step 0). Blur the token when it is pasted.
- Use an empty folder: `mkdir ~/demo-arm && cd ~/demo-arm && claude`.
- Lay out the screen: Claude Code on the left half, the browser on the right (the eitri.io project view for the
  combined run, or the local viewer at 127.0.0.1:879x for the single-domain B-roll).
- Turn off notifications. Use a big terminal font (18 pt or more).

## Shots
0. **Install (10 s, sped up).** The one curl line. Cut to the `ok  Claude Code: plugin eitri@eitri` line and the
   `saved eitr…` line from `eitri plugin login`.
1. **Ask (5 s).** Type:
   > Design a 2-DOF desk robot arm that lifts 100 g at 150 mm reach. Hobby servos, an Arduino Nano, a 5 V wall
   > supply, and the wiring. Make every check pass.
2. **Routing (5 s).** Claude loads the eitri `design` skill and says one line: *"Using eitri's combined workspace:
   this arm needs its servos, wiring and power checked together."* It then gives the privacy line once. Keep both
   on screen.
3. **Project (5 s).** `combined_reference`, then `combined_new_project`. Click the view link it prints. The browser
   on the right opens the empty assembly.
4. **First build (15 s).** `combined_put_files` (the structure and motion files), then `combined_build`. Zoom in on the
   result: a red `torque joint shoulder` (needed vs stall, with the datasheet source) and the next steps (*"wiring not
   built yet"*, *"electronics not built yet"*).
5. **The loop (30 s, sped up 8-16x).** Claude swaps the servo, adds the harness and the circuit, and rebuilds. The
   check list fills in and turns from red to green. Freeze on one cross-domain check passing, for example servo
   stall current against the supply rail, or wire gauge against the servo current.
6. **Done (10 s).** Every check passes and the next steps list is empty. Claude replies with the tightest margins and
   the view link. Rotate the assembly in the browser.
7. **End card (5 s).** "eitri: the checks your machine has to pass, inside the agent you already use.
   `curl -fsSL https://eitri.io/install | sh -s -- --plugin`". Then: no telemetry, six local domains, combined on
   eitri.io.

## B-roll: one local domain (no account)
Ask *"A wall bracket for a 25 mm pipe, 40 mm off the wall, two M4 countersunk holes, PLA"* in an empty folder. The
`design` skill picks local CAD. The viewer opens on the first build, a verify() check fails, Claude fixes it, and the
PROJECT STATUS line reads *every check passes*. The recorded run of this task is in
`reports/2026-11-06-plugin-core.md`.

## If something goes wrong on camera
- If the `combined_*` tools are missing, the token is unset or expired: run `eitri plugin login`, then `/mcp` →
  eitri-combined → Reconnect.
- If a build queues (a queue position shows), cut. Don't wait on camera.
- Don't edit the transcript or the check results. Retake instead. The demo's claim is that the checks are real.
