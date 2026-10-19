"""Every ```python block in the build123d reference must execute without error.

If a snippet fails here, the agent would be taught a wrong API: fix the doc.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REF = Path(__file__).resolve().parents[1] / "eitri" / "agent" / "build123d_ref.md"


def snippets() -> list[tuple[int, str]]:
    text = REF.read_text()
    out = []
    for m in re.finditer(r"```python\n(.*?)```", text, re.S):
        line = text[: m.start()].count("\n") + 1
        out.append((line, m.group(1)))
    return out


@pytest.mark.parametrize("line,code", snippets(), ids=lambda x: str(x) if isinstance(x, int) else "code")
def test_snippet_runs(line: int, code: str) -> None:
    ns: dict = {}
    exec("from eitri import *\n", ns)
    exec(code, ns)
