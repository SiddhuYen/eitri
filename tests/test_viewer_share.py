"""The read-only share page, as a host serves it (eitri-hosted's SharePolicy: GET only, a fixed list of routes), for every domain:
its download buttons are the project's own export formats (not the four CAD ones) and each one downloads; the page loads with a
clean console (no errors, no failed requests) and never asks for the owner's routes; the domain inspector shows values, not controls."""
from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from eitri.exports import CAD_EXPORTS, export_formats
from eitri.project import Project
from eitri.viewer.server import make_handler

pw = pytest.importorskip("playwright.sync_api")

DEMOS = Path(__file__).resolve().parents[1] / "eitri" / "demos"
CUBE = "from eitri import *\n\nPARAMS = dict(size=20.0)\n\ndef build(p):\n    return Box(p.size, p.size, p.size)\n\ndef verify(parts, p, c):\n    c.equal('width', parts.size.X, p.size)\n"
MODELS = {
    "cad": CUBE,
    "harness": (DEMOS / "quad-power" / "quad_power.py").read_text(encoding="utf-8"),
    "mechanism": (DEMOS / "pan-tilt" / "pan_tilt.py").read_text(encoding="utf-8"),
    "pneumatics": (DEMOS / "air-clamp" / "air_clamp.py").read_text(encoding="utf-8"),
    "firmware": (DEMOS / "servo-sweep" / "servo_sweep.py").read_text(encoding="utf-8"),
}
# eitri-hosted's share route list (cadhost/projects.py SHARE_GET), copied as it is: the page must work within it
SHARE_GET = {"/", "/index.html", "/state.json", "/view.glb", "/topology.json", "/image.png", "/model.py", "/download"}


class SharePolicy:
    """What eitri-hosted's SharePolicy does to the core editor handler."""
    def host_ok(self, handler, post):
        return not post

    def blocked(self, method, path):
        if method != "GET":
            return "This is a read-only shared view."
        if path in SHARE_GET or path.startswith(("/vendor/", "/assets/")):
            return None
        return "This is a read-only shared view."

    def state(self, st):
        st = dict(st)
        st["hosted"] = {"mode": "share", "public_id": "abc", "owner": "maker", "title": st.get("title") or st.get("name"), "domain": st.get("kind")}
        for k in ("edits_full", "edits", "edit_notes", "last_error"):
            st.pop(k, None)
        st.update(has_agent=False, home="/", needs_key=False, demo=None)
        return st


def test_every_domain_lists_its_own_exports():
    from eitri.domains import module
    assert [f["fmt"] for f in export_formats("cad")] == [f for f, _, _ in CAD_EXPORTS]
    for kind in ("harness", "mechanism", "pneumatics", "firmware"):
        fmts = [f["fmt"] for f in export_formats(kind)]
        assert set(fmts) == set(module(kind).DOWNLOADS), kind                # every file the build writes, each one served by /download
        assert all(f["label"] and f["title"] and f["href"] == f"download?fmt={f['fmt']}" for f in export_formats(kind))
    assert {"step", "stl", "3mf", "glb"} <= {f["fmt"] for f in export_formats("cad")}
    assert "step" not in {f["fmt"] for f in export_formats("harness")}


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    """One built project per domain, each behind its own share-policy server."""
    out = {}
    for kind, code in MODELS.items():
        pr = Project(tmp_path_factory.mktemp(kind) / "p", name="shared_" + kind)
        pr.write_model(code)
        assert pr.build(note="first", render=False).ok, kind          # no review image: what a quick rebuild after an edit by hand leaves
        assert pr.kind == kind
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(pr, threading.Lock(), policy=SharePolicy()))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        out[kind] = (pr, f"http://127.0.0.1:{srv.server_address[1]}/")
    yield out
    for _ in out.values():
        pass


@pytest.mark.parametrize("kind", list(MODELS))
def test_share_page_downloads_and_clean_console(shared, kind):
    pr, url = shared[kind]
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        problems: list[str] = []
        page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") and "GL Driver" not in m.text and "GPU stall" not in m.text else None)
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)
        requested: list[str] = []
        page.on("request", lambda r: requested.append(r.url.split("?")[0].replace(url, "/")))
        page.goto(url)
        page.wait_for_selector("#sharedl a", timeout=60000)
        page.wait_for_timeout(2500)                                        # a few polls, the diagram or the model loaded
        assert page.evaluate("() => document.body.classList.contains('readonly')")
        outputs = pr.last_ok_revision().outputs
        offered = [f for f in export_formats(kind) if not f["output"] or f["output"] in outputs]     # what this build can hand out
        fmts = page.eval_on_selector_all("#sharedl a", "els => els.map(a => a.dataset.fmt)")
        assert fmts == [f["fmt"] for f in offered]
        labels = page.eval_on_selector_all("#sharedl a", "els => els.map(a => a.textContent.trim())")
        assert labels == [f["label"] for f in offered]
        if kind != "cad":
            assert "png" not in fmts and "svg" in fmts                      # no PNG was drawn by this build: no button that would 404
        if kind != "cad":
            assert not {"step", "stl", "3mf", "glb"} & set(fmts)
            page.wait_for_selector("#hedit .hpaper svg g[data-id]", state="attached", timeout=30000)     # the diagram drew, through a share route
        # every button downloads, through the share policy (the CAD drawing formats are drawn on demand: checked by their own test)
        for f in offered:
            if kind == "cad" and f["fmt"] in ("svg", "dxf", "iges", "obj"):
                continue
            status = page.evaluate("href => fetch(href).then(r => r.status)", f["href"])
            assert status == 200, (kind, f["fmt"], status)
        assert not problems, problems
        owner_only = {"/revisions", "/thumb", "/events", "/domain.svg", "/harness.svg", "/edits"}
        assert not owner_only & set(requested), sorted(owner_only & set(requested))
        if kind != "cad":                                                   # the docked inspector: values only, nothing that would edit
            assert page.evaluate("() => [...document.querySelectorAll('#domaininsp .hp > button')].every(b => getComputedStyle(b).display === 'none')")
        browser.close()


def test_owner_page_keeps_the_owner_routes(tmp_path):
    """Without a share policy (the local app) the editor still loads the revisions and the domain diagram from their own routes."""
    from eitri.viewer.server import start_viewer
    pr = Project(tmp_path / "p", name="owner")
    pr.write_model(MODELS["mechanism"])
    assert pr.build(note="first", render=False).ok
    url = start_viewer(pr)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        requested: list[str] = []
        page.on("request", lambda r: requested.append(r.url.split("?")[0].replace(url, "/")))
        page.goto(url)
        page.wait_for_selector("#hedit .hpaper svg g[data-id]", state="attached", timeout=60000)
        page.wait_for_timeout(1500)
        assert "/domain.svg" in requested and "/revisions" in requested
        assert not page.evaluate("() => document.body.classList.contains('readonly')")
        fmts = page.eval_on_selector_all("#exportitems a", "els => els.map(a => a.dataset.fmt)")
        outputs = pr.last_ok_revision().outputs
        assert fmts == [f["fmt"] for f in export_formats("mechanism") if f["output"] in outputs] and "png" not in fmts
        browser.close()
