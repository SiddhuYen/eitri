"""A sandbox entry point for combined projects: the core's own (eitri.sandbox), plus the suite.

    python -m eitri_suite.sandbox        (same protocol as `python -m eitri.sandbox`)

The job says whether the project is combined (`"suite": true`). This sets EITRI_SUITE=1 for that job and EITRI_SUITE=0 otherwise
(so a single-domain job never runs the cross-domain checks, whatever its folder says), and it runs the suite's own job kinds
(`suite_bom`, `suite_package`) through the core's `python` job slot. Nothing else about the job protocol changes.
"""
from __future__ import annotations

import os
import sys

from . import ENV, jobs


def main() -> int:
    from eitri import sandbox as core
    read_input, python_job = core._read_input, core._python_job
    state: dict = {}

    def read(work):
        job = read_input(work)
        if job.get("suite") is True:
            os.environ[ENV] = "1"
        else:
            os.environ[ENV] = "0"
        kind = str(job.get("kind") or "")
        if kind.startswith("suite_"):
            if job.get("suite") is not True or kind[6:] not in jobs.KINDS:       # a suite job only for a multi-domain project
                raise ValueError(f"refused job {kind!r}")
            state["kind"], state["opts"] = kind[6:], job.get("opts") or {}
            job = {**job, "kind": "python", "code": ""}
        return job

    def python(model, code, out):
        if state.get("kind"):
            try:
                return jobs.run(state["kind"], model.parent, model.name, out, state["opts"])
            except Exception:
                import traceback
                traceback.print_exc()
                return 1
        return python_job(model, code, out)

    core._read_input, core._python_job = read, python
    return core.main()


if __name__ == "__main__":
    sys.exit(main())
