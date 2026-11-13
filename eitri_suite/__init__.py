"""eitri_suite: the cross-domain checks of a combined (multi-domain) project, as an eitri build extension.

Core registers it in every build process (eitri.extensions.load). It runs only where enabled() says so:

    EITRI_SUITE=1        on, whatever the project (a sandbox job of a combined project, the tests)
    EITRI_SUITE=0        off, whatever the project (a single-domain job that must never see cross-domain checks)
    unset                on when the model's folder is a combined project: its eitri.json says "workspace": "combined"
                         (the local combined workspace, eitri_suite.workspace, writes that when it makes the project)

The folder test is read in the build process itself, so the warm build worker (which keeps the environment it started
with) gets it right per build. In every other build the extension returns nothing: no checks, no data, no guard.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

NAME = "suite"
ENV = "EITRI_SUITE"
MARKER_KEY, MARKER_VALUE = "workspace", "combined"        # eitri.json: {"workspace": "combined", ...}


def is_combined_folder(folder: str | Path | None) -> bool:
    if folder is None:
        return False
    try:
        state = json.loads((Path(folder) / "eitri.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and state.get(MARKER_KEY) == MARKER_VALUE


def enabled(folder: str | Path | None = None) -> bool:
    v = os.environ.get(ENV, "").strip()
    if v == "1":
        return True
    if v == "0":
        return False
    return is_combined_folder(folder)


def run(ctx):
    """The extension: the mechanism check, the cross-domain checks and the declared-checks guard data for one build."""
    from eitri.extensions import Outcome
    if not enabled(Path(ctx.model_path).resolve().parent):
        return None
    from . import projectchecks
    checks, data = projectchecks.extras(ctx.kind, ctx.module, ctx.built, ctx.model_path)
    return Outcome(checks=checks, data=data or None, guard=projectchecks.flat_declared(ctx.module))


def register() -> None:
    from eitri import extensions
    extensions.register(NAME, run)
