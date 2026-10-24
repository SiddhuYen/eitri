"""Build extensions: a plugin point for extra checks on every build.

An extension is a function that runs after a model has been built and measured, in the build process, with everything
about the build in a BuildContext. It returns checks that are merged into the report beside the model's own verify()
checks (so the agent sees them, the editor's checks card shows them, and a failing one fails the build's verdict), and
optionally data for result.json and a guard. Register one of three ways:

    eitri.extensions.register("name", fn)                 in process (tests, embedding eitri)
    EITRI_EXTENSIONS=mypkg.checks,other.mod:setup           modules imported by every build process; importing registers
                                                            (or `module:function`, called with no arguments to register)
    an entry point in the "eitri.extensions" group         pip-installed packages; the entry point is a module or a function

    def fn(ctx: BuildContext) -> Outcome | Iterable[Check] | None

Guards keep an extension's checks from being deleted. An Outcome may carry `guard`, a dict describing what the model
declares (counts, mostly). The project remembers, per model file and extension, the largest value seen for each
numeric key (other values: the latest), and hands it back as ctx.guards[name] on the next build. A build whose guard
lacks a key, or has a smaller count, fails a check "<name>: declared checks are kept"; so does a build whose extension
returns no guard while one is remembered. Only Project.release_guard(name) lowers a guard, and the agent has no tool
for that: whatever a model once declared stays declared until a person says otherwise.

An extension that raises becomes a failed check, never a failed build. Builds run in a subprocess (or the warm build
worker), so in-process registration only reaches builds in the same process; EITRI_EXTENSIONS and entry points reach
every build.
"""
from __future__ import annotations

import importlib
import os
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .harness.checks import HCheck as Check   # name, ok, measured, expected, severity (pass|warn|fail), category, refs

ENV_VAR = "EITRI_EXTENSIONS"
ENTRY_POINT_GROUP = "eitri.extensions"


@dataclass
class BuildContext:
    model_path: Path
    out_dir: Path
    kind: str                                   # "cad" | "harness"
    module: Any                                 # the model module (declarations are module-level names)
    params: dict
    built: Any                                  # Parts (cad) | Harness (harness)
    report: Any                                 # Report | HarnessReport, with verify() already in .checks
    result: dict                                # result.json as assembled so far (read it; put data in Outcome.data)
    guards: dict[str, dict] = field(default_factory=dict)      # extension name -> what the project remembers
    progress: Callable[[str], None] = lambda message: None     # the build's stage line (the page shows it)


@dataclass
class Outcome:
    checks: list[Check] = field(default_factory=list)
    data: Any = None                            # stored as result["extensions"][name] (must be JSON-serialisable)
    guard: dict | None = None                   # what this build declares, for the guard


Extension = Callable[[BuildContext], "Outcome | Iterable[Check] | None"]

_REGISTRY: dict[str, Extension] = {}
_LOAD_ERRORS: list[str] = []
_LOADED = False


def register(name: str, fn: Extension) -> None:
    if not name or not callable(fn):
        raise ValueError("register(name, fn): a non-empty name and a callable")
    _REGISTRY[str(name)] = fn


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def registered() -> dict[str, Extension]:
    return dict(_REGISTRY)


def load(force: bool = False) -> list[str]:
    """Import the extensions named by EITRI_EXTENSIONS and the entry points, once per process. Returns the problems
    (each is also reported as a failed check by run())."""
    global _LOADED
    if _LOADED and not force:
        return list(_LOAD_ERRORS)
    _LOADED = True
    _LOAD_ERRORS.clear()
    specs = [s.strip() for s in os.environ.get(ENV_VAR, "").split(",") if s.strip()]
    for spec in specs:
        _import_spec(spec, f"{ENV_VAR} entry {spec!r}")
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        eps = []
    for ep in eps:
        try:
            obj = ep.load()
            if callable(obj):
                obj()
        except Exception as exc:
            _LOAD_ERRORS.append(f"entry point {ep.name!r}: {type(exc).__name__}: {exc}")
    return list(_LOAD_ERRORS)


def _import_spec(spec: str, what: str) -> None:
    mod_name, _, attr = spec.partition(":")
    try:
        mod = importlib.import_module(mod_name)
        if attr:
            getattr(mod, attr)()
    except Exception as exc:
        _LOAD_ERRORS.append(f"{what}: {type(exc).__name__}: {exc}")


def _check(name: str, severity: str, measured: str, expected: str = "", refs: Iterable[str] = ()) -> Check:
    return Check(name=name, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category="extension",
                 refs=list(dict.fromkeys(refs)))


def guard_check(name: str, remembered: dict | None, declared: dict | None) -> Check | None:
    """The guard verdict for one extension (None when nothing is remembered yet)."""
    if not remembered:
        return None
    label = f"{name}: declared checks are kept"
    if declared is None:
        return _check(label, "fail", f"nothing is declared any more; an earlier build declared {remembered}", "what an earlier build declared stays declared (Project.release_guard lowers it)")
    lost = []
    for key, old in remembered.items():
        new = declared.get(key)
        if isinstance(old, (int, float)) and not isinstance(old, bool):
            if new is None or (isinstance(new, (int, float)) and float(new) < float(old)):
                lost.append(f"{key} dropped from {old:g} to {0 if new is None else new}")
        elif new is None:
            lost.append(f"{key} is no longer declared (was {old!r})")
    if lost:
        return _check(label, "fail", "; ".join(lost), "what an earlier build declared stays declared (Project.release_guard lowers it)")
    return _check(label, "pass", ", ".join(f"{k} {v}" for k, v in remembered.items()))


def merge_guard(remembered: dict | None, declared: dict | None) -> dict | None:
    """What the project keeps after a build: the larger of each numeric value, the newer of anything else."""
    if declared is None:
        return dict(remembered) if remembered else None
    out = dict(remembered or {})
    for k, v in declared.items():
        old = out.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and isinstance(old, (int, float)) and not isinstance(old, bool):
            out[k] = max(old, v)
        else:
            out[k] = v
    return out


def run(ctx: BuildContext) -> tuple[list[Check], dict[str, Any], dict[str, dict]]:
    """Run every registered extension on a build: (checks to merge into the report, data by extension name, guards by
    extension name). Never raises: a failing extension is a failed check."""
    checks: list[Check] = []
    data: dict[str, Any] = {}
    guards: dict[str, dict] = {}
    for problem in load():
        checks.append(_check("extension loaded", "fail", problem, "an importable module or entry point"))
    for name, fn in list(_REGISTRY.items()):
        ctx.progress(f"running the {name} extension")
        try:
            out = fn(ctx)
            if out is None:
                out = Outcome()
            elif not isinstance(out, Outcome):
                out = Outcome(checks=list(out))
            for c in out.checks:
                if not isinstance(c, Check):
                    raise TypeError(f"returned {type(c).__name__}, not an eitri.extensions.Check")
        except Exception:
            tb = traceback.format_exc().strip().splitlines()
            checks.append(_check(f"extension {name} ran", "fail", tb[-1] if tb else "error", "no exception", [name]))
            continue
        checks.extend(out.checks)
        if out.data is not None:
            data[name] = out.data
        if out.guard is not None:
            guards[name] = dict(out.guard)
        g = guard_check(name, ctx.guards.get(name), out.guard)
        if g is not None:
            checks.append(g)
    return checks, data, guards
