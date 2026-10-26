"""The app shell (eitri/web/assets/design, the core's slots in assets/shell-host.js) on the core's pages: the top bar crumbs, the rail of
the five tools (and nothing gated), the status bar, the OSS defaults of the three host slots, a host's config replacing them, and the
editor's docked panels (chat collapses, the dock folds) and Export menu."""
from __future__ import annotations

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer
from eitri.web.app import Workspace, start_web

pw = pytest.importorskip("playwright.sync_api")

HARNESS = '''from eitri.harness import *

PARAMS = dict(n=1)

def build(p):
    h = Harness("Lead")
    a = h.add(Connector("A", "XT30", pins=["+", "-"]))
    b = h.add(Connector("B", "XT30", pins=["+", "-"]))
    h.connect(a["+"], b["+"], Wire(16, "RD", 200))
    h.connect(a["-"], b["-"], Wire(16, "BK", 200))
    return h
'''
HOST = """window.EITRI_SHELL = { host: true,
  railBottom: [{ key: 'app', href: '/app', name: 'Projects', short: 'Projects', glyph: 'projects' }],
  topbarEnd: function (end) { var s = document.createElement('span'); s.id = 'meter'; s.className = 'pill'; s.textContent = '$0.24 of $4.00 today'; end.appendChild(s); },
  status: function (footer, info) { footer.querySelector('#st-where').hidden = true; var s = document.createElement('span'); s.id = 'plan'; s.textContent = 'free · ' + info.page; footer.appendChild(s); } };"""


@pytest.fixture()
def editor(tmp_path):
    pr = Project(tmp_path / "lead", name="lead")
    pr.write_model(HARNESS)
    assert pr.build(note="first", render=False).ok
    return pr, start_viewer(pr)


def _rail(page):
    return page.eval_on_selector_all("eitri-rail a", "els => els.map(a => [a.dataset.tool, a.getAttribute('aria-current') || '', a.classList.contains('neutral')])")


def test_editor_in_the_shell_with_oss_defaults(editor):
    pr, url = editor
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_selector("#hedit .hpaper svg g[data-id]", state="attached", timeout=60000)
        page.wait_for_timeout(600)
        assert page.evaluate("() => document.body.dataset.tool") == "wires"
        crumbs = page.inner_text("eitri-topbar .crumbs")
        title = page.inner_text("#title")
        assert "wires" in crumbs and page.inner_text("eitri-topbar .crumbs .here") == title == "lead"
        # the rail: the five tools, the current one marked, then the local project list; no combined-workspace item
        rail = _rail(page)
        assert [r[0] for r in rail] == ["cad", "wires", "motion", "pneumatics", "firmware", "projects"]
        assert [r[0] for r in rail if r[1] == "page"] == ["wires"] and rail[-1][2]
        assert page.locator("eitri-rail").inner_text().lower().count("combined") == 0
        # OSS slot defaults: a "local" badge; the status bar: tool, core version, local, folder, build state, one eitri.io link
        assert page.inner_text("eitri-topbar .end .local-badge").lower() == "local"
        from eitri import __version__
        assert page.inner_text("#st-tool") == "eitri Wires" and page.inner_text("#st-version") == f"core v{__version__}"
        assert page.is_visible("#st-where") and page.get_attribute("#st-path", "title") == str(pr.folder)
        page.wait_for_function("() => document.querySelector('#st-buildtxt').textContent.includes('checks pass')", timeout=20000)
        assert page.get_attribute("#st-combined", "href") == "https://eitri.io" and page.locator("footer.status a").count() == 1
        # the inspector is docked: the domain's tables live in the dock, not over the diagram
        assert page.locator("#dock #domaininsp .hinspector").count() == 1 and page.locator("#hedit .hinspector").count() == 0
        # the viewport stays the largest thing on the page
        vp = page.locator("#canvas").bounding_box(); dock = page.locator("#dock").bounding_box()
        assert vp["width"] > 2.4 * dock["width"] and vp["height"] > 800
        # Export: the domain's own files
        page.click("#exportbtn")
        assert page.is_visible("#exportitems a[data-fmt='svg']") and page.locator("#exportitems a[data-fmt='step']").count() == 0
        page.keyboard.press("Escape")
        # the chat collapses to its header and comes back; the dock folds to a strip and the viewport takes the room
        page.click("#chathead h2")
        assert page.evaluate("() => document.getElementById('chatpanel').classList.contains('closed')") and not page.is_visible("#composer")
        page.click("#chathead h2")
        assert page.is_visible("#composer")
        page.click("#dockfold")
        page.wait_for_timeout(300)
        assert page.locator("#canvas").bounding_box()["width"] > vp["width"] + 300 and page.is_visible("#dockunfold")
        page.click("#dockunfold")
        assert page.is_visible("#side")
        assert not errors, errors
        b.close()


def test_a_host_fills_the_slots(editor):
    pr, url = editor
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script(HOST)                                  # what a host's script at the <!-- eitri:host-head --> marker does
        page.goto(url)
        page.wait_for_selector("#hedit .hpaper svg g[data-id]", state="attached", timeout=60000)
        assert page.inner_text("eitri-topbar .end #meter") == "$0.24 of $4.00 today" and page.locator(".local-badge").count() == 0
        assert [r[0] for r in _rail(page)][-1] == "app" and page.get_attribute("eitri-rail a[data-tool='app']", "href") == "/app"
        assert page.inner_text("#plan") == "free · editor" and not page.is_visible("#st-where")
        b.close()


def test_home_is_the_local_project_list_in_the_shell(tmp_path):
    ws = Workspace(tmp_path / "ws", lambda project, on_event: None, agent={"available": True, "label": "Claude · API key"})
    for name, code in (("lead", HARNESS), ("cube", "from eitri import *\n\nPARAMS = {}\n\ndef build(p):\n    return Box(10, 10, 10)\n")):
        pr = Project(ws.root / name, name=name)
        pr.write_model(code)
    url, server = start_web(ws, port=0)
    try:
        with pw.sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.wait_for_selector("#grid .card[data-id='lead']")
            assert [r[0] for r in _rail(page)] == ["cad", "wires", "motion", "pneumatics", "firmware", "projects"]
            assert _rail(page)[-1][1] == "page"                     # the project list is the rail-bottom item, and it is this page
            assert page.inner_text("eitri-topbar .end .local-badge").lower() == "local"
            assert page.get_attribute("#grid .card[data-id='lead']", "data-tool") == "wires" and page.get_attribute("#grid .card[data-id='cube']", "data-tool") == "cad"
            # a tool's list: the rail's link, filtered, the crumbs say which
            page.click("eitri-rail a[data-tool='wires']")
            page.wait_for_url("**/?tool=wires")
            page.wait_for_selector("#grid .card[data-id='lead']")
            assert page.locator("#grid .card").count() == 1 and page.inner_text("#pagetitle") == "eitri Wires"
            assert "wires" in page.inner_text("eitri-topbar .crumbs") and [r[0] for r in _rail(page) if r[1] == "page"] == ["wires"]
            # a project's name is text, never markup
            page.evaluate("""() => fetch('/api/rename', { method: 'POST', body: JSON.stringify({ id: 'lead', title: '<img src=x onerror=window.__pwned=1>' }) })""")
            page.goto(url)
            page.wait_for_selector("#grid .card[data-id='lead']")
            page.wait_for_timeout(300)
            assert page.inner_text("#grid .card[data-id='lead'] .name") == "<img src=x onerror=window.__pwned=1>"
            assert page.evaluate("() => !window.__pwned") and page.locator("#grid img[src='x']").count() == 0
            # an unknown address is a page in the shell for a browser, plain text for anything else
            page.goto(url + "p/no-such-project/")
            assert "No such project" in page.inner_text("h1") and page.locator("eitri-topbar").count() == 1
            assert not errors, errors
            b.close()
        import urllib.request, urllib.error
        try:
            urllib.request.urlopen(url + "nope")
            raise AssertionError("expected a 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404 and e.read() == b"not found"
    finally:
        server.shutdown()
