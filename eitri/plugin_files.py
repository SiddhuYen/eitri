"""The generated files of the eitri plugin bundle (eitri/plugin_bundle/): the six single-domain skills, made from the
app's own domain prompts so the two cannot drift; the Cursor rule; and the plugin's version.

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
`mcp__plugin_eitri_eitri__build_model`). No API key and no account are needed; nothing leaves the machine.
1. Open a project first: `new_project(name, goal)` (it goes in ~/eitri-projects/<name>; pass `folder` when the user wants
   it somewhere else, such as a folder of their repo) or `open_project(folder)` to continue one (`list_projects()`).
2. Read `{ref}()` before writing the model file.
3. Every build result ends with **PROJECT STATUS**: what is built, each failing or warning check with its numbers,
   files changed on disk but not rebuilt, and **STILL TO DO**. Work that list down; `project_status()` shows it again.
   You may also edit the model file with your own file tools, then call `rebuild()`.
4. The user watches the design in the eitri viewer, which opens in their browser at the first build and follows every
   rebuild (`open_viewer()` gives the link again). Give them the link.

"""


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
    return front + _PREAMBLE.format(title=title, kind=kind if kind != "cad" else "CAD", ref=ref) + text + "\n"


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


def generated() -> dict[str, str]:
    """{path inside the bundle: content} of every generated file."""
    out = {f"skills/{name}/SKILL.md": domain_skill(name) for name in DOMAINS}
    out["cursor/eitri.mdc"] = cursor_rule()
    out[".claude-plugin/plugin.json"] = plugin_json()
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
    return changed
