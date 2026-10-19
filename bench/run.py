"""Run benchmark tasks against eitri and/or the baseline (Claude Code + text-to-cad plugin).

    .venv/bin/python -m bench.run --system eitri --tasks plate,flange --model opus
    .venv/bin/python -m bench.run --system baseline --tasks all
    .venv/bin/python -m bench.run --report            # summarise bench/results/*.json

Each run gets a fresh folder under bench/results/<system>/<task>/ with the
transcript, the produced files and the checker verdicts. The checkers only read
the produced STEP, so both systems are scored identically.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

import build123d as bd

from eitri.model import Parts, normalize_result
from .tasks import TASKS, Task, get

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"

BASELINE_SUFFIX = " Deliver the result as a STEP file saved in the current directory."


def load_step_parts(path: Path) -> Parts:
    comp = bd.import_step(str(path))
    # top-level children are the parts; a bare solid is one part
    if comp.children:
        kids = []
        for i, child in enumerate(comp.children):
            label = child.label or f"part_{i + 1}"
            # flatten sub-assemblies into their solids as one part
            if not child.solids():
                continue
            kids.append((label, child))
        if kids:
            return normalize_result({k: v for k, v in kids}, path.stem)
    return normalize_result(comp if comp.solids() else comp, path.stem)


def score(task: Task, step: Path | None) -> dict:
    if step is None or not step.exists():
        return {"checks": [["produced a STEP file", False, "none found"]], "score": 0.0, "solved": False}
    try:
        parts = load_step_parts(step)
        checks = task.check(parts)
    except Exception as exc:
        return {"checks": [["checker ran", False, f"{type(exc).__name__}: {exc}"]], "score": 0.0, "solved": False,
                "traceback": traceback.format_exc()[-2000:]}
    checks = [("produced a STEP file", True, step.name)] + [tuple(c) for c in checks]
    n_ok = sum(1 for c in checks if c[1])
    return {"checks": [list(c) for c in checks], "score": n_ok / len(checks), "solved": n_ok == len(checks)}


def newest_step(folder: Path) -> Path | None:
    steps = [p for p in folder.rglob("*.st*p") if p.suffix.lower() in (".step", ".stp") and "revisions" not in p.parts]
    if not steps:
        return None
    return max(steps, key=lambda p: p.stat().st_mtime)


# ----------------------------------------------------------------------------- eitri

def _final_step(project):
    """The STEP of the last good revision: routine builds no longer write one, so export it (verified)."""
    rev = project.last_ok_revision()
    if rev is None:
        return None
    if "step" in rev.outputs and Path(rev.outputs["step"]).exists():
        return Path(rev.outputs["step"])
    res = project.build(note="benchmark: export STEP", formats=("step",), render=False)
    return Path(res.outputs["step"]) if res.ok and res.outputs.get("step") else None


def run_eitri(task: Task, out: Path, model: str, effort: str, budget: float) -> dict:
    from eitri.cli import Printer
    from eitri.agent.claude_cli import ClaudeCLISession
    from eitri.agent.prompt import system_prompt
    from eitri.project import Project

    project = Project(out / "project", name=task.name)
    events: list[dict] = []
    printer = Printer(verbose=False)

    def on_event(kind, data):
        if kind in ("text", "tool_use", "tool_result", "result"):
            d = dict(data)
            if kind == "tool_result":
                from eitri.cli import _tool_result_text
                d["text"] = _tool_result_text(d.get("text") or "")[:3000]
            if kind == "tool_use" and isinstance(d.get("input"), dict):
                d["input"] = {k: (v[:400] if isinstance(v, str) else v) for k, v in d["input"].items()}
            events.append({"kind": kind, **d})
        printer(kind, data)

    session = ClaudeCLISession(project, system_prompt(project), model=model, effort=effort, max_budget_usd=budget,
                               on_event=on_event)
    t0 = time.time()
    res = session.send(task.prompt, timeout=1800)
    elapsed = time.time() - t0
    step = _final_step(project)
    out_d = {"system": "eitri", "task": task.name, "seconds": round(elapsed, 1), "agent_seconds": round(res.duration_ms / 1000, 1),
             "cost_usd": round(res.cost_usd, 3),
             "llm_turns": res.turns, "tool_calls": res.tool_calls, "builds": len(project.history),
             "failed_builds": sum(1 for h in project.history if not h.ok), "final_text": res.text[-3000:],
             "is_error": res.is_error, "step": str(step) if step else None, "events": events, **score(task, step)}
    if task.followup and task.followup_check and step is not None:
        # keep the first result's STEP: the follow-up rebuilds in place
        keep = out / "first.step"
        shutil.copy2(step, keep)
        out_d["step"] = str(keep)
        print(f"\n--- follow-up: {task.followup}")
        t1 = time.time()
        res2 = session.send(task.followup, timeout=1800)
        step2 = _final_step(project)
        fu_task = Task(task.name + "_followup", task.followup, task.followup_check)
        out_d["followup"] = {"seconds": round(time.time() - t1, 1), "agent_seconds": round(res2.duration_ms / 1000, 1),
                             "cost_usd": round(res2.cost_usd, 3),
                             "tool_calls": res2.tool_calls, "final_text": res2.text[-2000:],
                             "step": str(step2) if step2 else None, **score(fu_task, step2)}
    session.close()
    return out_d


# ----------------------------------------------------------------------------- baseline

def run_baseline(task: Task, out: Path, model: str, effort: str, budget: float, claude_bin: str) -> dict:
    """Headless Claude Code with the user's installed text-to-cad plugin, working in a scratch dir."""
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    first = _baseline_turn(task.prompt + BASELINE_SUFFIX, work, model, effort, budget, claude_bin)
    step = newest_step(work)
    out_d = {"system": "baseline", "task": task.name, "step": str(step) if step else None, **first, **score(task, step)}
    if task.followup and task.followup_check and step is not None and first.get("session_id"):
        keep = out / "first.step"
        shutil.copy2(step, keep)
        out_d["step"] = str(keep)
        before = {p: p.stat().st_mtime for p in work.rglob("*.st*p")}
        print(f"\n--- follow-up: {task.followup}")
        second = _baseline_turn(task.followup + BASELINE_SUFFIX, work, model, effort, budget, claude_bin,
                                resume=first["session_id"])
        # the follow-up's STEP: the newest file written after the first turn
        step2 = newest_step(work)
        if step2 is not None and before.get(step2) == step2.stat().st_mtime:
            step2 = None
        fu_task = Task(task.name + "_followup", task.followup, task.followup_check)
        out_d["followup"] = {**second, "step": str(step2) if step2 else None, **score(fu_task, step2)}
    return out_d


def _baseline_turn(prompt: str, work: Path, model: str, effort: str, budget: float, claude_bin: str,
                   resume: str | None = None, max_seconds: float = 1500.0) -> dict:
    cmd = [claude_bin, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "bypassPermissions", "--model", model, "--max-budget-usd", str(budget),
           # the plugin's viewer tools open browser tabs on this machine; they do not affect the geometry
           "--disallowedTools", "mcp__plugin_text-to-cad_cad__cad_show", "mcp__plugin_text-to-cad_cad__cad_view",
           "mcp__plugin_text-to-cad_cad__cad_screenshot"]
    if resume:
        cmd += ["--resume", resume]
    if effort:
        cmd += ["--effort", effort]
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    t0 = time.time()
    events: list[dict] = []
    final: dict = {}
    tool_calls = 0
    texts: list[str] = []
    session_id = None
    stderr = ""
    proc = None
    try:
        proc = subprocess.Popen(cmd, cwd=str(work), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env)
        assert proc.stdout
        import threading
        timer = threading.Timer(max_seconds, lambda: proc.kill())
        timer.start()
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if ev.get("session_id"):
                session_id = ev["session_id"]
            if kind == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        tool_calls += 1
                        inp = block.get("input", {})
                        brief = {k: (v[:300] if isinstance(v, str) else v) for k, v in inp.items()}
                        events.append({"kind": "tool_use", "name": block.get("name"), "input": brief})
                        print(f"  > {block.get('name')} {str(brief)[:100]}")
                    elif block.get("type") == "text" and block.get("text"):
                        texts.append(block["text"])
                        events.append({"kind": "text", "text": block["text"][:3000]})
            elif kind == "result":
                final = ev
                break                      # the turn is over: do not wait for the process to decide to exit
        timer.cancel()
        if not final:
            stderr = f"no result event (killed after {max_seconds} s or crashed)"
    except Exception as exc:
        stderr = f"{type(exc).__name__}: {exc}"
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
    elapsed = time.time() - t0
    return {"seconds": round(elapsed, 1), "agent_seconds": round(float(final.get("duration_ms") or 0) / 1000, 1),
            "cost_usd": round(float(final.get("total_cost_usd") or 0.0), 3),
            "llm_turns": int(final.get("num_turns") or 0), "tool_calls": tool_calls,
            "final_text": (final.get("result") or "\n".join(texts))[-3000:], "is_error": bool(final.get("is_error")) or not final,
            "stderr": stderr[-2000:], "events": events, "session_id": session_id}


# ----------------------------------------------------------------------------- driver

def run(system: str, names: list[str], model: str, effort: str, budget: float, repeat: int = 1, force: bool = False) -> None:
    from eitri.agent.claude_cli import find_claude_binary
    claude_bin = find_claude_binary()
    for name in names:
        task = get(name)
        for r in range(repeat):
            out = RESULTS / system / (task.name if repeat == 1 else f"{task.name}_{r + 1}")
            result_file = out / "result.json"
            if result_file.exists() and not force:
                print(f"[{system}] {task.name}: cached ({json.loads(result_file.read_text(encoding="utf-8"))['score']:.2f})")
                continue
            if out.exists():
                shutil.rmtree(out)
            out.mkdir(parents=True)
            print(f"\n=== [{system}] {task.name} ===\n{task.prompt}")
            for attempt in range(3):
                try:
                    if system == "eitri":
                        res = run_eitri(task, out, model, effort, budget)
                    else:
                        res = run_baseline(task, out, model, effort, budget, claude_bin)
                except Exception as exc:
                    res = {"system": system, "task": task.name, "error": f"{type(exc).__name__}: {exc}",
                           "traceback": traceback.format_exc()[-3000:], "score": 0.0, "solved": False, "checks": []}
                if not _infrastructure_failure(res):
                    break
                print(f"--- infrastructure failure ({_infrastructure_failure(res)}); retrying in 30 s")
                time.sleep(30)
                shutil.rmtree(out, ignore_errors=True)
                out.mkdir(parents=True)
            res["model"] = model
            res["effort"] = effort
            result_file.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
            print(f"--- score {res['score']:.2f} solved={res['solved']} in {res.get('seconds', 0)}s ${res.get('cost_usd', 0)}")
            for c in res.get("checks", []):
                print(f"    {'PASS' if c[1] else 'FAIL'} {c[0]}  {c[2]}")


def list_systems() -> list[str]:
    """Result folders that hold task results (ignores judge renders etc.)."""
    if not RESULTS.exists():
        return []
    return sorted(p.name for p in RESULTS.iterdir() if p.is_dir() and any(p.glob("*/result.json")))


def rescore(systems: list[str] | None = None) -> None:
    """Re-run the checkers on existing outputs (checkers evolve; the geometry does not)."""
    systems = systems or list_systems()
    for system in systems:
        for rf in sorted((RESULTS / system).glob("*/result.json")):
            res = json.loads(rf.read_text(encoding="utf-8"))
            try:
                task = get(res["task"])
            except KeyError:
                continue
            step = Path(res["step"]) if res.get("step") else None
            if step is None or not step.exists():
                # results folders may have been renamed (e.g. eitri -> eitri_v1): look next to the result
                folder = rf.parent / "project" if (rf.parent / "project").exists() else rf.parent / "work"
                step = newest_step(folder) if folder.exists() else None
                if step is not None and step.name == "first.step":
                    step = None
                if step is None and (rf.parent / "first.step").exists():
                    step = rf.parent / "first.step"
                res["step"] = str(step) if step else None
            old = res.get("score")
            res.update(score(task, step))
            rf.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
            print(f"[{system}] {res['task']}: {old} -> {res['score']:.2f}")


def _infrastructure_failure(res: dict) -> str | None:
    """A run that never got to design anything because of the network or a tool server."""
    text = (res.get("final_text") or "") + (res.get("error") or "")
    if res.get("tool_calls", 0) == 0 or res.get("is_error"):
        for marker in ("API Error", "Can't reach the API server", "connection timed out", "didn't connect",
                       "failed to connect", "rate limit", "overloaded", "ENOTFOUND", "ECONNRESET"):
            if marker.lower() in text.lower():
                return marker
    return None


def report(systems: list[str] | None = None) -> str:
    systems = systems or list_systems()
    rows = []
    for system in systems:
        for rf in sorted((RESULTS / system).glob("*/result.json")):
            r = json.loads(rf.read_text(encoding="utf-8"))
            r["system"] = system          # the folder is the system (archived runs keep their old name inside)
            rows.append(r)
    if not rows:
        return "no results"
    tasks = [t.name for t in TASKS]
    lines = ["| task | " + " | ".join(systems) + " |", "|---|" + "---|" * len(systems)]
    totals = {s: [0.0, 0, 0, 0.0, 0.0] for s in systems}  # score sum, solved, n, seconds, cost
    for t in tasks:
        cells = []
        for s in systems:
            rs = [r for r in rows if r["system"] == s and r["task"].split("_")[0] == t.split("_")[0] and r["task"] == t]
            if not rs:
                cells.append("-")
                continue
            r = rs[0]
            secs = r.get("agent_seconds") or r.get("seconds", 0)   # the CLI's own turn duration when recorded
            totals[s][0] += r["score"]; totals[s][1] += int(r["solved"]); totals[s][2] += 1
            totals[s][3] += secs; totals[s][4] += r.get("cost_usd", 0)
            cells.append(f"{r['score']:.2f}{' ✓' if r['solved'] else ''} ({secs:.0f}s, ${r.get('cost_usd', 0):.2f})")
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    cells = []
    for s in systems:
        sc, solved, n, secs, cost = totals[s]
        cells.append(f"**{sc / n:.2f} avg, {solved}/{n} solved** ({secs / n:.0f}s, ${cost / n:.2f} avg)" if n else "-")
    lines.append("| **total** | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=["eitri", "baseline"], default="eitri")
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--budget", type=float, default=6.0, help="USD cap per task")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--rescore", action="store_true", help="re-run checkers on existing outputs, then report")
    args = ap.parse_args(argv)
    if args.rescore:
        rescore()
        print(report())
        return 0
    if args.report:
        print(write_report())
        return 0
    names = [t.name for t in TASKS] if args.tasks == "all" else args.tasks.split(",")
    run(args.system, names, args.model, args.effort, args.budget, args.repeat, args.force)
    print()
    print(write_report())
    return 0


def _summary_paragraph() -> str:
    """One paragraph of headline numbers for the systems that have results."""
    systems = list_systems()
    bits = []
    for s in systems:
        rows = [json.loads(rf.read_text(encoding="utf-8")) for rf in sorted((RESULTS / s).glob("*/result.json"))]
        if not rows:
            continue
        n = len(rows)
        solved = sum(int(r["solved"]) for r in rows)
        score = sum(r["score"] for r in rows) / n
        secs = sum((r.get("agent_seconds") or r.get("seconds", 0)) for r in rows) / n
        cost = sum(r.get("cost_usd", 0) for r in rows) / n
        fus = [r["followup"] for r in rows if r.get("followup")]
        fu_txt = f"; follow-ups {sum(int(f['solved']) for f in fus)}/{len(fus)} solved" if fus else ""
        bits.append(f"**{s}**: {solved}/{n} tasks solved (mean score {score:.2f}), {secs:.0f} s and ${cost:.2f} per task on average{fu_txt}")
    return "Headline: " + "; ".join(bits) + "." if bits else ""


def write_report() -> str:
    """Render the comparison table plus per-task check details into bench/RESULTS.md."""
    table = report()
    lines = ["# Benchmark results", "", _summary_paragraph(), "",
             "Scored by the geometry checkers in `bench/tasks.py` on the produced STEP. "
             "Score = fraction of checks passed; ✓ = all passed. Times and costs are per task as reported by the CLI. "
             "Both systems ran the same model (Claude Opus 5.5 via Claude Code, effort high) on the same prompts; "
             "the baseline is headless Claude Code with the text-to-cad plugin.",
             "", table, ""]
    systems = list_systems()
    # follow-up (multi-turn) results
    fu_rows = []
    for t_ in TASKS:
        if not t_.followup:
            continue
        cells = []
        for s in systems:
            rf = RESULTS / s / t_.name / "result.json"
            if not rf.exists():
                cells.append("-")
                continue
            r = json.loads(rf.read_text(encoding="utf-8"))
            fu = r.get("followup")
            fsecs = (fu.get("agent_seconds") or fu.get("seconds", 0)) if fu else 0
            cells.append(f"{fu['score']:.2f}{' ✓' if fu['solved'] else ''} ({fsecs:.0f}s, ${fu.get('cost_usd', 0):.2f})" if fu else "-")
        fu_rows.append(f"| {t_.name} | " + " | ".join(cells) + " |")
    if fu_rows:
        lines += ["## Follow-up edits (same session, second request)", "",
                  "| task | " + " | ".join(systems) + " |", "|---|" + "---|" * len(systems), *fu_rows, ""]
    lines += ["## Method notes", "",
              "- Times are the Claude Code CLI's own per-turn duration (`duration_ms`) for runs that recorded it, "
              "else wall time around the session. Wall time additionally includes CLI/MCP start-up and, during this "
              "run, long network retries on a flaky connection; both systems were affected the same way.",
              "- Costs are the CLI's list-price equivalents (tokens x API prices); the runs used a subscription login, "
              "so they consumed plan usage rather than money.",
              "- `eitri` is the final configuration. Four of its tasks (enclosure, name_tag, knob, rpi_case) were "
              "re-run once after product fixes made during the pass: a STEP-writer bug (offset-curve faces dropped on "
              "export, which also broke the enclosure follow-up) and a thin-wall warning raised from 0.8 to 1.5 mm. "
              "Their first attempts under the final configuration scored 1.00/0.75(follow-up), 0.62, 0.89 and 0.91. "
              "`eitri_v1` is the first configuration (inline MCP images, see bench/README.md), untouched.",
              "- The baseline ran once per task with no re-runs; its two misses are wall-thickness checks "
              "(1.84 mm and 1.32 mm where >= 2 and 1.6 mm were requested) that its agent never measured.",
              "- Checkers were validated against library-built reference solutions (`tests/test_bench_checkers.py`); "
              "checker fixes made during the run were applied to both systems by re-scoring the saved STEP files.",
              ""]
    judge_file = RESULTS / "judge.json"
    if judge_file.exists():
        from .judge import summary as judge_summary
        lines += ["## Blind pairwise judge (Claude, vision)", "",
                  "Both STEP files rendered with the same renderer, shown in random A/B order with the request; "
                  "scores 1-10 for fidelity / engineering / overall.", "",
                  judge_summary(json.loads(judge_file.read_text(encoding="utf-8"))), ""]
    for t in TASKS:
        lines.append(f"## {t.name}")
        lines.append("")
        lines.append(f"> {t.prompt}")
        lines.append("")
        for s in systems:
            rf = RESULTS / s / t.name / "result.json"
            if not rf.exists():
                continue
            r = json.loads(rf.read_text(encoding="utf-8"))
            lines.append(f"**{s}** - {r['score']:.2f}, {(r.get('agent_seconds') or r.get('seconds', 0)):.0f}s, ${r.get('cost_usd', 0):.2f}, "
                         f"{r.get('tool_calls', 0)} tool calls" + (f", {r.get('builds', 0)} builds" if "builds" in r else ""))
            for c in r.get("checks", []):
                lines.append(f"- {'PASS' if c[1] else 'FAIL'} {c[0]}" + (f" ({c[2]})" if c[2] else ""))
            lines.append("")
    (ROOT / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
    return table


if __name__ == "__main__":
    sys.exit(main())
