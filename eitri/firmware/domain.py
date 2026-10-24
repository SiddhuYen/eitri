"""The firmware domain behind the registry interface (eitri.domains)."""
from __future__ import annotations

from pathlib import Path

from . import build as _build
from . import edits as _edits
from . import report as _report
from .inspect import firmware_scope
from .model import Firmware

KIND = "firmware"
LABEL = "firmware project"
NOUN = "Firmware"
EDIT_OPS = _edits.FIRMWARE_OPS
EDITOR_JS = "firmware.js"
DOWNLOADS = {
    "svg": ("svg", "image/svg+xml", ".pinout.svg"), "png": ("png", "image/png", ".png"),
    "pinout": ("pinout_csv", "text/csv", ".pinout.csv"), "pinout_md": ("pinout_md", "text/markdown", ".pinout.md"),
    "header": ("header", "text/x-c", "_eitri_pins.h"), "log": ("compile_log", "text/plain", ".compile.log"),
}
BOM_KEYS = None
REVIEW_HINT = ("The image is the pinout: every declared pin on the board with its constant, direction and role, the peripherals wired "
               "to their pins, and the compile verdict; pins failing a check are red, warnings amber, and the failing checks are listed "
               "under it. Check it against the request before finishing.")
ATTENTION_HINT = "Fix the firmware (the pinout or the sources, until both agree and it compiles); never delete or loosen a check to make it pass."
EXPORT_DOC = "Its outputs (pinout table and diagram, eitri_pins.h, the sketch folder, the compile log) are written by every build; see `export`."
INSPECT_DOC = "`fw` (the built Firmware), `p`, pins(), board(), board_info(key), sources(), header(), compile_log()"


def model_class() -> type:
    return Firmware


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
    return firmware_scope(built, params)


def describe_selection(data: dict, ids: list[str], checks=None) -> str:
    return _report.describe_selection(data, ids, checks)


def describe_state(built) -> dict:
    return _edits.describe_state(built)


def prompt_text() -> str:
    from ..agent.prompt import domain_prompt_text
    return domain_prompt_text("firmware")


def reference_text() -> str:
    from ..agent.prompt import domain_reference_text
    from .boards import boards_table
    return domain_reference_text("firmware", {"BOARDS": boards_table()})
