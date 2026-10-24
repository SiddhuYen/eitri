"""The wire-harness domain behind the registry interface (eitri.domains)."""
from __future__ import annotations

from pathlib import Path

from . import build as _build
from . import edits as _edits
from . import report as _report
from .inspect import harness_scope
from .model import Harness

KIND = "harness"
LABEL = "wire harness"
NOUN = "Harness"
EDIT_OPS = _edits.HARNESS_OPS
EDITOR_JS = "harness.js"
DOWNLOADS = {
    "svg": ("svg", "image/svg+xml", ".harness.svg"), "png": ("png", "image/png", ".png"),
    "bom": ("csv", "text/csv", ".bom.csv"), "bom_md": ("bom_md", "text/markdown", ".bom.md"),
    "cutlist": ("cutlist_csv", "text/csv", ".cutlist.csv"), "cutlist_md": ("cutlist_md", "text/markdown", ".cutlist.md"),
    "wireviz": ("wireviz", "text/yaml", ".wireviz.yaml"),
}
BOM_KEYS = {"md": "bom_md", "csv": "csv", "cutlist_md": "cutlist_md", "cutlist_csv": "cutlist_csv", "files": ("bom_md", "csv", "cutlist_md", "cutlist_csv", "wireviz")}
REVIEW_HINT = ("The image is the harness diagram: failing wires and connectors are outlined red, warnings amber, and the failing checks "
               "are listed under it. Check it against the request before finishing.")
ATTENTION_HINT = "Fix the design (gauge, length, splices, nets); never delete or loosen a check to make it pass."
EXPORT_DOC = "Its outputs (diagram, BOM, cut list, WireViz YAML) are written by every build; see `export`."
INSPECT_DOC = ("`h` (the built Harness, also `parts`), `p`, and nets() (every net: voltage, current, computed drop, wires), "
               "wires(net=None) (ends, gauge, colour, length, current, ampacity used, drop per wire), connectors() (what is on each pin), "
               'drop("BATT.+", "ESC1.+") (volts and % lost along the wires between two endpoints), lengths() (wire to buy by gauge and '
               "colour) and report() (the text report again)")


def model_class() -> type:
    return Harness


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
    return harness_scope(built, params)


def describe_selection(data: dict, ids: list[str], checks=None) -> str:
    return _report.describe_selection(data, ids, checks)


def describe_state(built) -> dict:
    return _edits.describe_state(built)


def prompt_text() -> str:
    from ..agent.prompt import domain_prompt_text
    return domain_prompt_text("harness")


def reference_text() -> str:
    from ..agent.prompt import harness_reference_text
    return harness_reference_text()
