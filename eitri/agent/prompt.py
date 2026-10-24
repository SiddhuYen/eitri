"""Assemble the system prompt: behaviour, the model contract, the verified build123d
reference, the library catalog and the project's design notes."""
from __future__ import annotations

from pathlib import Path

from ..project import Project

_HERE = Path(__file__).parent


def domain_prompt_text(kind: str) -> str:
    """The behaviour prompt of a non-CAD domain (<kind>_prompt.md)."""
    return (_HERE / f"{kind}_prompt.md").read_text(encoding="utf-8").strip()


def domain_reference_text(kind: str, fill: dict[str, str] | None = None) -> str:
    """A domain's reference (<kind>_ref.md), with {{PLACEHOLDER}} tables filled in from the code so they cannot drift."""
    text = (_HERE / f"{kind}_ref.md").read_text(encoding="utf-8")
    for key, value in (fill or {}).items():
        text = text.replace("{{" + key + "}}", value)
    return text.strip()


def harness_reference_text() -> str:
    """The wire-harness reference (the connector catalog filled in from the code, so it cannot drift)."""
    from ..harness.catalog import catalog_table
    return (_HERE / "harness_ref.md").read_text(encoding="utf-8").replace("{{CATALOG}}", catalog_table()).strip()


def system_prompt(project: Project | None = None, include_reference: bool = True) -> str:
    from .library_docs import catalog

    if project is not None and project.kind != "cad":          # another domain: no CAD reference, its own prompt and reference
        from ..domains import module
        dom = module(project.kind)
        parts = [dom.prompt_text()]
        if include_reference:
            parts.append(dom.reference_text())
        notes = project.design_notes_path
        if notes.exists() and notes.read_text(encoding="utf-8").strip():
            parts.append("# Project design notes (DESIGN.md)\n\n" + notes.read_text(encoding="utf-8").strip())
        code = project.read_model()
        if code:
            parts.append(f"# Current model file ({project.model_path.name})\n\n```python\n{code.strip()}\n```")
        return "\n\n".join(parts)

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
