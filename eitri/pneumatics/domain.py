"""The pneumatics domain behind the registry interface (eitri.domains)."""
from __future__ import annotations

from pathlib import Path

from . import build as _build
from . import edits as _edits
from . import report as _report
from .inspect import circuit_scope
from .model import Circuit

KIND = "pneumatics"
LABEL = "pneumatic circuit"
NOUN = "Circuit"
EDIT_OPS = _edits.PNEUMATICS_OPS
EDITOR_JS = "pneumatics.js"
DOWNLOADS = {
    "svg": ("svg", "image/svg+xml", ".schematic.svg"), "png": ("png", "image/png", ".png"),
    "bom": ("csv", "text/csv", ".bom.csv"), "bom_md": ("bom_md", "text/markdown", ".bom.md"),
    "cylinders": ("cylinders_csv", "text/csv", ".cylinders.csv"), "cylinders_md": ("cylinders_md", "text/markdown", ".cylinders.md"),
}
# the Export menu and the share page's buttons, in order (eitri.exports); every fmt is a DOWNLOADS key
EXPORTS = (("svg", "Schematic SVG", "The ISO 1219 schematic (.svg)"), ("png", "Schematic PNG", "The schematic (.png)"),
           ("bom", "BOM CSV", "Bill of materials (.csv)"), ("bom_md", "BOM MD", "Bill of materials (Markdown)"),
           ("cylinders", "Cylinders CSV", "Cylinder sizing table (.csv)"), ("cylinders_md", "Cylinders MD", "Cylinder sizing table (Markdown)"))
BOM_KEYS = {"md": "bom_md", "csv": "csv", "files": ("bom_md", "csv", "cylinders_md", "cylinders_csv")}
REVIEW_HINT = ("The image is the schematic (ISO 1219-style symbols, supply at the bottom, cylinders at the top): components and tubes "
               "failing a check are red, warnings amber, and the failing checks are listed under it. Check it against the request before finishing.")
ATTENTION_HINT = "Fix the design (bore, pressure, valve and tube sizes, the circuit's connections); never delete or loosen a check to make it pass."
EXPORT_DOC = "Its outputs (schematic, BOM, cylinder table) are written by every build; see `export`."
INSPECT_DOC = "`c` (the built Circuit), `p`, cylinders(), paths(), flow(C, b, p1, p2), tube_C(id_mm, length_m), report()"


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
    return circuit_scope(built, params)


def describe_selection(data: dict, ids: list[str], checks=None) -> str:
    return _report.describe_selection(data, ids, checks)


def describe_state(built) -> dict:
    return _edits.describe_state(built)


def prompt_text() -> str:
    from ..agent.prompt import domain_prompt_text
    return domain_prompt_text("pneumatics")


def reference_text() -> str:
    from ..agent.prompt import domain_reference_text
    return domain_reference_text("pneumatics")
