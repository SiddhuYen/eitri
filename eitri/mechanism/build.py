"""Build one mechanism: checks, the kinematic diagram and the tables. Called by eitri._runner through eitri.domains."""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from ..model import Checker
from . import diagram, outputs
from .model import Mechanism
from .report import MechanismReport, analyze, mechanism_data, summarize


def write_outputs(m: Mechanism, rep: MechanismReport, out_dir: Path, stem: str, render: bool = True) -> tuple[dict[str, str], diagram.Scene]:
    out_dir = Path(out_dir)
    paths: dict[str, str] = {}
    status = diagram.status_from(rep)
    scene = diagram.build_scene(m, rep, status)
    svg = out_dir / f"{stem}.mechanism.svg"
    svg.write_text(diagram.scene_to_svg(scene), encoding="utf-8")
    paths["svg"] = str(svg)
    if render:
        png = out_dir / f"{stem}.png"
        diagram.scene_to_png(diagram.build_scene(m, rep, status, footer=diagram.footer_from(rep)), png)
        paths["png"] = str(png)
    for key, name, text in (("joints_md", f"{stem}.joints.md", outputs.joints_markdown(m, rep)),
                            ("joints_csv", f"{stem}.joints.csv", outputs.joints_csv(outputs.joint_rows(rep))),
                            ("mass_md", f"{stem}.mass.md", outputs.mass_markdown(m, rep)),
                            ("mass_csv", f"{stem}.mass.csv", outputs.mass_csv(outputs.mass_rows(m)))):
        f = out_dir / name
        f.write_text(text, encoding="utf-8")
        paths[key] = str(f)
    return paths, scene


def scene_json(scene: diagram.Scene) -> dict:
    return {"width": round(scene.width, 1), "height": round(scene.height, 1), "nodes": scene.nodes}


def run(model, m: Mechanism, model_path: Path, out_dir: Path, result: dict, stage, clean_traceback, render: bool = True, guards: dict | None = None) -> None:
    timings = result.setdefault("timings", {})
    result["kind"] = "mechanism"
    stage("running the model's own checks")
    t = time.time()
    try:
        checker = model.verify(m)
    except Exception:
        checker = Checker()
        checker.true("verify() ran without error", False, clean_traceback(traceback.format_exc(), model_path, short=True))
    timings["verify"] = round(time.time() - t, 3)

    stage("checking torque, speed, limits and reach")
    t = time.time()
    try:
        rep = analyze(m, checker)
        from .._runner import _run_extensions
        _run_extensions(result, rep, "mechanism", model, m, model_path, out_dir, json.dumps(guards) if guards else "", stage)
        for note in model.edit_notes:
            if not note.ok:
                rep.issues.append(f"direct edit failed: {note.line()}")
        summary = summarize(rep)
        if model.edit_notes:
            summary += "\nDirect edits (EDITS in the model file):\n" + "\n".join("  " + n.line() for n in model.edit_notes)
        result["report"] = rep.to_dict()
        result["summary"] = summary
        result["edit_notes"] = [n.__dict__ for n in model.edit_notes]
        result["mechanism"] = mechanism_data(m, rep)
    except Exception:
        result["error"] = "analysis failed:\n" + clean_traceback(traceback.format_exc(), model_path)
        return
    timings["analyze"] = round(time.time() - t, 3)

    stage("drawing the kinematic diagram and the tables")
    t = time.time()
    try:
        paths, scene = write_outputs(m, rep, out_dir, model_path.stem, render=render)
        result["outputs"].update(paths)
        result["mechanism"]["scene"] = scene_json(scene)
    except Exception:
        result["error"] = (result.get("error") or "") + "\noutputs failed:\n" + clean_traceback(traceback.format_exc(), model_path)
    timings["outputs"] = round(time.time() - t, 3)
