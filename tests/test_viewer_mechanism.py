"""The mechanism editor in a real browser: the kinematic diagram replaces the 3D view, joints and targets are edited in the
inspector, a failing check highlights its target, and every change lands in the model file's EDITS."""
from __future__ import annotations

import json
import time

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = '''from eitri.mechanism import *

PARAMS = dict(arm_mm=100.0, payload_g=100.0)


def build(p):
    m = Mechanism("Lever")
    m.link("base", fixed=True)
    m.link("arm", length_mm=p.arm_mm, mass_g=12.4)
    m.joint("j1", "base", "arm", axis=(0, -1, 0), at=(0, 0, 0), limit_deg=(-90, 90), actuator=Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=400, travel_deg=180))
    m.payload(mass_g=p.payload_g)
    m.target("up", (0, 0, p.arm_mm))
    m.target("far", (250, 0, 0))
    m.poses = 4
    return m
'''


def _state(page):
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _until(page, predicate: str):
    # poll with evaluate (it awaits the fetch); wait_for_function would take the returned Promise itself as truthy
    deadline = time.time() + 60
    while not page.evaluate(f"() => fetch('/state.json').then(r => r.json()).then(s => !!({predicate})(s))"):
        assert time.time() < deadline, f"the state never satisfied {predicate}"
        page.wait_for_timeout(150)
    page.wait_for_timeout(900)
    return _state(page)


def _start(tmp_path):
    project = Project(tmp_path / "proj", name="lever")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    return project, start_viewer(project)


def _open(p, url):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1500, "height": 950})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector("#hedit .hpaper svg g[data-kind='joint']", state="attached", timeout=60000)
    page.wait_for_timeout(500)
    return browser, page, errors


def _joint_point(page, name):
    return page.evaluate("""(name) => { const c = document.querySelector(`g[data-kind="joint"][data-id="${name}"] circle`); const r = c.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }""", name)


def test_the_mechanism_editor_replaces_the_3d_view(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        assert page.evaluate("() => document.body.classList.contains('domain') && document.body.classList.contains('mechanism')")
        assert page.evaluate("() => getComputedStyle(document.getElementById('tools')).display") == "none"
        assert page.locator("#chatinput").is_visible() and page.locator("#params").is_visible()                 # chat and the parameter panel stay
        st = _state(page)
        assert st["kind"] == "mechanism" and st["domain"]["joints"][0]["name"] == "j1" and st["params"]["arm_mm"] == 100.0
        assert page.locator("#checks .check").count() == len(st["checks"]) and page.locator("#checks .check.bad").count() == 1        # the far target
        assert "Joints" in page.inner_text("#domaininsp .hinspector")                                                 # the summary panel
        assert page.locator("#sheet").get_attribute("src") is None and not page.is_visible("#sheet")   # built without the review image: none is asked for (it would 404)
        assert not errors, errors
        browser.close()


def test_edit_a_joint_and_a_target_in_the_inspector(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        x, y = _joint_point(page, "j1")
        page.mouse.click(x, y)
        page.wait_for_selector("#domaininsp .hinspector .hh b:text('Joint j1')")
        assert "MG996R" in page.inner_text("#domaininsp .hinspector")
        hi = page.locator("#domaininsp .hinspector .hrow input").nth(1)
        hi.fill("45")
        hi.press("Enter")
        _until(page, "s => s.domain.joints[0].limit[1] === 45")
        assert project.edits() == [{"op": "set_joint", "joint": "j1", "limit": [-90, 45], "by": project.edits()[0].get("by")}] or project.edits()[0]["limit"] == [-90, 45]
        assert "45 deg" in page.evaluate("() => [...document.querySelectorAll('g[data-kind=\"joint\"][data-id=\"j1\"]')].map(g => g.textContent).join(' ')")
        assert page.inner_text("#domaininsp .hinspector .hh b") == "Joint j1"                                           # the selection survived the rebuild
        # the stall torque, in the same panel: merged into the joint's actuator edit
        stall = page.locator("#domaininsp .hinspector label.hf:has(span:text('stall torque')) input")
        stall.fill("12")
        stall.press("Enter")
        _until(page, "s => s.domain.joints[0].actuator.stall_kgcm === 12")
        ops = [e["op"] for e in project.edits()]
        assert ops == ["set_joint", "set_actuator"]
        # a target: click its cross, change its tolerance, remove it
        page.evaluate("() => window.__domain.select({kind: 'target', id: 'far'})")
        page.wait_for_selector("#domaininsp .hinspector .hh b:text('Target far')")
        page.get_by_role("button", name="Remove target").click()
        _until(page, "s => s.domain.targets.length === 1")
        bad = page.locator("#checks .check.bad")                                                            # "up" needs j1 = 90, now outside the 45 limit
        assert bad.count() == 1 and "target up" in bad.first.locator(".nm").inner_text()
        from eitri.agent.tools import Tools
        assert "joint j1: limit = [-90, 45]" in Tools(project).list_edits().text
        assert not errors, errors
        browser.close()


def test_a_failing_check_highlights_its_target(tmp_path):
    project, url = _start(tmp_path)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, url)
        bad = page.locator("#checks .check.bad.pickable").first
        assert "target far" in bad.locator(".nm").inner_text()
        bad.click()
        page.wait_for_selector("#hedit .hpaper g.hl", state="attached")
        assert set(page.eval_on_selector_all("#hedit .hpaper g.hl", "els => els.map(e => e.dataset.id)")) == {"far"}
        bad.click()
        page.wait_for_timeout(200)
        assert page.locator("#hedit .hpaper g.hl").count() == 0
        assert not errors, errors
        browser.close()
