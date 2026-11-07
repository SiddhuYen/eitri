---
name: design
description: Start here for any hardware design request - a robot, a machine, a rover, a plotter, a gripper, a part, an enclosure, a bracket, a wire harness, a linkage, an air circuit, microcontroller firmware or a breadboard circuit. Picks eitri's combined multi-domain workspace (the default) or one local single domain, says which, and hands over to that skill.
---

# eitri: pick the workspace

eitri designs hardware as plain Python model files. Every build runs checks with fixed limits, and each limit cites its
source (a datasheet, a standard, a measured figure). You write the files and fix the design until every check passes.
eitri never calls a model itself and needs no API key. It has two workspaces:

- **Combined workspace (default)**: the `eitri-combined` server's `combined_*` tools, hosted on eitri.io. One project
  holds every domain of a machine (structure, wiring, motion, air, firmware, electronics). Each build checks them
  together: the arm's torque against its servos, the servos' current against the supply, the wires against that current,
  the board against its enclosure. Then follow the **combined** skill.
- **One local domain**: the `eitri` server's tools, which run on this computer. Use it only when the task clearly fits one
  domain and nothing else needs checking with it:
  - **cad**: one part or a small assembly (a bracket, an enclosure, a mount, an adapter, a gear, a 3D-print or CNC part)
  - **wires**: one wire harness, cable or loom
  - **motion**: a linkage or an arm's kinematics and torque on its own, with no wiring, structure or firmware
  - **pneumatics**: one air circuit
  - **firmware**: one microcontroller sketch from a pinout
  - **electronics**: one breadboard or module-level circuit

## Decide

1. If the request is a machine or a product with moving parts and electronics, spans two or more of the domains above,
   or doesn't say, use the **combined workspace**. A robot arm, a pan-tilt camera with its servos and power, a plotter or
   a rover is combined.
2. If it clearly fits one domain, use that local domain.
3. Tell the user in one line which one you picked and why, and that they can ask for the other ("Using eitri's
   combined workspace: this arm needs its servos, wiring and power checked together. Say 'local CAD only' to just
   model the parts.").

## If the combined tools are missing or refuse the token

The `combined_*` tools may be missing, or a call may come back saying the token is missing or invalid. Then the user
isn't signed in to eitri.io yet. Tell them:

> Run `eitri plugin login` in a terminal. It opens eitri.io/account, where you make a personal token with any GitHub
> sign-in, and saves it. Then restart this agent (in Claude Code, `/mcp` → eitri-combined → Reconnect also works).

Offer to start with the local single-domain tools in the meantime (for example the CAD parts, or the circuit on its own).
Say that the cross-domain checks only run in the combined workspace.

If the local `eitri` tools are missing, eitri isn't installed: `curl -fsSL https://eitri.io/install | sh -s -- --plugin`
(or `uv tool install eitri-hardware && eitri plugin install`), then restart the agent.

## Privacy

The first time you use the combined workspace in a conversation, tell the user once: the model files go to eitri.io, are
kept there as projects (they show in their eitri.io project list), and are used to train eitri's models unless they turn
that off on eitri.io/account. The local tools keep everything on this computer. The plugin sends no telemetry.

## Then

Open the skill you picked (**combined**, or **cad**, **wires**, **motion**, **pneumatics**, **firmware**,
**electronics**) and follow it. Two rules hold everywhere:
- Never finish while a check fails. Fix the design, never the check.
- Never finish without looking at the latest review image or view.
