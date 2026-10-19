"""The warm build worker: repeated builds without re-importing the kernel; crash/timeout recovery."""
from __future__ import annotations

import json
import time
from pathlib import Path

from eitri._worker import Worker

PLATE = '''from eitri import *
PARAMS = dict(width=50.0)
def build(p):
    return Box(p.width, 30, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
'''


def _argv(model: Path, out: Path, *extra: str) -> list[str]:
    return ["--model", str(model), "--out", str(out), "--formats", "step", "--no-render", *extra]


def test_worker_builds_repeatedly_and_recovers(tmp_path):
    model = tmp_path / "plate.py"
    model.write_text(PLATE)
    w = Worker()
    try:
        t0 = time.time()
        code, _, err = w.run(_argv(model, tmp_path / "o1"), str(model))
        first = time.time() - t0
        assert code == 0, err
        res = json.loads((tmp_path / "o1" / "result.json").read_text())
        assert res["ok"] and res["report"]["size"][0] == 50
        # second build reuses the warm kernel: much faster than the first
        t0 = time.time()
        code, _, err = w.run(_argv(model, tmp_path / "o2", "--set", "width=70"), str(model))
        second = time.time() - t0
        assert code == 0 and json.loads((tmp_path / "o2" / "result.json").read_text())["report"]["size"][0] == 70
        assert second < first
        # a helper module edit is picked up (modules from the project folder are purged)
        helper = tmp_path / "helper.py"
        helper.write_text("W = 20.0\n")
        model.write_text("from eitri import *\nfrom helper import W\nPARAMS = {}\ndef build(p):\n    return Box(W, 10, 4)\n")
        w.run(_argv(model, tmp_path / "o3"), str(model))
        assert json.loads((tmp_path / "o3" / "result.json").read_text())["report"]["size"][0] == 20
        helper.write_text("W = 33.0\n")
        w.run(_argv(model, tmp_path / "o4"), str(model))
        assert json.loads((tmp_path / "o4" / "result.json").read_text())["report"]["size"][0] == 33
        # a model error is reported, the worker keeps serving
        model.write_text("from eitri import *\nPARAMS = {}\ndef build(p):\n    return Box(q, 1, 1)\n")
        code, _, err = w.run(_argv(model, tmp_path / "o5"), str(model))
        assert code != 0 and "NameError" in json.loads((tmp_path / "o5" / "result.json").read_text())["error"]
        # a timeout kills the worker; the next build restarts it
        model.write_text("from eitri import *\nimport time\nPARAMS = {}\ndef build(p):\n    time.sleep(5)\n    return Box(1,1,1)\n")
        code, _, err = w.run(_argv(model, tmp_path / "o6"), str(model), timeout=1.5)
        assert code == -9 and "timeout" in err
        model.write_text(PLATE)
        code, _, err = w.run(_argv(model, tmp_path / "o7"), str(model))
        assert code == 0 and w.restarts >= 1
    finally:
        w.stop()
