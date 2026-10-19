"""Build a model file in an isolated subprocess and collect its outputs.

Running the model out of process keeps the agent/REPL alive when OCCT crashes or
a model loops forever, and gives every build a wall-clock timeout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .report import Report


@dataclass
class BuildResult:
    ok: bool
    model_path: str
    out_dir: str
    params: dict = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)   # format -> path (step, glb, stl, 3mf, png)
    report: Report | None = None
    summary: str = ""
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    build_seconds: float = 0.0
    edit_notes: list = field(default_factory=list)

    @property
    def image(self) -> str | None:
        return self.outputs.get("png")


def build_model(model_path: str | Path, out_dir: str | Path, params: Mapping[str, str] | None = None,
                formats: tuple[str, ...] = ("step", "glb"), render: bool = True, timeout: float = 240.0,
                views: tuple[str, ...] = ("iso", "front", "top", "right"), quality: float = 1.0,
                wall_samples: int = 600) -> BuildResult:
    model_path = Path(model_path).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    result_file = out_dir / "result.json"
    # the previous result stays readable while this build runs (the page keeps showing its checks); the runner
    # replaces it atomically, and a result that was not rewritten by this build is never taken for its own
    before = result_file.stat().st_mtime_ns if result_file.exists() else None
    cmd = [sys.executable, "-m", "eitri._runner", "--model", str(model_path), "--out", str(out_dir),
           "--formats", ",".join(formats), "--views", ",".join(views), "--quality", str(quality),
           "--wall-samples", str(wall_samples)]
    if not render:
        cmd.append("--no-render")
    for k, v in (params or {}).items():
        cmd += ["--set", f"{k}={v}"]
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    t0 = time.time()
    from .executor import current as current_executor, require_local_exec
    executor = current_executor()
    if executor is not None:                          # a sandbox runs it; the outputs come back here
        res = executor.run("build", model_path.parent, model_path.name, cmd[7:], timeout)
        stdout, stderr, code = res.stdout, res.stderr, res.code
        if res.error or code == -9:
            return BuildResult(False, str(model_path), str(out_dir), params=dict(params or {}),
                               error=res.error or f"build stopped after {timeout:g} s (infinite loop, a very slow boolean, or a sandbox limit)",
                               stdout=stdout, stderr=stderr, seconds=time.time() - t0)
        _store_sandbox_outputs(res.files, out_dir)
    elif not local_exec_allowed_here():
        require_local_exec("a build")
    elif use_worker():
        code, stdout, stderr = _worker().run(cmd[3:], str(model_path), timeout=timeout)
        if code == -9:
            return BuildResult(False, str(model_path), str(out_dir), params=dict(params or {}),
                               error=f"build timed out after {timeout:g} s (infinite loop or a very slow boolean?)",
                               stdout=stdout, stderr=stderr, seconds=time.time() - t0)
    else:
        try:
            proc = subprocess.run(cmd, cwd=str(model_path.parent), capture_output=True, text=True, encoding="utf-8",
                                  timeout=timeout, env=env)
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return BuildResult(False, str(model_path), str(out_dir), params=dict(params or {}),
                               error=f"build timed out after {timeout:g} s (infinite loop or a very slow boolean?)",
                               stdout=stdout, stderr=stderr, seconds=time.time() - t0)
    seconds = time.time() - t0
    if result_file.exists() and result_file.stat().st_mtime_ns != before:
        data = json.loads(result_file.read_text(encoding="utf-8"))
        report = _report_from_dict(data["report"]) if data.get("report") else None
        return BuildResult(
            ok=bool(data.get("ok")), model_path=str(model_path), out_dir=str(out_dir),
            params=data.get("params", {}), outputs=data.get("outputs", {}), report=report,
            summary=data.get("summary", ""), error=data.get("error"), stdout=stdout, stderr=stderr,
            seconds=seconds, build_seconds=data.get("build_seconds", 0.0), edit_notes=data.get("edit_notes", []),
        )
    # the runner died before writing a result (crash, import error)
    tail = (stderr or stdout).strip().splitlines()[-25:]
    msg = "\n".join(tail) if tail else f"runner exited with code {code} and no output"
    if code and code < 0:
        msg = f"the CAD kernel crashed (signal {-code}). Last output:\n" + msg
    return BuildResult(False, str(model_path), str(out_dir), params=dict(params or {}), error=msg,
                       stdout=stdout, stderr=stderr, seconds=seconds)


def local_exec_allowed_here() -> bool:
    from .executor import local_exec_allowed
    return local_exec_allowed()


def _store_sandbox_outputs(files: dict[str, bytes], out_dir: Path) -> None:
    """A sandboxed build's outputs, written into out_dir; its result.json re-anchored here (paths it
    names that are not its own output files are dropped)."""
    from .executor import sanitize_result, write_files
    files = {k: v for k, v in files.items() if "/" not in k and k != "status.json"}
    raw_result = files.pop("result.json", None)
    write_files(files, out_dir)
    if raw_result is not None:
        try:
            data = json.loads(raw_result)
        except ValueError:
            return
        if isinstance(data, dict):
            (out_dir / "result.json").write_text(json.dumps(sanitize_result(data, out_dir), indent=1, default=str), encoding="utf-8")


_WORKER = None


def use_worker() -> bool:
    """A warm worker (one kernel import per process) unless EITRI_WORKER=0."""
    return os.environ.get("EITRI_WORKER", "1") != "0"


def _worker():
    global _WORKER
    if _WORKER is None:
        from ._worker import Worker
        _WORKER = Worker()
    return _WORKER


def warm_worker() -> None:
    """Start the build worker now: it imports the CAD kernel (~5 s), so starting it early (while Claude
    loads or the user types) keeps that out of the first build."""
    if not use_worker():
        return
    w = _worker()
    with w._lock:
        if not w.alive():
            w._start()


_ENGINE = {"state": "idle", "since": 0.0, "seconds": None}     # idle | loading | ready | failed


def engine_status() -> dict:
    """Whether the CAD kernel is loaded in the build worker yet (for the page's loading indicator)."""
    out = dict(_ENGINE)
    if out["state"] == "loading":
        out["elapsed"] = round(time.time() - out["since"], 1)
    if not use_worker():
        out["state"] = "ready"
    return out


def warm_worker_async() -> None:
    import threading

    def run():
        _ENGINE.update(state="loading", since=time.time())
        try:
            ok = _worker().ping() if use_worker() else True
            _ENGINE.update(state="ready" if ok else "failed", seconds=round(time.time() - _ENGINE["since"], 1))
        except Exception:
            _ENGINE.update(state="failed")
    threading.Thread(target=run, daemon=True).start()


def stop_worker() -> None:
    global _WORKER
    if _WORKER is not None:
        _WORKER.stop()
        _WORKER = None


def _report_from_dict(d: dict) -> Report:
    from .model import CheckResult
    from .report import HoleInfo, PairInfo, PartReport

    parts = []
    for p in d.get("parts", []):
        p = dict(p)
        p["holes"] = [HoleInfo(**h) for h in p.get("holes", [])]
        p["bosses"] = [HoleInfo(**h) for h in p.get("bosses", [])]
        for key in ("bbox_min", "bbox_max", "size", "center_of_mass", "min_wall_at"):
            if p.get(key) is not None:
                p[key] = tuple(p[key])
        for h in p["holes"] + p["bosses"]:
            h.center = tuple(h.center)
        parts.append(PartReport(**p))
    return Report(
        parts=parts, bbox_min=tuple(d["bbox_min"]), bbox_max=tuple(d["bbox_max"]), size=tuple(d["size"]),
        volume=d["volume"], pairs=[PairInfo(**x) for x in d.get("pairs", [])],
        checks=[CheckResult(**c) for c in d.get("checks", [])], issues=list(d.get("issues", [])),
        analysis_seconds=d.get("analysis_seconds", 0.0), far_pairs=d.get("far_pairs", 0), near_gap=d.get("near_gap", 0.0),
    )


def _has_offset_geometry(shape) -> bool:
    """Offset curves (rounded text outlines, offset sketches) cannot be written to STEP."""
    import build123d as bd
    from OCP.BRep import BRep_Tool
    for e in shape.edges():
        if e.geom_type == bd.GeomType.OFFSET:
            return True
    for f in shape.faces():
        if f.geom_type == bd.GeomType.OFFSET:
            return True
        if f.geom_type in (bd.GeomType.EXTRUSION, bd.GeomType.REVOLUTION):
            try:
                if BRep_Tool.Surface_s(f.wrapped).BasisCurve().DynamicType().Name() == "Geom_OffsetCurve":
                    return True
            except Exception:
                continue
    return False


def _nurbs_copy(shape):
    import build123d as bd
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert
    out = bd.Shape.cast(BRepBuilderAPI_NurbsConvert(shape.wrapped, True).Shape())
    out.label, out.color = shape.label, shape.color
    return out


def export_step_verified(parts, path: Path) -> str:
    """Write STEP and prove it holds what was built (OCCT's writer silently drops some faces).

    Parts with offset-curve geometry are converted to NURBS first; the file is then
    re-imported and every part's solid count and volume compared. Returns a note
    ("" when nothing had to be done).
    """
    import build123d as bd
    from .model import Part, Parts

    def write(part_list) -> dict[str, tuple[int, float]]:
        bd.export_step(Parts(part_list).compound, str(path))
        back = bd.import_step(str(path))
        kids = list(back.children) or [back]
        return {(c.label or f"part_{i + 1}"): (len(c.solids()), abs(c.volume)) for i, c in enumerate(kids)}

    expect = {p.name: (len(p.shape.solids()), abs(p.shape.volume)) for p in parts}
    converted = [p.name for p in parts if _has_offset_geometry(p.shape)]
    plist = [Part(p.name, _nurbs_copy(p.shape) if p.name in converted else p.shape, p.color) for p in parts]
    got = write(plist)

    def mismatches():
        bad = []
        for name, (ns, vol) in expect.items():
            gs, gv = got.get(name, (0, 0.0))
            if gs != ns or (vol > 1e-6 and abs(gv - vol) > 0.005 * vol + 1e-6):
                bad.append(f"{name}: file holds {gs} solid(s)/{gv:.1f} mm^3, model built {ns}/{vol:.1f}")
        return bad

    bad = mismatches()
    if bad and len(converted) < len(plist):
        converted = [p.name for p in parts]
        got = write([Part(p.name, _nurbs_copy(p.shape), p.color) for p in parts])
        bad = mismatches()
    note = f"STEP: {', '.join(converted)} written as NURBS (offset-curve geometry is not representable in STEP)" if converted else ""
    if bad:
        note += ("; " if note else "") + "STEP EXPORT LOST GEOMETRY: " + "; ".join(bad)
    return note


def export_parts(parts, out_dir: Path, stem: str, formats: tuple[str, ...]) -> dict[str, str]:
    """Write the requested formats for Parts; returns format -> path (plus "notes" when relevant)."""
    import build123d as bd
    out: dict[str, str] = {}
    if not len(parts):                        # a blank design has nothing to write yet
        return out
    # build123d's Compound(children=...) re-parents the child shapes, so every export
    # takes a fresh compound (sharing one would leave the others empty)
    if "step" in formats:
        p = out_dir / f"{stem}.step"
        note = export_step_verified(parts, p)
        if note:
            out["notes"] = note
        out["step"] = str(p)
    if "glb" in formats:
        p = out_dir / f"{stem}.glb"
        diag = max(parts.bbox.diagonal, 1.0)
        bd.export_gltf(parts.compound, str(p), binary=True, linear_deflection=diag * 0.0008, angular_deflection=0.2)
        out["glb"] = str(p)
    if "stl" in formats:
        p = out_dir / f"{stem}.stl"
        diag = max(parts.bbox.diagonal, 1.0)
        bd.export_stl(parts.compound, str(p), tolerance=min(0.02, diag * 0.0005), angular_tolerance=0.15)
        out["stl"] = str(p)
        if len(parts) > 1:
            for part in parts:
                pp = out_dir / f"{stem}_{part.name}.stl"
                bd.export_stl(part.shape, str(pp), tolerance=min(0.02, diag * 0.0005), angular_tolerance=0.15)
                out[f"stl:{part.name}"] = str(pp)
    if "csv" in formats:
        from .assembly import bom, bom_csv
        p = out_dir / f"{stem}.bom.csv"
        p.write_text(bom_csv(bom(parts)), encoding="utf-8")
        out["csv"] = str(p)
    if "obj" in formats:
        from .meshio import export_obj
        p = out_dir / f"{stem}.obj"
        export_obj(parts, p)
        out["obj"] = str(p)
    if "iges" in formats or "igs" in formats:
        from .meshio import export_iges
        p = out_dir / f"{stem}.iges"
        export_iges(parts, p)
        out["iges"] = str(p)
    if "3mf" in formats:
        p = out_dir / f"{stem}.3mf"
        mesher = bd.Mesher()
        for part in parts:
            mesher.add_shape(part.shape, linear_deflection=0.02, angular_deflection=0.15, part_number=part.name)
        mesher.write(str(p))
        out["3mf"] = str(p)
    return out
