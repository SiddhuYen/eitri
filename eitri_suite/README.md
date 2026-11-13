# eitri_suite: combined (multi-domain) projects

A combined project is one machine described by several model files side by side, one per domain (CAD, wire harness,
mechanism, pneumatics, firmware, electronics). Each file builds and is graded by its own domain's checks; this package adds
the checks **between** the files and the tools around them:

- `crossdomain.py`: CAD <-> harness route fit and mating faces, firmware pinout <-> harness.
- `coupling.py`: mechanism <-> CAD (link masses from the CAD bodies, torque re-run with them, self-collision).
- `kinematics.py`: actuators at their joints, every body attached, no interference at rest.
- `assembly.py`: the harness placed at the CAD model's anchors, board pin layouts, the wires' 3D paths and cut lengths,
  every firmware pin wired, the firmware's SEQUENCE as joint poses.
- `stability.py`: a free-standing base does not tip over; `power.py`: the supply covers the actuators that move together.
- `electronics.py` (+ `elec_sources.py`): the circuit against the harness, the firmware, the mechanism and the CAD model.
- `catalog.py`, `bom.py`, `buildpkg.py`: the parts catalog, the consolidated BOM and the build package.
- `rules.py`: every check's rule id and sources (core `eitri.check_sources`).
- `workspace.py`: the local combined workspace (projects as folders, builds on this computer) behind the `combined_*` MCP
  tools (`eitri mcp --combined`, the plugin's `eitri-combined` server).
- `demos/`: reference projects. `uno-arm` (an Arduino Uno arm with anchors, targets, a pick-and-place sequence and its
  circuit), `robot-arm` (ESP32 + PCA9685, the route-fit reference), `desk-gripper`.

`integration_ref.md` is what an agent reads to write the declarations (`combined_reference('integration')`).

## How it plugs into core

Core registers the suite as a build extension in every build process (`eitri.extensions.load`). It runs only when
`eitri_suite.enabled()` says so: `EITRI_SUITE=1` forces it on, `EITRI_SUITE=0` off, and otherwise it is on for a project
folder whose `eitri.json` says `"workspace": "combined"` (what `combined_new_project` writes). A single-domain project never
runs it. What each model file declares (routes, mating faces, coupled links, mapped pins) is handed back as the extension's
guard: a build that declares less than an earlier one fails "suite: declared checks are kept".

`sandbox.py` + `jobs.py` are the same for a sandboxed build service: a job carrying `"suite": true` gets the suite, and the
`suite_bom` / `suite_package` job kinds.

Tests: `tests/suite` (unit and build tests) and `tests/test_combined_local.py` (the local workspace and its MCP tools).

The checks are only as good as the models and figures behind them: catalog entries not compared with a datasheet are marked
unverified, and a check missing an input says "not run" (which is not a pass).
