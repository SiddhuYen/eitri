"""The generated files of the eitri plugin bundle (eitri/plugin_bundle/): the six single-domain skills, made from the
app's own domain prompts so the two cannot drift; the Cursor rule; the plugin's version; the server launcher (bin/eitri-mcp,
whose uvx fallback is pinned to this version); and the bundle's README.md (what a plugin directory shows).

`python scripts/gen_plugin.py` writes them; tests/test_plugin.py fails when one is out of date.
The routing skill (skills/design) and the combined-workspace skill (skills/combined) are written by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

BUNDLE = Path(__file__).parent / "plugin_bundle"
_AGENT = Path(__file__).parent / "agent"

# skill name -> (domain kind, prompt file, reference tool, what it is for)
DOMAINS = {
    "cad": ("cad", "prompt.md", "cad_reference",
            "Design one mechanical part or a small assembly (a bracket, an enclosure, a mount, an adapter, a gear, a 3D-print, "
            "CNC or sheet-metal part) with eitri's local CAD tools: a build123d model file whose every build measures the "
            "geometry and runs its verify() checks, with a live viewer, STEP/STL/3MF export, drawings and CAM."),
    "wires": ("harness", "harness_prompt.md", "harness_reference",
              "Design one wire harness, cable or loom with eitri's local tools: connectors from a catalog, wires and splices, "
              "checked on every build for connectivity, ampacity and voltage drop against sourced ratings; diagram, BOM and cut list."),
    "motion": ("mechanism", "mechanism_prompt.md", "mechanism_reference",
               "Design a mechanism on its own (an arm's links and joints, a pan-tilt, a linkage) with eitri's local tools: "
               "graded on every build for reach, worst-pose torque against real actuator figures, speed, joint limits and mass."),
    "pneumatics": ("pneumatics", "pneumatics_prompt.md", "pneumatics_reference",
                   "Design one pneumatic circuit (cylinders, valves, FRL, tubing) with eitri's local tools: checked on every "
                   "build for force, air consumption, ISO 6358 flow sizing, pressure drop and circuit validity."),
    "firmware": ("firmware", "firmware_prompt.md", "firmware_reference",
                 "Write microcontroller firmware from a pinout (Arduino Uno/Nano, ESP32) with eitri's local tools: every build "
                 "checks the pins against their declared roles and compiles the sketch headless with arduino-cli."),
    "electronics": ("electronics", "electronics_prompt.md", "electronics_reference",
                    "Design one breadboard or module-level circuit (a board, supplies, regulators, modules, LEDs, transistors, "
                    "relays, servos) with eitri's local tools: every build checks currents, rail budgets, regulator heat, "
                    "I2C, logic levels, flyback diodes and gate drive against datasheet figures with their sources."),
}

_PREAMBLE = """# eitri: {title} (local, single domain)

Generated from eitri's own {kind} guide by `scripts/gen_plugin.py`; the **design** skill decides when to use it (a
task that spans domains belongs in the combined workspace instead).

**In this plugin.** The tools come from the `eitri` MCP server on this computer (your host may prefix them, e.g.
`mcp__plugin_eitri_eitri__build_model`). No API key and no account are needed; your files never leave the machine.
1. Open a project first: `new_project(name, goal)` (it goes in ~/eitri-projects/<name>; pass `folder` when the user wants
   it somewhere else, such as a folder of their repo) or `open_project(folder)` to continue one (`list_projects()`).
2. Read `{ref}()` before writing the model file.
3. Every build result ends with **PROJECT STATUS**: what is built, each failing or warning check with its numbers,
   files changed on disk but not rebuilt, and **STILL TO DO**. Work that list down; `project_status()` shows it again.
   You may also edit the model file with your own file tools, then call `rebuild()`.
4. The user watches the design in the eitri viewer, which opens in their browser at the first build and follows every
   rebuild (`open_viewer()` gives the link again). Give them the link.

"""


# plugin-only notes per skill, after the preamble: the app's prompts are shared with `eitri web`, whose Claude has no shell
_PLUGIN_NOTES = {
    "firmware": """**arduino-cli in this plugin.** When the `compiles (arduino-cli)` check fails with "compiler unavailable", install it
yourself instead of stopping to ask the user to: say in one visible line what it installs ("Installing arduino-cli and the
Arduino AVR board core, about 65 MB, with Homebrew if you have it, else Arduino's official script into ~/.local/bin"), then
run `eitri setup firmware -y` in your shell (Bash) with a 10-minute timeout, then `rebuild()`. Add `--esp32` for an ESP32 board, and say
that one is about 1.2 GB (run it in the background). Your host still asks the user before the command runs, so it stays
their decision. If it fails, show the user its error and the steps by hand from the check's measured text. It is once per
machine, and it never uploads to a board.

""",
}


def domain_skill(name: str) -> str:
    kind, prompt_file, ref, desc = DOMAINS[name]
    text = (_AGENT / prompt_file).read_text(encoding="utf-8").strip()
    # the app's prompts start with the persona line ("You are eitri, an expert ..."); the skill keeps the rest verbatim
    first, _, rest = text.partition("\n")
    if first.startswith("You are eitri"):
        intro = first.replace("You are eitri, ", "Work as ", 1)
        text = intro + "\n" + rest
    title = {"cad": "mechanical CAD", "harness": "wire harnesses", "mechanism": "mechanisms", "pneumatics": "pneumatics",
             "firmware": "firmware", "electronics": "electronics"}[kind]
    front = f"---\nname: {name}\ndescription: {json.dumps(desc)}\n---\n\n"
    tickets = (_AGENT / "tickets_prompt.md").read_text(encoding="utf-8").strip()
    return (front + _PREAMBLE.format(title=title, kind=kind if kind != "cad" else "CAD", ref=ref) + _PLUGIN_NOTES.get(name, "")
            + text + "\n\n" + tickets + "\n")


def cursor_rule() -> str:
    """A Cursor project rule (.cursor/rules/eitri.mdc): the routing skill's decision, in Cursor's rule format."""
    design = (BUNDLE / "skills" / "design" / "SKILL.md").read_text(encoding="utf-8")
    body = design.split("---", 2)[2].strip()
    return ("---\ndescription: \"eitri hardware design: when the user asks for a part, an enclosure, a harness, a mechanism, an "
            "air circuit, firmware, a circuit or a whole machine, use eitri's MCP tools as below\"\nalwaysApply: false\n---\n\n"
            + body + "\n\nThe per-domain guides are what each `*_reference` tool returns plus the PROJECT STATUS at the end of "
            "every local build result; `domain_guide(domain)` returns the full working guide of a local domain (cad, wires, motion, "
            "pneumatics, firmware, electronics); for the combined workspace call `combined_reference()` first.\n")


def plugin_json() -> str:
    from . import __version__
    data = json.loads((BUNDLE / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    data["version"] = __version__
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# The server launcher. @VERSION@ is the eitri version being released: with no eitri installed, uvx runs exactly that one
# (a plugin directory reviews a bundle that starts a pinned package), on Python 3.12 (plain uvx may pick a Python eitri
# does not support). An installed eitri is used whenever it is new enough.
_LAUNCHER = """#!/bin/sh
# Start eitri's local MCP server in plugin mode (`eitri mcp`, or with --combined the combined workspace) for the agent
# that runs the eitri plugin. Generated by scripts/gen_plugin.py (eitri/plugin_files.py): edit it there.
# It finds the eitri command ($EITRI_BIN, PATH, the uv tool folder) and checks it is new enough; with no eitri
# installed it runs eitri-hardware==@VERSION@ once through uvx. Nothing here starts Claude or reads a login.
set -u
# the CAD kernel loads on first use, not at start: agents give a server ~30 s to answer
EITRI_LAZY_IMPORT=1
export EITRI_LAZY_IMPORT
need="0.9.0"                     # the first release with `eitri mcp --combined` (the local combined workspace)
try() {
  c="$1"; shift
  [ -n "$c" ] && [ -x "$c" ] || return 1
  if ! "$c" mcp --help 2>/dev/null | grep -q -- '--combined'; then
    have="$("$c" --version 2>/dev/null | head -n1)"
    echo "The eitri plugin needs eitri $need or newer (its servers are \\`eitri mcp\\` and \\`eitri mcp --combined\\`); $c is ${have:-an older eitri}." >&2
    echo "Run  eitri update, then restart your agent." >&2
    echo "If eitri update says ${have:-your version} is the newest, eitri $need is not released yet: the plugin cannot run until it is." >&2
    exit 1
  fi
  exec "$c" mcp "$@"
}
try "${EITRI_BIN:-}" "$@"
try "$(command -v eitri 2>/dev/null || true)" "$@"
try "$HOME/.local/bin/eitri" "$@"
if command -v uv >/dev/null 2>&1; then try "$(uv tool dir --bin 2>/dev/null)/eitri" "$@"; fi
for u in "$(command -v uvx 2>/dev/null || true)" "$HOME/.local/bin/uvx"; do
  if [ -n "$u" ] && [ -x "$u" ]; then
    echo "eitri is not installed; running eitri @VERSION@ through uvx (the first run downloads about 390 MB; install eitri to start faster)." >&2
    exec "$u" --python 3.12 --from "eitri-hardware==@VERSION@" eitri mcp "$@"
  fi
done
echo "eitri is not installed. Install it with:  curl -fsSL https://eitri.io/install | sh -s -- --plugin   (or: uv tool install --python 3.12 eitri-hardware==@VERSION@ && eitri plugin install), then restart your agent." >&2
exit 1
"""


def launcher() -> str:
    from . import __version__
    return _LAUNCHER.replace("@VERSION@", __version__)


# The bundle's README.md: what a plugin directory (claude.ai/directory) shows a reviewer and a user. Plain text, no
# "open source" (the license is FSL-1.1-MIT: source-available), and the counts exactly as eitri/telemetry.py sends them.
_README = """# eitri plugin @VERSION@

Hardware design inside your agent, with checks that run on every build, all on your computer. The plugin adds skills and
two local MCP servers to Claude Code: the combined workspace, which designs a whole machine (structure, mechanism, wiring,
firmware, pneumatics and its circuit) and checks every domain against the others, and six single domains (mechanical CAD,
wire harnesses, mechanisms, pneumatics, firmware, electronics). Every check names its source: a datasheet, a standard, a
vendor page, or an assumption that says so. There is no account and no API key: your agent does the design work on its
own subscription, and eitri never calls Claude.

## What it contains

- Skills: `design` (picks the combined workspace or one local domain, and says which), `combined`, and one skill per
  local domain (`cad`, `wires`, `motion`, `pneumatics`, `firmware`, `electronics`).
- Two MCP servers, both stdio processes on your computer, started by `bin/eitri-mcp`: `eitri` (`eitri mcp`, the six
  single domains) and `eitri-combined` (`eitri mcp --combined`, the combined workspace). A browser viewer on
  127.0.0.1 shows each build; it answers only this computer.

## It needs eitri installed

The servers are the `eitri` command (PyPI package `eitri-hardware`, Python 3.12, macOS or Linux; Windows is untested).
Install eitri and register the plugin in one line:

    curl -fsSL https://eitri.io/install | sh -s -- --plugin

With eitri installed, the plugin can also come from its marketplace inside Claude Code (2.1.275 or later):

    /plugin install eitri --marketplace SiddhuYen/eitri

If eitri is not installed, `bin/eitri-mcp` runs `eitri-hardware==@VERSION@` once through uvx instead; that first download
(about 390 MB) usually takes longer than the agent's start-up limit, so install eitri first.

## What it downloads, and when

- Installing: the installer fetches uv from astral.sh if it is missing, then eitri and its dependencies from PyPI (about
  390 MB, or the matching GitHub release archive if PyPI is unreachable). The uvx fallback above downloads the same.
- Firmware only: the first time a design's firmware needs compiling, your agent runs `eitri setup firmware -y` (your agent
  asks you first), which downloads arduino-cli and the Arduino AVR core (about 65 MB) from Arduino or Homebrew;
  `--esp32` adds the ESP32 core from Espressif (about 1.2 GB).
- Updates: only when you run `eitri update` (it asks pypi.org), or once a day if you turned on `eitri update --auto on`.
  Nothing else is downloaded.

## What it sends

eitri sends none of your designs, files or prompts anywhere (your agent talks to its own provider as it always does).
The plugin sends two anonymous counts to
https://eitri.io/api/ping: one `install` event when you run `eitri plugin install`, and one `project_created` event per
new project. Each holds exactly: the event name, a random install id (a uuid made once and kept in eitri's config folder,
tied to no account), the eitri version, the OS (darwin or linux), the agent app (claude-code, codex, cursor or unknown),
and, for `project_created`, the project's domain (combined, cad, wires, motion, pneumatics, firmware or electronics).
Nothing else: no prompts, files, paths, names or errors. eitri.io stores what it receives with the date, not the network
address. Turn the counts off with `eitri telemetry off` (or set `EITRI_TELEMETRY=off`; `EITRI_NO_PING=1` and
`DO_NOT_TRACK=1` work too); `eitri telemetry status` shows what would be sent.

Privacy policy: https://eitri.io/privacy

## License

FSL-1.1-MIT (Functional Source License, Version 1.1, MIT Future License): source-available. You may use, modify and
redistribute it for any purpose except offering a competing commercial product or service; each version becomes
MIT-licensed two years after it is released. Source: https://github.com/SiddhuYen/eitri
"""


def readme() -> str:
    from . import __version__
    return _README.replace("@VERSION@", __version__)


def generated() -> dict[str, str]:
    """{path inside the bundle: content} of every generated file."""
    out = {f"skills/{name}/SKILL.md": domain_skill(name) for name in DOMAINS}
    out["cursor/eitri.mdc"] = cursor_rule()
    out[".claude-plugin/plugin.json"] = plugin_json()
    out["bin/eitri-mcp"] = launcher()
    out["README.md"] = readme()
    return out


def stale() -> list[str]:
    return [rel for rel, text in generated().items()
            if not (BUNDLE / rel).is_file() or (BUNDLE / rel).read_text(encoding="utf-8") != text]


def write() -> list[str]:
    changed = stale()
    for rel, text in generated().items():
        f = BUNDLE / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        if rel.startswith("bin/"):
            f.chmod(0o755)
    return changed
