"""The local combined workspace (eitri_suite.workspace) and its MCP server (`eitri mcp --combined`): the same tool names and
contract as eitri.io's former remote combined tools, projects as folders, builds on this computer with the cross-domain checks on,
and the export rule (nothing leaves while a check fails, did not run or is out of date)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from eitri_suite import workspace as W

ROOT = Path(__file__).resolve().parent.parent
UNO = ROOT / "eitri_suite" / "demos" / "uno-arm"
# the tool contract the plugin's skills address (the names and arguments of eitri.io's former remote server, kept)
REMOTE_TOOLS = {
    "combined_reference": {"topic"}, "combined_new_project": {"name", "goal"}, "combined_list_projects": set(),
    "combined_read_files": {"project_id", "paths"}, "combined_put_files": {"project_id", "files", "delete"},
    "combined_build": {"project_id"}, "combined_results": {"project_id"}, "combined_export": {"project_id", "formats"},
}

BOX = '''from eitri import *

PARAMS = dict(w=40)

def build(p):
    return Box(p.w, 30, 5)

def verify(parts, p, c):
    c.true("plate is wide enough", p.w >= 30)
'''
QUAD = (ROOT / "eitri" / "demos" / "quad-power" / "quad_power.py").read_text(encoding="utf-8")   # declares no power source


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("EITRI_WORKER", "0")            # each build in a fresh process (the suite reads its folder per build anyway)
    monkeypatch.setenv("EITRI_ARDUINO_CLI", str(tmp_path / "no-arduino-cli"))    # the compile check fails the same everywhere
    monkeypatch.delenv("EITRI_SUITE", raising=False)
    return W.Workspace(tmp_path / "projects")


def _build(ws, pid):
    r = ws.build(pid, image=False, wait_s=900)
    assert r.get("complete"), r
    return r


def test_new_project_is_a_marked_folder(ws):
    out = ws.new_project("Desk arm", "a desk robot arm with three servos")
    folder = Path(out["folder"])
    assert out["project_id"] == "desk-arm" and folder.parent == ws.root.resolve()
    assert json.loads((folder / "eitri.json").read_text(encoding="utf-8"))["workspace"] == "combined"
    assert (folder / "model.py").read_text(encoding="utf-8") == W.BLANKS["cad"] and out["files"] == ["model.py"]
    assert (folder / ".eitri" / "goal.txt").read_text(encoding="utf-8").startswith("a desk robot arm")
    assert ws.new_project("Desk arm")["project_id"] == "desk-arm-2"
    assert [p["project_id"] for p in ws.list_projects()] == ["desk-arm-2", "desk-arm"] or \
        {p["project_id"] for p in ws.list_projects()} == {"desk-arm", "desk-arm-2"}
    from eitri_suite import enabled
    assert enabled(folder) and not enabled(folder.parent)
    # a single-domain project is not a combined one
    (ws.root / "bracket").mkdir()
    (ws.root / "bracket" / "eitri.json").write_text('{"model": "bracket.py"}', encoding="utf-8")
    with pytest.raises(W.ToolError, match="single-domain"):
        ws.read_files("bracket")
    with pytest.raises(W.ToolError, match="No combined project"):
        ws.read_files("nope")
    with pytest.raises(W.ToolError):
        ws.read_files("../desk-arm")


def test_put_files_checks_what_it_writes(ws):
    pid = ws.new_project("p")["project_id"]
    with pytest.raises(W.ToolError, match="Nothing was written") as e:
        ws.put_files(pid, {"Model.py": BOX, "notes.txt": "x", "mech.py": "print(1)"})
    assert "Model.py" in str(e.value) and "mech.py: a model file defines build(p)" in str(e.value)
    with pytest.raises(W.ToolError, match="at most 8"):
        ws.put_files(pid, {f"m{i}.py": BOX for i in range(9)})
    with pytest.raises(W.ToolError, match="no such file"):
        ws.put_files(pid, {}, delete=["ghost.py"])
    out = ws.put_files(pid, {"model.py": BOX, "wiring.py": QUAD, "DESIGN.md": "# plan\n"})
    assert out["files"] == ["model.py", "wiring.py"] and {w["path"]: w["kind"] for w in out["written"]} == \
        {"model.py": "cad", "wiring.py": "harness", "DESIGN.md": None}
    files = ws.read_files(pid)["files"]
    assert [f["path"] for f in files] == ["model.py", "wiring.py", "DESIGN.md"] and files[0]["blank"] is False


def test_build_results_and_the_export_rule(ws):
    """A CAD part and a harness that declares no power source: everything builds, the harness's overcurrent check is "not
    run", so no file and no package leaves. Unchanged files are not rebuilt; an edit makes the stored results stale."""
    pid = ws.new_project("Bench supply lead", "a wire harness for a bench supply")["project_id"]
    ws.put_files(pid, {"model.py": BOX, "wiring.py": QUAD})
    r = _build(ws, pid)
    assert [f["file"] for f in r["files"]] == ["model.py", "wiring.py"] and all(f["built"] for f in r["files"])
    harness = next(f for f in r["files"] if f["file"] == "wiring.py")
    not_run = [c for c in harness["checks"] if c["status"] == "not_run"]
    assert not_run, harness["checks"]
    text = W.results_text(r)
    assert "NOT RUN" in text and "Cross-domain checks:" in text and "Next steps:" in text
    # the suite ran on both builds (the folder is a combined project): its data is in the stored result
    from eitri.project import Project
    project = Project(Path(r["folder"]))
    data = project.build_result(project.last_ok_revision(model="model.py"))
    assert "suite" in (data.get("extensions") or {}) and "siblings" in data["extensions"]["suite"]

    exp = ws.export(pid, ["step", "package"])
    assert exp["files"] == [] and "refused" in exp["problems"][0] and "not run" in exp["problems"][0]
    assert any(b["status"] == "not_run" and b["file"] == "wiring.py" for b in exp["blocking"])

    again = ws.build(pid, image=False, wait_s=60)
    assert again.get("unchanged") and again["build"] == r["build"]
    ws.put_files(pid, {"model.py": BOX.replace("w=40", "w=45")})
    assert ws.results(pid).get("stale")
    rows = W.blocking(project)
    assert any(b["name"] == "model.py changed since its last build" for b in rows)


def test_a_passing_project_exports_and_a_failing_one_does_not(ws):
    pid = ws.new_project("Plate", "a mounting plate")["project_id"]
    ws.put_files(pid, {"model.py": BOX})
    r = _build(ws, pid)
    assert r["all_pass"], W.results_text(r, "all")
    exp = ws.export(pid, ["step", "bogus"])
    assert [x["format"] for x in exp["files"]] == ["step"] and Path(exp["files"][0]["path"]).is_file()
    assert Path(exp["files"][0]["path"]).parent == Path(r["folder"]) / "export"
    assert exp["problems"] and exp["problems"][0].startswith("bogus: not offered")
    # a failing verify() check: refused, naming it
    ws.put_files(pid, {"model.py": BOX.replace("w=40", "w=20")})
    r = _build(ws, pid)
    assert not r["all_pass"] and any('"plate is wide enough" fails' in s for s in r["next_steps"])
    exp = ws.export(pid, ["step"])
    assert exp["files"] == [] and "1 failing: model.py: plate is wide enough" in exp["problems"][0]


def test_the_uno_arm_demo_end_to_end(ws):
    """The reference project: five files, the cross-domain checks between them pass; the sketch's compile check fails
    without arduino-cli, and that alone refuses the package."""
    pid = ws.new_project("Uno arm", "a desk robot arm with an Arduino Uno, servos, wiring and its circuit")["project_id"]
    ws.put_files(pid, {f.name: f.read_text(encoding="utf-8") for f in UNO.glob("*.py")})
    r = _build(ws, pid)
    assert [f["file"] for f in r["files"]] == ["model.py", "mech.py", "wiring.py", "fw.py", "elec.py"]
    assert r["cross_domain"]["checks"] >= 40 and r["cross_domain"]["failing"] == 0, W.results_text(r)
    failing = [(f["file"], c["name"]) for f in r["files"] for c in f["checks"] if not c["ok"]]
    assert failing == [("fw.py", "compiles (arduino-cli)")]
    exp = ws.export(pid, ["package"])
    assert exp["files"] == [] and exp["blocking"] == [{"file": "fw.py", "kind": "firmware", "name": "compiles (arduino-cli)",
                                                       "status": "fail", "measured": exp["blocking"][0]["measured"]}]


def test_reference_topics(ws):
    ov = ws.reference()
    assert "| electronics | elec.py" in ov and "on this computer" in ov and "eitri.io" not in ov
    integ = ws.reference("integration")
    assert "use_model" not in integ and "combined_put_files" in integ and "parts_catalog" not in integ
    assert "MG996R" in ws.reference("catalog servo")
    assert "Mechanism" in ws.reference("mechanism")
    with pytest.raises(W.ToolError, match="Topics"):
        ws.reference("nope")


# ------------------------------------------------------------------------------------------------ the MCP server
def _call(server, name, args=None):
    from mcp.server.mcpserver.exceptions import ToolError
    try:
        res = asyncio.run(server.call_tool(name, args or {}))
    except ToolError as exc:
        return str(exc), True
    return "\n".join(getattr(c, "text", "") for c in res.content), bool(res.is_error)


def test_server_has_the_remote_contract(tmp_path, monkeypatch):
    """The same tool names and arguments as eitri.io's former remote server (what the plugin's skills address)."""
    monkeypatch.setenv("EITRI_NO_WARM", "1")
    from eitri.agent.combined_server import CombinedSession, make_server
    server = make_server(CombinedSession(root=tmp_path / "projects", open_browser=False))
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(tools) == set(REMOTE_TOOLS)
    for name, props in REMOTE_TOOLS.items():
        assert set(props) <= set(tools[name].input_schema.get("properties") or {}), name
    text, err = _call(server, "combined_list_projects")
    assert not err and "No combined projects yet" in text
    text, err = _call(server, "combined_new_project", {"name": "Lead", "goal": "a lead"})
    assert not err and "Made project lead" in text and "this computer" in text
    text, err = _call(server, "combined_put_files", {"project_id": "lead", "files": {"Bad.py": "x"}})
    assert err and "Nothing was written" in text
    text, err = _call(server, "combined_results", {"project_id": "lead"})
    assert not err and "Nothing has been built" in text
    text, err = _call(server, "combined_export", {"project_id": "nope", "formats": ["step"]})
    assert err and "No combined project" in text


def test_eitri_mcp_combined_speaks_stdio_fast(tmp_path):
    """`eitri mcp --combined` as the plugin's launcher starts it: it answers initialize without loading the CAD kernel."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env.update(EITRI_NO_WARM="1", EITRI_NO_BROWSER="1", EITRI_WORKSPACE=str(tmp_path), PYTHONPATH=str(ROOT), EITRI_LAZY_IMPORT="1")

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "eitri", "mcp", "--combined"], env=env, cwd=str(tmp_path))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                res = await s.call_tool("combined_list_projects", {})
                return init, tools, res
    init, tools, res = asyncio.run(go())
    assert tools == set(REMOTE_TOOLS) and "on this computer" in (init.instructions or "")
    assert "No combined projects yet" in res.content[0].text


def test_viewer_shows_a_tab_per_file(tmp_path, monkeypatch):
    """A combined project's viewer fills the editor's switchModel slot (a tab per model file) and switches files on POST
    /model; a single-domain project's viewer does not."""
    import urllib.request
    from eitri.project import Project
    from eitri.viewer.server import start_viewer
    ws = W.Workspace(tmp_path / "projects")
    folder = Path(ws.new_project("Two files")["folder"])
    ws.put_files("two-files", {"model.py": BOX, "wiring.py": QUAD})
    busy = {"why": None}
    url = start_viewer(Project(folder), combined=True, busy=lambda: busy["why"])
    page = urllib.request.urlopen(url).read().decode("utf-8")
    assert "window.EITRI_SHELL" in page and "switchModel" in page

    def post(body):
        req = urllib.request.Request(url + "model", data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    status, out = post({"file": "wiring.py"})
    assert status == 200 and out["ok"] and out["state"]["file"] == "wiring.py"
    assert json.loads((folder / "eitri.json").read_text(encoding="utf-8"))["model"] == "wiring.py"
    assert post({"file": "ghost.py"})[0] == 404
    busy["why"] = "build 1 is running"
    assert post({"file": "model.py"}) == (409, {"ok": False, "error": "build 1 is running"})
    plain = start_viewer(Project(tmp_path / "single"))
    assert "window.EITRI_SHELL = Object.assign" not in urllib.request.urlopen(plain).read().decode("utf-8")
