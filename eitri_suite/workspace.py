"""The local combined workspace: what the `combined_*` MCP tools do on this computer (eitri.agent.combined_server speaks MCP).

The person's agent (Claude Code, Codex, Cursor, on its own subscription) writes a combined project's model files; eitri builds
them here, one file after another with the cross-domain extension on, and answers with the results: each check's name, pass or
fail, its numbers and their source, what is still unbuilt, and what to do next. eitri never calls a model here. No account, no
token, no database, no queue: a project is a folder, its builds are core's (eitri.project.Project), and its last combined build
is stored in <folder>/.eitri/combined.json.

The tools have the same names and arguments as eitri.io's remote combined tools, so the skills and the agents do not change:

    combined_reference(topic?)                       how to write the files
    combined_new_project(name, goal)                 -> project id (the folder's name under ~/eitri-projects), viewer link
    combined_list_projects()
    combined_read_files(project_id, paths?)
    combined_put_files(project_id, files, delete?)
    combined_build(project_id, detail?, image?)      every file built, every check run; results and next steps
    combined_results(project_id, detail?)            the last build's results, without building
    combined_export(project_id, formats)             files on this computer (the build package only when every check has run and passes)

A build runs in a thread of its own, so a call answers within WAIT_S whatever the build does: when it has not finished, the
answer says how far it got and to call combined_results, which waits again.

A combined project is marked in its eitri.json ("workspace": "combined"): every build of it, from these tools, the viewer, `eitri
build` or `eitri web`, then runs the cross-domain checks (eitri_suite.enabled).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import MARKER_KEY, MARKER_VALUE, is_combined_folder

WAIT_S = float(os.environ.get("EITRI_COMBINED_WAIT_S", "100"))  # a tool call answers within this; the build goes on in the background
BUILD_TIMEOUT_S = float(os.environ.get("EITRI_COMBINED_BUILD_TIMEOUT_S", "300"))
MAX_FILE_BYTES_TEXT = 64_000        # DESIGN.md and other text files
MAX_MODEL_BYTES = 400_000
MAX_READ_BYTES = 400_000            # combined_read_files answers at most this much file content
MAX_MODEL_FILES = 8
STATE = "combined.json"             # .eitri/combined.json: the last combined build's results
# built in this order: each file's checks see fresh siblings (electronics last: its checks read every other file)
KIND_ORDER = ("cad", "mechanism", "harness", "firmware", "pneumatics", "electronics")
COMBINED_KINDS = KIND_ORDER
KIND_FILE = {"cad": "model.py", "mechanism": "mech.py", "harness": "wiring.py", "firmware": "fw.py", "pneumatics": "circuit.py",
             "electronics": "elec.py"}
KIND_LABEL = {"cad": "the CAD model", "mechanism": "the mechanism", "harness": "the wire harness", "firmware": "the firmware",
              "pneumatics": "the pneumatic circuit", "electronics": "the electronics"}
_MODEL = re.compile(r"^[a-z][a-z0-9_]{0,30}\.py$")
_TEXT = {"DESIGN.md", "pinmap.json"}

BLANKS = {
    "cad": "from eitri import *\n\nPARAMS = dict()\n\ndef build(p):\n    return {}\n",
    "harness": "from eitri.harness import *\n\nPARAMS = dict()\n\ndef build(p):\n    return Harness(\"untitled\")\n",
    "mechanism": "from eitri.mechanism import *\n\nPARAMS = dict()\n\ndef build(p):\n    m = Mechanism(\"untitled\")\n    m.link(\"base\", fixed=True)\n    return m\n",
    "pneumatics": "from eitri.pneumatics import *\n\nPARAMS = dict()\n\ndef build(p):\n    return Circuit(\"untitled\")\n",
    "firmware": "from eitri.firmware import *\n\nPARAMS = dict()\n\ndef build(p):\n    return Firmware(\"untitled\", board=\"uno\")\n",
    "electronics": "from eitri.electronics import *\n\nPARAMS = dict()\n\ndef build(p):\n    return Circuit(\"untitled\")\n",
}


class ToolError(Exception):
    """A refusal the agent should read (the tool result is an error with this text)."""


def kind_of(text: str) -> str:
    from eitri.domains import kind_of_source
    return kind_of_source(text or "")


def is_blank(text: str) -> bool:
    return (text or "").strip() in {b.strip() for b in BLANKS.values()}


def default_root() -> Path:
    return Path(os.environ.get("EITRI_WORKSPACE") or (Path.home() / "eitri-projects")).expanduser()


def _tilde(p: Path | str) -> str:
    p = Path(p)
    try:
        return "~/" + str(p.resolve().relative_to(Path.home().resolve()))
    except ValueError:
        return str(p)


# ------------------------------------------------------------------------------------------------------------- valve ports
# Pneumatic models written before core 0.8.0 named valve ports by letters; a build of one stops with the core's error. The next
# step then says how to migrate (P->1, A->2, B->4, R->3, S->5 keeps what eitri graded before).
VALVE_ERROR_MARK = "valve ports are the ISO 5599 numbers"
VALVE_MESSAGE = ("eitri 0.8.0 numbers pneumatic valve ports by ISO 5599: 1 supply, 2 fed at rest (spring or coil 12), 4 fed by coil 14, 3 "
                 "and 5 exhausts. This model still uses the old letters. To keep what eitri checked before, rename P→1, A→2, B→4, R→3, "
                 "S→5. On an SMC valve (part=\"SMC …\") also check the fail-safe states after renaming: SMC marks 4 as A and 2 as B, and "
                 "eitri before 0.8.0 graded such models with A and B swapped.")
_VALVE_VAR = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*[A-Za-z_][\w.]*\.valve\(([^)]*)\)")


def old_valve_ports(code: str) -> list[dict]:
    """[{valve, letters, smc}] for each valve variable the code addresses by an old letter."""
    out = []
    for name, args in _VALVE_VAR.findall(code or ""):
        used = sorted(set(re.findall(r"\b" + re.escape(name) + r"""\[\s*["'](P|A|B|R|S|EA|EB)["']\s*\]""", code)))
        if used:
            out.append({"valve": name, "letters": used, "smc": "SMC" in args.upper()})
    return out


# ------------------------------------------------------------------------------------------------------------- the export rule
def _status(c: dict) -> str:
    from eitri.checks import status_of
    return status_of(c)


# what each kind's cross-domain checks read of the other files: when one of those changed since that kind's newest good build,
# its cross-domain checks are out of date
CROSS_READS = {"electronics": ("cad", "harness", "firmware", "mechanism"), "firmware": ("harness", "mechanism", "cad"),
               "mechanism": ("cad", "firmware"), "cad": ("harness", "mechanism", "firmware"), "harness": ("cad", "mechanism")}


def _revision_file(rev) -> str:
    return Path(rev.model).name


def stale_checks(project, files: list[dict]) -> dict[str, list[dict]]:
    """{kind: [{kind, file}]}: the files a kind's newest good build read that have changed since (by content: rebuilding a file
    unchanged marks nothing). The digests are what the suite recorded in that build's data ("siblings")."""
    hist = project.history
    latest_ok: dict[str, object] = {}
    for h in hist:
        if not h.ok:
            continue
        f = project.folder / _revision_file(h)
        kind = next((x["kind"] for x in files if x["file"] == _revision_file(h)), None)
        if kind is None or not f.is_file():
            continue
        latest_ok[kind] = h
    out: dict[str, list[dict]] = {}
    for kind, rev in latest_ok.items():
        data = project.build_result(rev, hist) or {}
        sib = ((data.get("extensions") or {}).get("suite") or {}).get("siblings")
        if not isinstance(sib, dict):
            continue
        changed = []
        for f in files:
            if f["kind"] not in CROSS_READS.get(kind, ()) or f["file"] == _revision_file(rev):
                continue
            try:
                now = hashlib.sha256((project.folder / f["file"]).read_bytes()).hexdigest()
            except OSError:
                now = None
            if sib.get(f["file"]) != now:
                changed.append({"kind": f["kind"], "file": f["file"]})
        if changed:
            out[kind] = changed
    return out


def blocking(project) -> list[dict]:
    """[{file, kind, name, status, measured}] for everything that stops an export or the build package: a failing check, a check
    that did not run (a harness with no declared source, an unverified catalog figure), a check out of date (a file it reads
    changed since), and a file whose newest build failed or that was never built. Read from this project's own stored builds."""
    project.reload()
    hist = project.history
    out: list[dict] = []
    files = []
    for f in project.model_files():
        path = project.folder / f
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        kind = kind_of(text)
        files.append({"file": f, "kind": kind})
        mine = [h for h in hist if _revision_file(h) == f]
        if not mine:
            if not text.strip() or is_blank(text):
                continue
            out.append({"file": f, "kind": kind, "name": f"{f} is not built", "status": "not_run", "measured": "no build of this file yet"})
            continue
        last = mine[-1]
        if not last.ok:
            lines = (last.summary or "build failed").strip().splitlines()
            out.append({"file": f, "kind": kind, "name": f"{f} does not build", "status": "fail", "measured": (lines[-1] if lines else "build failed")[:300]})
            continue
        data = project.build_result(last, hist) or {}
        for c in (data.get("report") or {}).get("checks") or []:
            s = _status(c)
            if s in ("fail", "not_run"):
                out.append({"file": f, "kind": kind, "name": str(c.get("name")), "status": s, "measured": str(c.get("measured", ""))[:300]})
        try:
            if path.read_text(encoding="utf-8") != (project.folder / last.model).read_text(encoding="utf-8"):
                out.append({"file": f, "kind": kind, "name": f"{f} changed since its last build", "status": "not_run",
                            "measured": "the file on disk is not the one that was checked; build again"})
        except OSError:
            pass
    for kind, changed in stale_checks(project, files).items():
        f = next((x["file"] for x in files if x["kind"] == kind), kind)
        out.append({"file": f, "kind": kind, "name": f"{f}: its cross-domain checks are out of date", "status": "not_run",
                    "measured": "changed since it was checked: " + ", ".join(x["file"] for x in changed) + f"; build {f} again"})
    return out


def blocking_message(rows: list[dict], what: str = "this export") -> str:
    fails = [r for r in rows if r["status"] == "fail"]
    off = [r for r in rows if r["status"] != "fail"]
    parts = []
    if fails:
        parts.append(f"{len(fails)} failing: " + "; ".join(f"{r['file']}: {r['name']}" for r in fails[:8]) + (" ..." if len(fails) > 8 else ""))
    if off:
        parts.append(f"{len(off)} not run: " + "; ".join(f"{r['file']}: {r['name']}" for r in off[:8]) + (" ..." if len(off) > 8 else ""))
    return (f"refused: {what} is made only when every check of every file has run and passes. " + ". ".join(parts)
            + ". Fix the design (or give the missing input, e.g. h.source(...) for a harness), build again, then ask again.")


# ------------------------------------------------------------------------------------------------------------- the build
class CombinedBuild:
    """One combined build of a project, in a thread of its own: its progress and its result."""

    def __init__(self, ws: "Workspace", folder: Path, number: int, files: list[str], digest: str, image: bool):
        self.ws, self.folder, self.number, self.files, self.digest, self.image = ws, folder, number, files, digest, image
        self.started = time.time()
        self.done = threading.Event()
        self.current: str | None = None
        self.built: list[dict] = []
        self.result: dict | None = None
        self.error: str | None = None
        self.delivered = False

    def progress(self) -> dict:
        return {"build": self.number, "files": len(self.files), "done": len(self.built), "current": self.current,
                "elapsed_s": round(time.time() - self.started, 1)}

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name=f"combined-{self.folder.name}-{self.number}").start()

    def _run(self) -> None:
        try:
            self._build_files()
            self.result = self.ws.results_of(self)
            self.ws.save_state(self.folder, self.result)
        except Exception as exc:  # noqa: BLE001 - reported to the agent
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.error = f"the build did not finish ({type(exc).__name__}: {exc}); call combined_build again"
        finally:
            self.current = None
            self.done.set()

    def _build_files(self) -> None:
        from eitri.project import Project
        with self.ws.lock_for(self.folder):
            project = Project(self.folder)
            keep = project.model_path.name
            try:
                for f in self.files:
                    self.current = f
                    project.set_model_file(f)
                    text = (self.folder / f).read_text(encoding="utf-8")
                    kind = kind_of(text)
                    t0 = time.time()
                    res = project.build(note=f"combined build {self.number}: {f}"[:100], render=(kind == "cad" and self.image),
                                        timeout=BUILD_TIMEOUT_S)
                    o = {"file": f, "kind": kind, "built": bool(res.ok), "blank": is_blank(text), "revision": project.revision,
                         "seconds": round(time.time() - t0, 1)}
                    if not res.ok:
                        o["error"] = clean_error(res.error)
                    else:
                        rev = project.last_ok_revision(model=f)
                        report = ((project.build_result(rev) if rev else None) or {}).get("report") or {}
                        o["checks"] = [_check(c) for c in report.get("checks") or []]
                        o["issues"] = [str(i)[:300] for i in (report.get("issues") or [])][:20]
                        png = (rev.outputs or {}).get("png") if rev and kind == "cad" and self.image else None
                        if png and Path(png).is_file():
                            o["image_path"] = str(png)
                    self.built.append(o)
            finally:
                if (self.folder / keep).is_file():
                    project.set_model_file(keep)


def clean_error(text: str | None) -> str:
    return (text or "build failed").strip()[-3000:]


_CROSS = ("integration", "coupling", "assembly", "suite", "placement", "kinematics", "sequence", "stability", "power", "electronics")


def _check(c: dict) -> dict:
    cat = str(c.get("category") or "")
    name = str(c.get("name") or "")[:200]
    out = {"name": name, "ok": bool(c.get("ok")), "severity": str(c.get("severity") or ("pass" if c.get("ok") else "fail")),
           "measured": str(c.get("measured") or "")[:600], "expected": str(c.get("expected") or "")[:300],
           "cross_domain": cat in _CROSS or name.startswith("suite:") or "(masses from CAD)" in name}
    src = c.get("source")
    if isinstance(src, list):
        out["source"] = "; ".join(str(s.get("text") or "") + (f" ({s['url']})" if s.get("url") else "") for s in src
                                  if isinstance(s, dict) and s.get("text") and "gives no source" not in str(s.get("text")))[:400]
    elif src:
        out["source"] = str(src)[:300]
    out["status"] = _status(c)
    return out


# ------------------------------------------------------------------------------------------------------------- the tools
class Workspace:
    def __init__(self, root: str | Path | None = None, viewer=None):
        self.root = Path(root).expanduser() if root else default_root()
        self._builds: dict[str, CombinedBuild] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self.viewer = viewer                     # callable(folder) -> viewer URL, or None (no viewer: tests, headless)
        self.on_new_project = None               # callable(folder): the anonymous project count (eitri.telemetry), set by the server

    # ---------------------------------------------------------------- helpers
    def lock_for(self, folder: Path) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(str(folder), threading.Lock())

    def folder(self, project_id) -> Path:
        pid = str(project_id or "").strip()
        if not pid:
            raise ToolError("project_id is empty. combined_list_projects() lists the projects; combined_new_project makes one.")
        p = Path(pid).expanduser()
        f = p if p.is_absolute() else self.root / pid
        if "/" in pid and not p.is_absolute():
            raise ToolError(f"No combined project {pid!r}: a project id is its folder's name in {_tilde(self.root)} (or an absolute path).")
        if not (f / "eitri.json").is_file():
            raise ToolError(f"No combined project {pid!r} in {_tilde(self.root)}. combined_list_projects() lists them; "
                            "combined_new_project makes one.")
        if not is_combined_folder(f):
            raise ToolError(f"{pid} is a single-domain eitri project; these tools work on combined projects. Make one with "
                            "combined_new_project, or open this one with the local single-domain tools (open_project).")
        return f.resolve()

    def pid(self, folder: Path) -> str:
        try:
            if folder.parent.resolve() == self.root.resolve():
                return folder.name
        except OSError:
            pass
        return str(folder)

    def view_link(self, folder: Path) -> str:
        if self.viewer is None:
            return f"(no viewer in this session; run `eitri web {folder}` to open it)"
        try:
            return self.viewer(folder)
        except Exception as exc:  # noqa: BLE001 - a viewer that cannot start must not stop the tools
            return f"(the viewer did not start: {exc}; `eitri web {folder}` opens it)"

    def _state_path(self, folder: Path) -> Path:
        return folder / ".eitri" / STATE

    def load_state(self, folder: Path) -> dict | None:
        try:
            return json.loads(self._state_path(folder).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_state(self, folder: Path, result: dict) -> None:
        p = self._state_path(folder)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, default=str), encoding="utf-8")
        tmp.replace(p)

    def model_files(self, folder: Path) -> list[str]:
        """The project's model files in build order (CAD, mechanism, harness, firmware, pneumatics, electronics; then by name)."""
        files = []
        for f in sorted(folder.glob("*.py")):
            if not _MODEL.match(f.name) or f.is_symlink():
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if "def build(" in text:
                k = kind_of(text)
                files.append((KIND_ORDER.index(k) if k in KIND_ORDER else 9, f.name))
        return [n for _k, n in sorted(files)]

    def digest(self, folder: Path) -> str:
        h = hashlib.sha256()
        for f in self.model_files(folder):
            h.update(f.encode() + b"\0" + (folder / f).read_bytes() + b"\0")
        for t in sorted(_TEXT):
            if (folder / t).is_file():
                h.update(t.encode() + b"\0" + (folder / t).read_bytes() + b"\0")
        return h.hexdigest()

    def busy(self, folder: Path) -> str | None:
        b = self._builds.get(str(folder))
        if b is not None and not b.done.is_set():
            return f"build {b.number} is running ({len(b.built)} of {len(b.files)} files done); call combined_results(project_id) to wait for it"
        return None

    def goal(self, folder: Path) -> str:
        try:
            return (folder / ".eitri" / "goal.txt").read_text(encoding="utf-8")
        except OSError:
            return ""

    # ---------------------------------------------------------------- combined_reference
    def reference(self, topic: str | None = None) -> str:
        return reference_text(topic)

    # ---------------------------------------------------------------- combined_new_project
    def new_project(self, name: str, goal: str = "") -> dict:
        from eitri.project import Project
        from eitri.web.app import set_project_title, slugify, title_from
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
        goal = str(goal or "").strip()
        if not name:
            raise ToolError("name is empty: give the project a short name (\"Desk robot arm\")")
        if len(goal) > 8000:
            raise ToolError("keep the goal under 8000 characters")
        slug, k = slugify(name), 2
        base = slug
        while (self.root / slug).exists():
            slug, k = f"{base}-{k}", k + 1
        folder = self.root / slug
        folder.mkdir(parents=True)
        (folder / "model.py").write_text(BLANKS["cad"], encoding="utf-8")
        project = Project(folder, name="model")
        project.state[MARKER_KEY] = MARKER_VALUE
        project._save()
        set_project_title(folder, title_from(name) or "Untitled")
        (folder / ".eitri").mkdir(exist_ok=True)
        if goal:
            (folder / ".eitri" / "goal.txt").write_text(goal, encoding="utf-8")
        if self.on_new_project is not None:
            try:
                self.on_new_project(folder)
            except Exception:  # noqa: BLE001 - counting never stops the tool
                pass
        folder = folder.resolve()
        return {"project_id": self.pid(folder), "name": title_from(name), "folder": str(folder), "view": self.view_link(folder),
                "files": self.model_files(folder)}

    # ---------------------------------------------------------------- combined_list_projects
    def list_projects(self) -> list[dict]:
        from eitri.web.app import project_title
        if not self.root.is_dir():
            return []
        out = []
        for f in sorted((p for p in self.root.iterdir() if p.is_dir() and not p.name.startswith(".")),
                        key=lambda p: p.stat().st_mtime, reverse=True):
            if not is_combined_folder(f):
                continue
            st = self.load_state(f) or {}
            out.append({"project_id": f.name, "name": project_title(f) or f.name, "folder": str(f),
                        "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
                        "last_build": st.get("build"), "all_pass": st.get("all_pass")})
            if len(out) >= 100:
                break
        return out

    # ---------------------------------------------------------------- combined_read_files
    def read_files(self, project_id, paths=None) -> dict:
        folder = self.folder(project_id)
        names = self.model_files(folder) + [t for t in sorted(_TEXT) if (folder / t).is_file()]
        if paths:
            if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                raise ToolError("paths is a list of file names")
            missing = [p for p in paths if p not in names]
            if missing:
                raise ToolError(f"no such file: {', '.join(missing)}. The project holds: {', '.join(names)}")
            names = list(dict.fromkeys(paths))
        out, total = [], 0
        for n in names:
            text = (folder / n).read_text(encoding="utf-8", errors="replace")
            if total + len(text) > MAX_READ_BYTES:
                out.append({"path": n, "skipped": "the answer is full; ask for this file on its own"})
                continue
            total += len(text)
            out.append({"path": n, "kind": kind_of(text) if n.endswith(".py") else None,
                        "blank": is_blank(text) if n.endswith(".py") else None, "content": text})
        return {"project_id": self.pid(folder), "folder": str(folder), "files": out}

    # ---------------------------------------------------------------- combined_put_files
    def put_files(self, project_id, files, delete=None) -> dict:
        folder = self.folder(project_id)
        files = files or {}
        delete = delete or []
        if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
            raise ToolError("files is an object of {file name: its whole content as text}")
        if not isinstance(delete, list) or not all(isinstance(d, str) for d in delete):
            raise ToolError("delete is a list of file names")
        if not files and not delete:
            raise ToolError("nothing to write: give files and/or delete")
        _ticket_gate(folder, "write the project's files")
        why = self.busy(folder)
        if why:
            raise ToolError(f"Not written: {why}.")
        problems = []
        for path, text in files.items():
            if path in _TEXT:
                if len(text.encode()) > MAX_FILE_BYTES_TEXT:
                    problems.append(f"{path}: larger than {MAX_FILE_BYTES_TEXT // 1000} kB")
                if path == "pinmap.json":
                    try:
                        json.loads(text)
                    except ValueError:
                        problems.append("pinmap.json: not valid JSON")
                continue
            if not _MODEL.match(path):
                problems.append(f"{path}: a model file is a top-level name of lowercase letters, digits and underscores ending in .py "
                                f"(model.py, mech.py, wiring.py, fw.py); the other files allowed are {', '.join(sorted(_TEXT))}")
                continue
            if len(text.encode()) > MAX_MODEL_BYTES:
                problems.append(f"{path}: larger than {MAX_MODEL_BYTES // 1000} kB")
                continue
            if "def build(" not in text:
                problems.append(f"{path}: a model file defines build(p) (see combined_reference())")
                continue
        current = set(self.model_files(folder))
        for d in delete:
            if d not in current and d not in _TEXT:
                problems.append(f"delete {d}: no such file")
        after = (current - set(delete)) | {p for p in files if p.endswith(".py")}
        if len(after) > MAX_MODEL_FILES:
            problems.append(f"a project holds at most {MAX_MODEL_FILES} model files")
        if not after:
            problems.append("a project keeps at least one model file")
        if problems:
            raise ToolError("Nothing was written:\n- " + "\n- ".join(problems))
        written = []
        with self.lock_for(folder):
            for d in delete:
                f = folder / d
                if f.is_file() and not f.is_symlink():
                    f.unlink()
            for path, text in files.items():
                f = folder / path
                if f.is_symlink():
                    f.unlink()
                f.write_text(text, encoding="utf-8")
                written.append({"path": path, "kind": kind_of(text) if path.endswith(".py") else None, "bytes": len(text.encode())})
            from eitri.project import Project
            project = Project(folder)
            if not (folder / project.model_path.name).is_file():
                project.set_model_file(self.model_files(folder)[0])
        return {"project_id": self.pid(folder), "written": written, "deleted": list(delete), "files": self.model_files(folder)}

    # ---------------------------------------------------------------- combined_build / combined_results
    def build(self, project_id, image: bool = True, wait_s: float | None = None) -> dict:
        folder = self.folder(project_id)
        _ticket_gate(folder, "build the project")
        with self._lock:
            b = self._builds.get(str(folder))
            if b is not None and not b.done.is_set():
                started = False
            else:
                digest = self.digest(folder)
                st = self.load_state(folder)
                if st and st.get("digest") == digest and st.get("complete"):
                    return {**st, "unchanged": True, "view": self.view_link(folder)}
                files = self.model_files(folder)
                if not files:
                    raise ToolError("the project has no model files: write one with combined_put_files")
                number = int((st or {}).get("build") or 0) + 1
                b = self._builds[str(folder)] = CombinedBuild(self, folder, number, files, digest, bool(image))
                b.start()
                started = True
        return self._wait(b, wait_s, started)

    def results(self, project_id, wait_s: float | None = None) -> dict:
        folder = self.folder(project_id)
        b = self._builds.get(str(folder))
        if b is not None and not b.done.is_set():
            return self._wait(b, wait_s, False)
        st = self.load_state(folder)
        if not st:
            return {"never_built": True, "project_id": self.pid(folder), "view": self.view_link(folder),
                    "next_steps": ["Nothing has been built yet: write the model files (combined_put_files), then call combined_build(project_id)."]}
        st = {**st, "view": self.view_link(folder)}
        if st.get("digest") != self.digest(folder):
            st["stale"] = True
        return st

    def _wait(self, b: CombinedBuild, wait_s: float | None, started: bool) -> dict:
        b.done.wait(max(0.5, float(WAIT_S if wait_s is None else wait_s)))
        if not b.done.is_set():
            p = b.progress()
            return {"running": True, "started_now": started, **p,
                    "next_steps": [f"Build {b.number} is still running ({p['done']} of {p['files']} files built, now {p['current'] or 'starting'}). "
                                   "Call combined_results(project_id) to wait for it."]}
        if b.result is None:
            raise ToolError(b.error or "the build did not finish")
        out = {**b.result, "view": self.view_link(b.folder)}
        if not b.delivered:                             # the first call to receive a build carries its review image
            b.delivered = True
            out["_fresh"] = True
        return out

    def results_of(self, b: CombinedBuild) -> dict:
        folder = b.folder
        files_out, cross_total, cross_fail = [], 0, 0
        all_checks_pass = True
        for entry in b.built:
            if not entry["built"]:
                all_checks_pass = False
            else:
                for c in entry["checks"]:
                    if c["cross_domain"]:
                        cross_total += 1
                        cross_fail += 0 if c["ok"] else 1
                if any(not c["ok"] for c in entry["checks"]):
                    all_checks_pass = False
            files_out.append(entry)
        texts = {f: (folder / f).read_text(encoding="utf-8") for f in b.files if (folder / f).is_file()}
        design = (folder / "DESIGN.md").read_text(encoding="utf-8", errors="replace")[:20000] if (folder / "DESIGN.md").is_file() else ""
        steps, planned_missing = next_steps(files_out, texts, self.goal(folder) + "\n" + design)
        all_pass = all_checks_pass and not any(f["blank"] for f in files_out)
        return {"project_id": self.pid(folder), "folder": str(folder), "build": b.number, "complete": True, "digest": b.digest,
                "checked_by": "this computer", "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "seconds": round(time.time() - b.started, 1), "files": files_out,
                "cross_domain": {"checks": cross_total, "failing": cross_fail},
                "all_pass": all_pass, "not_built": planned_missing, "next_steps": steps}

    # ---------------------------------------------------------------- combined_export
    def export(self, project_id, formats) -> dict:
        from eitri.exports import export_formats
        from eitri.project import Project
        folder = self.folder(project_id)
        if isinstance(formats, str):
            formats = [formats]
        if not isinstance(formats, list) or not formats or not all(isinstance(f, str) for f in formats):
            raise ToolError("formats is a list such as [\"step\", \"package\"] (see the list in this tool's description)")
        why = self.busy(folder)
        if why:
            raise ToolError(f"Not exported: {why}.")
        project = Project(folder)
        rows = blocking(project)
        if rows:                                          # nothing leaves while a check fails, did not run or is out of date
            return {"project_id": self.pid(folder), "files": [], "problems": [blocking_message(rows, "combined_export")],
                    "blocking": rows[:40], "note": "No files: fix what is listed (combined_build shows each check), then call combined_export again."}
        st = self.load_state(folder) or {}
        out_dir = folder / "export"
        offered: dict[str, list] = {}
        for f in self.model_files(folder):
            rev = project.last_ok_revision(model=f)
            if rev is None:
                continue
            kind = kind_of((folder / f).read_text(encoding="utf-8"))
            for x in export_formats(kind):
                if x["href"].startswith("download?"):
                    offered.setdefault(x["fmt"], []).append((f, kind, x.get("label") or x["fmt"], x.get("output")))
        written, problems = [], []
        with self.lock_for(folder):
            for want in dict.fromkeys(fm.strip().lower() for fm in formats):
                try:
                    if want in ("package", "bom"):
                        if want == "package" and (not st.get("all_pass") or st.get("digest") != self.digest(folder)):
                            problems.append("package: the build package is written only when every check passes in a combined build of "
                                            "the current files (combined_build, then fix what fails)")
                            continue
                        written += self._suite_job(folder, project, want, out_dir)
                        continue
                    if want not in offered:
                        problems.append(f"{want}: not offered. This project exports as: {', '.join(sorted(set(offered) | {'package', 'bom'}))}")
                        continue
                    for f, kind, label, output in offered[want]:
                        written.append({"format": want, "file": f, "path": self._export_file(project, f, kind, want, output, out_dir),
                                        "what": f"{label} of {f} ({kind})"})
                except ToolError as exc:
                    problems.append(f"{want}: {exc}")
        return {"project_id": self.pid(folder), "files": written, "problems": problems,
                "note": f"The files are on this computer, in {_tilde(out_dir)}."}

    def _export_file(self, project, f: str, kind: str, fmt: str, output: str | None, out_dir: Path) -> str:
        keep = project.model_path.name
        try:
            project.set_model_file(f)
            if kind == "cad":
                res = project.build(note=f"export {fmt}", formats=(fmt,), render=False, timeout=BUILD_TIMEOUT_S)
                key = fmt
            else:
                res = project.build(note=f"export {fmt}", render=False, timeout=BUILD_TIMEOUT_S)
                from eitri.domains import module
                key = module(kind).DOWNLOADS[fmt][0]
            if not res.ok:
                raise ToolError(f"{f} did not build: {clean_error(res.error).splitlines()[-1]}")
            src = res.outputs.get(key) or res.outputs.get(output or "")
            if not src or not Path(src).is_file():
                raise ToolError(f"{f}: the build wrote no {fmt} file")
        finally:
            if (project.folder / keep).is_file():
                project.set_model_file(keep)
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / Path(src).name
        shutil.copy2(src, dst)
        return str(dst)

    def _suite_job(self, folder: Path, project, kind: str, out_dir: Path) -> list[dict]:
        """The consolidated BOM or the build package, in a build process of its own (it runs the model files)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        work = out_dir / f".{kind}-job"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        code = ("import sys; from pathlib import Path; from eitri_suite import jobs; "
                "sys.exit(jobs.run(sys.argv[1], Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), {'strict': True}))")
        env = {**os.environ, "EITRI_SUITE": "1", "EITRI_AGENT_PROCESS": "1"}
        env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parents[1])] + [p for p in [env.get("PYTHONPATH")] if p])
        try:
            p = subprocess.run([sys.executable, "-c", code, kind, str(folder), project.model_path.name, str(work)], cwd=str(folder),
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=BUILD_TIMEOUT_S * 3, env=env)
        except subprocess.TimeoutExpired:
            raise ToolError("took too long") from None
        try:
            result = json.loads((work / "suite_result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tail = (p.stderr or p.stdout or "").strip().splitlines()[-1:] or ["no output"]
            raise ToolError(f"did not finish: {tail[0][:300]}") from None
        if not result.get("ok"):
            raise ToolError(str(result.get("refused") or "refused"))
        if kind == "bom":
            out = []
            for name in ("bom.md", "bom.csv", "bom.json"):
                shutil.copy2(work / name, out_dir / name)
                out.append({"format": "bom", "file": None, "path": str(out_dir / name), "what": f"the consolidated bill of materials ({name.split('.')[-1]})"})
            shutil.rmtree(work, ignore_errors=True)
            return out
        dst = out_dir / "build_package"
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(str(work / "build_package"), str(dst))
        shutil.rmtree(work, ignore_errors=True)
        z = shutil.make_archive(str(out_dir / f"{folder.name}-build-package"), "zip", root_dir=str(dst))
        return [{"format": "package", "file": None, "path": str(dst), "what": "the build package folder (README.md first)"},
                {"format": "package", "file": None, "path": z, "what": "the build package (print files, BOM, harness cut list and "
                 "diagram, wiring steps, assembly order), zip"}]


def _ticket_gate(folder: Path, action: str) -> None:
    """The core's ticket gate (eitri.tickets.store.gate) on the paths that write or build files: the refusal is the core's message."""
    if not (folder / "tickets" / "active").is_file():
        return
    from eitri.project import Project
    from eitri.tickets import store
    try:
        store.gate(Project(folder), action)
    except store.ChangeRefused as exc:
        raise ToolError(str(exc)) from None


# ------------------------------------------------------------------------------------------------------------- next steps
# What eitri's own agent loop would remind the agent of, said in every result instead: what is unbuilt, which link between domains is
# not declared yet, and for each failing check what usually fixes it. Matched on the check's name.
HINTS = [
    ("pins: harness", "Name the circuit's parts as the harness connectors (or map them in the electronics COUPLING connectors=), and wire "
     "in the harness exactly the pins the circuit joins on one net, no others."),
    ("pins: firmware", "Make the firmware pin and the circuit agree: the same board pin, an output in the firmware is not c.gpio(..., 'in'), "
     "the same pull-up, a servo pin wired to the servo's SIG, and the servo's power where the firmware says it is."),
    ("pins: every circuit gpio", "Declare in the firmware every GPIO the circuit wires, or take it out of the circuit."),
    ("harness power", "The harness must carry the circuit's currents at the actuators' stall: a heavier gauge or shorter wires, a connector "
     "rated for the current, current_A= in the harness to match, and a supply rated for the total (part_number= the BOM's supply)."),
    ("mounting:", "Put the board's mounting holes over CAD holes (standoffs tapped for the mount's screw at the board's published pattern), "
     "keep its outline clear of every body, and keep clearance_mm to the nearest one (COUPLING mounts in the electronics file)."),
    ("actuators:", "Every actuated mechanism joint needs its servo or motor in the circuit (the same model, COUPLING actuators=), and what "
     "carries their stall current (the supply, a MOSFET, a relay) must be rated for all of them stalled at once."),
    ("stability: the base", "The machine tips over on its base: widen or weight the base under the reach, cut the reach or the payload, "
     "or bolt it down (through holes in the base and COUPLING base_fixing='bolted' in the mechanism file; 'clamped' or 'free' otherwise)."),
    ("power: the supply covers", "The supply cannot feed the actuators that start together at stall: buy a bigger supply (PURCHASED in the "
     "CAD file), or move fewer servos at once in the SEQUENCE (and COUPLING motion='sequential' in the firmware file if the sketch "
     "moves one joint at a time)."),
    ("dissipation:", "A regulator or driver gets too hot at the actuators' stall current: power the actuators from their own supply, use a "
     "switching regulator or a part with a lower RDS(on), or add a heatsink."),
    ("every harness connector has a cad anchor", "In the CAD file's build(), declare anchor(\"<CONNECTOR>\", at=(x, y, z), z=(...), part=\"<part>\") for each "
     "harness connector and splice, named exactly as the connector (combined_reference('integration'))."),
    ("every actuator is at the joint it drives", "Put each servo body in the CAD model at the joint it drives: its output face centred on the joint's "
     "at= point (within 2 mm), the joint axis normal to that face, named after the joint (elbow_servo), and listed in COUPLING links under the "
     "link it is bolted to."),
    ("every body is attached", "A body floats: on each link the CAD bodies must touch one another back to the joint that moves the link (the base "
     "to the table), at rest, at home and at every SEQUENCE step; harness connectors sit on the part their anchor names."),
    ("no interference", "Two bodies overlap at rest: move or resize them (bodies on the two links of one joint may touch)."),
    ("sequence stays within", "Keep each SEQUENCE angle inside the servo's travel and the joint's limit (COUPLING joints and servo_zero in the firmware)."),
    ("firmware pin has a wire", "Wire every firmware pin to the board connector in the harness, and map it in the firmware's COUPLING pins "
     "(\"YAW\": \"UNO.D9\")."),
    ("declared checks are kept", "This build declares fewer cross-domain items (routes, mating faces, coupled links, mapped pins) than an earlier one: "
     "put them back. A declared check cannot be deleted to make it pass; fix the design instead."),
    ("compiles", "The sketch does not compile: the measured text is the compiler's error (\"compiler unavailable\": the person runs "
     "`eitri setup firmware` once to install arduino-cli)."),
    ("torque", "A joint's torque margin fails: shorten the links, lighten the parts it carries, lower the payload or pick a stronger actuator "
     "(the check names the joint and the pose)."),
    ("reach", "A target is out of reach: lengthen the links, move the target, or widen the joint limits."),
    ("fits", "A pass-through hole is too small for its bundle: enlarge the hole in the CAD model or route fewer wires through it."),
    ("route length", "A wire is shorter than its route plus the service loop: lengthen it in the harness."),
    ("cut length", "A wire's cut length is shorter than its 3D path (plus the service loop): lengthen it in the harness, or move the "
     "anchors it runs between closer together."),
    ("sits on a part", "A harness connector, splice or board is not on the part its anchor names: put the anchor on that part's surface "
     "(its at= point within 0.5 mm of the part), or name the part it really sits on (part=)."),
    ("wire paths agree", "A wire's two ends are further apart in the CAD model than the harness says: move the anchors or change the length."),
    ("mating", "A connector has no free corridor in front of its component's face: move it or clear the space (approach_mm)."),
    ("extension suite ran", "eitri's cross-domain checks stopped on this file (the measured text says why): usually a malformed INTEGRATION or "
     "COUPLING; compare it with combined_reference('integration')."),
]


def hint_for(name: str) -> str | None:
    n = name.lower()
    return next((h for k, h in HINTS if k in n), None)


def next_steps(files: list[dict], texts: dict[str, str], goal: str = "") -> tuple[list[str], list[str]]:
    """(the next steps, in order, the planned kinds still missing). Failures first, then what is unbuilt, then the links not declared."""
    steps: list[str] = []
    kinds = {f["kind"] for f in files if not f["blank"]}
    for f in files:
        if not f["built"]:
            first = (f.get("error") or "").strip().splitlines()
            steps.append(f"{f['file']} did not build: {first[-1] if first else 'see the error'}. Fix it, then combined_build again."
                         + (" " + VALVE_MESSAGE if VALVE_ERROR_MARK in (f.get("error") or "") else ""))
    for f in files:
        if f["blank"]:
            steps.append(f"{f['file']} is still the blank {f['kind']} design: write it (combined_reference('{f['kind']}')) or delete it.")
    shown = 0
    for f in files:
        for c in f.get("checks") or []:
            if c["ok"]:
                continue
            if shown >= 12:
                break
            shown += 1
            h = hint_for(c["name"])
            steps.append(f"{f['file']}: \"{c['name']}\" fails (measured {c['measured'] or '-'}; expected {c['expected'] or '-'})." + (f" {h}" if h else ""))
    # the domains a machine like this needs (read from what the agent said it is making: the goal and DESIGN.md, never the model code,
    # where "Cylinder(" is geometry), and the links between them that are not declared yet
    g = " " + re.sub(r"[^a-z0-9]+", " ", (goal or "").lower()) + " "
    has = lambda *ws: any(f" {w} " in g or f" {w}s " in g for w in ws)        # noqa: E731
    moving = has("arm", "robot", "joint", "servo", "gripper", "mechanism", "pan tilt", "actuator", "linkage", "manipulator")
    wired = moving or has("wire", "wiring", "harness", "arduino", "uno", "nano", "microcontroller", "sensor", "led", "cable", "firmware")
    planned = ["cad"] + (["mechanism"] if moving else []) + (["harness", "firmware"] if wired else [])
    if has("pneumatic", "air cylinder", "compressed air", "solenoid valve", "pneumatics"):
        planned.append("pneumatics")
    missing = [k for k in planned if k not in kinds]
    if missing:
        names = ", ".join(f"{KIND_LABEL[k]} ({KIND_FILE[k]}, combined_reference('{k}'))" for k in missing)
        steps.append(f"Not built yet: {names}. Write a first version of each before refining the others; if this machine really "
                     "does not need one, say so and leave it out.")
    by_kind: dict[str, list[str]] = {}
    for f, t in texts.items():
        if not is_blank(t):
            by_kind.setdefault(kind_of(t), []).append(t)
    cad = " ".join(by_kind.get("cad", []))
    if "harness" in by_kind and "cad" in by_kind and "anchor(" not in cad:
        steps.append("The wiring is not placed on the machine yet: in the CAD file's build(), declare an anchor for every harness connector, "
                     "board and bus block, named as the harness connector (combined_reference('integration')).")
    if "harness" in by_kind and "cad" in by_kind and "INTEGRATION" not in cad:
        steps.append("The CAD model declares no INTEGRATION: give the harness routes through the parts (and mating faces) so the route "
                     "fit and cable length checks run (combined_reference('integration')).")
    mech = " ".join(by_kind.get("mechanism", []))
    if "mechanism" in by_kind and "cad" in by_kind and "COUPLING" not in mech:
        steps.append("The mechanism is not coupled to the CAD model yet: add COUPLING = dict(cad=\"model.py\", links={link: [CAD parts]}) so "
                     "its link masses come from the CAD bodies and the arm is checked for collisions (combined_reference('integration')).")
    fw = " ".join(by_kind.get("firmware", []))
    if "firmware" in by_kind and "harness" in by_kind and "pins" not in fw:
        steps.append("The firmware's pins are not mapped to the harness: COUPLING = dict(harness=\"wiring.py\", pins={\"YAW\": \"UNO.D9\", ...}).")
    if "firmware" in by_kind and "mechanism" in by_kind and "SEQUENCE" not in fw:
        steps.append("The firmware declares no SEQUENCE: list the servo angles the sketch writes, step by step (\"home\", \"pick\", ...), "
                     "and COUPLING joints mapping each servo to its mechanism joint, so the cycle is checked against the joints.")
    if "mechanism" in by_kind and "target(" not in mech and moving:
        steps.append("The mechanism has no targets: m.target(\"pick\", at=(x, y, z)) and m.target(\"place\", ...) so reach is checked.")
    if not steps:
        steps.append("Every check passes in every file. Show the person the viewer link, then export it: "
                     "combined_export(project_id, [\"package\", \"step\"]).")
    return steps, missing


# ------------------------------------------------------------------------------------------------------------- the reference
def _sections(text: str, drop: tuple[str, ...]) -> str:
    """A markdown text without the top-level sections whose heading starts with one of `drop` (the agent loop's own: its tools)."""
    out, skip = [], False
    for ln in text.splitlines():
        if ln.startswith("# "):
            skip = any(ln[2:].strip().lower().startswith(d) for d in drop)
        if not skip:
            out.append(ln)
    return "\n".join(out).strip()


TOPICS = ("overview", "integration", "cad", "mechanism", "harness", "firmware", "pneumatics", "electronics", "catalog")
_DROP = ("how you work", "tools", "the viewer")

OVERVIEW = """# eitri combined projects: how to write the files

A combined project is one machine described by several model files side by side, one per domain, each a Python file with
PARAMS and build(p). eitri builds every file on this computer and runs each domain's checks and the checks BETWEEN the files.
You write the files with combined_put_files and build with combined_build. The project is a folder (combined_new_project
says where); the person watches it in eitri's viewer.

| kind        | usual file  | first line                         | reference topic |
|-------------|-------------|------------------------------------|-----------------|
| cad         | model.py    | from eitri import *                | cad             |
| mechanism   | mech.py     | from eitri.mechanism import *      | mechanism       |
| harness     | wiring.py   | from eitri.harness import *        | harness         |
| firmware    | fw.py       | from eitri.firmware import *       | firmware        |
| pneumatics  | circuit.py  | from eitri.pneumatics import *     | pneumatics      |
| electronics | elec.py     | from eitri.electronics import *    | electronics     |

A file's kind is its import line. A project holds at most 8 model files (lowercase names ending in .py), plus DESIGN.md for your
plan. A new project starts with a blank model.py.

## The loop
1. Plan in a few lines which domains the machine needs (only those: a bracket needs CAD only; a robot arm needs CAD, the
   mechanism, the harness and the firmware, and its circuit when the board, supply and drivers matter) and the facts that tie
   them together (servo models, link lengths, supply voltage, pins). Put the plan in DESIGN.md.
2. Read the reference of each domain you will write (combined_reference('<kind>')) and the cross-domain one
   (combined_reference('integration')). combined_reference('catalog') lists the parts eitri knows (servos, boards, supplies).
3. Write a first working version of EVERY planned domain before refining any of them, in this order: CAD (the bodies and their
   controlling dimensions; simple shapes are fine at first), the mechanism (its link masses come from the CAD bodies), the
   harness, the firmware, the electronics last. Then combined_build.
4. Read the results: every file's checks, the cross-domain ones, and the next steps. Fix what fails, starting where the next
   steps point (failures between domains first), and build again. Repeat until every check passes.
5. A failing check is fixed in the design, never by deleting the check or a declaration: a build that declares fewer
   cross-domain items than an earlier one fails "suite: declared checks are kept".

## Where the domains meet
- In the FIRST CAD build, when the plan includes wiring, declare an anchor for every connector, board and bus the harness will
  use, inside build(p), NAMED EXACTLY AS THE HARNESS CONNECTOR OR SPLICE: anchor("UNO", at=(x, y, z), z=(0, 0, 1), x=(1, 0, 0),
  part="base") for the board (x along its long side, USB at -x), anchor("SERVO_YAW", at=..., z=<the way the lead leaves>,
  part="yaw_servo") for each servo plug, one for the supply lead and one per bus block. Give part= for anything on a moving part.
- Name the harness connectors to match: the board connector's pins are named like the board's (D9, D10, GND, 5V), the servo plugs
  SERVO_<JOINT> (GND, V+, SIG), the supply PSU, the power bus as splices (h.splice("BUS_5V", kind="terminal")).
- In the firmware, declare the pick-and-place cycle as SEQUENCE = [("home", dict(YAW=90, ...)), ("pick", ...), ...] (the servo
  angles the sketch writes, step by step, and the sketch runs that same table) and COUPLING joints={"YAW": "yaw", ...} mapping each
  servo to its mechanism joint (servo_zero={"ELBOW": 45} when a horn is set off 90), and pins={"YAW": "UNO.D9", ...} mapping each
  pin to the harness connector pin that carries it.
- Give the mechanism pick and place targets (m.target("pick", at=...)) the arm must reach, and COUPLING = dict(cad="model.py",
  links={"<link>": ["<CAD part>", ...]}) listing the CAD parts bolted to each link.
- Every servo is a body in the CAD model AT THE JOINT IT DRIVES: a box of its catalog size, named after the joint (elbow_servo
  for joint "elbow"), its output face centred on the joint's at= point with the joint axis normal to that face, listed in
  HARDWARE, and listed in COUPLING links under the link it is bolted to (the joint's parent link).
- Do not model the object the arm picks up unless asked to: m.payload(mass_g) carries its mass through the torque checks.

## The electronics file
A combined project may also hold its circuit at module level: elec.py, `from eitri.electronics import *` (reference topic
'electronics'). Write it after the harness and the firmware; it is built last, and its build checks the circuit against every other
file: the harness pins and nets, each harness wire's current at the actuators' stall (ampacity, connector pins, the voltage left at
each load, the supply and the BOM's supply), the firmware's pins, the mechanism's actuated joints and what carries their stall
current, regulator and driver dissipation, and each board's mounting holes, outline and clearance against the CAD part that carries
it. Name the circuit's parts as the harness connectors (UNO, PSU, SERVO_YAW) and declare COUPLING = dict(actuators={"yaw":
"SERVO_YAW", ...}, mounts={"UNO": dict(anchor="UNO", stand_in="controller", carrier=["standoffs"], screw="M3", clearance_mm=1.0)})
(combined_reference('integration')). A check whose input is missing says "not run" and why: it is not a pass.

## Reading results
Each check has a name, pass or fail, what was measured, what was expected, and where the limit comes from (a datasheet, a
standard, the catalog). Warnings pass but are worth a look; "not run" is not a pass. "Next steps" says what to do; when every
check in every file passes and every planned domain is built, the result says so: show the person the viewer link, and offer
combined_export (the build package is written only when every check has run and passes).
"""


def reference_text(topic: str | None) -> str:
    t = (topic or "overview").strip().lower()
    if t in ("", "overview", "layout", "files", "workflow"):
        return OVERVIEW + "\n\nTopics: " + ", ".join(TOPICS) + " (and 'catalog <query>' for one part)."
    if t in ("integration", "cross-domain", "crossdomain", "links"):
        ref = (Path(__file__).parent / "integration_ref.md").read_text(encoding="utf-8")
        ref = ref.replace("Use `use_model` to switch between them or add one.", "Write each one with combined_put_files.")
        ref = re.sub(r"`?use_model`?\([^)]*\)", "combined_put_files", ref)
        ref = ref.replace("Only when the combined workspace offers an electronics file (elec.py, `from eitri.electronics import *`).",
                          "In an electronics file (elec.py, `from eitri.electronics import *`).")
        ref = ref.replace("`parts_catalog(query)`", "`combined_reference('catalog <query>')`").replace("`bom` rolls", "combined_export(project_id, ['bom']) rolls")
        ref = ref.replace("`build_package` writes", "combined_export(project_id, ['package']) writes")
        return ref
    if t.startswith("catalog") or t.startswith("parts"):
        from . import catalog
        q = t.split(None, 1)[1] if " " in t else ""
        try:
            return catalog_text(catalog, q)
        except ValueError as exc:
            return str(exc)
    if t in ("cad", "mechanism", "harness", "firmware", "pneumatics", "electronics", "wires", "motion"):
        kind = {"wires": "harness", "motion": "mechanism"}.get(t, t)
        from eitri.agent import prompt as P
        if kind == "cad":
            base = Path(P.__file__).parent
            from eitri.agent.library_docs import catalog
            text = _sections((base / "prompt.md").read_text(encoding="utf-8"), _DROP) + "\n\n" + \
                (base / "build123d_ref.md").read_text(encoding="utf-8").strip() + \
                "\n\n# Feature library (eitri's parametric features, usable in build())\n\n```\n" + catalog() + "\n```"
        else:
            from eitri.domains import module
            dom = module(kind)
            text = _sections(dom.prompt_text(), _DROP) + "\n\n" + dom.reference_text()
        return (f"# The {kind} file of a combined project\n(In a combined project the combined loop comes first: a first version of every "
                "planned domain before iterating on any one; see combined_reference('overview').)\n\n" + text)
    raise ToolError(f"no reference topic {topic!r}. Topics: {', '.join(TOPICS)}")


def catalog_text(C, query: str) -> str:
    """One part in full (by id or alias), or the parts whose id, name, category or maker contain every word of the query."""
    if not query.strip():
        return C.catalog_table()
    p = C.lookup(query)
    if p is None:
        words = query.lower().split()
        hits = [x for x in C.PARTS.values()
                if all(w in " ".join([x.id, x.name, x.category or "", x.manufacturer or "", *(x.aliases or [])]).lower() for w in words)]
        if len(hits) == 1:
            p = hits[0]
        elif hits:
            return f"{len(hits)} catalog parts match {query!r} (ask for one by id for its details):\n" + "\n".join(f"  {x.id}: {x.name}" for x in hits)
        else:
            raise ValueError(f"No catalog part matches {query!r}. The catalog holds: {', '.join(C.PARTS)}.")
    lines = [f"{p.id}: {p.name}", f"  part number {p.part_number}; {p.manufacturer}; {p.source or 'no source link'}",
             f"  price {('$%g per %s' % (p.price_usd, p.unit)) if p.price_usd is not None else 'none'} ({p.price_note})"]
    for k, sp in p.specs.items():
        v = ", ".join(f"{x:g} V: {y:g}" for x, y in sorted(sp.value.items())) if sp.is_curve else sp.value
        lines.append(f"  {k}: {v} {sp.unit}".rstrip() + ("" if sp.verified else "  [unverified]") + (f"  ({sp.note})" if sp.note else ""))
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------- results as text
def _rev_tag(f: dict) -> str:
    return f" · revision {f['revision']}" if f.get("revision") is not None else ""


def results_text(r: dict, detail: str = "failing") -> str:
    """A build's results as the agent reads them: one block per file, failing checks in full (all checks with detail="all"),
    the cross-domain tally, the next steps."""
    if r.get("running"):
        return ("Build {build} is running: {done} of {files} files built{cur} ({elapsed_s:.0f} s so far).\n\nNext steps:\n- ".format(
            cur=f", now {r['current']}" if r.get("current") else "", **r) + "\n- ".join(r["next_steps"]))
    if r.get("never_built"):
        return "Nothing has been built in this project yet.\nViewer: " + r["view"] + "\n\nNext steps:\n- " + "\n- ".join(r["next_steps"])
    head = [f"eitri combined build {r['build']} of project {r['project_id']}: "
            + (("EVERY CHECK PASSES" + (" (not built yet: " + ", ".join(r["not_built"]) + ")" if r.get("not_built") else "")) if r["all_pass"] else "not done yet")
            + f" · {len(r['files'])} files · {r['seconds']:.0f} s · built on this computer"]
    if r.get("unchanged"):
        head.append("Nothing changed since this build, so it was not run again.")
    if r.get("stale"):
        head.append("The files changed since this build: call combined_build for fresh results.")
    head.append(f"Folder: {_tilde(r['folder'])}" if r.get("folder") else "")
    head.append(f"Viewer (every file, live): {r.get('view') or ''}")
    out = ["\n".join(h for h in head if h)]
    for f in r["files"]:
        if not f["built"]:
            out.append(f"{f['file']} ({f['kind']}){_rev_tag(f)} · BUILD FAILED\n{f.get('error') or ''}")
            continue
        checks = f.get("checks") or []
        bad = [c for c in checks if not c["ok"]]
        warn = [c for c in checks if c["ok"] and c["severity"] == "warn"]
        lines = [f"{f['file']} ({f['kind']}){' · BLANK' if f['blank'] else ''}{_rev_tag(f)} · "
                 f"{len(checks) - len(bad)}/{len(checks)} checks pass" + (f", {len(warn)} warnings" if warn else "")]
        shown = checks if detail == "all" else bad + warn
        for c in shown:
            tag = "FAIL" if not c["ok"] else "NOT RUN" if c.get("status") == "not_run" else "warn" if c["severity"] == "warn" else "pass"
            lines.append(f"  {tag} {'[cross-domain] ' if c['cross_domain'] else ''}{c['name']}: measured {c['measured'] or '-'}"
                         + (f"; expected {c['expected']}" if c["expected"] else "") + (f"; source: {c['source']}" if c.get("source") else ""))
        if detail != "all" and len(checks) > len(shown):
            names = [c["name"] for c in checks if c["ok"] and c["severity"] != "warn"]
            lines.append("  pass: " + "; ".join(names)[:1500])
        for i in f.get("issues") or []:
            lines.append(f"  geometry issue: {i}")
        out.append("\n".join(lines))
    cd = r.get("cross_domain") or {}
    out.append(f"Cross-domain checks: {cd.get('checks', 0) - cd.get('failing', 0)}/{cd.get('checks', 0)} pass.")
    out.append("Next steps:\n- " + "\n- ".join(r["next_steps"]))
    return "\n\n".join(out)
