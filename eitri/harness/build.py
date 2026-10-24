"""Build one harness: checks, the diagram and the shop outputs. Called by eitri._runner for a model whose
build(p) returned a Harness (the harness counterpart of the geometry pipeline)."""
from __future__ import annotations

import time
import traceback
from pathlib import Path

from ..model import Checker
from . import diagram, outputs
from .model import Harness
from .report import HarnessReport, analyze, harness_data, summarize


def write_outputs(h: Harness, rep: HarnessReport, out_dir: Path, stem: str, render: bool = True) -> tuple[dict[str, str], diagram.Scene]:
    """The diagram (SVG, and the PNG review image when render), BOM, cut list and WireViz YAML. Returns (paths, scene)."""
    out_dir = Path(out_dir)
    paths: dict[str, str] = {}
    status = diagram.status_from(rep)
    scene = diagram.build_scene(h, status)
    svg = out_dir / f"{stem}.harness.svg"
    svg.write_text(diagram.scene_to_svg(scene), encoding="utf-8")
    paths["svg"] = str(svg)
    if render:
        png = out_dir / f"{stem}.png"
        diagram.scene_to_png(diagram.build_scene(h, status, footer=diagram.footer_from(rep)), png)
        paths["png"] = str(png)
    rows = outputs.bom(h)
    for key, name, text in (("bom_md", f"{stem}.bom.md", outputs.bom_markdown(h, rows)),
                            ("csv", f"{stem}.bom.csv", outputs.bom_csv(rows)),
                            ("cutlist_md", f"{stem}.cutlist.md", outputs.cutlist_markdown(h)),
                            ("cutlist_csv", f"{stem}.cutlist.csv", outputs.cutlist_csv(outputs.cutlist(h))),
                            ("wireviz", f"{stem}.wireviz.yaml", outputs.wireviz_yaml(h))):
        f = out_dir / name
        f.write_text(text, encoding="utf-8")
        paths[key] = str(f)
    return paths, scene


def scene_json(scene: diagram.Scene) -> dict:
    return {"width": round(scene.width, 1), "height": round(scene.height, 1), "nodes": scene.nodes, "pins": scene.pins,
            "wires": {k: {"pts": [list(p) for p in v["pts"]], "t": v["t"], "sides": v["sides"], "label": list(v["label"])} for k, v in scene.wires.items()}}


def run(model, h: Harness, model_path: Path, out_dir: Path, result: dict, stage, clean_traceback, render: bool = True) -> None:
    """Fill `result` (the runner's result.json) for a built harness."""
    timings = result.setdefault("timings", {})
    result["kind"] = "harness"
    stage("running the model's own checks")
    t = time.time()
    try:
        checker = model.verify(h)
    except Exception:
        checker = Checker()
        checker.true("verify() ran without error", False, clean_traceback(traceback.format_exc(), model_path, short=True))
    timings["verify"] = round(time.time() - t, 3)

    stage("checking connectivity, ampacity and voltage drop")
    t = time.time()
    try:
        rep = analyze(h, checker)
        for note in model.edit_notes:
            if not note.ok:
                rep.issues.append(f"direct edit failed: {note.line()}")
        summary = summarize(rep)
        if model.edit_notes:
            summary += "\nDirect edits (EDITS in the model file):\n" + "\n".join("  " + n.line() for n in model.edit_notes)
        result["report"] = rep.to_dict()
        result["summary"] = summary
        result["edit_notes"] = [n.__dict__ for n in model.edit_notes]
        result["harness"] = harness_data(h)
    except Exception:
        result["error"] = "analysis failed:\n" + clean_traceback(traceback.format_exc(), model_path)
        return
    timings["analyze"] = round(time.time() - t, 3)

    stage("drawing the diagram, BOM and cut list")
    t = time.time()
    try:
        paths, scene = write_outputs(h, rep, out_dir, model_path.stem, render=render)
        result["outputs"].update(paths)
        result["harness"]["scene"] = scene_json(scene)
    except Exception:
        result["error"] = (result.get("error") or "") + "\noutputs failed:\n" + clean_traceback(traceback.format_exc(), model_path)
    timings["outputs"] = round(time.time() - t, 3)
