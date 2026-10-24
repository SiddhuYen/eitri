"""Build one firmware project: write the sketch folder (the sources and eitri_pins.h), compile it with arduino-cli, run the
static checks, draw the pinout. Called by eitri._runner through eitri.domains."""
from __future__ import annotations

import json
import shutil
import time
import traceback
from pathlib import Path

from ..model import Checker
from . import diagram, outputs
from .compile import CompileResult, compile_sketch
from .model import Firmware
from .report import FirmwareReport, analyze, firmware_data, summarize


def stage_sketch(fw: Firmware, out_dir: Path) -> Path:
    """out/sketch/<name>/ with every source and the generated eitri_pins.h (arduino-cli wants the folder named after the .ino)."""
    root = Path(out_dir) / "sketch"
    shutil.rmtree(root, ignore_errors=True)
    folder = root / fw.sketch_name
    folder.mkdir(parents=True)
    for s in fw.sources:
        (folder / s.name).write_text(s.code, encoding="utf-8")
    (folder / "eitri_pins.h").write_text(fw.pins_header(), encoding="utf-8")
    return folder


def compile_fw(fw: Firmware, out_dir: Path) -> tuple[Path, CompileResult | None]:
    folder = stage_sketch(fw, out_dir)
    b = fw.board
    if b is None or fw.main_sketch is None:
        return folder, None
    build_dir = Path(out_dir) / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    return folder, compile_sketch(folder, b.fqbn, b.core, build_dir, cache_file=Path(out_dir) / "compile_cache.json")


def write_outputs(fw: Firmware, rep: FirmwareReport, out_dir: Path, stem: str, render: bool = True) -> tuple[dict[str, str], diagram.Scene]:
    out_dir = Path(out_dir)
    paths: dict[str, str] = {}
    status = diagram.status_from(rep)
    scene = diagram.build_scene(fw, rep, status)
    svg = out_dir / f"{stem}.pinout.svg"
    svg.write_text(diagram.scene_to_svg(scene), encoding="utf-8")
    paths["svg"] = str(svg)
    if render:
        png = out_dir / f"{stem}.png"
        diagram.scene_to_png(diagram.build_scene(fw, rep, status, footer=diagram.footer_from(rep)), png)
        paths["png"] = str(png)
    for key, name, text in (("pinout_md", f"{stem}.pinout.md", outputs.pinout_markdown(fw, rep)), ("pinout_csv", f"{stem}.pinout.csv", outputs.pinout_csv(outputs.pinout_rows(fw, rep))),
                            ("header", "eitri_pins.h", fw.pins_header()), ("compile_log", f"{stem}.compile.log", (rep.compile or {}).get("log") or (rep.compile or {}).get("message", "not compiled"))):
        f = out_dir / name
        f.write_text(text, encoding="utf-8")
        paths[key] = str(f)
    return paths, scene


def scene_json(scene: diagram.Scene) -> dict:
    return {"width": round(scene.width, 1), "height": round(scene.height, 1), "nodes": scene.nodes}


def run(model, fw: Firmware, model_path: Path, out_dir: Path, result: dict, stage, clean_traceback, render: bool = True, guards: dict | None = None) -> None:
    timings = result.setdefault("timings", {})
    result["kind"] = "firmware"
    stage("running the model's own checks")
    t = time.time()
    try:
        checker = model.verify(fw)
    except Exception:
        checker = Checker()
        checker.true("verify() ran without error", False, clean_traceback(traceback.format_exc(), model_path, short=True))
    timings["verify"] = round(time.time() - t, 3)

    stage("compiling with arduino-cli")
    t = time.time()
    try:
        _folder, compiled = compile_fw(fw, out_dir)
    except Exception:
        compiled = CompileResult(True, False, "staging or compiling failed: " + clean_traceback(traceback.format_exc(), model_path, short=True))
    timings["compile"] = round(time.time() - t, 3)

    stage("checking the pinout against the board and the sources")
    t = time.time()
    try:
        rep = analyze(fw, checker, compiled)
        from .._runner import _run_extensions
        _run_extensions(result, rep, "firmware", model, fw, model_path, out_dir, json.dumps(guards) if guards else "", stage)
        for note in model.edit_notes:
            if not note.ok:
                rep.issues.append(f"direct edit failed: {note.line()}")
        summary = summarize(rep)
        if model.edit_notes:
            summary += "\nDirect edits (EDITS in the model file):\n" + "\n".join("  " + n.line() for n in model.edit_notes)
        result["report"] = rep.to_dict()
        result["summary"] = summary
        result["edit_notes"] = [n.__dict__ for n in model.edit_notes]
        result["firmware"] = firmware_data(fw, rep)
    except Exception:
        result["error"] = "analysis failed:\n" + clean_traceback(traceback.format_exc(), model_path)
        return
    timings["analyze"] = round(time.time() - t, 3)

    stage("drawing the pinout")
    t = time.time()
    try:
        paths, scene = write_outputs(fw, rep, out_dir, model_path.stem, render=render)
        result["outputs"].update(paths)
        result["firmware"]["scene"] = scene_json(scene)
    except Exception:
        result["error"] = (result.get("error") or "") + "\noutputs failed:\n" + clean_traceback(traceback.format_exc(), model_path)
    timings["outputs"] = round(time.time() - t, 3)
