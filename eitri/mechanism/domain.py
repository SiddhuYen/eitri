"""The mechanism domain behind the registry interface (eitri.domains)."""
from __future__ import annotations

from pathlib import Path

from . import build as _build
from . import edits as _edits
from . import report as _report
from .inspect import mechanism_scope
from .model import Mechanism

KIND = "mechanism"
LABEL = "mechanism"
NOUN = "Mechanism"
EDIT_OPS = _edits.MECHANISM_OPS
EDITOR_JS = "mechanism.js"
DOWNLOADS = {
    "svg": ("svg", "image/svg+xml", ".mechanism.svg"), "png": ("png", "image/png", ".png"),
    "joints": ("joints_csv", "text/csv", ".joints.csv"), "joints_md": ("joints_md", "text/markdown", ".joints.md"),
    "mass": ("mass_csv", "text/csv", ".mass.csv"), "mass_md": ("mass_md", "text/markdown", ".mass.md"),
}
BOM_KEYS = {"md": "mass_md", "csv": "mass_csv", "files": ("joints_md", "joints_csv", "mass_md", "mass_csv")}
REVIEW_HINT = ("The image is the kinematic diagram at the home pose (side and plan views), with the worst-load pose ghosted: joints "
               "failing their torque or speed check are red, warnings amber, targets are crosses (green reached, red missed), and the "
               "failing checks are listed under it. Check it against the request before finishing.")
ATTENTION_HINT = "Fix the design (link lengths, masses, actuators, gearing, limits, targets); never delete or loosen a check to make it pass."
EXPORT_DOC = "Its outputs (kinematic diagram, joint/torque table, mass rollup) are written by every build; see `export`."
INSPECT_DOC = "`m` (the built Mechanism), `p`, joints(), links(), fk(**q), torque(**q), reach(target), masses(), report()"


def model_class() -> type:
    return Mechanism


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
    return mechanism_scope(built, params)


def describe_selection(data: dict, ids: list[str], checks=None) -> str:
    return _report.describe_selection(data, ids, checks)


def describe_state(built) -> dict:
    return _edits.describe_state(built)


def prompt_text() -> str:
    from ..agent.prompt import domain_prompt_text
    return domain_prompt_text("mechanism")


def reference_text() -> str:
    from ..agent.prompt import domain_reference_text
    return domain_reference_text("mechanism")
