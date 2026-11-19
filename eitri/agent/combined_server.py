"""`eitri mcp --combined`: the local combined workspace over MCP (stdio), the plugin's `eitri-combined` server.

The tools are eitri_suite.workspace's, with the same names, arguments and answers as the (now closed) eitri.io remote combined tools, so the
plugin's skills work with either. Everything runs on this computer: projects are folders in ~/eitri-projects, builds are core's
with the cross-domain checks on, no account and no token. The person watches each project in eitri's viewer (a tab per model
file), which opens in the browser at the project's first build.

Nothing heavy is imported at start (the agent gives a server about 30 s to answer): the CAD kernel loads in the build worker
when a project is first made or built.
"""
from __future__ import annotations

import os
import threading
import webbrowser
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent

from .. import __version__

IMAGE_MODE = os.environ.get("EITRI_IMAGE_MODE", "path")   # "path" or "inline"

INSTRUCTIONS = ("eitri's combined workspace, on this computer: one machine as several model files (CAD, mechanism, wire harness, "
                "firmware, pneumatics, electronics) that eitri builds here and checks, each domain on its own and against each other. "
                "Start with combined_reference(), make a project with combined_new_project, write files with combined_put_files, then call "
                "combined_build and follow its next steps until every check passes. Tell the person, in one short visible message before "
                "the first combined_put_files, your plan and the viewer link. Everything stays on this computer.")

PID = "The project's id (combined_new_project or combined_list_projects gives it): its folder's name in ~/eitri-projects."


class CombinedSession:
    """The workspace, its viewers (one per project, kept) and the browser, for one `eitri mcp --combined` process."""

    def __init__(self, root: str | Path | None = None, open_browser: bool | None = None):
        from eitri_suite.workspace import Workspace
        if open_browser is None:
            open_browser = os.environ.get("EITRI_NO_BROWSER", "").strip().lower() not in ("1", "true", "yes")
        self.open_browser = open_browser
        self.ws = Workspace(root, viewer=self.viewer_url)
        self.ws.on_new_project = self._count_project
        self._viewers: dict[str, str] = {}
        self._opened: set[str] = set()
        self._lock = threading.Lock()
        self._warmed = False

    def _count_project(self, folder: Path) -> None:
        from .. import telemetry
        telemetry.send("project_created", kind="combined")

    def warm(self) -> None:
        if self._warmed or os.environ.get("EITRI_NO_WARM"):
            return
        self._warmed = True
        from ..kernel import warm_worker_async
        warm_worker_async()

    def viewer_url(self, folder: Path) -> str:
        key = str(Path(folder).resolve())
        with self._lock:
            url = self._viewers.get(key)
            if url is None:
                from ..project import Project
                from ..viewer import server as viewer_server
                from .plugin import _viewer_port, host_agent
                url = viewer_server.start_viewer(Project(folder), port=_viewer_port(), background=True, plugin=host_agent(),
                                                 combined=True, busy=lambda: self.ws.busy(Path(key)))
                self._viewers[key] = url
        return url

    def browse(self, folder: Path) -> None:
        """Open the project's viewer in the browser once (at its first build): the person watches it arrive."""
        key = str(Path(folder).resolve())
        if not self.open_browser or key in self._opened:
            return
        self._opened.add(key)
        try:
            webbrowser.open(self.viewer_url(folder))
        except Exception:                     # no browser on this machine: the link is in the result
            pass


def _err(fn):
    import functools

    @functools.wraps(fn)
    def inner(*a, **kw):
        from eitri_suite.workspace import ToolError as WsError
        try:
            return fn(*a, **kw)
        except (WsError, PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except ToolError:
            raise
        except Exception as exc:             # the SDK hides a crash's text from the agent: say what went wrong
            raise ToolError(f"{type(exc).__name__}: {exc}") from exc
    return inner


def _with_image(text: str, res: dict, image: bool) -> list[Any]:
    """The results text, with the CAD review render of a fresh build: as a path to Read (Claude Code) or attached (inline)."""
    paths = [f["image_path"] for f in res.get("files") or [] if f.get("image_path") and Path(f["image_path"]).is_file()]
    if not (image and res.get("_fresh") and paths):
        return [TextContent(type="text", text=text)]
    if IMAGE_MODE == "path":
        return [TextContent(type="text", text=text + f"\n\nReview image of the CAD model: {paths[0]}  <- Read this file now and check it.")]
    return [TextContent(type="text", text=text + "\n\nReview image of the CAD model (attached)."), Image(path=paths[0]).to_image_content()]


def make_server(session: CombinedSession | None = None) -> MCPServer:
    from eitri_suite import workspace as W
    session = session or CombinedSession()
    ws = session.ws
    server = MCPServer("eitri-combined", instructions=INSTRUCTIONS, version=__version__)

    @server.tool(title="How to write a combined project",
                 description="How to write a combined eitri project's files: the file layout and the loop (topic 'overview', the default), the links "
                             "between domains (INTEGRATION, COUPLING, anchors, SEQUENCE: 'integration'), each domain's API and checks ('cad', 'mechanism', "
                             "'harness', 'firmware', 'pneumatics', 'electronics'), and the parts catalog ('catalog', or 'catalog servo' for matching "
                             "parts). Read 'overview' first, then the topic of each domain before writing its first file.")
    @_err
    def combined_reference(topic: str = "") -> str:
        return ws.reference(topic or None)

    @server.tool(title="New combined project",
                 description="Make a new combined project on this computer (a folder in ~/eitri-projects that starts with a blank model.py). "
                             "Returns its project_id and the link to its viewer. goal: what the machine is for, in a sentence or two.")
    @_err
    def combined_new_project(name: str, goal: str = "") -> str:
        out = ws.new_project(name, goal)
        session.warm()
        from .. import telemetry
        note = telemetry.notice_once()
        return ((f"Tell the person once, in your next message: {note}\n\n" if note else "") + f"Made project {out['project_id']} ({out['name']}) in {W._tilde(out['folder'])}. It holds a blank model.py.\n"
                f"Viewer (a tab per model file; it opens in the browser at the first build): {out['view']}\n\n"
                "Before the first combined_put_files, tell the person in one short visible message: this runs in eitri's combined "
                "workspace on this computer, your plan in one or two lines, and the viewer link.\n"
                "Next: read combined_reference() if you have not, write the files with combined_put_files, then combined_build.")

    @server.tool(title="Your combined projects",
                 description="The combined projects on this computer (in ~/eitri-projects): id, name, last build and whether every check passed.")
    @_err
    def combined_list_projects() -> str:
        ps = ws.list_projects()
        return "\n".join(f"{p['project_id']} · {p['name']} · updated {p['updated']} · "
                         + (f"build {p['last_build']}: {'every check passes' if p['all_pass'] else 'not done yet'}" if p["last_build"] else "never built")
                         + f" · {W._tilde(p['folder'])}" for p in ps) or "No combined projects yet: combined_new_project makes one."

    @server.tool(title="Read project files",
                 description="Read a combined project's files (all of them, or the paths given): each model file's content and kind.")
    @_err
    def combined_read_files(project_id: str, paths: list[str] | None = None) -> str:
        out = ws.read_files(project_id, paths)
        return "\n\n".join(f"===== {f['path']}" + (f" ({f['kind']}{', blank' if f.get('blank') else ''})" if f.get("kind") else "")
                           + " =====\n" + (f.get("content") if "content" in f else f"[{f['skipped']}]") for f in out["files"])

    @server.tool(title="Write project files",
                 description="Write (create or replace) a combined project's files, each with its whole content: model files (model.py, mech.py, "
                             "wiring.py, fw.py, circuit.py, elec.py or any lowercase name ending in .py; the import line sets its kind) and DESIGN.md. "
                             "delete: file names to remove. Nothing is built: call combined_build after. (You may also edit the files in the "
                             "project folder with your own tools; combined_build builds what is on disk.)")
    @_err
    def combined_put_files(project_id: str, files: dict[str, str] | None = None, delete: list[str] | None = None) -> str:
        out = ws.put_files(project_id, files, delete)
        return ("Written: " + (", ".join(f"{w['path']}" + (f" ({w['kind']})" if w["kind"] else "") for w in out["written"]) or "nothing")
                + (f"\nDeleted: {', '.join(out['deleted'])}" if out["deleted"] else "")
                + f"\nThe project's model files: {', '.join(out['files'])}\nNothing is built yet: call combined_build(project_id).")

    @server.tool(title="Build and check everything",
                 description="Build every model file of the project on this computer (CAD, then the mechanism, the harness, the firmware, the "
                             "pneumatics, the electronics last) and run every check: each domain's own and the checks between them. Returns each "
                             "file's checks (failing ones in full: measured, expected, source), what is still unbuilt, the next steps, and the viewer "
                             "link; with the CAD model's review render. If the build is not finished within about 100 s it goes on in the "
                             "background: call combined_results to wait for it. detail='all' lists passing checks with their numbers too. A check "
                             "that lacks an input is reported as 'not run' with the reason; it is not a pass.")
    @_err
    def combined_build(project_id: str, detail: str = "failing", image: bool = True) -> list[Any]:
        folder = ws.folder(project_id)
        session.warm()
        session.browse(folder)
        res = ws.build(project_id, image=image is not False)
        return _with_image(W.results_text(res, "all" if detail == "all" else "failing"), res, image is not False)

    @server.tool(title="Last build's results",
                 description="The last combined build's results without building again (waits up to about 100 s for a build that is still running).")
    @_err
    def combined_results(project_id: str, detail: str = "failing") -> list[Any]:
        res = ws.results(project_id)
        return _with_image(W.results_text(res, "all" if detail == "all" else "failing"), res, True)

    @server.tool(title="Export files",
                 description="Write the project's files for making it, on this computer (in the project's export/ folder), and return their paths. "
                             "formats: 'package' (the build package: print files, BOM, cut list, wiring steps, assembly order, as a folder and a zip), "
                             "'bom', the CAD formats ('step', 'stl', '3mf', 'glb', 'obj', 'iges', 'svg', 'dxf'), and each domain's own files "
                             "('pinout', 'header', 'cutlist', 'wireviz', 'joints', 'mass'...). Refused while any check of any file fails, did not "
                             "run, or is out of date.")
    @_err
    def combined_export(project_id: str, formats: list[str]) -> str:
        out = ws.export(project_id, formats)
        text = ("\n".join(f"{x['format']}: {x['path']}   ({x['what']})" for x in out["files"]) or "No files.") \
            + ("\n\nNot written:\n- " + "\n- ".join(out["problems"]) if out["problems"] else "") + "\n\n" + out["note"]
        if not out["files"]:
            raise ToolError(text)
        return text

    # tickets on a combined project: open, show, plan, package. There is deliberately NO tool that approves, waives or closes
    # one: those are a person's, in their own terminal (`eitri ticket approve|waive|close -C <folder>`)
    def tools_for(project_id: str):
        from ..project import Project
        from .tools import Tools
        return Tools(Project(ws.folder(project_id)))

    def answer(res) -> str:
        if res.is_error:
            raise ToolError(res.text)
        return res.text

    @server.tool(title="Open a ticket",
                 description="Open a ticket on a combined project: a customer's change request, in their words (request, verbatim), "
                             "with acceptance criteria. It covers every model file of the project: the package rebuilds them all together "
                             "with the cross-domain checks. acceptance: a list of {\"text\", \"check\": \"<check name or glob>\"} (a check "
                             "name as combined_ticket_show lists it, \"wiring.py: <check>\", or the bare name to match it in any file) or "
                             "{\"text\", \"human_verify\": true}; \"text => check\" / \"text => human\" strings work too. base: "
                             "\"latest\" (every file's last good build) or \"new\". The project may not be written or built until you "
                             "write the plan (combined_ticket_plan) and a PERSON approves it in their own terminal. You cannot approve it.")
    @_err
    def combined_ticket_new(project_id: str, title: str, request: str, acceptance: list[Any] | str | None = None,
                            constraints: list[str] | str | None = None, base: str = "latest", customer: str = "") -> str:
        return answer(tools_for(project_id).ticket_new(title, request, acceptance, constraints, "", base, customer))

    @server.tool(title="Show a ticket",
                 description="A combined project's ticket (default: the active one): request, criteria, plan, status, approval, "
                             "waivers, package, and the names of the checks the base builds run (to map criteria to).")
    @_err
    def combined_ticket_show(project_id: str, ticket: str = "") -> str:
        t = tools_for(project_id)
        return answer(t.ticket_show(ticket)) + "\n\n" + answer(t.ticket_list())

    @server.tool(title="Write a ticket's plan",
                 description="Write the plan for a combined project's ticket before any change: summary, the files it will change "
                             "(new files too), parameters [{\"file\", \"name\", \"from\", \"to\", \"why\"}], checks_affected, "
                             "not_checked (what eitri will NOT verify), open_questions. Then STOP: a person approves it with `eitri ticket "
                             "approve <id> -C <project folder>` in their own terminal (the answer gives the exact command). Rewriting the plan after approval voids it.")
    @_err
    def combined_ticket_plan(project_id: str, ticket: str, summary: str, files: list[str] | None = None, parameters: list[Any] | None = None,
                             checks_affected: list[str] | None = None, not_checked: list[str] | None = None,
                             open_questions: list[str] | None = None, acceptance: list[Any] | None = None) -> str:
        return answer(tools_for(project_id).ticket_plan(ticket, summary, files, parameters, checks_affected, not_checked,
                                                       open_questions, acceptance))

    @server.tool(title="Make the change package",
                 description="The change package of a combined project's approved ticket: every model file rebuilt fresh together at the "
                             "base and at the result, every check of every file and between the files before and after with its source, "
                             "parameter and BOM diffs per file, the source diff, the regenerated outputs and eitri's build package, the "
                             "revision table, Not checked and a draft change order, as a folder + zip. Refused, listing every reason, when "
                             "any check of any file fails or did not run (a person may waive a not-run check; a failed one is fixed in the "
                             "design), a file on disk has no good build, or the approval does not cover the ticket as it is.")
    @_err
    def combined_package(project_id: str, ticket: str = "") -> str:
        why = ws.busy(ws.folder(project_id))
        if why:
            raise ToolError(f"Not packaged: {why}.")
        return answer(tools_for(project_id).package(ticket))

    return server


def serve() -> None:
    """`eitri mcp --combined`: the plugin's local combined server on stdio. Logs go to stderr (stdout is the protocol)."""
    from .mcp_server import AGENT_MARKER
    os.environ[AGENT_MARKER] = "1"
    os.environ.pop("EITRI_LAZY_IMPORT", None)
    make_server(CombinedSession()).run("stdio")
