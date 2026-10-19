"""Assemble the system prompt: behaviour, the model contract, the verified build123d
reference, the library catalog and the project's design notes."""
from __future__ import annotations

from pathlib import Path

from ..project import Project

_HERE = Path(__file__).parent


def system_prompt(project: Project | None = None, include_reference: bool = True) -> str:
    from .library_docs import catalog

    parts = [(_HERE / "prompt.md").read_text(encoding="utf-8").strip()]
    if include_reference:
        parts.append((_HERE / "build123d_ref.md").read_text(encoding="utf-8").strip())
    parts.append("# Feature library (call library(name) for full docs)\n\n```\n" + catalog() + "\n```")
    if project is not None:
        notes = project.design_notes_path
        if notes.exists() and notes.read_text(encoding="utf-8").strip():
            parts.append("# Project design notes (DESIGN.md)\n\n" + notes.read_text(encoding="utf-8").strip())
        code = project.read_model()
        if code:
            parts.append(f"# Current model file ({project.model_path.name})\n\n```python\n{code.strip()}\n```")
    return "\n\n".join(parts)
