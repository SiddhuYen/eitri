"""Assemblies in the browser: a revolute joint made with the Joint tool (a face on each part), the joints panel
previewing and driving it with a slider, animation, the bill of materials and grouping."""
from __future__ import annotations

import json
import math

import pytest

from eitri.project import Project
from eitri.viewer.server import start_viewer

pw = pytest.importorskip("playwright.sync_api")

MODEL = ("from eitri import *\nPARAMS = dict(pin_d=8.0)\n\ndef build(p):\n"
         "    base = Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN)) + Pos(0, 0, 6) * Cylinder(p.pin_d / 2, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
         "    lever = (Pos(100, 0, 0) * Box(50, 10, 5, align=(Align.MIN, Align.CENTER, Align.MIN))) - Pos(100, 0, 0) * Cylinder(p.pin_d / 2, 20)\n"
         "    return {'base': base, 'lever': lever}\n\nMATERIALS = {'base': 'aluminum', 'lever': 'steel'}\n")


def _state(page):
    return json.loads(page.evaluate("() => fetch('/state.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _topo(page):
    return json.loads(page.evaluate("() => fetch('/topology.json').then(r => r.json()).then(s => JSON.stringify(s))"))


def _ready(page, n_edits, prev_rev):
    page.wait_for_function(
        f"() => window.__eitri_debug().rev > {prev_rev} && document.querySelectorAll('#edits .edit').length === {n_edits}"
        " && window.__eitri_debug().faces > 0 && !window.__eitri_debug().drag", timeout=90000)
    page.wait_for_timeout(300)
    return page.evaluate("() => window.__eitri_debug().rev")


def _part(st, name):
    return next(p for p in st["parts"] if p["name"] == name)


def test_joint_tool_drive_animate_bom_and_group(tmp_path):
    project = Project(tmp_path / "proj", name="lever")
    project.write_model(MODEL)
    assert project.build(note="first", render=False).ok
    url = start_viewer(project)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        answers = []
        page.on("dialog", lambda d: d.accept(answers.pop(0)) if answers else d.dismiss())
        page.goto(url)
        page.wait_for_function("() => window.__eitri_debug && window.__eitri_debug().faces > 0 && window.__eitri_debug().topo", timeout=60000)
        page.wait_for_timeout(400)
        rev = page.evaluate("() => window.__eitri_debug().rev")
        assert page.evaluate("() => document.getElementById('bomsec').style.display") == "block"            # two parts: a BOM
        assert "lever" in page.evaluate("() => document.getElementById('bom').textContent")

        # 1. the Joint tool: the lever's bore (chosen) and a click on the pin's round face, revolute with limits 0..90
        topo = _topo(page)
        bore = next(f for f in topo["faces"] if f["part"] == "lever" and f["type"] == "cylinder" and f.get("concave"))
        pin = next(f for f in topo["faces"] if f["part"] == "base" and f["type"] == "cylinder" and not f.get("concave"))
        page.evaluate(f"() => {{ window.__eitri_tool('joint'); window.__eitri_tooldata({{ moving: {json.dumps(bore['id'])} }}); }}")
        a = math.radians(-45)
        x, y = page.evaluate(f"() => window.__eitri_project([{4 * math.cos(a)}, {4 * math.sin(a)}, 16])")   # the pin's side facing the iso camera
        page.mouse.move(x, y)
        page.mouse.click(x, y)
        page.wait_for_function(f"() => (window.__eitri_tooldata() || {{}}).target === {json.dumps(pin['id'])}", timeout=10000)
        page.select_option("#selactions select[data-field=jtype]", "revolute")
        page.evaluate("() => window.__eitri_tooldata({ lmin: '0', lmax: '90', name: 'pivot', center: false })")
        assert page.evaluate("() => window.__eitri_apply()")
        rev = _ready(page, 1, rev)
        st = _state(page)
        e = st["edits_full"][0]
        assert e["op"] == "joint" and e["type"] == "revolute" and e["to_part"] == "base" and e["part"] == "lever" and e["limits"] == {"min": 0, "max": 90} and st["edit_notes"][0]["ok"]
        assert len(st["joints"]) == 1 and st["joints"][0]["name"] == "pivot" and st["joints"][0]["limits"] == {"angle": [0, 90]}
        page.wait_for_selector("#joints input[data-joint=pivot]")

        # 2. preview on screen only: the lever's node turns about the pin, nothing is written
        q = page.evaluate("() => window.__eitri_joints.preview('pivot', { angle: 90 })")
        assert abs(abs(q["quat"][2]) - math.sin(math.radians(45))) < 1e-6 and abs(abs(q["quat"][3]) - math.cos(math.radians(45))) < 1e-6
        page.evaluate("() => window.__eitri_joints.reset()")
        assert _state(page)["joints"][0]["value"]["angle"] == 0
        # 3. the slider writes a drive edit on release
        page.evaluate("() => { const s = document.querySelector('#joints input[data-joint=pivot]'); s.value = 45; s.dispatchEvent(new Event('input')); s.dispatchEvent(new Event('change')); }")
        rev = _ready(page, 2, rev)
        st = _state(page)
        assert st["edits_full"][1] == {"op": "drive_joint", "joint": "pivot", "value": 45} and st["joints"][0]["value"]["angle"] == 45
        size = _part(st, "lever")["size"]
        c45, s45 = math.cos(math.radians(45)), math.sin(math.radians(45))
        assert abs(size[0] - (50 * c45 + 5 * s45 + 5 * s45)) < 0.6 and abs(size[1] - size[0]) < 0.6                   # turned 45 degrees
        # 4. animate, then stop
        page.evaluate("() => window.__eitri_joints.animate('pivot')")
        page.wait_for_timeout(400)
        assert page.evaluate("() => window.__eitri_joints.animating()") == "pivot"
        q1 = page.evaluate("() => window.__eitri_joints.nodePos('lever')")
        page.evaluate("() => window.__eitri_joints.stop()")
        assert page.evaluate("() => window.__eitri_joints.animating()") is None
        # 5. group the two parts from the part panel
        page.evaluate("() => { window.__eitri_tool('select'); window.__eitri_select(['lever']); }")
        page.wait_for_selector("text=Group with…")
        answers[:] = ["arm, base"]
        page.click("text=Group with…")
        rev = _ready(page, 3, rev)
        st = _state(page)
        assert st["edits_full"][2] == {"op": "group", "name": "arm", "parts": ["lever", "base"]} and st["groups"] == {"base": "arm", "lever": "arm"}
        assert "arm ›" in page.evaluate("() => document.getElementById('parts').textContent")
        browser.close()
