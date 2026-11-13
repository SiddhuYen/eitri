"""The suite's tests build with the cross-domain extension on (EITRI_SUITE=1, as a combined project's build has it) and in a
fresh build process each time (the warm worker keeps the environment it started with). Set per test, so the rest of the
core tests never see it."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _suite_extension_on(monkeypatch):
    monkeypatch.setenv("EITRI_SUITE", "1")
    monkeypatch.setenv("EITRI_WORKER", "0")
