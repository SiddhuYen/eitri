"""Formats in the browser: a reference image plane placed from a data URL, a DXF file imported into a sketch,
and the OBJ/IGES downloads."""
from __future__ import annotations

import base64
import io
import json

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = ("from eitri import *\nPARAMS = dict(w=60.0)\n\ndef build(p):\n"
         "    return Box(p.w, 40, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))\n")


def test_reference_image_dxf_and_downloads(tmp_path):
    project = Project(tmp_path / "proj", name="plate")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    import ezdxf
    doc = ezdxf.new(); msp = doc.modelspace()
    msp.add_circle((10, 0), 3); msp.add_lwpolyline([(-20, -10), (-10, -10), (-10, 0)], close=False)
    dxf_path = tmp_path / "shape.dxf"; doc.saveas(str(dxf_path))
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (200, 100), (200, 40, 40)).save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("dialog", lambda d: d.dismiss())
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(400)
        # a reference image 120 mm wide on the XZ plane
        page.evaluate(f"() => window.__eitri_render.refImage({json.dumps(data_url)}, 120, 'XZ', -20)")
        page.wait_for_function("() => window.__eitri_render.refPlane() !== null", timeout=10000)
        rp = page.evaluate("() => window.__eitri_render.refPlane()")
        assert rp["w"] == 120 and abs(rp["h"] - 60) < 1e-6 and rp["pos"][1] == -20
        # a sketch on the top face with the DXF's entities merged in
        page.evaluate("() => window.__eitri_tool('sketch')")
        x, y = page.evaluate("() => window.__eitri_project([20, 15, 4])")
        page.mouse.click(x, y)
        page.wait_for_function("() => window.__eitri_debug().sketch", timeout=10000)
        page.wait_for_timeout(300)
        r = page.evaluate(f"() => window.__eitri_sketch.op('dxf', {{ path: {json.dumps(str(dxf_path))}, scale: 1 }})")
        assert r and r["ok"] and len(r["result"]) == 3
        sk = page.evaluate("() => window.__eitri_debug().sketch")
        assert sorted(e["type"] for e in sk["model"]["entities"]) == ["circle", "line", "line"] and sk["profiles"] == 1 and sk["open"] == 1
        page.keyboard.press("Escape"); page.keyboard.press("Escape")
        # downloads
        for fmt, needle in (("obj", b"v "), ("iges", b"IGES")):
            body = page.evaluate(f"() => fetch('/download?fmt={fmt}').then(r => r.arrayBuffer()).then(b => Array.from(new Uint8Array(b).slice(0, 4000)))")
            assert needle in bytes(body), fmt
        browser.close()
