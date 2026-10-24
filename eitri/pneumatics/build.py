"""Build one circuit: checks, the schematic, the BOM and the cylinder table. Called by eitri._runner through eitri.domains."""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from ..model import Checker
from . import diagram, outputs
from .model import Circuit
from .report import CircuitReport, analyze, circuit_data, summarize


def write_outputs(c: Circuit, rep: CircuitReport, out_dir: Path, stem: str, render: bool = True) -> tuple[dict[str, str], diagram.Scene]:
    out_dir = Path(out_dir)
    paths: dict[str, str] = {}
    status = diagram.status_from(rep)
    scene = diagram.build_scene(c, rep, status)
    svg = out_dir / f"{stem}.schematic.svg"
    svg.write_text(diagram.scene_to_svg(scene), encoding="utf-8")
    paths["svg"] = str(svg)
    if render:
        png = out_dir / f"{stem}.png"
        diagram.scene_to_png(diagram.build_scene(c, rep, status, footer=diagram.footer_from(rep)), png)
        paths["png"] = str(png)
    rows = outputs.bom(c)
    for key, name, text in (("bom_md", f"{stem}.bom.md", outputs.bom_markdown(c, rows)), ("csv", f"{stem}.bom.csv", outputs.bom_csv(rows)),
                            ("cylinders_md", f"{stem}.cylinders.md", outputs.cylinders_markdown(c, rep)), ("cylinders_csv", f"{stem}.cylinders.csv", outputs.cylinders_csv(outputs.cylinder_rows(rep)))):
        f = out_dir / name
        f.write_text(text, encoding="utf-8")
        paths[key] = str(f)
    return paths, scene


def scene_json(scene: diagram.Scene) -> dict:
    return {"width": round(scene.width, 1), "height": round(scene.height, 1), "nodes": scene.nodes}


def run(model, c: Circuit, model_path: Path, out_dir: Path, result: dict, stage, clean_traceback, render: bool = True, guards: dict | None = None) -> None:
    timings = result.setdefault("timings", {})
    result["kind"] = "pneumatics"
    stage("running the model's own checks")
    t = time.time()
    try:
        checker = model.verify(c)
    except Exception:
        checker = Checker()
        checker.true("verify() ran without error", False, clean_traceback(traceback.format_exc(), model_path, short=True))
    timings["verify"] = round(time.time() - t, 3)

    stage("sizing cylinders, flow and air")
    t = time.time()
    try:
        rep = analyze(c, checker)
        from .._runner import _run_extensions
        _run_extensions(result, rep, "pneumatics", model, c, model_path, out_dir, json.dumps(guards) if guards else "", stage)
        for note in model.edit_notes:
            if not note.ok:
                rep.issues.append(f"direct edit failed: {note.line()}")
        summary = summarize(rep)
        if model.edit_notes:
            summary += "\nDirect edits (EDITS in the model file):\n" + "\n".join("  " + n.line() for n in model.edit_notes)
        result["report"] = rep.to_dict()
        result["summary"] = summary
        result["edit_notes"] = [n.__dict__ for n in model.edit_notes]
        result["pneumatics"] = circuit_data(c, rep)
    except Exception:
        result["error"] = "analysis failed:\n" + clean_traceback(traceback.format_exc(), model_path)
        return
    timings["analyze"] = round(time.time() - t, 3)

    stage("drawing the schematic, BOM and tables")
    t = time.time()
    try:
        paths, scene = write_outputs(c, rep, out_dir, model_path.stem, render=render)
        result["outputs"].update(paths)
        result["pneumatics"]["scene"] = scene_json(scene)
    except Exception:
        result["error"] = (result.get("error") or "") + "\noutputs failed:\n" + clean_traceback(traceback.format_exc(), model_path)
    timings["outputs"] = round(time.time() - t, 3)
