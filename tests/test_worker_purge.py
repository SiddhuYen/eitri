"""The warm worker forgets a project's own helper modules between builds, but never installed packages: a project folder that
holds its own venv (numpy, build123d inside it) must keep them, since an extension module cannot be imported twice."""
from __future__ import annotations

import sys
import types
from pathlib import Path

from eitri import _worker


def test_purge_keeps_installed_packages_and_eitri(tmp_path, monkeypatch):
    helper = tmp_path / "helper.py"
    helper.write_text("X = 1\n")
    fake_numpy = types.ModuleType("fake_numpy_ext")
    fake_numpy.__file__ = str(tmp_path / ".venv" / "lib" / "site-packages" / "fake_numpy_ext" / "__init__.py")
    mine = types.ModuleType("helper")
    mine.__file__ = str(helper)
    monkeypatch.setitem(sys.modules, "helper", mine)
    monkeypatch.setitem(sys.modules, "fake_numpy_ext", fake_numpy)
    venv_site = str((tmp_path / ".venv" / "lib" / "site-packages").resolve()) + "/"
    monkeypatch.setattr(_worker, "_installed_roots", lambda: [venv_site])
    _worker._purge_project_modules(tmp_path.resolve())
    assert "helper" not in sys.modules                       # the project's own helper is forgotten
    assert "fake_numpy_ext" in sys.modules                    # an installed package in the folder's venv is kept
    assert "eitri" in sys.modules


def test_installed_roots_cover_this_interpreter():
    roots = _worker._installed_roots()
    assert any(str(Path(sys.prefix).resolve()).startswith(r.rstrip("/")) for r in roots)
