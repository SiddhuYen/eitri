"""Every test runs against its own empty eitri config: no saved API key, no key in the environment. The suite
must not depend on (or touch) the developer's own settings."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("EITRI_CONFIG_DIR", str(tmp_path_factory.mktemp("eitri-config")))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
