# Setting up eitri (instructions for AI agents)

You are helping a person install and start **eitri**, a local AI CAD app: a browser CAD editor plus
chat with Claude, running on their own computer. Follow these steps in order. Every command is
non-interactive unless noted, and each step says what success looks like. If a step's output does not
match, stop and use **Troubleshooting** at the end.

Requirements: macOS or Linux (tested on macOS on Apple Silicon and Ubuntu 24.04; Windows is untested; use WSL),
internet access during install, about 1 GB of disk. The install downloads about 390 MB.
Python is **not** required in advance: the installer uses `uv`, which fetches Python 3.12 itself.

## 1. Install

Pick ONE of these.

**A. The one-line installer (recommended):**

```sh
curl -fsSL https://eitri.io/install | sh
```

(The same script is `scripts/install-remote.sh` in the repository:
`curl -fsSL https://raw.githubusercontent.com/SiddhuYen/eitri/main/scripts/install-remote.sh | sh`.)

**B. Already have `uv` (https://docs.astral.sh/uv/):**

```sh
uv tool install --python 3.12 eitri-hardware
eitri doctor
```

**C. From a clone of the repository:**

```sh
git clone https://github.com/SiddhuYen/eitri eitri
cd eitri
./install.sh
```

The installer (A, and C, which runs the same script on the clone) installs `uv` if it is missing (from
`https://astral.sh/uv/install.sh`), installs eitri as a command-line tool in its own environment with
`uv tool install --python 3.12 eitri-hardware` (the CAD kernel and Claude Code, which comes inside Anthropic's
`claude-agent-sdk` package from PyPI: about 390 MB of downloads, 1 to 5 minutes), runs `eitri doctor` (up to a
minute the first time), and adds the tool folder to the shell `PATH`. It needs no sudo. Set
`EITRI_NO_PATH_EDIT=1` to leave the shell profile alone (then call eitri by the full path it prints),
`EITRI_NO_UV_INSTALL=1` to stop it from installing uv, `EITRI_VERSION=0.7.0` to pin a version. If the
PyPI is unreachable, the installer installs the matching GitHub release archive instead. An older eitri installed as
`eitri-cad` (the package name up to 0.5.6) is uninstalled first; projects, key and settings are kept.

**Success:** the install ends with `eitri doctor` output (step 2) and then:

```
Done. Next: run   eitri
```

If it printed `Note: ... was added to your shell's PATH`, open a new shell (or run the `export PATH=...`
line it printed) before the next step.

## 2. Verify

```sh
eitri doctor
```

**Success** (exit code 0; versions and timings will differ):

```
eitri doctor
  ok   python: 3.12.x (...)
  ok   eitri: 0.7.0 (installed from PyPI with uv tool)
  ok   CAD kernel: build123d 0.13.0, OCP 8.0.1.1.0
  ok   test build: 20 mm cube, volume 8000 mm^3, STEP + report + review image in 2.0 s
  ok   Claude Code binary: 2.1.301 (Claude Code) (from Anthropic's claude-agent-sdk package); API-key-only mode (--bare) supported
  warn API key: not set - the editor and the demo projects work without one; ...
  ok   demo projects: V8 engine kit, Wall racket holder, Marble maze
  info firmware toolchain: arduino-cli not installed - only firmware projects need it; `eitri setup firmware` installs it
eitri is ready. Next: run `eitri` - it opens http://127.0.0.1:8765 in your browser.
```

Every line starts with `ok`, `info`, `warn` or `FAIL`. `warn` on the API key is fine at this point; `info` marks
something optional that is not installed (the firmware toolchain: only firmware projects need it). A `warn` on `eitri` means a newer version is known: `eitri update` installs it. Any `FAIL`
means the install is broken (see Troubleshooting). For machine-readable output: `eitri doctor --json`
(`{"ready": true, "checks": [...], "next": "..."}`).

## 3. The Anthropic API key (optional, needed to chat with Claude)

eitri uses the person's **own Anthropic API key**; requests are billed to it by Anthropic. It never uses
a Claude.ai subscription, even if one is logged in on the machine. Without a key, the editor, the
exports and the three demo projects (recorded sessions, replayed with no API calls) all work.

Ask the person for a key (they create one at https://console.anthropic.com/settings/keys). Do not invent,
log or echo it. Then either:

```sh
printf '%s' "$KEY" | eitri key set        # validates it with one free API call, saves it
```

**Success:** `saved sk-ant-…xxxx to <config folder>/config.json (readable only by you)`. A bad key prints
`not saved: Anthropic rejected this key (invalid or revoked)` and exits 1.

or tell them they can paste it on eitri's home page instead, or set `ANTHROPIC_API_KEY` in the
environment eitri is started from (that takes precedence over the saved key).

Check it: `eitri key show` (prints the masked key and where it comes from); `eitri doctor --online`
validates it with Anthropic.

Cost, measured: about **$0.24 per request** on average across the 22-task benchmark (range $0.07 to
$1.10 per task); long design sessions cost more (the recorded V8 engine session in the demos: $3.21).

## 4. Launch

```sh
eitri
```

This starts the local website, loads the CAD engine in the background and opens the default browser.
**Success** (the port may be 8766–8774 if 8765 is taken):

```
eitri web  http://127.0.0.1:8765/
projects: /Users/<name>/eitri-projects
Claude: Claude · API key - requests are billed to ...       (or: No API key yet - ...)
Ctrl-C stops the server
```

It keeps running in the foreground; Ctrl-C stops it. If you (the agent) run it, run it in the background
and give the person the URL: `eitri web --no-open` prints the URL without opening a browser. A second
`eitri` while one is running just reopens the running one. The server only answers this computer.

Tell the person: the home page shows **Try it without a key** (three recorded demos; "Watch replay"
replays a real Claude session in the editor) and a box to describe a part when a key is set.

## As a plugin in the person's own agent (instead of steps 3-4)

If the person wants eitri inside the agent they already use (Claude Code, Codex or Cursor), on that agent's own
subscription with no Anthropic API key, run after step 2. **This needs eitri 0.7.0 or newer** (`eitri --version`; the
release that adds `eitri plugin` and `eitri mcp`). On an older eitri, run `eitri update` first; if it reports an older
version as the newest, the plugin is not released yet, so stop and tell the person. Do not run `eitri plugin ...` on an
older eitri: it reads `plugin` as a project folder.

```sh
eitri plugin install          # registers the plugin in every agent app found
eitri plugin login            # opens eitri.io/account; the person makes a token and pastes it (hidden prompt)
```

(or `curl -fsSL https://eitri.io/install | sh -s -- --plugin` does install + plugin + login in one go).
**Success:** one `ok` line per agent app found, then `saved eitri…xxxx to <config folder>/plugin-token`. A token
eitri.io rejects prints `not saved: eitri.io rejected this token` and exits 1. Do not invent or echo the token;
`printf '%s' "$TOKEN" | eitri plugin login --token-stdin` works when the person gives it to you. Without a token the
local single-domain tools still work; only the combined workspace (eitri.io) needs one. Tell the person: in combined
mode the model files go to eitri.io, are kept as projects, and are used to train eitri's models unless they turn that
off on eitri.io/account. Then they restart their agent and ask it for a design. `eitri plugin status` shows the state;
`eitri plugin uninstall` removes it.

## Other useful commands (no browser, no API key)

```sh
eitri build path/to/model.py            # build a model file and print its geometry report
eitri print path/to/project             # 3D-print export: parts, orientations, plates, STL + 3MF + zip
eitri bom path/to/project               # harness or pneumatics: bill of materials (--cutlist for harnesses, --csv)
eitri chat path/to/project              # the terminal REPL (needs a key)
eitri --help
```

Projects are folders under `~/eitri-projects` (change with `eitri web <folder>`). Each holds a
plain Python model file (`PARAMS`, `build(p)`, `verify()`).

A project is either mechanical CAD (`from eitri import *`, `build(p)` returns build123d geometry) or a **wire
harness** (`from eitri.harness import *`, `build(p)` returns a `Harness`). `eitri build model.py` works on both:
for a harness it prints the connectivity / ampacity / voltage-drop checks and writes the diagram (SVG + PNG), BOM,
cut list and WireViz YAML to `out/`. `eitri build eitri/demos/quad-power/quad_power.py` is a quick harness smoke test
(it should report `28 pass, 5 warn, 0 FAIL`; the warnings are intended: the connectors run at or past their continuous ratings). No extra install or system package is needed for harnesses.

Four more domains work the same way, each chosen by its model file's import: **mechanism** (`from eitri.mechanism import *`:
links, joints, actuators, targets; reach, worst-pose torque, speed, limits, mass), **pneumatics** (`from eitri.pneumatics import *`:
ISO 1219 circuits; force, ISO 6358 flow, air consumption, pressure drop, circuit validity), **firmware**
(`from eitri.firmware import *`: board, pinout, peripherals, sources; pin checks and an arduino-cli compile) and **electronics**
(`from eitri.electronics import *`: a board, modules, passives, transistors, relays, servos and nets at breadboard level; current,
power, logic-level, I2C, flyback and gate checks, each against a datasheet figure with its source). Smoke tests:

```sh
eitri build eitri/demos/pan-tilt/pan_tilt.py         # 12 pass, 0 warn, 0 FAIL
eitri build eitri/demos/air-clamp/air_clamp.py       # 23 pass, 1 warn, 0 FAIL (the warning is intended: estimated flow data)
eitri build eitri/demos/servo-sweep/servo_sweep.py   # 12 pass, 0 warn, 0 FAIL once arduino-cli is installed (below)
eitri build eitri/demos/uno-arm-circuit/uno_arm_circuit.py   # 32 pass, 0 warn, 0 FAIL
```

Mechanism, pneumatics and electronics need nothing extra (electronics is breadboard level: no PCB layout). **Firmware needs arduino-cli** (a system program, not a Python package). Install it
only if the person wants firmware, and ask them first: it installs system software (with Homebrew on a Mac that has it). Without it, firmware builds still run and report one failed check,
`compiles (arduino-cli)`, saying "compiler unavailable". eitri never uploads to a board, and you must not either.

```sh
eitri setup firmware --dry-run    # show the person what it will run
eitri setup firmware --yes        # install arduino-cli if missing (--yes: no prompt), arduino:avr, Servo; then a test compile
eitri setup firmware --esp32 --yes   # only if they need ESP32 as well
```

**Success:** the last line is `firmware is ready: ...`, after `ok   compiled a test sketch for arduino:avr:uno`.
It skips what is already installed, so running it again is safe. The same steps by hand:

```sh
brew install arduino-cli          # or: curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
arduino-cli core update-index
arduino-cli core install arduino:avr     # Uno and Nano
arduino-cli lib install Servo
# ESP32, only if needed:
arduino-cli config init          # skip if it says the config file already exists
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index && arduino-cli core install esp32:esp32 && arduino-cli lib install ESP32Servo
```

eitri also looks in `~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin`. If arduino-cli is elsewhere, set
`EITRI_ARDUINO_CLI` to its full path in the environment eitri is started from.

## Updating

```sh
eitri update --check   # is there a newer release? (asks pypi.org)
eitri update           # install it (uv tool upgrade eitri-hardware)
```

An install from a clone is updated with `git pull && ./install.sh` in the clone; `eitri update` says so.
eitri never checks for updates on its own unless the person turns it on with `eitri update --auto on`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `eitri: command not found` after install | open a new shell, or run the full path printed by the installer (`$(uv tool dir --bin)/eitri`) |
| `FAIL CAD kernel` or `FAIL test build` | reinstall: `uv tool install --force --python 3.12 eitri-hardware` (or the clone folder); Python 3.10–3.13 only |
| `FAIL Claude Code binary` | the `claude-agent-sdk` wheel is missing for this platform; reinstall, or set `EITRI_CLAUDE_BIN` to a recent `claude` that supports `--bare` |
| `uv` will not install | install it by hand (https://docs.astral.sh/uv/getting-started/installation/), then run the installer again |
| the page says it could not start three.js / WebGL | use a current Chrome, Edge, Firefox or Safari with hardware acceleration on |
| firmware check `compiles (arduino-cli)` says "compiler unavailable" | `eitri setup firmware` (`--esp32` for an ESP32 board), or set `EITRI_ARDUINO_CLI`; then rebuild |
| `eitri update` says it could not reach pypi.org | check the internet connection; or reinstall with the one-line installer |
| chat says `Invalid API key` | `eitri key set` with a valid key (or fix `ANTHROPIC_API_KEY`) |

Network: on its own, eitri contacts nothing but Anthropic's API (with the key). It asks pypi.org for the newest
version only on `eitri update`, `eitri doctor --online`, or once a day if the person ran `eitri update --auto on`.
The installer downloads packages from PyPI (or the release archive from GitHub) and, if needed, uv from astral.sh;
`eitri setup firmware` downloads from Arduino and Espressif. No telemetry.
