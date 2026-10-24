"""A eitri project: one folder, one current model file, numbered revisions.

Layout:

    <project>/
      <name>.py            the model the agent and the user edit
      eitri.json         state: current model, revision counter, history
      DESIGN.md            the agent's running design notes (requirements, assumptions)
      out/                 latest outputs (<name>.step/.glb/.png, result.json)
      revisions/NNN/       a copy of the model file + outputs after every successful build
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .kernel import BuildResult, build_model

STATE_FILE = "eitri.json"
_HARNESS_IMPORT = re.compile(r"^\s*(from\s+eitri\.harness\b|import\s+eitri\.harness\b)", re.M)


@dataclass
class Revision:
    number: int
    model: str              # relative path of the model file copy
    note: str
    ok: bool
    created: float
    summary: str = ""
    outputs: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)


class Project:
    # callables (project, revision, result) run after every recorded build: a hosted deployment stores each
    # revision in its database and object store; a failing hook never breaks the build
    revision_hooks: list = []

    def __init__(self, folder: str | Path, name: str | None = None):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state_path = self.folder / STATE_FILE
        self.state: dict[str, Any] = {"model": None, "revision": 0, "history": []}
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        if name:
            self.state["model"] = f"{name}.py"
        if not self.state.get("model"):
            # adopt an existing single model file, else name the model after the folder
            candidates = [p for p in self.folder.glob("*.py") if _looks_like_model(p)]
            default = re.sub(r"[^A-Za-z0-9_]+", "_", self.folder.name).strip("_") or "model"
            if default[0].isdigit():
                default = "part_" + default
            self.state["model"] = candidates[0].name if len(candidates) == 1 else f"{default}.py"
        self.out_dir = self.folder / "out"
        self.revisions_dir = self.folder / "revisions"
        self._save()

    # -- paths -------------------------------------------------------------
    @property
    def kind(self) -> str:
        """"harness" when the model file builds a wire harness (it imports eitri.harness), else "cad"."""
        code = self.read_model()
        return "harness" if code and _HARNESS_IMPORT.search(code) else "cad"

    @property
    def model_path(self) -> Path:
        return self.folder / self.state["model"]

    @property
    def name(self) -> str:
        return self.model_path.stem

    def reload(self) -> None:
        """Re-read eitri.json: builds may happen in another process (the MCP server)."""
        if self.state_path.exists():
            try:
                fresh = json.loads(self.state_path.read_text(encoding="utf-8"))
                mine = self.state
                if (fresh.get("revision", 0), len(fresh.get("history", []))) >= (mine.get("revision", 0), len(mine.get("history", []))):
                    self.state = fresh
            except Exception:
                pass

    @property
    def revision(self) -> int:
        self.reload()
        return int(self.state.get("revision", 0))

    @property
    def history(self) -> list[Revision]:
        self.reload()
        return [Revision(**h) for h in self.state.get("history", [])]

    def snapshot(self) -> tuple[int, list[Revision]]:
        """(revision, history) from one consistent state."""
        self.reload()
        st = self.state
        return int(st.get("revision", 0)), [Revision(**h) for h in st.get("history", [])]

    @property
    def design_notes_path(self) -> Path:
        return self.folder / "DESIGN.md"

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    # -- model source --------------------------------------------------------
    def read_model(self) -> str | None:
        return self.model_path.read_text(encoding="utf-8") if self.model_path.exists() else None

    def ensure_model(self) -> bool:
        """Create a blank design when the project has no model yet (returns True if created)."""
        if self.model_path.exists():
            return False
        self.write_model(BLANK_MODEL)
        return True

    def write_model(self, code: str) -> None:
        self.model_path.write_text(code, encoding="utf-8")

    def set_model_file(self, filename: str) -> None:
        self.state["model"] = filename
        self._save()

    # -- building --------------------------------------------------------------
    def build(self, note: str = "", params: Mapping[str, str] | None = None, render: bool = True,
              formats: tuple[str, ...] = (), timeout: float = 240.0, **kw) -> BuildResult:
        """Build and record a revision. A routine build writes what the viewer and the review need (the
        viewer model, topology, review image); STEP/GLB/STL... are written when asked for (`formats`, the
        export tool, the viewer's downloads), each verified then - on a 40-part assembly that saves ~8 s
        per build."""
        self.last_build_stderr = ""
        result = build_model(self.model_path, self.out_dir, params=params, render=render, formats=formats,
                             timeout=timeout, guards=self.guards() or None, **kw)
        self.last_result = result
        self._remember_guards(result)
        self._record(result, note)
        return result

    # -- guards: what build extensions saw this model declare (eitri.extensions) ---------------------------
    def guards(self) -> dict[str, dict]:
        """{extension name: what earlier builds of the current model file declared}; handed to the next build."""
        return {k: dict(v) for k, v in ((self.state.get("guards") or {}).get(self.model_path.name) or {}).items()}

    def _remember_guards(self, result: BuildResult) -> None:
        if not result.ok or not result.guards:
            return
        from .extensions import merge_guard
        self.reload()
        all_guards = dict(self.state.get("guards") or {})
        mine = {k: dict(v) for k, v in (all_guards.get(self.model_path.name) or {}).items()}
        for name, declared in result.guards.items():
            mine[name] = merge_guard(mine.get(name), declared) or {}
        all_guards[self.model_path.name] = mine
        self.state["guards"] = all_guards
        self._save()

    def release_guard(self, name: str) -> None:
        """Forget what the extension `name` saw this model declare: for a person who means to remove a declared
        check (the agent has no tool for this; the next build starts the guard afresh)."""
        self.reload()
        g = dict(self.state.get("guards") or {})
        mine = dict(g.get(self.model_path.name) or {})
        mine.pop(name, None)
        g[self.model_path.name] = mine
        self.state["guards"] = g
        self._save()

    def _record(self, result: BuildResult, note: str) -> None:
        self.reload()
        new_state = dict(self.state)                 # assembled completely, then swapped in atomically
        new_state["history"] = list(self.state.get("history", []))
        new_state["revision"] = int(self.state.get("revision", 0)) + 1
        n = new_state["revision"]
        rev_dir = self.revisions_dir / f"{n:03d}"
        rev_dir.mkdir(parents=True, exist_ok=True)
        model_copy = rev_dir / self.model_path.name
        if self.model_path.exists():
            shutil.copy2(self.model_path, model_copy)
        outputs: dict[str, str] = {}
        for fmt, path in result.outputs.items():
            src = Path(path)
            if src.exists():
                dst = rev_dir / src.name
                shutil.copy2(src, dst)
                outputs[fmt] = str(dst)
        (rev_dir / "summary.txt").write_text(result.summary or (result.error or ""), encoding="utf-8")
        rev = Revision(number=n, model=str(model_copy.relative_to(self.folder)), note=note, ok=result.ok,
                       created=time.time(), summary=(result.summary or "")[:4000], outputs=outputs,
                       params=dict(result.params))
        new_state["history"].append(rev.__dict__)
        self.state = new_state
        self._save()
        for hook in list(Project.revision_hooks):
            try:
                hook(self, rev, result)
            except Exception:  # noqa: BLE001 - recording elsewhere must not lose the build here
                import logging
                logging.getLogger("eitri.project").exception("revision hook failed")

    def compare(self, a: int | None = None, b: int | None = None) -> dict:
        """Compare two revisions (default: the previous successful one against the current): the source
        diff, and per part the volume / bounding box before and after (added, removed, changed)."""
        import difflib
        from .model import load_model
        ok_hist = [h for h in self.history if h.ok]
        if not ok_hist:
            raise ValueError("no successful build to compare")
        rb = next((h for h in ok_hist if h.number == b), ok_hist[-1]) if b else ok_hist[-1]
        earlier = [h for h in ok_hist if h.number < rb.number]
        ra = next((h for h in ok_hist if h.number == a), None) if a else (earlier[-1] if earlier else None)
        if ra is None:
            raise ValueError("nothing earlier to compare with" if not a else f"no successful revision {a}")
        src_a = (self.folder / ra.model).read_text(encoding="utf-8") if (self.folder / ra.model).exists() else ""
        src_b = (self.folder / rb.model).read_text(encoding="utf-8") if (self.folder / rb.model).exists() else ""
        diff = "".join(difflib.unified_diff(src_a.splitlines(keepends=True), src_b.splitlines(keepends=True), f"revision {ra.number}", f"revision {rb.number}"))
        def fp(rev):
            parts = load_model(self.folder / rev.model).build()
            from .harness.model import Harness
            if isinstance(parts, Harness):               # a harness: what each wire / connector is, not its geometry
                from .harness.edits import describe_state
                return describe_state(parts)
            out = {}
            for p in parts:
                bb = p.shape.bounding_box()
                out[p.name] = {"volume": round(abs(p.shape.volume), 3), "bbox": [round(v, 3) for v in (bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z)], "faces": len(p.shape.faces())}
            return out
        pa, pb = fp(ra), fp(rb)
        rows = []
        if self.kind == "harness":
            for name in list(pa) + [n for n in pb if n not in pa]:
                x, y = pa.get(name), pb.get(name)
                status = "removed" if x and not y else "added" if y and not x else "changed" if x != y else "same"
                rows.append({"name": name, "status": status, "before": x, "after": y})
            changed = [r for r in rows if r["status"] != "same"]
            lines = [f"revision {ra.number} -> {rb.number}: {len(changed)} wire(s)/connector(s) differ"
                     + (f" ({', '.join(r['name'] + ' ' + r['status'] for r in changed)})" if changed else "")]
            for r in changed:
                lines.append(f"  {r['name']}: {r['status']}" + (f": {r['before']} -> {r['after']}" if r["status"] == "changed" else f": {r['before'] or r['after']}"))
            return {"a": ra.number, "b": rb.number, "source_diff": diff, "parts": rows, "summary": "\n".join(lines)}
        for name in list(pa) + [n for n in pb if n not in pa]:
            x, y = pa.get(name), pb.get(name)
            if x and not y:
                status = "removed"
            elif y and not x:
                status = "added"
            elif abs(x["volume"] - y["volume"]) > 1e-3 or any(abs(u - v) > 1e-3 for u, v in zip(x["bbox"], y["bbox"])) or x["faces"] != y["faces"]:
                status = "changed"
            else:
                status = "same"
            rows.append({"name": name, "status": status, "before": x, "after": y})
        changed = [r for r in rows if r["status"] != "same"]
        lines = [f"revision {ra.number} -> {rb.number}: {len(changed)} part(s) differ" + (f" ({', '.join(r['name'] + ' ' + r['status'] for r in changed)})" if changed else "")]
        for r in changed:
            if r["status"] == "changed":
                x, y = r["before"], r["after"]
                lines.append(f"  {r['name']}: volume {x['volume']:g} -> {y['volume']:g} mm^3, bbox {x['bbox']} -> {y['bbox']}, faces {x['faces']} -> {y['faces']}")
            else:
                z = r["before"] or r["after"]
                lines.append(f"  {r['name']}: {r['status']} (volume {z['volume']:g} mm^3)")
        return {"a": ra.number, "b": rb.number, "source_diff": diff, "parts": rows, "summary": "\n".join(lines)}

    def restore(self, number: int) -> Revision:
        """Copy a revision's model file back as the current model (does not rebuild)."""
        for h in self.history:
            if h.number == number:
                src = self.folder / h.model
                if not src.exists():
                    raise FileNotFoundError(src)
                shutil.copy2(src, self.model_path)
                return h
        raise KeyError(f"no revision {number}")

    def last_ok_revision(self, before: int | None = None) -> Revision | None:
        hist = [h for h in self.history if h.ok and (before is None or h.number < before)]
        return hist[-1] if hist else None

    # -- parameters ------------------------------------------------------------
    def current_params(self) -> dict[str, Any]:
        code = self.read_model()
        if not code:
            return {}
        try:
            return _extract_params(code)
        except Exception:
            return {}

    def configs(self) -> dict[str, dict]:
        """Named parameter sets (CONFIGS in the model file)."""
        try:
            from .model import load_model
            return load_model(self.model_path).configs
        except Exception:
            return {}

    def apply_config(self, name: str) -> dict[str, Any]:
        """Write a configuration's values into PARAMS (the model file keeps CONFIGS for the others)."""
        cfgs = self.configs()
        if name not in cfgs:
            raise KeyError(f"unknown configuration {name!r}; CONFIGS has: {', '.join(cfgs) or 'none'}")
        return self.set_params(cfgs[name])

    def set_params(self, updates: Mapping[str, Any]) -> dict[str, Any]:
        """Rewrite PARAMS in the model file with new values; returns the resulting PARAMS."""
        code = self.read_model()
        if code is None:
            raise FileNotFoundError(self.model_path)
        params = _extract_params(code)
        from .model import coerce_param
        for k, v in updates.items():
            if k not in params:
                raise KeyError(f"unknown parameter {k!r}; PARAMS has: {', '.join(params)}")
            if isinstance(v, str) and not isinstance(params[k], str):
                try:
                    v = coerce_param(params[k], v)
                except (ValueError, SyntaxError):
                    pass                                    # an expression of other parameters: kept as text
            params[k] = v
        new_code = _replace_params(code, params)
        self.write_model(new_code)
        return params

    # -- direct edits (EDITS list in the model file) --------------------------------
    def edits(self) -> list[dict]:
        code = self.read_model()
        if not code:
            return []
        try:
            value = _extract_assignment(code, "EDITS")
        except Exception:
            # EDITS built by code (helper calls, variables, comprehensions): read it from the loaded module
            # instead, so the timeline shows those ops and a direct edit appends to them instead of
            # replacing them (set_edits then writes the expanded literal list back)
            try:
                from .model import load_model
                return load_model(self.model_path).edits
            except Exception:
                return []
        return [dict(e) for e in (value or []) if isinstance(e, dict)]

    def set_edits(self, edits: list[dict]) -> None:
        code = self.read_model()
        if code is None:
            raise FileNotFoundError(self.model_path)
        self.write_model(_replace_assignment(code, "EDITS", list(edits), create=True))

    def add_edit(self, edit: dict, merge: bool = True) -> list[dict]:
        """Append a direct edit; a repeated gesture on the same geometry merges into the last one."""
        from .edits import merge_edit
        edits = self.edits()
        merged = merge_edit(edits[-1], dict(edit)) if (merge and edits) else None
        if merged is not None:
            edits[-1] = merged
        else:
            edits.append(dict(edit))
        self.set_edits(edits)
        return edits

    def remove_edit(self, index: int) -> list[dict]:
        edits = self.edits()
        if 0 <= index < len(edits):
            del edits[index]
        self.set_edits(edits)
        return edits

    def update_edit(self, index: int, edit: dict) -> list[dict]:
        """Replace one edit in place (the viewer's "edit feature" / suppress)."""
        edits = self.edits()
        if not 0 <= index < len(edits):
            raise IndexError(f"no edit #{index + 1}")
        edits[index] = dict(edit)
        self.set_edits(edits)
        return edits

    def move_edit(self, index: int, to: int) -> list[dict]:
        """Reorder the timeline: move edit `index` so it sits at position `to`."""
        edits = self.edits()
        if not 0 <= index < len(edits):
            raise IndexError(f"no edit #{index + 1}")
        e = edits.pop(index)
        edits.insert(max(0, min(int(to), len(edits))), e)
        self.set_edits(edits)
        return edits

    def clear_edits(self) -> None:
        self.set_edits([])

    # -- layout hints (a harness diagram: where the editor's user dragged the connectors) --------------
    def layout(self) -> dict[str, list[float]]:
        code = self.read_model()
        try:
            value = _extract_assignment(code or "", "LAYOUT")
        except Exception:
            return {}
        return {str(k): [float(v[0]), float(v[1])] for k, v in (value or {}).items()} if isinstance(value, dict) else {}

    def set_layout(self, positions: Mapping[str, Any]) -> dict[str, list[float]]:
        """Merge node positions ({name: [x, y]}) into the model file's LAYOUT (layout hints, not geometry)."""
        code = self.read_model()
        if code is None:
            raise FileNotFoundError(self.model_path)
        layout = self.layout()
        for k, v in positions.items():
            layout[str(k)] = [round(float(v[0]), 1), round(float(v[1]), 1)]
        self.write_model(_replace_assignment(code, "LAYOUT", layout, create=True,
                                             comment="diagram positions dragged in the editor (layout hints for the drawing, not wiring)"))
        return layout

    # -- scratch python ----------------------------------------------------------
    def run_python(self, code: str, timeout: float = 120.0) -> tuple[bool, str]:
        """Run code with `parts`, `p`, `bd` and the measurement helpers in scope."""
        from .executor import current, require_local_exec
        executor = current()
        if executor is not None:
            res = executor.run("python", self.folder, self.model_path.name, [], timeout, code=code)
            if res.error:
                return False, res.error
            out = res.files.get("stdout.txt", b"").decode("utf-8", errors="replace")
            return res.code == 0, (out + ("" if res.code == 0 else "\n" + res.stderr[-3000:])).strip()
        require_local_exec("inspect")
        if self.kind == "harness":                       # the harness scope: h, nets(), wires(), drop(a, b), ...
            preamble = (
                "import sys, traceback\n"
                "from eitri.model import load_model\n"
                "from eitri.harness.inspect import harness_scope\n"
                f"_m = load_model({str(self.model_path)!r})\n"
                "globals().update(harness_scope(_m.build(), _m.params))\n"
            )
        else:
            preamble = (
                "import sys, traceback\n"
                "from eitri import *\n"
                "from eitri import bd\n"
                "from eitri.model import load_model\n"
                f"_m = load_model({str(self.model_path)!r})\n"
                "p = _m.params\n"
                "parts = _m.build()\n"
                "model = parts.compound\n"
            )
        script = preamble + "try:\n" + "\n".join("    " + line for line in code.splitlines()) + \
            "\nexcept Exception:\n    traceback.print_exc()\n    sys.exit(1)\n"
        try:
            proc = subprocess.run([sys.executable, "-c", script], cwd=str(self.folder), capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"timed out after {timeout:g} s"
        out = proc.stdout
        if proc.returncode != 0:
            out += "\n" + proc.stderr[-3000:]
        return proc.returncode == 0, out.strip()


BLANK_MODEL = '''from eitri import *

PARAMS = {}


def build(p):
    return {}   # a blank design: sketch on the grid in the viewer, or describe a part to eitri
'''


def _looks_like_model(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    return "def build(" in text


def _extract_params(code: str) -> dict[str, Any]:
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PARAMS" for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "dict":
                return {kw.arg: ast.literal_eval(kw.value) for kw in value.keywords if kw.arg}
            if isinstance(value, ast.Dict):
                return {ast.literal_eval(k): ast.literal_eval(v) for k, v in zip(value.keys, value.values)}
            return dict(ast.literal_eval(value))
    return {}


def _extract_assignment(code: str, name: str) -> Any:
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None


def _replace_assignment(code: str, name: str, value: Any, create: bool = False,
                        comment: str = "direct edits made in the viewer (applied after build(); fold them into the design when convenient)") -> str:
    """Replace (or append) a top-level `name = <literal>` assignment, pretty-printed."""
    import pprint
    rendered = f"{name} = " + pprint.pformat(value, width=100, sort_dicts=False) + "\n"
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            lines = code.splitlines(keepends=True)
            start, end = node.lineno - 1, node.end_lineno
            return "".join(lines[:start]) + rendered + "".join(lines[end:])
    if not create:
        raise ValueError(f"no {name} assignment found in the model file")
    sep = "" if code.endswith("\n\n") else ("\n" if code.endswith("\n") else "\n\n")
    return code + sep + f"# {comment}\n" + rendered


def _replace_params(code: str, params: Mapping[str, Any]) -> str:
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PARAMS" for t in node.targets):
            lines = code.splitlines(keepends=True)
            start, end = node.lineno - 1, node.end_lineno
            items = ",\n".join(f"    {k}={v!r}" for k, v in params.items())
            new = f"PARAMS = dict(\n{items},\n)\n"
            return "".join(lines[:start]) + new + "".join(lines[end:])
    raise ValueError("no PARAMS assignment found in the model file")
