"""Subprocess entry point: build one model file and write result.json.

Invoked by eitri.kernel.build_model; not meant to be called by hand.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--formats", default="step,glb")
    ap.add_argument("--views", default="iso,front,top,right")
    ap.add_argument("--quality", type=float, default=1.0)
    ap.add_argument("--wall-samples", type=int, default=600)
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--set", action="append", default=[], help="param=value override")
    ap.add_argument("--max-parts", type=int, default=0, help="refuse a model with more parts than this (0: no limit)")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = Path(args.model)
    result: dict = {"ok": False, "outputs": {}, "params": {}, "report": None, "summary": "", "error": None}

    def finish(code: int) -> int:
        (out_dir / "result.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
        _Stages(out_dir)("done", done=True)
        return code

    overrides = {}
    for item in args.set:
        if "=" not in item:
            result["error"] = f"bad --set {item!r}; use name=value"
            return finish(2)
        k, v = item.split("=", 1)
        overrides[k.strip()] = v.strip()

    stage = _Stages(out_dir)
    stage("building the geometry")
    t0 = time.time()
    try:
        from .model import load_model
        model = load_model(model_path, overrides)
        result["params"] = {k: v for k, v in model.params.items()}
        parts = model.build()
    except Exception:
        result["error"] = _clean_traceback(traceback.format_exc(), model_path)
        return finish(1)
    if args.max_parts and len(parts) > args.max_parts:
        result["error"] = (f"the model has {len(parts)} parts; this deployment allows at most {args.max_parts} per assembly "
                           "(combine parts, or build the assembly in smaller projects)")
        return finish(1)
    build_seconds = time.time() - t0
    result["build_seconds"] = round(build_seconds, 2)
    timings = result["timings"] = {"build": round(build_seconds, 3)}

    # the viewer's model first, announced as a preview: the page shows the new geometry at once while the
    # checks, the exports and the review render (the slow part) are still running
    stage("making the 3D view")
    t = time.time()
    try:
        from .viewglb import write_view_glb
        vg = out_dir / f"{model_path.stem}.view.glb"
        tj = out_dir / f"{model_path.stem}.topology.json"
        write_view_glb(parts, vg, tj, quality=args.quality)
        result["outputs"]["view_glb"] = str(vg)
        result["outputs"]["topology"] = str(tj)
        _announce_preview(out_dir, vg, tj)
    except Exception:
        result["error"] = (result.get("error") or "") + "\nviewer export failed:\n" + _clean_traceback(traceback.format_exc(), model_path)
    timings["view"] = round(time.time() - t, 3)

    stage("running the model's own checks")
    t = time.time()
    try:
        checker = model.verify(parts)
    except Exception:
        from .model import Checker
        checker = Checker()
        checker.true("verify() ran without error", False, _clean_traceback(traceback.format_exc(), model_path, short=True))
    timings["verify"] = round(time.time() - t, 3)

    stage("exporting " + ", ".join(f.upper() for f in args.formats.split(",") if f))
    t = time.time()
    export_note = ""
    try:
        from .kernel import export_parts
        formats = tuple(f for f in args.formats.split(",") if f)
        outputs = export_parts(parts, out_dir, model_path.stem, formats)
        export_note = outputs.pop("notes", "")
        result["outputs"].update(outputs)
    except Exception:
        result["error"] = "export failed:\n" + _clean_traceback(traceback.format_exc(), model_path)
    timings["export"] = round(time.time() - t, 3)

    t = time.time()
    try:
        from .report import analyze, summarize
        report = analyze(parts, checker, wall_samples=args.wall_samples, progress=stage)
        if export_note:
            report.issues.append(export_note) if "LOST" in export_note else None
        for note in model.edit_notes:
            if not note.ok:
                report.issues.append(f"direct edit failed: {note.line()}")
        result["report"] = report.to_dict()
        summary = summarize(report) + (f"\n{export_note}" if export_note else "")
        if model.edit_notes:
            summary += "\nDirect edits (EDITS in the model file):\n" + "\n".join("  " + n.line() for n in model.edit_notes)
        result["summary"] = summary
        result["edit_notes"] = [n.__dict__ for n in model.edit_notes]
        result["joints"] = list(getattr(parts, "joints", []) or [])
        result["sheets"] = dict(getattr(parts, "sheets", {}) or {})
        result["flats"] = dict(getattr(parts, "flats", {}) or {})
        result["forms"] = dict(getattr(parts, "forms", {}) or {})
        result["groups"] = {q.name: q.group for q in parts if getattr(q, "group", None)}
    except Exception:
        result["error"] = (result.get("error") or "") + "\nanalysis failed:\n" + _clean_traceback(traceback.format_exc(), model_path)
    timings["analyze"] = round(time.time() - t, 3)

    t = time.time()
    if not args.no_render:
        stage("rendering the review image")
        try:
            from .render import contact_sheet
            views = tuple(v for v in args.views.split(",") if v)
            png = out_dir / f"{model_path.stem}.png"
            contact_sheet(parts, str(png), views=views, quality=args.quality, title=model_path.stem)
            result["outputs"]["png"] = str(png)
        except Exception:
            result["error"] = (result.get("error") or "") + "\nrender failed:\n" + _clean_traceback(traceback.format_exc(), model_path)
        timings["render"] = round(time.time() - t, 3)

    result["ok"] = result["error"] is None
    return finish(0 if result["ok"] else 1)


class _Stages:
    """out/progress.json: what the build is doing now, for the page (written when the stage changes)."""

    def __init__(self, out_dir: Path):
        self.out_dir, self.last = out_dir, None

    def __call__(self, message: str, done: bool = False) -> None:
        if message == self.last:
            return
        self.last = message
        try:
            tmp = self.out_dir / "progress.json.tmp"
            tmp.write_text(json.dumps({"stage": message, "ts": time.time(), "done": done}), encoding="utf-8")
            os.replace(tmp, self.out_dir / "progress.json")
        except OSError:
            pass


def _announce_preview(out_dir: Path, glb: Path, topo: Path) -> None:
    """out/preview.json: the newest geometry, before the build is checked and recorded as a revision."""
    data = {"glb": glb.name, "topology": topo.name, "ts": time.time()}
    tmp = out_dir / "preview.json.tmp"
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, out_dir / "preview.json")


def _clean_traceback(tb: str, model_path: Path, short: bool = False) -> str:
    """Keep the frames that are in the model file; drop eitri/importlib noise."""
    lines = tb.splitlines()
    keep: list[str] = []
    model_name = model_path.name
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("  File "):
            in_model = model_name in line or "/build123d/" in line or "/OCP/" in line
            if in_model:
                keep.append(line)
                if i + 1 < len(lines) and not lines[i + 1].startswith("  File "):
                    keep.append(lines[i + 1])
                    i += 1
            else:
                if i + 1 < len(lines) and not lines[i + 1].startswith("  File ") and not lines[i + 1].startswith("Traceback"):
                    i += 1
            i += 1
            continue
        if line.startswith("Traceback"):
            i += 1
            continue
        keep.append(line)
        i += 1
    text = "\n".join(keep).strip()
    if short:
        return text.splitlines()[-1] if text else tb.strip().splitlines()[-1]
    text = text or tb
    hint = _hint_for(tb)
    return text + ("\n\nHint: " + hint if hint else "")


def _hint_for(tb: str) -> str | None:
    """Turn the kernel's opaque errors into advice."""
    low = tb.lower()
    if "fillet" in low or "chamfer" in low:
        return ("the fillet/chamfer failed in the kernel. Usual causes: radius larger than an adjacent face or wall, "
                "edges that meet tangent faces (cylinder seams, previous fillets), or tiny faces left by a boolean. "
                "Use a smaller radius, select fewer edges, fillet the 2D sketch before extruding, or try_fillet(body, "
                "edges, r, fallbacks=(...)).")
    if "command not done" in low or "brep_api" in low:
        return ("an OCCT operation failed (BRep_API: command not done). Check that booleans have overlapping, "
                "non-coincident faces (extend cutters past walls by 1 mm), that lofted sections have the same "
                "orientation, and that sweeps/revolves do not self-intersect.")
    if "no attribute 'to_tuple'" in low:
        return "Vector has no to_tuple() here; use tuple(v) or v.X/v.Y/v.Z."
    if "object has no attribute" in low and "'shapelist'" in low:
        return "ShapeList methods: filter_by(Axis|GeomType|callable), sort_by(Axis|SortBy), group_by(Axis)[i], first/last."
    if "unexpected keyword argument 'align'" in low:
        return "this object takes no align=; place it with Pos(...) * shape instead."
    if "zero_vector" in low or "zero length" in low or ("null" in low and "direction" in low):
        return "a degenerate (zero-length) vector or edge was created; check for coincident points or zero dimensions."
    return None


if __name__ == "__main__":
    sys.exit(main())
