# Setting up eitri (instructions for AI agents)

You are helping a person install and start **eitri**, a local AI CAD app: a browser CAD editor plus
chat with Claude, running on their own computer. Follow these steps in order. Every command is
non-interactive unless noted, and each step says what success looks like. If a step's output does not
match, stop and use **Troubleshooting** at the end.

Requirements: macOS or Linux (tested on macOS on Apple Silicon; the same packages exist for Linux x86-64/arm64;
Windows is untested), internet access during install, about 1 GB of disk.
Python is **not** required in advance: the installer uses `uv`, which fetches Python 3.12 itself.

## 1. Install

Pick ONE of these.

**A. From a clone of the repository (recommended):**

```sh
git clone https://github.com/SiddhuYen/eitri eitri
cd eitri
./install.sh
```

**B. Without cloning (needs `uv`, https://docs.astral.sh/uv/):**

```sh
uv tool install --python 3.12 git+https://github.com/SiddhuYen/eitri
eitri doctor
```

`install.sh` installs `uv` if it is missing (from `https://astral.sh/uv/install.sh`), installs eitri as a
command-line tool in its own environment (the CAD kernel and Claude Code, which comes inside Anthropic's `claude-agent-sdk` package from PyPI: a few hundred MB
of downloads; the first install takes a few minutes), runs `eitri doctor`, and adds the tool folder to the
shell `PATH` (set `EITRI_NO_PATH_EDIT=1` to skip that and call eitri by the full path it prints).

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
  ok   CAD kernel: build123d 0.13.0, OCP 8.0.1.1.0
  ok   test build: 20 mm cube, volume 8000 mm^3, STEP + report + review image in 2.0 s
  ok   Claude Code binary: 2.1.301 (Claude Code) (from Anthropic's claude-agent-sdk package); API-key-only mode (--bare) supported
  warn API key: not set - the editor and the demo projects work without one; ...
  ok   demo projects: V8 engine kit, Wall racket holder, Marble maze
eitri is ready. Next: run `eitri` - it opens http://127.0.0.1:8765 in your browser.
```

Every line starts with `ok`, `warn` or `FAIL`. `warn` on the API key is fine at this point. Any `FAIL`
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
(it should report `32 pass, 0 warn, 0 FAIL`). No extra install or system package is needed for harnesses.

Three more domains work the same way, each chosen by its model file's import: **mechanism** (`from eitri.mechanism import *`:
links, joints, actuators, targets; reach, worst-pose torque, speed, limits, mass), **pneumatics** (`from eitri.pneumatics import *`:
ISO 1219 circuits; force, ISO 6358 flow, air consumption, pressure drop, circuit validity) and **firmware**
(`from eitri.firmware import *`: board, pinout, peripherals, sources; pin checks and an arduino-cli compile). Smoke tests:

```sh
eitri build eitri/demos/pan-tilt/pan_tilt.py         # 11 pass, 0 warn, 0 FAIL
eitri build eitri/demos/air-clamp/air_clamp.py       # 20 pass, 1 warn, 0 FAIL (the warning is intended: estimated flow data)
eitri build eitri/demos/servo-sweep/servo_sweep.py   # 11 pass, 0 warn, 0 FAIL once arduino-cli is installed (below)
```

Mechanism and pneumatics need nothing extra. **Firmware needs arduino-cli** (a system program, not a Python package). Install it
only if the person wants firmware. Without it, firmware builds still run and report one failed check,
`compiles (arduino-cli)`, saying "compiler unavailable". eitri never uploads to a board, and you must not either.

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

If arduino-cli is not on `PATH`, set `EITRI_ARDUINO_CLI` to its full path in the environment eitri is started from.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `eitri: command not found` after install | open a new shell, or run the full path printed by `install.sh` (`$(uv tool dir --bin)/eitri`) |
| `FAIL CAD kernel` or `FAIL test build` | reinstall: `uv tool install --force --python 3.12 <REPO_URL or the clone folder>`; Python 3.10–3.13 only |
| `FAIL Claude Code binary` | the `claude-agent-sdk` wheel is missing for this platform; reinstall, or set `EITRI_CLAUDE_BIN` to a recent `claude` that supports `--bare` |
| `uv` will not install | install it by hand (https://docs.astral.sh/uv/getting-started/installation/), then `./install.sh` again |
| the page says it could not start three.js / WebGL | use a current Chrome, Edge, Firefox or Safari with hardware acceleration on |
| firmware check `compiles (arduino-cli)` says "compiler unavailable" | install arduino-cli and the board core (commands above), or set `EITRI_ARDUINO_CLI`; then rebuild |
| chat says `Invalid API key` | `eitri key set` with a valid key (or fix `ANTHROPIC_API_KEY`) |

Network: eitri itself contacts nothing but Anthropic's API (with the key). The installer downloads
packages from PyPI and, if needed, uv from astral.sh. No telemetry.
