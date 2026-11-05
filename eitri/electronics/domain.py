"""The electronics domain behind the registry interface (eitri.domains)."""
from __future__ import annotations

from pathlib import Path

from . import build as _build
from . import edits as _edits
from . import report as _report
from .inspect import electronics_scope
from .model import Circuit

KIND = "electronics"
LABEL = "circuit"
NOUN = "Circuit"
EDIT_OPS = _edits.ELECTRONICS_OPS
EDITOR_JS = "electronics.js"
DOWNLOADS = {
    "svg": ("svg", "image/svg+xml", ".schematic.svg"), "png": ("png", "image/png", ".png"),
    "bom": ("csv", "text/csv", ".bom.csv"), "bom_md": ("bom_md", "text/markdown", ".bom.md"),
    "netlist": ("netlist_csv", "text/csv", ".netlist.csv"), "netlist_md": ("netlist_md", "text/markdown", ".netlist.md"),
    "sources": ("sources_md", "text/markdown", ".sources.md"),
}
# the Export menu and the share page's buttons, in order (eitri.exports); every fmt is a DOWNLOADS key
EXPORTS = (("svg", "Schematic SVG", "The schematic (.svg)"), ("png", "Schematic PNG", "The schematic (.png)"),
           ("bom", "BOM CSV", "Bill of materials (.csv)"), ("bom_md", "BOM MD", "Bill of materials (Markdown)"),
           ("netlist", "Netlist CSV", "Every net and its pins (.csv)"), ("netlist_md", "Netlist MD", "Netlist (Markdown)"),
           ("sources", "Sources MD", "Where every number the checks used comes from (Markdown)"))
BOM_KEYS = {"md": "bom_md", "csv": "csv", "files": ("bom_md", "csv", "netlist_md", "netlist_csv", "sources_md")}
REVIEW_HINT = ("The image is the schematic: rails and grounds are flags at each pin, every other net a routed wire; failing parts and "
               "nets are red, warnings amber, and the failing checks are listed under it. Check it against the request before finishing.")
ATTENTION_HINT = "Fix the circuit (resistor values, supplies, drivers, diodes, pull-ups, level shifting); never delete or loosen a check to make it pass."
EXPORT_DOC = "Its outputs (schematic, BOM, netlist, sources) are written by every build; see `export`."
INSPECT_DOC = ("`c` (the built Circuit, also `parts`), `p`, and nets() (what each net is: rail, ground, driven pin, switched low side), "
               "currents() (every string the kernel solved, series or a piece of a branched network, its worst case and each case), rails() (what each rail supplies, "
               "item by item, against its limit), part(name) (its numbers with their sources), sources() and report()")


def model_class() -> type:
    return Circuit


def build(model, built, model_path: Path, out_dir: Path, result: dict, stage, clean_traceback, render: bool = True, guards: dict | None = None) -> None:
    _build.run(model, built, model_path, out_dir, result, stage, clean_traceback, render=render, guards=guards)


def apply_edits(built, edits: list[dict]) -> list:
    return _edits.apply_edits(built, edits)


def apply_layout(built, layout) -> None:
    built.apply_layout(layout)


def describe_edit(e: dict) -> str:
    return _edits.describe(e)


def merge_edit(prev: dict, new: dict) -> dict | None:
    return _edits.merge_edit(prev, new)


def inspect_scope(built, params) -> dict:
    return electronics_scope(built, params)


def describe_selection(data: dict, ids: list[str], checks=None) -> str:
    return _report.describe_selection(data, ids, checks)


def describe_state(built) -> dict:
    return _edits.describe_state(built)


def prompt_text() -> str:
    from ..agent.prompt import domain_prompt_text
    return domain_prompt_text("electronics")


def reference_text() -> str:
    from ..agent.prompt import domain_reference_text
    from .catalog import catalog_table
    from .sources import sources_table
    return domain_reference_text("electronics", {"CATALOG": catalog_table(), "SOURCES": sources_table()})
