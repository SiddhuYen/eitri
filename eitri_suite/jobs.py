"""The multi-domain jobs that run inside the sandbox besides a plain build: the consolidated BOM and the build package.

Both load and build the project's model files (CAD and harness), so they run where builds run (a sandbox, or a build process
of the local combined workspace); the caller only reads the files they write to the output folder.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import bom as bom_mod
from . import buildpkg

KINDS = ("bom", "package")


def run(kind: str, folder: Path, model_name: str, out: Path, opts: dict) -> int:
    from eitri.project import Project
    project = Project(folder, name=Path(model_name).stem)
    if kind == "bom":
        bom = bom_mod.consolidated(folder, project.model_path, name=project.name)
        (out / "bom.json").write_text(bom_mod.to_json(bom), encoding="utf-8")
        (out / "bom.md").write_text(bom_mod.to_markdown(bom), encoding="utf-8")
        (out / "bom.csv").write_text(bom_mod.to_csv(bom), encoding="utf-8")
        (out / "suite_result.json").write_text(json.dumps({"ok": True, "totals": bom["totals"], "unverified": bom["unverified"]}), encoding="utf-8")
        return 0
    if kind == "package":
        try:
            manifest = buildpkg.build_package(project, out_dir=out / "build_package", force=bool((opts or {}).get("force")),
                                              strict=bool((opts or {}).get("strict")))
            manifest.pop("out", None)
            result = {"ok": True, "manifest": manifest}
        except buildpkg.PackageRefused as exc:
            result = {"ok": False, "refused": str(exc), "failures": exc.failures}
        (out / "suite_result.json").write_text(json.dumps(result), encoding="utf-8")
        return 0
    raise ValueError(f"unknown suite job {kind!r}")
