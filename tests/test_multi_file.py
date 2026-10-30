"""A project with several model files (a host's CAD part, its wiring, its firmware): each file keeps its own last good build,
checks and exports, the state lists the files, and the editor shows a tab per file only when the host can switch them
(EITRI_SHELL.switchModel), with EITRI_SHELL.place(state) for the crumb, rail and status bar. A project with one file is unchanged."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from eitri.project import Project
from eitri.viewer.server import _state, start_viewer

CAD = '''from eitri import *

PARAMS = dict(w=40)

def build(p):
    return Box(p.w, 20, 6)

def verify(parts, p, c):
    c.true("plate is wide enough", p.w >= 30)
'''
WIRES = '''from eitri.harness import *

PARAMS = dict(n=1)

def build(p):
    h = Harness("Lead")
    a = h.add(Connector("A", "XT30", pins=["+", "-"]))
    b = h.add(Connector("B", "XT30", pins=["+", "-"]))
    h.connect(a["+"], b["+"], Wire(16, "RD", 200))
    h.connect(a["-"], b["-"], Wire(16, "BK", 200))
    return h
'''
FW = '''from eitri.firmware import *

PARAMS = dict(ms=500)

SKETCH = r"""
#include "eitri_pins.h"
void setup() { pinMode(PIN_LED, OUTPUT); }
void loop() { digitalWrite(PIN_LED, HIGH); delay(500); digitalWrite(PIN_LED, LOW); delay(500); }
"""

def build(p):
    fw = Firmware("Blink", board="uno")
    fw.pin("D13", "out", "digital_out", net="LED")
    fw.source("blink.ino", SKETCH)
    return fw
'''
FILES = (("model.py", CAD), ("wiring.py", WIRES), ("fw.py", FW))


def _three(folder: Path) -> Project:
    """CAD + wires + firmware, each built once (revisions 1, 2, 3), model.py current."""
    pr = Project(folder, name="model")
    for f, code in FILES:
        (pr.folder / f).write_text(code, encoding="utf-8")
    for f, _ in FILES:
        pr.set_model_file(f)
        assert pr.build(note="first " + f, render=False).ok, f
    pr.set_model_file("model.py")
    return pr


def _json(url, path, payload=None):
    req = urllib.request.Request(url + path.lstrip("/"), data=None if payload is None else json.dumps(payload).encode(),
                                 method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:                     # a refused request answers with its error as JSON
        return json.loads(e.read())


def _tally(checks):
    fail = sum(1 for c in checks if not c["ok"])
    warn = sum(1 for c in checks if c["ok"] and c.get("severity") == "warn")
    return {"pass": len(checks) - fail - warn, "warn": warn, "fail": fail}


def test_each_model_file_keeps_its_own_last_good_build(tmp_path):
    pr = _three(tmp_path / "rig")
    seen = {}
    for f, kind, rev, check in (("model.py", "cad", 1, "plate is wide enough"), ("wiring.py", "harness", 2, "no duplicate wires"),
                                ("fw.py", "firmware", 3, "target board")):
        pr.set_model_file(f)
        s = _state(pr)
        # the page shows this file's own build: its kind, revision, checks, outputs, geometry URL
        assert (s["file"], s["kind"], s["ok_revision"], s["last_ok"]) == (f, kind, rev, True)
        assert check in [c["name"] for c in s["checks"]] and s["glb"] == f"view.glb?r={rev}"
        assert pr.last_ok_revision().number == rev == pr.last_ok_revision(model=f).number
        assert (pr.folder / f"revisions/{rev:03d}/result.json").is_file()      # the report is kept with the revision
        seen[f] = s
    # the files, in the tools' order, each with its last good revision and that build's check counts
    models = seen["fw.py"]["models"]
    assert [(m["file"], m["kind"], m["name"], m["current"], m["ok_revision"]) for m in models] == [
        ("model.py", "cad", "model", False, 1), ("wiring.py", "harness", "wiring", False, 2), ("fw.py", "firmware", "fw", True, 3)]
    assert [m["checks"] for m in models] == [_tally(seen[f]["checks"]) for f in ("model.py", "wiring.py", "fw.py")]
    assert seen["wiring.py"]["outputs"].keys() != seen["fw.py"]["outputs"].keys()
    assert "svg" in seen["wiring.py"]["outputs"] and "header" in seen["fw.py"]["outputs"]

    # a file never built shows no build, even though the others have one
    (pr.folder / "arm.py").write_text(CAD.replace("40", "50"), encoding="utf-8")
    pr.set_model_file("arm.py")
    s = _state(pr)
    assert (s["ok_revision"], s["checks"], s["outputs"], s["last_ok"], s["last_error"]) == (0, [], {}, False, None)
    assert [m["file"] for m in s["models"]] == ["arm.py", "model.py", "wiring.py", "fw.py"] and pr.last_ok_revision() is None

    # a failed build belongs to its file: the others still read as good
    pr.set_model_file("wiring.py")
    (pr.folder / "wiring.py").write_text(WIRES.replace("def build(p):", "def build(p):\n    raise ValueError('nope')"), encoding="utf-8")
    assert not pr.build(note="broken", render=False).ok
    s = _state(pr)
    assert (s["ok_revision"], s["last_ok"]) == (2, False) and s["last_error"]
    pr.set_model_file("model.py")
    s = _state(pr)
    assert (s["ok_revision"], s["last_ok"], s["last_error"]) == (1, True, None)
    assert [c["name"] for c in s["checks"]] == ["plate is wide enough"]
    assert next(m for m in s["models"] if m["file"] == "wiring.py")["ok_revision"] == 2


def test_undo_restore_revisions_and_downloads_stay_in_the_current_file(tmp_path):
    pr = _three(tmp_path / "rig")
    url = start_viewer(pr)
    (pr.folder / "model.py").write_text(CAD.replace("w=40", "w=60"), encoding="utf-8")
    assert pr.build(note="wider", render=False).ok                         # revision 4, model.py
    wiring = (pr.folder / "wiring.py").read_text(encoding="utf-8")
    # the history panel: this file's revisions only
    assert [r["number"] for r in _json(url, "revisions")] == [1, 4]
    pr.set_model_file("wiring.py")
    assert [r["number"] for r in _json(url, "revisions")] == [2]
    # restoring another file's revision would copy its source over this file: refused, nothing written
    with pytest.raises(ValueError, match="model.py"):
        pr.restore(1)
    r = _json(url, "restore", {"number": 4})
    assert not r["ok"] and "model.py" in r["error"] and (pr.folder / "wiring.py").read_text(encoding="utf-8") == wiring
    assert _json(url, "undo", {})["error"] == "nothing to undo"                # wiring.py has one build
    # undo in model.py goes back to model.py's own previous build
    pr.set_model_file("model.py")
    r = _json(url, "undo", {})
    assert r["ok"] and "w=40" in (pr.folder / "model.py").read_text(encoding="utf-8") and r["state"]["file"] == "model.py"
    assert (pr.folder / "wiring.py").read_text(encoding="utf-8") == wiring
    # compare defaults to this file's last two builds
    assert (pr.compare()["a"], pr.compare()["b"]) == (4, 5)
    # a domain's download is the current file's build
    pr.set_model_file("wiring.py")
    with urllib.request.urlopen(url + "download?fmt=svg", timeout=60) as resp:
        assert b"XT30" in resp.read()
    pr.set_model_file("fw.py")
    with urllib.request.urlopen(url + "download?fmt=svg", timeout=60) as resp:
        assert b"D13" in resp.read()


def test_a_single_file_project_is_unchanged(tmp_path):
    pr = Project(tmp_path / "lead", name="lead")
    pr.write_model(WIRES)
    assert pr.build(note="first", render=False).ok
    s = _state(pr)
    assert s["file"] == "lead.py" and s["models"] == [{"file": "lead.py", "kind": "harness", "name": "lead", "current": True,
                                                       "ok_revision": 1, "checks": _tally(s["checks"])}]
    # the newest build is this file's: its result is read from out/ as before (a failed build there has no report)
    pr.write_model(WIRES.replace("def build(p):", "def build(p):\n    raise ValueError('nope')"))
    assert not pr.build(note="broken", render=False).ok
    s = _state(pr)
    assert (s["ok_revision"], s["last_ok"], s["checks"]) == (1, False, []) and s["last_error"]
    assert pr.last_ok_revision().number == 1 and [h.number for h in pr.revisions_of()] == [1, 2]
    # a project folder with no model yet lists the file it will write
    fresh = Project(tmp_path / "fresh", name="fresh")
    assert [m["file"] for m in _state(fresh)["models"]] == ["fresh.py"]


# ---------------------------------------------------------------- the editor
pw = pytest.importorskip("playwright.sync_api")

HOST = """window.EITRI_SHELL = { host: true,
  railBottom: [{ key: 'all', href: '/workspace', name: 'Combined workspace', short: 'Workspace', glyph: 'all' },
               { key: 'app', href: '/app', name: 'Projects', short: 'Projects', glyph: 'projects' }],
  switchModel: function (file) { (window.__switched = window.__switched || []).push(file); return window.pySwitch(file); },
  addModel: function (kind) { window.__added = kind; } %s };"""
PLACE = """, place: function (s) { return s.models.length > 1 ? { crumb: ['workspace', '/workspace'], railKey: 'all', toolName: 'eitri workspace' } : null; }"""


def _open(p, url, host=None, pr=None, viewport=None):
    b = p.chromium.launch()
    page = b.new_page(viewport=viewport or {"width": 1440, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if pr is not None:
        page.expose_function("pySwitch", lambda f: pr.set_model_file(f))
    if host:
        page.add_init_script(host)
    page.goto(url)
    return b, page, errors


def _wait_file(page, f, ready):
    page.wait_for_function(f"() => document.querySelector('#filetabs [aria-selected=true]')?.dataset.file === '{f}'", timeout=30000)
    page.wait_for_function(ready, timeout=60000)


CAD_READY = "() => window.__eitri_debug().faces > 0 && !document.body.classList.contains('domain')"


def test_tabs_switch_between_the_files_when_the_host_can(tmp_path):
    pr = _three(tmp_path / "rig")
    url = start_viewer(pr)
    with pw.sync_playwright() as p:
        b, page, errors = _open(p, url, HOST % "", pr)
        page.wait_for_selector("#filetabs [role=tab]")
        page.wait_for_function(CAD_READY, timeout=60000)
        tabs = page.eval_on_selector_all("#filetabs [role=tab]", "els => els.map(t => [t.dataset.file, t.dataset.tool, t.querySelector('.nm').textContent, t.getAttribute('aria-selected'), t.tabIndex])")
        assert tabs == [["model.py", "cad", "CAD", "true", 0], ["wiring.py", "wires", "Wires", "false", -1], ["fw.py", "firmware", "Firmware", "false", -1]]
        assert page.get_attribute("#filetabs [data-file='wiring.py'] .v", "class") == "v warn"     # its build: 6 pass, 1 warns
        assert "1 warn" in page.get_attribute("#filetabs [data-file='wiring.py']", "title")
        assert page.inner_text("#checks").count("plate is wide enough") == 1
        page.click("#exportbtn")
        assert page.locator("#exportitems a[data-fmt='step']").count() == 1
        page.keyboard.press("Escape")
        # without place(): the shell follows the current file's kind, as before
        assert "cad" in page.inner_text("eitri-topbar .crumbs") and page.inner_text("#st-tool") == "eitri CAD"

        # Wires: the host switches, the page shows the harness's own diagram, checks and exports; nothing of the CAD view stays
        page.click("#filetabs [data-file='wiring.py']")
        _wait_file(page, "wiring.py", "() => document.body.classList.contains('harness') && !!document.querySelector('#hedit .hpaper svg g[data-id]')")
        assert page.evaluate("() => window.__switched") == ["wiring.py"] and pr.model_path.name == "wiring.py"
        assert page.evaluate("() => window.__eitri_debug().faces") == 0
        page.wait_for_function("() => document.querySelector('#checks').textContent.includes('no duplicate wires')", timeout=20000)
        assert "plate is wide enough" not in page.inner_text("#checks")
        page.wait_for_function("() => !!document.querySelector('#exportitems a[data-fmt=svg]') && !document.querySelector('#exportitems a[data-fmt=step]')", timeout=20000)
        assert page.inner_text("#st-tool") == "eitri Wires" and page.get_attribute("body", "data-tool") == "wires"

        # the keyboard: arrows move between the tabs, Enter opens one
        page.focus("#filetabs [data-file='wiring.py']")
        page.keyboard.press("ArrowRight")
        assert page.evaluate("() => document.activeElement.dataset.file") == "fw.py"
        page.keyboard.press("Enter")
        _wait_file(page, "fw.py", "() => document.body.classList.contains('firmware') && !!document.querySelector('#hedit .hpaper svg g[data-id]')")
        # one editor at a time: the harness editor, its docked inspector and its keys are gone
        assert not page.evaluate("() => document.body.classList.contains('harness')")
        assert page.locator("#hedit").count() == 1 and page.locator("#domaininsp .hinspector").count() == 1
        page.wait_for_function("() => document.querySelector('#checks').textContent.includes('target board')", timeout=20000)

        # back to CAD: the model is loaded again, the domain editor is gone
        page.click("#filetabs [data-file='model.py']")
        _wait_file(page, "model.py", CAD_READY)
        assert page.locator("#hedit").count() == 0 and page.locator("#domaininsp .hinspector").count() == 0
        page.wait_for_function("() => document.querySelector('#checks').textContent.includes('plate is wide enough')", timeout=20000)

        # Add: one entry per kind the project has no file of; it calls the host with that kind
        page.click("#fileadd")
        items = page.eval_on_selector_all(".filemenu [role=menuitem]", "els => els.map(e => e.dataset.kind)")
        assert items == ["mechanism", "pneumatics"]
        page.click(".filemenu [role=menuitem][data-kind='pneumatics']")
        assert page.evaluate("() => window.__added") == "pneumatics"
        assert not errors, errors
        b.close()


def test_place_and_a_file_switched_from_outside(tmp_path):
    pr = _three(tmp_path / "rig")
    url = start_viewer(pr)
    with pw.sync_playwright() as p:
        b, page, errors = _open(p, url, HOST % PLACE, pr, viewport={"width": 390, "height": 844})
        page.wait_for_function(CAD_READY, timeout=60000)
        # place(): the host's crumb, rail item (a bottom one) and status name instead of the file's tool
        assert page.get_attribute("eitri-topbar .crumbs a[href='/workspace']", "href") == "/workspace"
        assert page.eval_on_selector_all("eitri-rail a[aria-current]", "els => els.map(a => a.dataset.key)") == ["all"]
        assert page.inner_text("#st-tool") == "eitri workspace"
        assert page.get_attribute("body", "data-tool") == "cad"                 # the editor keeps the file's own accent
        # the agent (or the host) makes another file current: the page follows without a click
        pr.set_model_file("wiring.py")
        _wait_file(page, "wiring.py", "() => document.body.classList.contains('harness') && !!document.querySelector('#hedit .hpaper svg g[data-id]')")
        assert page.eval_on_selector_all("eitri-rail a[aria-current]", "els => els.map(a => a.dataset.key)") == ["all"]
        assert page.inner_text("#st-tool") == "eitri workspace" and page.evaluate("() => window.__switched || []") == []
        # on a phone the strip sits above the viewport and fits the width (it scrolls inside itself, the page never does)
        bar, vp = page.locator("#filebar").bounding_box(), page.locator("#canvas").bounding_box()
        assert bar["y"] + bar["height"] <= vp["y"] + 1 and page.evaluate("() => document.documentElement.scrollWidth") <= 390
        assert not errors, errors
        b.close()


def test_no_tabs_without_a_host_switch(tmp_path):
    single = Project(tmp_path / "lead", name="lead")
    single.write_model(WIRES)
    assert single.build(note="first", render=False).ok
    multi = _three(tmp_path / "rig")
    with pw.sync_playwright() as p:
        for pr, host in ((single, None), (multi, None), (multi, "window.EITRI_SHELL = { host: true };"),
                         (single, HOST % ""),
                         (multi, HOST % ", models: function (s) { return null; }")):
            b, page, errors = _open(p, start_viewer(pr), host, pr if host and "pySwitch" in host else None)
            page.wait_for_function("() => window.__eitri_debug && (window.__eitri_debug().faces > 0 || !!document.querySelector('#hedit .hpaper svg g[data-id]'))", timeout=60000)
            page.wait_for_timeout(300)
            visible = page.is_visible("#filebar")
            if pr is single and host:
                # a single-file project under a switching host: one tab, and Add for the other kinds
                assert visible and page.locator("#filetabs [role=tab]").count() == 1
            else:
                assert not visible and page.locator("#filetabs [role=tab]").count() == 0, (pr.folder.name, host)
            # the shell is the file's tool's, as before (no place())
            if pr is single:
                assert "wires" in page.inner_text("eitri-topbar .crumbs") and page.inner_text("#st-tool") == "eitri Wires"
            assert not errors, errors
            b.close()


class _SharePolicy:
    """A read-only share page, as eitri-hosted serves one (GET only)."""
    def host_ok(self, handler, post):
        return not post

    def blocked(self, method, path):
        return None if method == "GET" else "This is a read-only shared view."

    def state(self, st):
        st = dict(st)
        st["hosted"] = {"mode": "share", "public_id": "abc", "owner": "maker", "title": "Rig", "domain": "multi"}
        st.update(has_agent=False, home="/", needs_key=False, demo=None)
        return st


def test_the_share_card_follows_the_file(tmp_path):
    """The read-only share card lists the current file's downloads: after a switch to the harness it never offers STEP/STL."""
    import threading
    from http.server import ThreadingHTTPServer
    from eitri.viewer.server import make_handler
    pr = _three(tmp_path / "rig")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(pr, threading.Lock(), policy=_SharePolicy()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    fmts = "() => [...document.querySelectorAll('#sharedl a')].map(a => a.dataset.fmt)"
    try:
        with pw.sync_playwright() as p:
            b, page, errors = _open(p, url, HOST % "", pr)
            page.wait_for_function(CAD_READY, timeout=60000)
            page.wait_for_selector("#sharedl a[data-fmt='step']", timeout=30000)
            assert {"step", "stl"} <= set(page.evaluate(fmts)) and "Orbit it" in page.inner_text("#sharebar .d")
            page.click("#filetabs [data-file='wiring.py']")
            _wait_file(page, "wiring.py", "() => document.body.classList.contains('harness') && !!document.querySelector('#sharedl a[data-fmt=svg]')")
            shown = page.evaluate(fmts)
            assert "svg" in shown and not {"step", "stl", "3mf", "glb"} & set(shown), shown
            assert "diagram" in page.inner_text("#sharebar .d") and page.locator("#sharebar .t").count() == 1
            # each link downloads this file's build
            assert page.get_attribute("#sharedl a[data-fmt='svg']", "href") == "download?fmt=svg"
            page.click("#filetabs [data-file='fw.py']")
            _wait_file(page, "fw.py", "() => document.body.classList.contains('firmware') && !!document.querySelector('#sharedl a[data-fmt=header]')")
            assert not {"step", "stl", "bom"} & set(page.evaluate(fmts))
            page.click("#filetabs [data-file='model.py']")
            _wait_file(page, "model.py", "() => !!document.querySelector('#sharedl a[data-fmt=step]')")
            assert "header" not in page.evaluate(fmts)
            assert not errors, errors
            b.close()
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- a host's own view beside the file tabs, and the viewer for its scripts
VIEWS = """, views: function (s) { return [{ key: 'assembly', name: 'Assembly', short: 'Assembly', glyph: 'all', active: !!window.__viewOn, verdict: 'ok', said: '3 checks pass' }]; },
  switchView: function (key) { window.__viewOn = key === 'assembly'; (window.__views = window.__views || []).push(key); }"""


def test_a_host_view_beside_the_file_tabs_and_the_viewer_api(tmp_path):
    pr = _three(tmp_path / "rig")
    url = start_viewer(pr)
    with pw.sync_playwright() as p:
        b, page, errors = _open(p, url, HOST % VIEWS, pr)
        page.wait_for_selector("#filetabs [role=tab]")
        page.wait_for_function(CAD_READY, timeout=60000)
        tabs = page.eval_on_selector_all("#filetabs [role=tab]", "els => els.map(t => [t.dataset.file || 'view:' + t.dataset.view, t.dataset.tool, t.querySelector('.nm').textContent, t.getAttribute('aria-selected'), t.tabIndex])")
        assert tabs == [["model.py", "cad", "CAD", "true", 0], ["wiring.py", "wires", "Wires", "false", -1], ["fw.py", "firmware", "Firmware", "false", -1],
                        ["view:assembly", "all", "Assembly", "false", -1]]
        assert page.get_attribute("#filetabs [data-view='assembly'] .v", "class") == "v ok" and "3 checks pass" in page.get_attribute("#filetabs [data-view='assembly']", "title")
        # the viewer for a host's scripts: the scene, its own group in it, the loaded parts, framing, labels, and the state event
        assert page.evaluate("() => Object.keys(window.eitriViewer).sort()") == sorted(["scene", "renderer", "controls", "hostGroup", "camera", "model", "bbox", "radius", "centre", "parts", "meshes", "rev", "state", "busy", "interacting", "setView", "frame", "addLabel", "clearLabels", "labelLayer", "light", "hideLoading"])
        assert page.evaluate("() => window.eitriViewer.hostGroup.parent === window.eitriViewer.scene && Object.keys(window.eitriViewer.parts())") == ["model"]
        page.evaluate("""() => { window.__st = []; document.addEventListener('eitri:state', e => window.__st.push(e.detail.file)); window.__md = []; document.addEventListener('eitri:model', e => window.__md.push(e.detail));
                               const V = window.eitriViewer, b = V.bbox(); V.frame(b, 'hero', 3, false); window.__cam = V.camera().position.toArray(); V.addLabel(b.max, 'here', 'host'); }""")
        page.wait_for_function("() => window.__st.length > 0 && window.__st[0] === 'model.py'", timeout=10000)
        assert page.evaluate("() => window.__cam[0] > 0 && window.__cam[1] < 0 && window.__cam[2] > 0") and page.inner_text("#labels .lbl.host") == "here"
        # opening the view tells the host, selects no file tab, and marks the body; the host redraws the strip with 'eitri:views'
        page.click("#filetabs [data-view='assembly']")
        page.wait_for_function("() => document.body.dataset.view === 'assembly'")
        assert page.evaluate("() => window.__views") == ["assembly"]
        assert page.eval_on_selector_all("#filetabs [role=tab][aria-selected=true]", "els => els.map(t => t.dataset.view || t.dataset.file)") == ["assembly"]
        assert page.evaluate("() => window.__eitri_debug().faces") > 0                 # the page keeps the current file underneath
        page.evaluate("() => { window.__viewOn = false; document.dispatchEvent(new Event('eitri:views')); }")
        page.wait_for_function("() => !document.body.dataset.view")
        assert page.eval_on_selector_all("#filetabs [role=tab][aria-selected=true]", "els => els.map(t => t.dataset.file)") == ["model.py"]
        # a file tab while the view is up: the view is closed first, then the file switched; the model event says the geometry went
        page.click("#filetabs [data-view='assembly']")
        page.wait_for_function("() => document.body.dataset.view === 'assembly'")
        page.click("#filetabs [data-file='wiring.py']")
        _wait_file(page, "wiring.py", "() => document.body.classList.contains('harness') && !!document.querySelector('#hedit .hpaper svg g[data-id]')")
        assert page.evaluate("() => window.__views") == ["assembly", "assembly", None] and not page.evaluate("() => document.body.dataset.view")
        assert page.evaluate("() => window.__md.some(d => d.rev === null && d.parts.length === 0)")
        # the keyboard moves onto the view tab like any other
        page.focus("#filetabs [data-file='fw.py']")
        page.keyboard.press("ArrowRight")
        assert page.evaluate("() => document.activeElement.dataset.view") == "assembly"
        assert not errors, errors
        b.close()
