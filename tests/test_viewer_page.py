"""The viewer page loads a built model in a real browser (needs playwright + chromium; skipped otherwise)."""
from __future__ import annotations

from pathlib import Path

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

pw = pytest.importorskip("playwright.sync_api")


def test_viewer_page_loads_model(tmp_path):
    project = Project(tmp_path / "proj", name="enclosure")
    project.write_model((EXAMPLES / "enclosure.py").read_text())
    res = project.build(note="test")
    assert res.ok
    url = start_viewer(project)
    errors: list[str] = []
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # chromium not installed
            pytest.skip(f"chromium not available: {exc}")
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(url)
        page.wait_for_function("document.querySelectorAll('#parts .part').length >= 2", timeout=120000)
        names = page.eval_on_selector_all("#parts .part", "els => els.map(e => e.textContent)")
        assert any("base" in n for n in names) and any("lid" in n for n in names)
        assert page.eval_on_selector_all("#params [data-key]", "els => els.length") >= 5
        assert "revision 1" in page.text_content("#status")
        checks = page.eval_on_selector_all("#checks .ok", "els => els.length")
        assert checks >= 3
        # the camera is fitted to the model in mm and the canvas actually shows it
        dbg = page.evaluate("() => window.__eitri_debug()")
        assert 40 < dbg["radius"] < 80, dbg            # enclosure 100 x 60 x 40 mm -> half-diagonal ~ 60
        assert set(dbg["meshes"]) == {"base", "lid"}, dbg
        import io
        import numpy as np
        from PIL import Image
        page.wait_for_timeout(500)
        img = np.array(Image.open(io.BytesIO(page.screenshot())).convert("RGB"))
        h, w = img.shape[:2]
        canvas = img[int(h * 0.15):int(h * 0.85), int(w * 0.1):int(w * 0.6)]
        bg = np.array([244, 245, 247])
        drawn = (np.abs(canvas.astype(int) - bg).sum(axis=2) > 40).mean()
        assert drawn > 0.15, f"only {drawn:.1%} of the canvas is drawn"
        browser.close()
    assert not [e for e in errors if "favicon" not in e], errors
