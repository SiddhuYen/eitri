---
name: design
description: Start here for any hardware design request - a robot, a machine, a rover, a plotter, a gripper, a part, an enclosure, a bracket, a wire harness, a linkage, an air circuit, microcontroller firmware or a breadboard circuit. Picks eitri's combined multi-domain workspace (the default) or one local single domain, says which, and hands over to that skill.
---

# eitri: pick the workspace

eitri designs hardware as plain Python model files. Every build runs checks with fixed limits, and each limit cites its
source (a datasheet, a standard, a measured figure). You write the files and fix the design until every check passes.
eitri never calls a model itself and needs no API key, account or token. Both workspaces run on this computer:

- **Combined workspace (default)**: the `eitri-combined` server's `combined_*` tools. One project holds the domains of a
  machine (structure, mechanism, wiring, firmware, air, and its circuit). Each build checks them together: the arm's torque
  against its servos, the servos' current against the supply, the wires against that current, the board's holes against the
  CAD. Then follow the **combined** skill.
- **One local domain**: the `eitri` server's tools. Use it only when the task clearly fits one domain and nothing else needs
  checking with it:
  - **cad**: one part or a small assembly (a bracket, an enclosure, a mount, an adapter, a gear, a 3D-print or CNC part)
  - **wires**: one wire harness, cable or loom
  - **motion**: a linkage or an arm's kinematics and torque on its own, with no wiring, structure or firmware
  - **pneumatics**: one air circuit
  - **firmware**: one microcontroller sketch from a pinout
  - **electronics**: one breadboard or module-level circuit

## Decide

1. If the request is a machine or a product with moving parts, wiring or firmware, spans two or more of the domains above,
   or doesn't say, use the **combined workspace**. A robot arm, a pan-tilt camera with its servos and power, a plotter or a
   rover is combined. A machine whose circuit matters (its board, supply, regulator, drivers) puts the circuit in the same
   combined project (elec.py), where it is checked against the harness, the firmware and the CAD.
2. If it clearly fits one domain, use that local domain. A circuit on its own (no machine around it) is the local
   **electronics** domain.
3. Tell the user in one line which one you picked and why, and that they can ask for the other ("Using eitri's
   combined workspace: this arm needs its servos, wiring and power checked together. Say 'local CAD only' to just
   model the parts.").

## If the tools are missing

If the `eitri` or `eitri-combined` tools are missing, eitri isn't installed (or is older than 0.9.0). Tell the user to run
`curl -fsSL https://eitri.io/install | sh -s -- --plugin` (or `eitri update && eitri plugin install`), then restart the
agent. If a server fails to connect, `eitri doctor` in a terminal says why.

## Privacy

Everything the local servers do stays on this computer. eitri's plugin sends two anonymous counts (an install, and a project
created: a random id, the eitri version, the OS, the agent app and the project's domain, nothing else); the user turns them
off with `eitri telemetry off`. Say so if the user asks what eitri sends.

## Then

Open the skill you picked (**combined**, or **cad**, **wires**, **motion**, **pneumatics**, **firmware**,
**electronics**) and follow it. Two rules hold everywhere:
- Never finish while a check fails or did not run. Fix the design, never the check.
- Never finish without looking at the latest review image or view.
