"""Catalog and docs of the feature library, for the `library` tool and the prompt."""
from __future__ import annotations

import inspect

from .. import lib


def _functions() -> list[tuple[str, object]]:
    out = []
    for name in lib.__all__:
        obj = getattr(lib, name, None)
        if obj is not None and (callable(obj) or isinstance(obj, dict)):
            out.append((name, obj))
    return out


def _first_line(obj: object) -> str:
    doc = inspect.getdoc(obj) or ""
    return doc.splitlines()[0] if doc else ""


def catalog() -> str:
    lines = []
    for name, obj in _functions():
        if callable(obj):
            try:
                sig = str(inspect.signature(obj))
            except (TypeError, ValueError):
                sig = "(...)"
            lines.append(f"{name}{sig}\n    {_first_line(obj)}")
        else:
            lines.append(f"{name}: table - {_first_line(obj) or 'see library(\"' + name + '\")'}")
    return "\n".join(lines) if lines else "(library is empty)"


def describe(query: str) -> str:
    q = query.strip().lower()
    hits = []
    for name, obj in _functions():
        doc = inspect.getdoc(obj) or ""
        if q == name.lower() or q in name.lower() or q in doc.lower():
            if callable(obj):
                try:
                    sig = str(inspect.signature(obj))
                except (TypeError, ValueError):
                    sig = "(...)"
                hits.append((name == q.lower(), f"{name}{sig}\n{doc}"))
            else:
                hits.append((name == q.lower(), f"{name} = {obj!r}"))
    if not hits:
        return f"nothing in the library matches {query!r}. Catalog:\n" + catalog()
    hits.sort(key=lambda h: not h[0])
    return "\n\n".join(h[1] for h in hits[:8])
