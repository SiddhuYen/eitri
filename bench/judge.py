"""Blind pairwise judging of benchmark outputs by Claude (vision).

For every task where both systems produced a STEP, render both with eitri's
renderer (identical views), show them to Claude in random A/B order with the
original request, and ask for 1-10 scores on fidelity to the request,
engineering soundness and overall quality. The judge never sees which system
made which image. Results go to bench/results/judge.json and the report.

    .venv/bin/python -m bench.judge --model opus
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

from .run import RESULTS, load_step_parts
from .tasks import TASKS

JUDGE_FILE = RESULTS / "judge.json"

PROMPT = """You are a senior mechanical engineer reviewing two CAD models that were each produced from the same request by different CAD agents. Judge the GEOMETRY you see, not the rendering style (both images come from the same renderer).

Request:
\"\"\"{prompt}\"\"\"

Image A is the first image, image B the second. Each shows iso / front / top / right views with a scale bar and the overall bounding box.

Score each model from 1 to 10 on:
- fidelity: does it contain exactly what was asked (dimensions, features, counts, positions)?
- engineering: is it a sound, manufacturable design (wall thickness, proportions, sensible details, no floating/missing geometry)?
- overall: would you accept this as the part to make?

Reply with ONLY a JSON object like {{"A": {{"fidelity": 7, "engineering": 8, "overall": 7, "notes": "..."}}, "B": {{...}}, "preferred": "A" or "B" or "tie"}}."""


def render_step(step: Path, out_png: Path) -> bool:
    from eitri.model import load_model
    from eitri.render import contact_sheet
    try:
        parts = load_model(step).build()
        contact_sheet(parts, str(out_png), title=step.stem)
        return True
    except Exception as exc:
        print(f"render failed for {step}: {exc}", file=sys.stderr)
        return False


def ask_claude(prompt: str, images: list[Path], model: str, claude_bin: str) -> str:
    content = []
    for img in images:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.standard_b64encode(img.read_bytes()).decode()}})
    content.append({"type": "text", "text": prompt})
    msg = json.dumps({"type": "user", "message": {"role": "user", "content": content}})
    cmd = [claude_bin, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--tools", "", "--model", model, "--no-session-persistence", "--setting-sources", "",
           "--system-prompt", "You are a careful, impartial mechanical engineering reviewer. Answer with JSON only."]
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    proc = subprocess.run(cmd, input=msg + "\n", capture_output=True, text=True, encoding="utf-8", env=env, timeout=600)
    result = ""
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result = ev.get("result") or ""
    return result


def parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="opus")
    ap.add_argument("--systems", default="baseline,eitri")
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    from eitri.agent.claude_cli import find_claude_binary
    claude_bin = find_claude_binary()
    sys_a, sys_b = args.systems.split(",")
    rng = random.Random(args.seed)
    judged: dict = json.loads(JUDGE_FILE.read_text(encoding="utf-8")) if JUDGE_FILE.exists() else {}
    names = [t.name for t in TASKS] if args.tasks == "all" else args.tasks.split(",")
    for task in TASKS:
        if task.name not in names:
            continue
        if task.name in judged and not args.force:
            continue
        steps = {}
        for s in (sys_a, sys_b):
            rf = RESULTS / s / task.name / "result.json"
            if not rf.exists():
                continue
            r = json.loads(rf.read_text(encoding="utf-8"))
            if r.get("step") and Path(r["step"]).exists():
                steps[s] = Path(r["step"])
        if len(steps) < 2:
            print(f"{task.name}: need both outputs, have {list(steps)}")
            continue
        imgs = {}
        for s, step in steps.items():
            png = RESULTS / "judge_renders" / f"{task.name}_{s}.png"
            png.parent.mkdir(parents=True, exist_ok=True)
            if render_step(step, png):
                imgs[s] = png
        if len(imgs) < 2:
            continue
        order = [sys_a, sys_b]
        rng.shuffle(order)
        text = ask_claude(PROMPT.format(prompt=task.prompt), [imgs[order[0]], imgs[order[1]]], args.model, claude_bin)
        data = parse_json(text)
        if not data:
            print(f"{task.name}: unparseable judge reply: {text[:200]}")
            continue
        entry = {"order": order, "raw": data}
        for label, s in zip(("A", "B"), order):
            entry[s] = data.get(label, {})
        pref = data.get("preferred")
        entry["preferred"] = order[0] if pref == "A" else order[1] if pref == "B" else "tie"
        judged[task.name] = entry
        JUDGE_FILE.write_text(json.dumps(judged, indent=1), encoding="utf-8")
        print(f"{task.name}: {sys_a} {entry[sys_a].get('overall')} vs {sys_b} {entry[sys_b].get('overall')} -> {entry['preferred']}")
    print()
    print(summary(judged, sys_a, sys_b))
    return 0


def summary(judged: dict, sys_a: str = "baseline", sys_b: str = "eitri") -> str:
    if not judged:
        return "no judgements"
    lines = [f"| task | {sys_a} fid/eng/overall | {sys_b} fid/eng/overall | preferred |", "|---|---|---|---|"]
    wins = {sys_a: 0, sys_b: 0, "tie": 0}
    tot = {sys_a: [0, 0, 0], sys_b: [0, 0, 0]}
    for name, e in judged.items():
        cells = []
        for s in (sys_a, sys_b):
            d = e.get(s, {})
            vals = [d.get("fidelity", 0), d.get("engineering", 0), d.get("overall", 0)]
            for i, v in enumerate(vals):
                tot[s][i] += v or 0
            cells.append("/".join(str(v) for v in vals))
        wins[e.get("preferred", "tie")] = wins.get(e.get("preferred", "tie"), 0) + 1
        lines.append(f"| {name} | {cells[0]} | {cells[1]} | {e.get('preferred')} |")
    n = len(judged)
    lines.append(f"| **mean** | {'/'.join(f'{v / n:.1f}' for v in tot[sys_a])} | {'/'.join(f'{v / n:.1f}' for v in tot[sys_b])} | "
                 f"{sys_a} {wins[sys_a]}, {sys_b} {wins[sys_b]}, tie {wins['tie']} |")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
