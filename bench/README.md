# eitri benchmark

Everything needed to reproduce the comparison between eitri and the reference
system (earthtojake/text-to-cad) lives here.

## What is measured

`tasks.py` defines 22 natural-language CAD requests, from a drilled plate to a
print-in-place hinge, a threaded bottle cap, a Raspberry Pi case and a meshing
gear pair. Each task has a **checker**: a function that loads the produced STEP
file and measures it (bounding box, hole diameters/positions/through-ness, wall
thickness by ray casting, interference between parts, feature presence). A task's
score is the fraction of checks passed; it is *solved* when all pass. Two tasks
also carry a **follow-up** request sent in the same session, with its own checker,
to measure iterative editing.

`tests/test_bench_checkers.py` builds a known-good reference solution for the
tasks with the eitri library and asserts every checker accepts it, so the
checkers cannot be biased by being wrong.

`judge.py` is a second, independent measure: Claude (with vision) sees the two
systems' outputs rendered by the same renderer in random A/B order, with the
request, and scores fidelity / engineering / overall 1-10 and a preference.

## Systems

- **eitri**: `eitri` driving headless Claude Code (Opus, effort high) with its
  own MCP tools (`python -m bench.run --system eitri`).
- **baseline**: headless Claude Code (same model and effort) with the
  text-to-cad plugin (`text-to-cad@earthtojake`, cadgen 0.7.21) installed and
  enabled, given the same prompt plus "Deliver the result as a STEP file saved in
  the current directory." Its viewer tools are disabled (they open browser tabs
  and do not affect the geometry); everything else runs with permissions bypassed
  in a scratch folder (`python -m bench.run --system baseline`).
- **eitri_v1**: eitri's first configuration, kept for the record. It returned
  review images inline through MCP, which headless Claude Code also serialises as
  base64 text into the context (~19k tokens per image); the final configuration
  returns the image path and the agent views it with the Read tool.

## Reproduce

```bash
.venv/bin/python -m bench.run --system eitri --tasks all
.venv/bin/python -m bench.run --system baseline --tasks all
.venv/bin/python -m bench.run --rescore          # re-run checkers on existing outputs
.venv/bin/python -m bench.judge --model opus     # blind pairwise judge
.venv/bin/python -m bench.run --report           # writes RESULTS.md
```

Every run leaves its transcript (`events`), produced files and per-check verdicts
in `results/<system>/<task>/`. Costs and times come from the CLI's own `result`
events.
