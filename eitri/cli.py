"""The eitri command line: a Claude-Code-like REPL for CAD, plus utility commands."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import textwrap
import time
import webbrowser
from pathlib import Path

from .project import Project

C = {
    "dim": "\033[2m", "bold": "\033[1m", "reset": "\033[0m", "cyan": "\033[36m", "green": "\033[32m",
    "yellow": "\033[33m", "red": "\033[31m", "blue": "\033[34m", "magenta": "\033[35m",
}


def _c(color: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{C[color]}{text}{C['reset']}"


def _wrap(text: str, indent: str = "") -> str:
    width = min(shutil.get_terminal_size((100, 20)).columns - 2, 110)
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        out.append(textwrap.fill(para, width=width, initial_indent=indent, subsequent_indent=indent,
                                 replace_whitespace=False))
    return "\n".join(out)


# ----------------------------------------------------------------------------- events

class Printer:
    """Renders agent events to the terminal, Claude-Code style."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.pending_tool: dict[str, str] = {}

    def __call__(self, kind: str, data: dict) -> None:
        try:
            self._render(kind, data)
        except Exception as exc:  # a display glitch must never kill the session
            print(_c("dim", f"  (display: {type(exc).__name__}: {exc})"))

    def _render(self, kind: str, data: dict) -> None:
        if kind == "user":
            if data.get("source") == "viewer":
                sel = f"  [selection: {', '.join(data.get('selection') or [])}]" if data.get("selection") else ""
                print(_c("magenta", f"\n  [viewer] {data.get('text', '')}") + _c("dim", sel))
            return
        if kind == "error":
            print(_c("red", f"  {data.get('text', '')}"))
            return
        if kind == "text":
            print(_wrap(data["text"].strip()))
            print()
        elif kind == "thinking":
            if self.verbose:
                first = data["text"].strip().splitlines()[0][:120]
                print(_c("dim", f"  ~ {first}"))
        elif kind == "tool_use":
            name = data["name"].split("__")[-1]
            inp = data.get("input") if isinstance(data.get("input"), dict) else {}
            detail = str(inp.get("note") or inp.get("query") or "")
            if name == "set_params":
                params = inp.get("params") or {}
                detail = ", ".join(f"{k}={v}" for k, v in params.items()) if isinstance(params, dict) else str(params)[:80]
            elif name == "view":
                bits = []
                if inp.get("views"):
                    bits.append(",".join(str(v) for v in (inp["views"] if isinstance(inp["views"], list) else [inp["views"]])))
                if inp.get("section"):
                    bits.append("section " + str(inp["section"]))
                if inp.get("exploded"):
                    bits.append("exploded")
                detail = " ".join(bits)
            elif name == "inspect":
                detail = (inp.get("code") or "").strip().splitlines()[0][:80] if inp.get("code") else ""
            elif name == "export":
                detail = ",".join(inp.get("formats") or [])
            self.pending_tool[data.get("id", "")] = name
            print(_c("cyan", f"  > {name}") + (_c("dim", f"  {detail}") if detail else ""))
        elif kind == "tool_result":
            name = self.pending_tool.pop(data.get("id", ""), "")
            text = _tool_result_text(data.get("text") or "")
            if not text:
                return
            lines = text.splitlines()
            if name in ("build_model", "edit_model", "set_params"):
                head = lines[0]
                passes = sum(1 for l in lines if "[PASS]" in l)
                fails = sum(1 for l in lines if "[FAIL]" in l)
                issues = sum(1 for l in lines if l.strip().startswith("!"))
                if "BUILD FAILED" in text:
                    print(_c("red", "    x build failed: ") + _c("dim", " ".join(lines[1:3])[:160]))
                else:
                    status = _c("green", f"{passes} pass") + (_c("red", f" {fails} fail") if fails else "") + \
                        (_c("yellow", f" {issues} issue(s)") if issues else "")
                    print(_c("dim", f"    {head[:90]}") + "  " + status)
                if self.verbose:
                    print(_c("dim", textwrap.indent(text[:1500], "    ")))
            elif name == "inspect":
                print(_c("dim", textwrap.indent(text[:600], "    ")))
            elif self.verbose:
                print(_c("dim", textwrap.indent(text[:500], "    ")))
        elif kind == "result":
            cost = data.get("turn_cost_usd", data.get("total_cost_usd"))
            dur = (data.get("duration_ms") or 0) / 1000
            turns = data.get("num_turns")
            print(_c("dim", f"  [{turns} steps, {dur:.0f}s" + (f", ${cost:.2f}" if cost else "") + "]"))


def _tool_result_text(raw: str) -> str:
    """MCP results arrive as a JSON envelope string; pull out the text parts (never the image)."""
    raw = raw.strip()
    if raw.startswith("{") and '"result"' in raw[:20]:
        try:
            import json
            obj = json.loads(raw)
            items = obj.get("result") if isinstance(obj, dict) else None
            if isinstance(items, list):
                return "\n".join(i.get("text", "") for i in items if i.get("type") == "text").strip()
            if isinstance(items, str):
                return items.strip()
        except Exception:
            pass
    return raw


# ----------------------------------------------------------------------------- REPL

HELP = """\
Type a request to design or change the model. Slash commands:
  /show            open the viewer in your browser
  /image <path>    attach an image (sketch, photo, drawing) to your next message
  /params          list parameters          /set name=value ...   change them and rebuild (no LLM)
  /undo            go back one revision     /history              list revisions
  /model           print the model file     /notes                print DESIGN.md
  /export stl 3mf  write extra formats      /build                rebuild the current file
  /view [args]     render extra views (e.g. /view section XZ:0 or /view exploded)
  /drawing [views] [A4|A3]  engineering drawing (SVG + DXF; views: front, top, right, iso, section:XZ@20, halfsection:XZ@0|YZ@0, detail:front@x,y:r:k, aux:nx,ny,nz, broken:front@x1..x2)
  /cost            session cost so far      /help                 this text
  /quit            exit
"""


def repl(project: Project, model: str, effort: str | None, viewer: bool, verbose: bool,
         max_budget: float | None, backend: str) -> int:
    from .agent.hub import AgentHub
    printer = Printer(verbose=verbose)
    hub = AgentHub(project, lambda on_event: _make_session(project, model, effort, max_budget, on_event, backend), printer, history=True)
    session = hub
    url = None
    hub.prewarm()                                   # Claude loads while you type your first request
    from .kernel import warm_worker_async
    warm_worker_async()
    if project.ensure_model():
        project.build(note="blank design", render=False)
    if viewer:
        try:
            from .viewer.server import start_viewer
            url = start_viewer(project, hub=hub)
            print(_c("dim", f"viewer: {url}   (click geometry there to edit it or to send it to the agent)"))
        except Exception as exc:  # pragma: no cover - viewer is optional
            print(_c("yellow", f"viewer not started: {exc}"))
    print(_c("bold", f"eitri") + _c("dim", f"  project {project.folder}  model {project.model_path.name}  ({backend}, {model})"))
    print(_c("dim", "describe a part to make, or /help"))
    opened = False
    pending_images: list[str] = []
    try:
        import readline  # noqa: F401
    except Exception:
        pass
    while True:
        try:
            line = input(_c("bold", "\ncad> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, arg = line[1:].partition(" ")
            cmd = cmd.lower()
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print(HELP)
            elif cmd == "show":
                if url:
                    webbrowser.open(url)
                    print(url)
                else:
                    print("viewer is off (start without --no-viewer)")
            elif cmd == "image":
                p = Path(arg.strip().strip("'\"")).expanduser()
                if p.exists():
                    pending_images.append(str(p))
                    print(f"attached {p.name}; it will go with your next message")
                else:
                    print(f"no such file: {p}")
            elif cmd == "params":
                for k, v in project.current_params().items():
                    print(f"  {k} = {v}")
            elif cmd == "set":
                updates = dict(kv.split("=", 1) for kv in arg.split() if "=" in kv)
                if not updates:
                    print("usage: /set name=value [name=value ...]")
                    continue
                try:
                    project.set_params(updates)
                except Exception as exc:
                    print(_c("red", str(exc)))
                    continue
                _local_build(project, "set " + arg, render=False)   # a tweak needs the viewer, not the agent's image
            elif cmd == "undo":
                prev = project.last_ok_revision(before=project.revision)
                if prev is None:
                    print("nothing to undo")
                    continue
                project.restore(prev.number)
                print(f"restored model from revision {prev.number}; rebuilding")
                _local_build(project, f"undo -> {prev.number}")
            elif cmd == "history":
                for h in project.history:
                    mark = "ok " if h.ok else "ERR"
                    print(f"  {h.number:3d} {mark} {time.strftime('%H:%M:%S', time.localtime(h.created))}  {h.note[:60]}")
            elif cmd == "model":
                print(project.read_model() or "(no model yet)")
            elif cmd == "notes":
                print(project.design_notes_path.read_text(encoding="utf-8") if project.design_notes_path.exists() else "(no DESIGN.md)")
            elif cmd == "export":
                fmts = tuple(arg.split()) or ("stl",)
                res = project.build(note="export", formats=fmts, render=False)
                print("\n".join(f"  {k}: {v}" for k, v in res.outputs.items()) if res.ok else _c("red", res.error or ""))
            elif cmd == "build":
                _local_build(project, "manual build")
            elif cmd == "view":
                from .agent.tools import Tools
                t = Tools(project)
                kwargs: dict = {}
                toks = arg.split()
                if "section" in toks:
                    i = toks.index("section")
                    kwargs["section"] = toks[i + 1] if i + 1 < len(toks) else "XZ"
                if "exploded" in toks:
                    kwargs["exploded"] = True
                views = [v for v in toks if v in ("iso", "iso_back", "iso_below", "front", "back", "top", "bottom", "left", "right")]
                if views:
                    kwargs["views"] = views
                res = t.view(**kwargs)
                print(res.text)
                for img in res.images:
                    print(f"  image: {img}")
                    _open_file(img)
            elif cmd == "drawing":
                from .agent.tools import Tools
                toks = arg.split()
                sheet = next((s for s in toks if s.upper() in ("A4", "A3", "LETTER")), "A4")
                views = [v for v in toks if v in ("front", "back", "top", "bottom", "left", "right", "iso")] or None
                res = Tools(project).drawing(views, sheet)
                print(res.text)
                if not res.is_error:
                    _open_file(res.text.splitlines()[1].strip("- "))
            elif cmd == "cost":
                print(f"  ${getattr(session, 'total_cost', 0.0):.2f} this session")
            else:
                print(f"unknown command /{cmd}; /help lists commands")
            continue

        # a design request
        if getattr(hub, "busy", False):
            print(_c("yellow", "  the agent is busy with a message from the viewer; wait for it to finish"))
            continue
        try:
            result = session.send(line, images=pending_images or None)
        except KeyboardInterrupt:
            print(_c("yellow", "\n  interrupted; the next message starts a fresh session with the current model"))
            session.close()
            pending_images = []
            continue
        except Exception as exc:
            print(_c("red", f"  turn failed: {type(exc).__name__}: {exc}"))
            session.close()
            pending_images = []
            continue
        pending_images = []
        if result.is_error:
            print(_c("red", result.text[-2000:]))
        elif not result.text.strip():
            print(_c("dim", "(no reply)"))
        if url and not opened and project.last_ok_revision() is not None:
            opened = True
            webbrowser.open(url)
    session.close()
    return 0


def _make_session(project: Project, model: str, effort: str | None, max_budget: float | None, printer, backend: str):
    from .agent.prompt import system_prompt
    if backend == "api":
        from .agent.api_backend import APISession
        return APISession(project, system_prompt(project), model=model, effort=effort, on_event=printer)
    from .agent.claude_cli import ClaudeCLISession
    return ClaudeCLISession(project, system_prompt(project), model=model, effort=effort, max_budget_usd=max_budget,
                            on_event=printer)


def _local_build(project: Project, note: str, render: bool = True) -> None:
    res = project.build(note=note, render=render)
    if res.ok:
        print(_c("green", f"  built revision {project.revision} in {res.seconds:.1f}s"))
        print(_c("dim", textwrap.indent(res.summary, "  ")))
    else:
        print(_c("red", "  build failed:\n") + textwrap.indent(res.error or "", "  "))


def _open_file(path: str) -> None:
    try:
        if sys.platform == "darwin":
            os.system(f"open '{path}'")
        elif sys.platform.startswith("linux"):
            os.system(f"xdg-open '{path}' >/dev/null 2>&1 &")
        else:
            os.startfile(path)  # type: ignore[attr-defined]
    except Exception:
        pass


def pick_backend(requested: str) -> str:
    if requested != "auto":
        return requested
    from .agent.claude_cli import find_claude_binary
    return "cli" if find_claude_binary() else "api"


# ----------------------------------------------------------------------------- the local website

def agent_info(backend: str) -> dict:
    """What the website says about Claude: whether it can run (an API key is set, the binary is there) and
    how requests are paid for. Asked again on every page load, so a key added on the page counts at once."""
    from .config import MEASURED_COST_PER_REQUEST_USD, key_source
    source = key_source()
    cost = MEASURED_COST_PER_REQUEST_USD
    if not source:
        return {"available": False, "backend": backend, "needs_key": True, "label": "No API key yet",
                "detail": "add an Anthropic API key to chat with Claude; the editor and the demo projects work without one",
                "cost_per_request": cost}
    if backend == "cli":
        from .agent.claude_cli import find_claude_binary
        if not find_claude_binary():
            return {"available": False, "backend": "cli", "needs_key": False, "label": "Claude Code binary missing",
                    "detail": "reinstall eitri (the binary comes with claude-agent-sdk) or use --backend api",
                    "cost_per_request": cost}
    where = "ANTHROPIC_API_KEY" if source == "environment" else "the key saved in eitri's settings"
    return {"available": True, "backend": backend, "needs_key": False, "key_source": source, "label": "Claude · API key",
            "detail": f"requests are billed to {where} (about ${cost:.2f} per request on average)",
            "cost_per_request": cost}


def _running_web(port: int) -> str | None:
    """The URL of a eitri website already serving on this port or one of the next few (where it goes
    when the port is taken), if there is one."""
    import json as _json
    import socket
    import urllib.request
    for candidate in range(port, port + 10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", candidate)) != 0:
                continue                                  # nothing listening there
        url = f"http://127.0.0.1:{candidate}/"
        try:
            with urllib.request.urlopen(url + "api/projects", timeout=1.5) as r:
                data = _json.loads(r.read())
            if isinstance(data, dict) and "projects" in data:
                return url
        except Exception:
            continue
    return None


def web_main(args) -> int:
    from .web.app import Workspace, start_web
    root = Path(args.workspace or os.environ.get("EITRI_WORKSPACE") or (Path.home() / "eitri-projects")).expanduser()
    running = _running_web(args.port)
    if running:
        print(f"eitri web is already running at {running} (stop it with Ctrl-C in its terminal to serve another folder)")
        if not args.no_open:
            webbrowser.open(running)
        return 0
    backend = pick_backend(args.backend)
    agent = agent_info(backend)
    printer = Printer(verbose=True) if args.verbose else None
    ws = Workspace(root, lambda project, on_event: _make_session(project, args.llm, args.effort, args.max_budget, on_event, backend),
                   printer=printer, agent=lambda: agent_info(backend))
    from .kernel import warm_worker_async
    warm_worker_async()                      # viewer edits build in this process: load the kernel now
    url, server = start_web(ws, port=args.port, background=True)
    print(_c("bold", "eitri web") + f"  {url}")
    print(_c("dim", f"projects: {ws.root}"))
    print(_c("dim" if agent["available"] else "yellow", f"Claude: {agent['label']} - {agent['detail']}"))
    print(_c("dim", "Ctrl-C stops the server"), flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print()
    finally:
        ws.close()
        server.shutdown()
    return 0


# ----------------------------------------------------------------------------- 3D-print export

def print_main(args) -> int:
    from .printexport import ProjectPrinter, export_plan, human_count, plate_picture, settings_from
    target = Path(args.project).expanduser().resolve()
    if target.is_file():
        project = Project(target.parent, name=target.stem)
    elif target.is_dir():
        project = Project(target)
        if not project.model_path.exists():
            print(f"no eitri model in {target}", file=sys.stderr)
            return 2
    else:
        print(f"no such project or model: {target}", file=sys.stderr)
        return 2
    st = settings_from({"bed": args.bed, "bed_height": args.height, "min_wall": args.min_wall, "spacing": args.spacing,
                        "include_bought": args.include_bought, "include": args.include, "exclude": args.exclude,
                        "orient": not args.no_orient})
    printer = ProjectPrinter(project)
    t0 = time.time()
    print(_c("dim", f"planning {project.name} for a {st.bed[0]:g} x {st.bed[1]:g} mm bed..."), flush=True)
    plan = printer.plan(st, pictures=False)
    print(_c("bold", f"{project.name} -> {human_count(plan)}") + _c("dim", f"  ({time.time() - t0:.1f} s)"))
    for q in plan.parts:
        if not q.included:
            print(_c("dim", f"  -  {q.name:<22} left out: {q.bought_reason or 'excluded'}"))
            continue
        flags = []
        if q.problem:
            flags.append(_c("red", q.problem))
        if q.thin:
            flags.append(_c("yellow", f"thin wall {q.min_wall:g} mm"))
        if q.overhang_risk in ("medium", "high"):
            flags.append(_c("yellow", f"{q.overhang_risk} overhang"))
        size = " x ".join(f"{v:g}" for v in q.size) if q.size else "?"
        plate = f"plate {q.plate}" if q.plate else "no plate"
        print(f"  {'!' if q.problem else '+'}  {q.name:<22} {plate:<8} {q.orientation:<12} {size:<22} " + "; ".join(flags))
    out = Path(args.out).expanduser() if args.out else printer.folder / "files"
    formats = tuple(f.strip() for f in args.formats.split(",") if f.strip() in ("stl", "3mf")) or ("stl", "3mf")
    res = export_plan(plan, out, formats=formats, stem=project.name)
    if args.pictures:
        for n in range(1, plan.plates + 1):
            plate_picture(plan, n, out / "plates" / f"{project.name}_plate{n}.png", theme="light", caption=True)
    print(f"wrote {res['parts_written']} part(s) and {plan.plates} plate(s) to {out}")
    if res.get("zip"):
        print(f"zip: {res['zip']}")
    for name, why in res["problems"].items():
        print(_c("red", f"  not written: {name}: {why}"))
    return 1 if res["problems"] else 0


def bom_main(args) -> int:
    """eitri bom <project>: build a harness project and print its bill of materials (and cut list)."""
    from .kernel import build_model
    target = Path(args.project).expanduser().resolve()
    if target.is_file():
        project = Project(target.parent, name=target.stem)
    elif target.is_dir():
        project = Project(target)
    else:
        print(f"no such project or model: {target}", file=sys.stderr)
        return 2
    if not project.model_path.exists():
        print(f"no eitri model in {target}", file=sys.stderr)
        return 2
    res = build_model(project.model_path, Path(args.out).expanduser() if args.out else project.out_dir, render=False)
    if not res.ok:
        print("BUILD FAILED\n" + (res.error or ""), file=sys.stderr)
        return 1
    if res.kind != "harness":
        print(f"{project.model_path.name} is a CAD model; `eitri bom` is for wire-harness projects "
              "(for a CAD assembly's parts list, use `eitri build` and read the report)", file=sys.stderr)
        return 2
    keys = ["csv"] if args.csv else ["bom_md"]
    if args.cutlist:
        keys += ["cutlist_csv"] if args.csv else ["cutlist_md"]
    for k in keys:
        print(Path(res.outputs[k]).read_text(encoding="utf-8"))
    print(_c("dim", "files: " + ", ".join(f"{k}: {v}" for k, v in res.outputs.items() if k in ("bom_md", "csv", "cutlist_md", "cutlist_csv", "wireviz"))))
    failed = [c for c in (res.harness_report or {}).get("checks", []) if c.get("severity") == "fail"]
    if failed:
        print(_c("yellow", f"note: {len(failed)} check(s) fail; this BOM is for a design that does not pass its checks (eitri build shows them)"))
    return 0


# ----------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eitri", description="A Claude-Code-like agent for parametric CAD.",
                                 epilog="With no command, eitri opens the local website in your browser "
                                        "(the same as `eitri web`); `eitri chat [project]` is the terminal REPL.")
    sub = ap.add_subparsers(dest="cmd")

    chat = sub.add_parser("chat", help="the terminal REPL (also: eitri <project folder>)")
    chat.add_argument("project", nargs="?", default=".", help="project folder (default: current directory)")
    chat.add_argument("-m", "--model-name", default=None, help="model file stem (default: the folder's model or 'model')")
    chat.add_argument("-p", "--prompt", default=None, help="one-shot: run this request and exit")
    chat.add_argument("--image", action="append", default=[], help="attach an image to the one-shot prompt")
    chat.add_argument("--llm", default="opus", help="LLM: opus (default), sonnet, or a full model id")
    chat.add_argument("--effort", default="high", help="low|medium|high|xhigh|max")
    chat.add_argument("--backend", default="auto", choices=["auto", "cli", "api"])
    chat.add_argument("--no-viewer", action="store_true")
    chat.add_argument("--max-budget", type=float, default=None, help="USD cap per session (cli backend)")
    chat.add_argument("-v", "--verbose", action="store_true", help="show tool results and thinking")

    b = sub.add_parser("build", help="build a model file and print its report (no LLM)")
    b.add_argument("model")
    b.add_argument("--set", action="append", default=[], help="name=value")
    b.add_argument("--config", default=None, help="apply a named parameter set from CONFIGS before building")
    b.add_argument("--formats", default="step,glb")
    b.add_argument("--out", default=None)
    b.add_argument("--open", action="store_true", help="open the review image")

    bm = sub.add_parser("bom", help="the bill of materials (and cut list) of a wire-harness project (no LLM)")
    bm.add_argument("project", nargs="?", default=".", help="project folder or harness model file (default: current directory)")
    bm.add_argument("--csv", action="store_true", help="print CSV instead of markdown")
    bm.add_argument("--cutlist", action="store_true", help="also print the cut list")
    bm.add_argument("--out", default=None, help="output folder (default: <project>/out)")

    v = sub.add_parser("view", help="render views of a model file (no LLM)")
    v.add_argument("model")
    v.add_argument("--views", default="iso,front,top,right")
    v.add_argument("--section", default=None)
    v.add_argument("--exploded", action="store_true")
    v.add_argument("--turntable", type=int, default=0, help="write an animated GIF with this many frames")
    v.add_argument("--out", default=None)

    d = sub.add_parser("drawing", help="engineering drawing (SVG + DXF) of a model file (no LLM)")
    d.add_argument("model")
    d.add_argument("--views", default="front,top,right,iso")
    d.add_argument("--sheet", default="A4", help="A4, A3 or letter")
    d.add_argument("--style", default="iso", help="iso or ansi dimension text")
    d.add_argument("--projection", default="third", help="third or first angle")
    d.add_argument("--pdf", action="store_true", help="also write a PDF (needs Playwright's Chromium)")
    d.add_argument("--dims", default=None, help='JSON list of your own dimensions, e.g. \'[{"view":"front","from":[0,0],"to":[40,0]}]\'')
    d.add_argument("--out", default=None, help="output folder (default: <model folder>/out)")

    s = sub.add_parser("serve", help="the browser viewer for one project, without chat (`eitri web` adds Claude)")
    s.add_argument("project", nargs="?", default=".")
    s.add_argument("--port", type=int, default=0)

    w = sub.add_parser("web", help="the local website: your projects, the 3D editor and chat with Claude, in the browser")
    w.add_argument("workspace", nargs="?", default=None,
                   help="folder of projects (default: $EITRI_WORKSPACE, else ~/eitri-projects)")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--no-open", action="store_true", help="don't open the browser")
    w.add_argument("--llm", default="opus", help="LLM: opus (default), sonnet, or a full model id")
    w.add_argument("--effort", default="high", help="low|medium|high|xhigh|max")
    w.add_argument("--backend", default="auto", choices=["auto", "cli", "api"])
    w.add_argument("--max-budget", type=float, default=None, help="USD cap per Claude session (cli backend)")
    w.add_argument("-v", "--verbose", action="store_true", help="also print the agents' activity in this terminal")

    c = sub.add_parser("cam", help="2.5-D toolpaths and G-code for a part of a model file (no LLM)")
    c.add_argument("model")
    c.add_argument("--part", default=None, help="part name (default: the first part)")
    c.add_argument("--ops", default="face,contour,pocket,drill", help="comma list or a JSON list of operations: face, contour, pocket, drill, chamfer")
    c.add_argument("--post", default="grbl", help="grbl or linuxcnc")
    c.add_argument("--material", default=None, help="feeds for this material (default: the part's)")
    c.add_argument("--safe-z", type=float, default=5.0)
    c.add_argument("--out", default=None, help="output folder (default: <model folder>/out)")

    cf = sub.add_parser("cut", help="a laser/waterjet/plasma cut file (DXF or SVG) of a flat part (no LLM)")
    cf.add_argument("model")
    cf.add_argument("--part", default=None)
    cf.add_argument("--kerf", type=float, default=0.2)
    cf.add_argument("--fmt", default="dxf", choices=["dxf", "svg"])
    cf.add_argument("--flat", action="store_true", help="cut the sheet-metal flat pattern instead of the part")
    cf.add_argument("--out", default=None)

    pp = sub.add_parser("print-prep", help="print orientation per part and the parts arranged on a bed (no LLM)")
    pp.add_argument("model")
    pp.add_argument("--bed", default="220x220", help="bed size WxD in mm")
    pp.add_argument("--layer", type=float, default=0.2)
    pp.add_argument("--no-orient", action="store_true", help="keep the parts as modelled")
    pp.add_argument("--parts", default=None, help="comma list of part names (default: all)")
    pp.add_argument("--out", default=None)

    pr = sub.add_parser("print", help="export for 3D printing: the parts to print, oriented, on plates, as STL + 3MF + zip (no LLM)")
    pr.add_argument("project", nargs="?", default=".", help="project folder or model .py (default: current folder)")
    pr.add_argument("--bed", default="220x220", help="bed size WxD in mm (default 220x220)")
    pr.add_argument("--height", type=float, default=250.0, help="build height in mm (default 250)")
    pr.add_argument("--min-wall", type=float, default=0.8, help="flag walls thinner than this, mm (default 0.8)")
    pr.add_argument("--spacing", type=float, default=5.0, help="gap between parts on a plate, mm")
    pr.add_argument("--include-bought", action="store_true", help="also print screws, nuts, bearings and metal parts")
    pr.add_argument("--include", default=None, help="comma list of parts to print even if they look bought")
    pr.add_argument("--exclude", default=None, help="comma list of parts to leave out")
    pr.add_argument("--no-orient", action="store_true", help="keep each part as modelled")
    pr.add_argument("--formats", default="stl,3mf")
    pr.add_argument("--out", default=None, help="output folder (default: <project>/out/print/files)")
    pr.add_argument("--pictures", action="store_true", help="also write a picture of each plate")

    doc = sub.add_parser("doctor", help="check that this install works (kernel, test build, Claude binary, API key)")
    doc.add_argument("--online", action="store_true", help="also check the API key with Anthropic (one free call)")
    doc.add_argument("--json", action="store_true", help="machine-readable output")

    k = sub.add_parser("key", help="the Anthropic API key eitri uses: set, show or forget")
    k.add_argument("action", choices=["set", "show", "forget"])
    k.add_argument("key", nargs="?", default=None, help="the key (default: read it hidden from the terminal, or from stdin)")
    k.add_argument("--no-check", action="store_true", help="save without checking it with Anthropic")

    argv = list(sys.argv[1:] if argv is None else argv)
    commands = ("chat", "build", "bom", "view", "drawing", "serve", "web", "cam", "cut", "print-prep", "print", "doctor", "key",
                "-h", "--help")
    if not argv:
        argv = ["web"]                   # plain `eitri`: the website, in the browser
    elif argv[0] not in commands:
        argv = ["chat"] + argv           # `eitri <folder>` / `eitri -p "..."`: the terminal REPL
    args = ap.parse_args(argv)

    if args.cmd == "print":
        return print_main(args)
    if args.cmd == "doctor":
        from .doctor import run_doctor
        return run_doctor(online=args.online, as_json=args.json)
    if args.cmd == "key":
        from .doctor import run_key
        return run_key(args.action, args.key, check=not args.no_check)

    if args.cmd == "build":
        from .kernel import build_model
        model = Path(args.model)
        out = Path(args.out) if args.out else model.parent / "out"
        params = dict(kv.split("=", 1) for kv in args.set)
        if args.config:
            from .model import load_model
            cfg = load_model(model).configs
            if args.config not in cfg:
                print(f"unknown configuration {args.config!r}; CONFIGS has: {', '.join(cfg) or 'none'}", file=sys.stderr)
                return 2
            params = {**{k: str(v) for k, v in cfg[args.config].items()}, **params}
        res = build_model(model, out, params=params, formats=tuple(args.formats.split(",")))
        if res.ok:
            print(res.summary)
            print("\n".join(f"{k}: {v}" for k, v in res.outputs.items()))
            if args.open and res.image:
                _open_file(res.image)
            return 0
        print("BUILD FAILED\n" + (res.error or ""), file=sys.stderr)
        return 1

    if args.cmd == "bom":
        return bom_main(args)

    if args.cmd == "view":
        from . import _render_cli
        model = Path(args.model)
        out = args.out or str(model.parent / "out" / "view.png")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        argv2 = ["--model", str(model), "--out", out, "--views", args.views]
        if args.section:
            argv2 += ["--section", args.section]
        if args.exploded:
            argv2.append("--exploded")
        if args.turntable:
            argv2 += ["--turntable", str(args.turntable)]
        rc = _render_cli.main(argv2)
        if rc == 0:
            print(out)
            _open_file(out)
        return rc

    if args.cmd == "drawing":
        from .drawing import drawing_dxf, drawing_svg
        from .model import load_model
        model = Path(args.model)
        out_dir = Path(args.out) if args.out else model.parent / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        views = tuple(v.strip() for v in args.views.split(",") if v.strip())
        parts = load_model(model).build()
        svg_path = out_dir / f"{model.stem}.drawing.svg"
        import json as _json
        svg = drawing_svg(parts, model.stem, views=views, sheet=args.sheet, style=args.style, projection=args.projection,
                          dims=_json.loads(args.dims) if args.dims else None)
        svg_path.write_text(svg, encoding="utf-8")
        dxf_path = drawing_dxf(parts, model.stem, out_dir / f"{model.stem}.dxf", views=tuple(v for v in views if v != "iso"), sheet=args.sheet)
        print(svg_path)
        print(dxf_path)
        if args.pdf:
            from .drawing import drawing_pdf
            print(drawing_pdf(svg, out_dir / f"{model.stem}.drawing.pdf", args.sheet))
        _open_file(str(svg_path))
        return 0

    if args.cmd in ("cam", "cut", "print-prep"):
        from .model import load_model
        model = Path(args.model)
        out_dir = Path(args.out) if args.out else model.parent / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        parts = load_model(model).build()
        if args.cmd == "print-prep":
            from . import additive as A
            from .kernel import export_parts
            from .model import Part, Parts
            w, _, d = args.bed.lower().partition("x")
            bed = (float(w), float(d or w))
            names = [n.strip() for n in args.parts.split(",")] if args.parts else None
            sel = Parts([p for p in parts if not names or p.name in names])
            print(A.print_report(sel, bed, layer=args.layer))
            placed, placements, skipped = A.arrange(sel, bed, orient=not args.no_orient)
            plate = Parts([Part(n, s, next(p.color for p in sel if p.name == n)) for n, s in placed])
            if len(plate):
                out = export_parts(plate, out_dir, f"{model.stem}.bed", ("stl", "3mf"))
                print("\n".join(str(v) for v in out.values()))
            return 0
        name = args.part or parts.names[0]
        if name not in parts.names:
            print(f"no part {name!r}; parts: {', '.join(parts.names)}", file=sys.stderr)
            return 2
        shape = parts[name]
        from . import cam as C
        if args.cmd == "cam":
            import json as _json
            ops = _json.loads(args.ops) if args.ops.strip().startswith("[") else [o.strip() for o in args.ops.split(",") if o.strip()]
            mat = args.material or getattr(next(p for p in parts if p.name == name), "material", None)
            job = C.plan(shape, name, ops, post=args.post, material=mat, safe_z=args.safe_z)
            gpath = out_dir / f"{name}.gcode"
            gpath.write_text(C.gcode(job), encoding="utf-8")
            png = C.preview_png(job, shape, out_dir / f"{name}.toolpaths.png")
            print(job.summary())
            print(gpath)
            print(png)
            return 0
        if args.flat:
            states = getattr(parts, "sheet_states", {}) or {}
            if name not in states:
                print(f"{name} is not a sheet-metal part", file=sys.stderr)
                return 2
            from . import sheetmetal as SM
            shape = SM.flat_pattern(states[name])[0]
        path = C.cut_file(shape, out_dir / f"{name}.cut.{args.fmt}", kerf=args.kerf, fmt=args.fmt)
        print(path)
        return 0

    if args.cmd == "web":
        return web_main(args)

    if args.cmd == "serve":
        from .viewer.server import start_viewer
        project = Project(args.project)
        if project.ensure_model():
            project.build(note="blank design", render=False)
        url = start_viewer(project, port=args.port, background=False)
        return 0

    project = Project(args.project, name=args.model_name)
    backend = pick_backend(args.backend)
    if args.prompt:
        printer = Printer(verbose=args.verbose)
        session = _make_session(project, args.llm, args.effort, args.max_budget, printer, backend)
        result = session.send(args.prompt, images=args.image or None)
        session.close()
        if project.last_ok_revision():
            res = project.last_ok_revision()
            print("\n".join(f"{k}: {v}" for k, v in res.outputs.items()))
        return 1 if result.is_error else 0
    return repl(project, args.llm, args.effort, not args.no_viewer, args.verbose, args.max_budget, backend)


if __name__ == "__main__":
    sys.exit(main())
