"""Finding a project's model files: a project folder may hold a model of each domain, told apart by their import line."""
from __future__ import annotations

from pathlib import Path


def files_by_kind(folder: Path) -> dict[str, list[Path]]:
    """{core kind: model files} among the folder's top-level .py files ("cad", "harness", "mechanism", "pneumatics", "firmware")."""
    from eitri.domains import kind_of_source
    out: dict[str, list[Path]] = {}
    for f in sorted(Path(folder).glob("*.py")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if "def build(" in text:
            out.setdefault(kind_of_source(text), []).append(f)
    return out


def model_files(folder: Path) -> tuple[list[Path], list[Path]]:
    """(CAD model files, harness model files)."""
    by = files_by_kind(folder)
    return by.get("cad", []), by.get("harness", [])
