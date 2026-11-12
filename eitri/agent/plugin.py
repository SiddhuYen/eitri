"""Plugin mode: eitri's local tools for an agent the user runs themselves (Claude Code, Codex, Cursor...).

`eitri mcp` serves these over MCP (stdio). Nothing here starts Claude, reads a Claude login or needs an Anthropic
key: the user's own agent calls the tools. What the app's agent loop adds on top of the tools (a project picked in
the browser, a viewer next to the chat, reminders while checks are red) is done here instead:

- the project folder is the user's choice: `new_project` / `open_project` (default root ~/eitri-projects);
- the viewer opens in the browser at the first build and follows every rebuild (it polls the project state);
- every build result ends with the project's status: which model files are built, which checks still fail or warn,
  which files changed on disk since their last build, and what to do next.
"""
from __future__ import annotations

import json
import os
import threading
import webbrowser
from pathlib import Path

from ..project import Project
from .tools import ToolResult, Tools

NO_PROJECT = ("No eitri project is open yet. Ask the user where to keep the design if they have not said (the default is "
              "{root}/<name>), then call new_project(name, goal) - or open_project(folder) for an existing one. "
              "list_projects() shows the projects in {root}.")


def default_root() -> Path:
    return Path(os.environ.get("EITRI_WORKSPACE") or (Path.home() / "eitri-projects")).expanduser()


def host_agent() -> str:
    """The agent that started this server (the plugin's MCP config sets EITRI_HOST_AGENT), for the viewer's caption."""
    return os.environ.get("EITRI_HOST_AGENT") or "your agent"


class PluginSession:
    """The open project, its tools and its viewer, for one `eitri mcp` process."""

    def __init__(self, root: str | Path | None = None, open_browser: bool | None = None, quality: float = 1.0):
        self.root = Path(root).expanduser() if root else default_root()
        if open_browser is None:
            open_browser = os.environ.get("EITRI_NO_BROWSER", "").strip().lower() not in ("1", "true", "yes")
        self.open_browser = open_browser
        self.quality = quality
        self.project: Project | None = None
        self.tools: Tools | None = None
        self._viewers: dict[str, str] = {}          # project folder -> viewer URL (one server per project, kept)
        self._opened: set[str] = set()              # folders whose viewer the browser was sent to
        self._lock = threading.Lock()
        self._warmed = False

    # ------------------------------------------------------------------ projects
    def adopt_startup_folder(self) -> Project | None:
        """Open the folder the agent was started in when it is already an eitri project (EITRI_PROJECT, else the
        agent's project dir, else the cwd). Never creates anything."""
        for cand in (os.environ.get("EITRI_PROJECT"), os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()):
            if cand and (Path(cand) / "eitri.json").is_file():
                return self._open(Path(cand))
        return None

    def require(self) -> Tools | None:
        return self.tools

    def no_project_text(self) -> str:
        return NO_PROJECT.format(root=_tilde(self.root))

    def new_project(self, name: str, goal: str = "", folder: str = "") -> ToolResult:
        from ..web.app import model_stem, set_project_title, slugify, title_from
        name = (name or "").strip() or (goal or "").strip() or "design"
        if folder.strip():
            target = Path(folder).expanduser()
            if not target.is_absolute():
                target = self.root / target
            if (target / "eitri.json").is_file():
                return ToolResult(f"{_tilde(target)} is already an eitri project: call open_project({str(target)!r}) to "
                                  "continue it, or give another folder.", is_error=True)
            if target.exists() and any(p.suffix == ".py" for p in target.glob("*.py")):
                return ToolResult(f"{_tilde(target)} already holds Python files; pick an empty or new folder (or a "
                                  "subfolder such as <folder>/cad) so eitri does not adopt one of them as the model.",
                                  is_error=True)
        else:
            slug, k = slugify(name), 2
            base = slug
            while (self.root / slug).exists():
                slug, k = f"{base}-{k}", k + 1
            target = self.root / slug
        target.mkdir(parents=True, exist_ok=True)
        project = Project(target, name=model_stem(slugify(target.name)))
        set_project_title(target, title_from(name) or "Untitled")
        if goal.strip():
            project.design_notes_path.write_text(f"# {title_from(name)}\n\nGoal: {goal.strip()}\n", encoding="utf-8")
        self._activate(project)
        return ToolResult(
            f"Created the eitri project {_tilde(target)} (model file {project.model_path.name}, not written yet).\n"
            f"Viewer: {self.viewer_url(project)} (it opens in the browser at the first build and follows every rebuild).\n"
            "Next: read the reference for the domain (cad_reference, harness_reference, mechanism_reference, "
            "pneumatics_reference, firmware_reference or electronics_reference), then build_model with the complete file.")

    def open_project(self, folder: str, file: str = "") -> ToolResult:
        target = Path(folder).expanduser()
        if not target.is_absolute():
            target = self.root / target
        if not target.is_dir():
            return ToolResult(f"no folder {_tilde(target)}; list_projects() shows the projects in {_tilde(self.root)}, "
                              "or new_project(name, goal, folder) creates one.", is_error=True)
        project = self._open(target)
        if file.strip():
            name = Path(file).name if file.endswith(".py") else f"{Path(file).name}.py"
            if not (project.folder / name).is_file():
                return ToolResult(f"no model file {name} in {_tilde(project.folder)}; it has: "
                                  f"{', '.join(project.model_files()) or 'none yet'}", is_error=True)
            project.set_model_file(name)
        return ToolResult(f"Opened {_tilde(project.folder)}.\nViewer: {self.viewer_url(project)}\n\n" + self.status_text())

    def list_projects(self) -> ToolResult:
        from ..web.app import is_project, project_title
        if not self.root.is_dir():
            return ToolResult(f"No projects yet: {_tilde(self.root)} does not exist. new_project(name, goal) creates the first.")
        rows = []
        for f in sorted((p for p in self.root.iterdir() if p.is_dir() and not p.name.startswith(".")),
                        key=lambda p: p.stat().st_mtime, reverse=True):
            if not is_project(f):
                continue
            title = project_title(f) or f.name
            try:
                st = json.loads((f / "eitri.json").read_text(encoding="utf-8"))
                hist = st.get("history", [])
                last = hist[-1] if hist else None
                state = (f"revision {last['number']} {'built' if last.get('ok') else 'FAILED'}") if last else "not built"
            except (OSError, ValueError, KeyError):
                state = "not built"
            rows.append(f"- {f.name}: {title} ({state})")
            if len(rows) >= 40:
                break
        if not rows:
            return ToolResult(f"No projects in {_tilde(self.root)} yet. new_project(name, goal) creates one.")
        return ToolResult(f"Projects in {_tilde(self.root)}, newest first (open_project(folder) to continue one):\n" + "\n".join(rows))

    def _open(self, folder: Path) -> Project:
        project = Project(folder)
        self._activate(project)
        return project

    def _activate(self, project: Project) -> None:
        with self._lock:
            self.project = project
            self.tools = Tools(project, on_event=self._on_event, render_quality=self.quality)
        self.warm()

    def warm(self) -> None:
        """Load the CAD kernel in the background once a project is open (not at server start: the agent starts this
        server in every session, also the ones that never design anything)."""
        if self._warmed or os.environ.get("EITRI_NO_WARM"):
            return
        self._warmed = True
        from ..kernel import warm_worker_async
        warm_worker_async()

    # ------------------------------------------------------------------ viewer
    def viewer_url(self, project: Project | None = None) -> str:
        project = project or self.project
        if project is None:
            return ""
        key = str(project.folder)
        with self._lock:
            url = self._viewers.get(key)
            if url is None:
                from ..viewer import server as viewer_server
                url = viewer_server.start_viewer(project, port=_viewer_port(), background=True, plugin=host_agent())
                self._viewers[key] = url
        return url

    def open_viewer(self, force: bool = True) -> ToolResult:
        if self.project is None:
            return ToolResult(self.no_project_text(), is_error=True)
        url = self.viewer_url()
        opened = self._browse(url, force=force)
        return ToolResult(f"Viewer: {url}" + (" (opened in the browser)" if opened else " (give the user this link)"))

    def _browse(self, url: str, force: bool = False) -> bool:
        key = str(self.project.folder) if self.project else url
        if not self.open_browser or (key in self._opened and not force):
            return False
        self._opened.add(key)
        try:
            return bool(webbrowser.open(url))
        except Exception:                 # no browser on this machine (a server, CI): the link is in the result
            return False

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "build_start" and self.project is not None:
            try:
                self._browse(self.viewer_url())       # the first build of a project: the user watches it arrive
            except Exception:
                pass

    # ------------------------------------------------------------------ status
    def status_text(self) -> str:
        if self.project is None:
            return self.no_project_text()
        return project_status(self.project, self._viewers.get(str(self.project.folder)))


def project_status(project: Project, viewer: str | None = None) -> str:
    """What is built, what still fails or warns, what changed since its last build, and what to do next - the
    reminder the app's agent loop would give, in every tool result."""
    from ..domains import kind_of_source, label
    revision, history = project.snapshot()
    lines = [f"PROJECT STATUS ({_tilde(project.folder)}" + (f"; viewer {viewer}" if viewer else "") + ")"]
    todo: list[str] = []
    files = project.model_files()
    for f in files:
        path = project.folder / f
        code = path.read_text(encoding="utf-8") if path.is_file() else None
        kind = kind_of_source(code)
        what = "CAD" if kind == "cad" else label(kind)
        mine = project.revisions_of(f, history)
        last = mine[-1] if mine else None
        ok = [h for h in mine if h.ok]
        current = " (current)" if f == project.model_path.name and len(files) > 1 else ""
        if code is None:
            lines.append(f"- {f}{current}: not written yet")
            todo.append(f"write {f} with build_model")
            continue
        if last is None:
            lines.append(f"- {f}{current} ({what}): NOT BUILT")
            todo.append(f"build {f} (rebuild)")
            continue
        changed = _changed_since(project, last, code)
        if not last.ok:
            lines.append(f"- {f}{current} ({what}): revision {last.number} FAILED TO BUILD" +
                         (f"; last good build: revision {ok[-1].number}" if ok else ""))
            todo.append(f"fix the build error in {f} and rebuild")
        else:
            checks, issues = _checks(project, last, history)
            fail = [c for c in checks if not c.get("ok") or c.get("severity") == "fail"]
            warn = [c for c in checks if c.get("ok") and c.get("severity") == "warn"]
            npass = len(checks) - len(fail) - len(warn)
            lines.append(f"- {f}{current} ({what}): revision {last.number} built; checks {npass} pass, {len(warn)} warn, "
                         f"{len(fail)} FAIL" + (f"; {len(issues)} geometry issue(s)" if issues else ""))
            for c in fail[:12]:
                lines.append("    FAIL " + _check_line(c))
            if len(fail) > 12:
                lines.append(f"    ... and {len(fail) - 12} more failing")
            for c in warn[:6]:
                lines.append("    warn " + _check_line(c))
            for i in issues[:6]:
                lines.append("    issue " + str(i)[:300])
            if fail:
                todo.append(f"fix the design in {f} until its {len(fail)} failing check(s) pass (never delete or loosen a check)")
            if issues:
                todo.append(f"fix or justify the geometry issue(s) in {f}")
            if kind == "cad" and not checks:
                todo.append(f"add verify() checks for the request's requirements to {f}")
        if changed:
            lines.append(f"    {f} changed on disk after revision {last.number}: that edit is NOT BUILT")
            todo.insert(0, f"rebuild {f} (call rebuild) to check the edit")
    if todo:
        lines.append("STILL TO DO: " + "; ".join(todo) + ". Then look at the latest review image before you finish.")
    else:
        warned = sum(1 for l in lines if l.startswith("    warn "))
        lines.append("Every model file is built and every check passes" +
                     (f" ({warned} warning(s): fix them, or tell the user why each is acceptable)" if warned else "") +
                     ". Look at the latest review image against the request; if it matches, you are done.")
    return "\n".join(lines)


def _checks(project: Project, rev, history) -> tuple[list[dict], list]:
    data = project.build_result(rev, history) or {}
    report = data.get("report") or {}
    checks = list(report.get("checks") or [])
    issues = list(report.get("issues") or []) + [i for p in (report.get("parts") or []) for i in (p.get("issues") or [])]
    return checks, issues


def _check_line(c: dict) -> str:
    text = str(c.get("name", "check"))
    if c.get("measured") not in (None, ""):
        text += f": {c['measured']}"
    if c.get("expected"):
        text += f" (expected {c['expected']})"
    src = c.get("source") or c.get("sources")
    if src:
        text += f" [source: {src if isinstance(src, str) else '; '.join(map(str, src))}]"
    return text[:400]


def _changed_since(project: Project, rev, code: str) -> bool:
    try:
        built = (project.folder / rev.model).read_text(encoding="utf-8")
    except OSError:
        return False
    return built != code


def _tilde(p: Path | str) -> str:
    p = Path(p)
    try:
        return "~/" + str(p.resolve().relative_to(Path.home().resolve()))
    except ValueError:
        return str(p)


def _viewer_port() -> int:
    """A port for the plugin's viewer: 8790-8799 when one is free (a stable bookmark), else any. Never 8765-8774,
    which `eitri web` uses."""
    import socket
    for port in range(8790, 8800):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def with_status(session: PluginSession, res: ToolResult) -> ToolResult:
    """A tool result with the project status appended (after builds and edits)."""
    if session.project is None or res.refused:         # a ticket-gate refusal goes back as it is (an error)
        return res
    return ToolResult(res.text + "\n\n" + session.status_text(), res.images, res.is_error)


def cad_reference_text() -> str:
    """The CAD reference the app puts in its system prompt: the build123d reference and the feature-library catalog."""
    from .library_docs import catalog
    here = Path(__file__).parent
    return ((here / "build123d_ref.md").read_text(encoding="utf-8").strip() +
            "\n\n# Feature library (call library(name) for full docs)\n\n```\n" + catalog() + "\n```")

