"""Expose the eitri tools over MCP (stdio).

Two ways it is started:
- by the app (`eitri web` / `eitri chat` driving Claude Code with the user's API key): EITRI_PROJECT names the project;
- by the user's own agent as a plugin (`eitri mcp`, from the eitri plugin for Claude Code, Codex or Cursor): no key,
  no Claude started here; the agent opens or creates the project with new_project / open_project (eitri.agent.plugin).
Tool results carry the report text and the review image.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent, TextContent

from .. import __version__
from ..project import Project
from .tools import ToolResult, Tools


IMAGE_MODE = os.environ.get("EITRI_IMAGE_MODE", "path")   # "path" or "inline"

PLUGIN_INSTRUCTIONS = """eitri's local design tools: six single domains (mechanical CAD, wire harnesses, mechanisms, pneumatics,
firmware, electronics), each a Python model file whose every build runs that domain's checks (every limit cites its source).
Workflow: new_project(name, goal) or open_project(folder) first; read the domain's *_reference (cad_reference for CAD); write
the complete model file with build_model; read the report and the review image; fix with edit_model / set_params (or edit the
file yourself and call rebuild) until the PROJECT STATUS at the end of every result says every check passes. Never delete or
loosen a check to make it pass. A whole machine that spans several domains (a robot, a rover, a plotter) belongs in the eitri
combined workspace (the eitri-combined server's combined_* tools) when it is connected."""


class _NoProject(Exception):
    pass


def _content(res: ToolResult) -> list[Any]:
    """Tool result content (a ticket-gate refusal is an error the agent reads, not a report). Images go back as paths by default: the host's Read tool
    then shows them as real image blocks, whereas an inline MCP image is also
    serialised as base64 text into the model's context (tens of thousands of tokens)."""
    if res.refused:
        raise ToolError(res.text)
    text = res.text
    images = [Path(i) for i in res.images if Path(i).exists()]
    if images and IMAGE_MODE == "path":
        text += "\n\nReview image: " + ", ".join(str(p) for p in images) + "  <- Read this file now and check it."
        return [TextContent(type="text", text=text)]
    if images:
        text += "\n\nReview image (attached; also at " + ", ".join(str(p) for p in images) + "): check it."
    out: list[Any] = [TextContent(type="text", text=text)]
    for p in images:
        out.append(Image(path=str(p)).to_image_content())
    return out


def make_server(project: Project, quality: float = 1.0) -> MCPServer:
    """The app's server: one project, fixed by the app that started Claude (EITRI_PROJECT)."""
    tools = Tools(project, render_quality=quality)
    server = MCPServer("eitri", instructions="CAD modelling tools for the eitri agent.", version=__version__)
    _register(server, lambda: tools, lambda res: res, lambda: "")
    return server


def make_plugin_server(session=None) -> MCPServer:
    """Plugin mode (`eitri mcp`): started by the user's own agent. No API key, no Claude: the agent calls these tools.
    The project is the user's choice (new_project / open_project), the viewer opens at the first build and follows
    every rebuild, and every build result ends with what still fails or is unbuilt."""
    from .plugin import PluginSession, cad_reference_text, with_status
    session = session or PluginSession()
    server = MCPServer("eitri", instructions=PLUGIN_INSTRUCTIONS, version=__version__)
    _register(server, session.require, lambda res: with_status(session, res), session.no_project_text)

    @server.tool(description="Create a new eitri project folder and open it. `name` names it; `goal` (the request, in a "
                 "sentence) goes into its DESIGN.md; `folder` is where the user wants it (absolute, or relative to "
                 "~/eitri-projects; default ~/eitri-projects/<name>). Returns the folder and the viewer link.")
    def new_project(name: str, goal: str = "", folder: str = "") -> str:
        return session.new_project(name, goal, folder).text

    @server.tool(description="Open an existing eitri project folder (absolute, or a folder name in ~/eitri-projects) and "
                 "make it the one every other tool works on; `file` picks one model file when the folder has several. "
                 "Returns its status: what is built, what fails, what is not built yet.")
    def open_project(folder: str, file: str = "") -> str:
        return session.open_project(folder, file).text

    @server.tool(description="List the eitri projects in ~/eitri-projects (newest first) with their last build state.")
    def list_projects() -> str:
        return session.list_projects().text

    @server.tool(description="The open project's status without building: every model file, whether it is built, its "
                 "failing and warning checks with their numbers, files changed on disk since their last build, and what "
                 "to do next.")
    def project_status() -> str:
        return session.status_text()

    @server.tool(description="Open the open project's live viewer in the user's browser again and return its link "
                 "(it follows every rebuild on its own).")
    def open_viewer() -> str:
        return session.open_viewer().text

    @server.tool(description="Build the current model file as it is on disk (after you edited it with your own file "
                 "tools) and return the same report, review image and status as build_model.")
    def rebuild(note: str = "") -> list[Any]:
        t = session.require()
        if t is None:
            raise ToolError(session.no_project_text())
        if not t.project.model_path.exists():
            raise ToolError("There is no model file yet; call build_model with the complete file first.")
        from ..tickets.store import ChangeRefused
        try:
            return _content(with_status(session, t._build_and_report(note or "rebuild")))
        except ChangeRefused as exc:           # the ticket gate: the agent reads why
            raise ToolError(str(exc)) from None

    @server.tool(description="The working guide of one local domain (cad, wires, motion, pneumatics, firmware, "
                 "electronics): how to plan, build, review and finish, and the rules its checks hold you to. The eitri "
                 "plugin's skills carry the same text; read this when your host has no eitri skill loaded.")
    def domain_guide(domain: str) -> str:
        from ..plugin_files import DOMAINS, domain_skill
        alias = {"harness": "wires", "mechanism": "motion", "mechanical": "cad", "circuit": "electronics"}
        name = alias.get(domain.strip().lower(), domain.strip().lower())
        if name not in DOMAINS:
            raise ToolError(f"unknown domain {domain!r}; one of: {', '.join(DOMAINS)}")
        return domain_skill(name).split("---", 2)[2].strip()

    @server.tool(description="The mechanical CAD reference: the model-file contract's build123d API (verified "
                 "snippets) and the eitri feature-library catalog. Read it before writing a CAD model file "
                 "(`from eitri import *`).")
    def cad_reference() -> str:
        session.warm()
        return cad_reference_text()

    return server


def _register(server: MCPServer, get: Callable[[], Tools | None], wrap: Callable[[ToolResult], ToolResult],
              missing: Callable[[], str]) -> None:
    """The model tools, over whatever project `get()` returns (None: no project open yet, and `missing()` says how to
    open one). `wrap` adds the plugin's status to results that build."""

    def need() -> Tools:
        t = get()
        if t is None:
            raise _NoProject(missing())
        return t

    def any_tools() -> Tools:
        """References and the library need no project."""
        return get() or Tools(None)  # type: ignore[arg-type]

    def guard(fn):
        import functools

        @functools.wraps(fn)
        def inner(*a, **kw):
            try:
                return fn(*a, **kw)
            except _NoProject as exc:            # an is_error result the agent reads, not a crash
                raise ToolError(str(exc)) from None
            except ToolError:
                raise
            except Exception as exc:             # the SDK hides a crash's text from the agent: say what went wrong
                raise ToolError(f"{type(exc).__name__}: {exc}") from exc
        return inner

    @server.tool(description=Tools.build_model.__doc__)
    @guard
    def build_model(code: str, note: str = "") -> list[Any]:
        return _content(wrap(need().build_model(code, note)))

    @server.tool(description=Tools.edit_model.__doc__)
    @guard
    def edit_model(old: str, new: str, note: str = "") -> list[Any]:
        return _content(wrap(need().edit_model(old, new, note)))

    @server.tool(description=Tools.set_params.__doc__)
    @guard
    def set_params(params: dict[str, Any] | str | None = None, config: str | None = None) -> list[Any]:
        return _content(wrap(need().set_params(params, config=config)))

    @server.tool(description=Tools.read_model.__doc__)
    @guard
    def read_model() -> str:
        return need().read_model().text

    @server.tool(description=Tools.inspect.__doc__)
    @guard
    def inspect(code: str) -> str:
        return need().inspect(code).text

    @server.tool(description=Tools.view.__doc__)
    @guard
    def view(views: list[str] | None = None, section: str | None = None, exploded: bool = False,
             hide: list[str] | None = None, only: list[str] | None = None, turntable: int = 0) -> list[Any]:
        return _content(need().view(views, section, exploded, hide, only, turntable=turntable))

    @server.tool(description=Tools.library.__doc__)
    @guard
    def library(query: str = "") -> str:
        return any_tools().library(query).text

    @server.tool(description=Tools.export.__doc__)
    @guard
    def export(formats: list[str]) -> str:
        return need().export(formats).text

    @server.tool(description=Tools.design_notes.__doc__)
    @guard
    def design_notes(content: str) -> str:
        return need().design_notes(content).text

    @server.tool(description=Tools.harness_reference.__doc__)
    @guard
    def harness_reference() -> str:
        return any_tools().harness_reference().text

    @server.tool(description=Tools.mechanism_reference.__doc__)
    @guard
    def mechanism_reference() -> str:
        return any_tools().mechanism_reference().text

    @server.tool(description=Tools.pneumatics_reference.__doc__)
    @guard
    def pneumatics_reference() -> str:
        return any_tools().pneumatics_reference().text

    @server.tool(description=Tools.firmware_reference.__doc__)
    @guard
    def firmware_reference() -> str:
        return any_tools().firmware_reference().text

    @server.tool(description=Tools.electronics_reference.__doc__)
    @guard
    def electronics_reference() -> str:
        return any_tools().electronics_reference().text

    @server.tool(description=Tools.cam.__doc__)
    @guard
    def cam(ops: list | str | None = None, part: str | None = None, post: str = "grbl", material: str | None = None, safe_z: float = 5.0) -> list[Any]:
        return _content(need().cam(ops, part, post, material, safe_z))

    @server.tool(description=Tools.cut_file.__doc__)
    @guard
    def cut_file(part: str | None = None, kerf: float = 0.2, fmt: str = "dxf", flat_pattern: bool = False) -> str:
        return need().cut_file(part, kerf, fmt, flat_pattern).text

    @server.tool(description=Tools.print_prep.__doc__)
    @guard
    def print_prep(bed: list[float] | str = "220x220", layer: float = 0.2, orient: bool = True, parts: list[str] | None = None) -> str:
        return need().print_prep(bed, layer, orient, parts).text

    @server.tool(description=Tools.compare_revisions.__doc__)
    @guard
    def compare_revisions(a: int | None = None, b: int | None = None) -> str:
        return need().compare_revisions(a, b).text

    @server.tool(description=Tools.drawing.__doc__)
    @guard
    def drawing(views: list[str] | None = None, sheet: str = "A4", style: str = "iso", projection: str = "third", notes: list[str] | str | None = None,
                annotations: list[dict] | str | None = None, gdt: list[dict] | str | None = None, datums: list[dict] | str | None = None,
                scales: dict | str | None = None, pdf: bool = False,
                dims: list[dict] | str | None = None) -> str:
        if isinstance(notes, str):
            notes = [n for n in notes.split("\n") if n.strip()]
        return _content(need().drawing(views, sheet, style, projection, notes, annotations, gdt, datums, scales, pdf, dims))

    @server.tool(description=Tools.list_edits.__doc__)
    @guard
    def list_edits() -> str:
        return need().list_edits().text

    @server.tool(description=Tools.update_edit.__doc__)
    @guard
    def update_edit(index: int, patch: dict[str, Any] | str) -> list[Any]:
        return _content(wrap(need().update_edit(index, patch)))

    @server.tool(description=Tools.sketch_op.__doc__)
    @guard
    def sketch_op(index: int, op: str, args: dict[str, Any] | str | None = None) -> list[Any]:
        return _content(wrap(need().sketch_op(index, op, args)))

    @server.tool(description=Tools.remove_edit.__doc__)
    @guard
    def remove_edit(index: int) -> list[Any]:
        return _content(wrap(need().remove_edit(index)))

    # tickets: open, show, list, plan, package. There is deliberately NO tool that approves, waives or closes a ticket:
    # those are a person's, in their own terminal (`eitri ticket approve|waive|close`; tests/test_tickets.py checks this)
    def _text(res: ToolResult) -> str:
        if res.is_error:
            raise ToolError(res.text)
        return res.text

    @server.tool(description=Tools.ticket_new.__doc__)
    @guard
    def ticket_new(title: str, request: str, acceptance: list[Any] | str | None = None, constraints: list[str] | str | None = None,
                   file: str = "", base: str = "latest", customer: str = "") -> str:
        return _text(need().ticket_new(title, request, acceptance, constraints, file, base, customer))

    @server.tool(description=Tools.ticket_show.__doc__)
    @guard
    def ticket_show(ticket: str = "") -> str:
        return _text(need().ticket_show(ticket))

    @server.tool(description=Tools.ticket_list.__doc__)
    @guard
    def ticket_list() -> str:
        return _text(need().ticket_list())

    @server.tool(description=Tools.ticket_plan.__doc__)
    @guard
    def ticket_plan(ticket: str, summary: str, files: list[str] | None = None, parameters: list[Any] | None = None,
                    checks_affected: list[str] | None = None, not_checked: list[str] | None = None,
                    open_questions: list[str] | None = None, acceptance: list[Any] | None = None) -> str:
        return _text(need().ticket_plan(ticket, summary, files, parameters, checks_affected, not_checked, open_questions, acceptance))

    @server.tool(name="package", description=Tools.package.__doc__)
    @guard
    def package_tool(ticket: str = "") -> str:
        return _text(need().package(ticket))



AGENT_MARKER = "EITRI_AGENT_PROCESS"     # inherited by every process this server starts: person-only actions refuse there


def main(argv: list[str] | None = None) -> None:
    os.environ[AGENT_MARKER] = "1"
    args = list(sys.argv[1:] if argv is None else argv)
    if "--plugin" in args or (not os.environ.get("EITRI_PROJECT") and os.environ.get("EITRI_PLUGIN")):
        serve_plugin()
        return
    folder = os.environ.get("EITRI_PROJECT") or os.getcwd()
    name = os.environ.get("EITRI_MODEL_NAME") or None
    quality = float(os.environ.get("EITRI_RENDER_QUALITY", "1.0"))
    project = Project(folder, name=name)
    from ..kernel import warm_worker_async
    warm_worker_async()                      # the CAD kernel loads while Claude reads the request
    server = make_server(project, quality)
    server.run("stdio")


def serve_plugin() -> None:
    """`eitri mcp`: the plugin's local server on stdio. Logs go to stderr (stdout is the protocol)."""
    from .plugin import PluginSession
    os.environ[AGENT_MARKER] = "1"
    os.environ.pop("EITRI_LAZY_IMPORT", None)   # this process started lazily (fast handshake); its builds need not
    session = PluginSession(quality=float(os.environ.get("EITRI_RENDER_QUALITY", "1.0")))
    session.adopt_startup_folder()
    make_plugin_server(session).run("stdio")


if __name__ == "__main__":
    main()
