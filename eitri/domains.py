"""The design domains eitri knows, and how the build pipeline tells them apart.

A model file's domain is its import: `from eitri import *` is mechanical CAD (build(p) returns build123d geometry); `from
eitri.<kind> import *` is one of the other domains, whose build(p) returns that domain's model object. Every non-CAD domain is a
package eitri/<kind>/ with a `domain` module that presents the same small interface, so the runner, the project, the agent's tools
and the browser editor dispatch on the kind and never on a domain's types:

    KIND, LABEL, NOUN            "harness", "wire harness", "Harness" (what build(p) returns)
    model_class()                the class build(p) returns
    build(model, built, model_path, out_dir, result, stage, clean_traceback, render, guards)   the runner's build of this domain
    apply_edits(built, edits)    direct edits from the model file's EDITS list -> EditNotes
    apply_layout(built, layout)  optional: LAYOUT hints from the model file (editor drag positions)
    EDIT_OPS, describe_edit(e), merge_edit(prev, new)
    inspect_scope(built, params) names for the agent's inspect tool;  INSPECT_DOC describes them
    describe_selection(data, ids, checks)   an editor selection, as text for the agent
    describe_state(built)        {name: plain values} for compare_revisions
    DOWNLOADS                    /download?fmt=... -> (output key, content type, file suffix)
    BOM_KEYS                     {"md": output key, "csv": output key, ...} for `eitri bom`, or None
    REVIEW_HINT, ATTENTION_HINT, EXPORT_DOC     sentences the agent's build result uses
    EDITOR_JS                    the browser editor module under web/assets/
    prompt_text(), reference_text()             the agent's system prompt and reference for this domain

The CAD domain is not in this registry: its kind is "cad" and the pipeline's default path is its code.
"""
from __future__ import annotations

import importlib
import importlib.util
import re
from typing import Any

KINDS = ("harness", "mechanism", "pneumatics", "firmware")
LABELS = {"harness": "wire harness", "mechanism": "mechanism", "pneumatics": "pneumatic circuit", "firmware": "firmware project"}
_IMPORT_RE = {k: re.compile(rf"^\s*(from\s+eitri\.{k}\b|import\s+eitri\.{k}\b)", re.M) for k in KINDS}
_OPS: dict[str, str] | None = None
_AVAILABLE: tuple[str, ...] | None = None


def kind_of_source(code: str | None) -> str:
    """The domain a model file belongs to, from its import line ("cad" when it imports eitri itself)."""
    if not code:
        return "cad"
    for k, rx in _IMPORT_RE.items():
        if rx.search(code):
            return k
    return "cad"


def module(kind: str):
    """The domain module eitri.<kind>.domain."""
    if kind not in KINDS:
        raise KeyError(f"unknown domain {kind!r}; known: {', '.join(KINDS)}")
    return importlib.import_module(f"eitri.{kind}.domain")


def available() -> tuple[str, ...]:
    """The domains whose package is installed (a build must not fail because one is missing)."""
    global _AVAILABLE
    if _AVAILABLE is None:
        found = []
        for k in KINDS:
            try:
                if importlib.util.find_spec(f"eitri.{k}.domain") is not None:
                    found.append(k)
            except (ImportError, ValueError):            # a folder that is not a package (a domain being written)
                continue
        _AVAILABLE = tuple(found)
    return _AVAILABLE


def kind_of_object(obj: Any) -> str | None:
    """Which domain's model object this is (None for CAD geometry or anything else)."""
    if obj is None or isinstance(obj, (dict, list, tuple)):
        return None
    for k in available():
        if isinstance(obj, module(k).model_class()):
            return k
    return None


def op_owner(op: str | None) -> str | None:
    """The domain whose direct-edit op this is (None: a CAD edit)."""
    global _OPS
    if _OPS is None:
        _OPS = {}
        for k in available():
            for o in module(k).EDIT_OPS:
                _OPS[o] = k
    return _OPS.get(str(op))


def label(kind: str) -> str:
    return LABELS.get(kind, kind)
