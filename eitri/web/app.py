"""eitri's local website: one address (http://127.0.0.1:8765 by default) with

  /                 the projects home page: describe a part to start one, open, import, trash
  /api/projects     GET the project list (+ agent status and plan usage) · POST {name, prompt} create one
  /api/import       POST {filename, data}: a STEP / IGES / mesh file or a model .py as a new project
  /api/link         POST {path}: list an existing project folder kept elsewhere
  /api/trash        POST {id}: move a project to <workspace>/.trash (a linked folder is only unlisted)
  /api/rename       POST {id, title}: the name shown for a project (kept in <project>/.eitri/web.json)
  /thumb/<id>       the project's thumbnail
  /p/<id>/...       the project's 3D editor and its whole API (the viewer server's routes)

Each project gets its own agent hub, so every project has its own conversation with Claude (headless
Claude Code on your plan, or the API with a key), kept in <project>/.eitri/chat.jsonl. Idle Claude
sessions are closed after a while to free memory; the next message starts a new one with the recent
conversation. The server only answers this machine (see viewer.server.local_request).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from ..project import Project
from ..viewer.server import _free_port, local_request, make_handler

_HERE = Path(__file__).parent
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
IMPORT_KINDS = {".step": "step", ".stp": "step", ".iges": "iges", ".igs": "iges",
                ".stl": "mesh", ".obj": "mesh", ".3mf": "mesh", ".ply": "mesh"}
BLANK = "from eitri import *\n\nPARAMS = dict()\n\ndef build(p):\n    return {}\n"
MAX_IMPORT = 200 * 1024 * 1024

SessionFactory = Callable[[Project, Callable[[str, dict], None]], Any]


def slugify(text: str, fallback: str = "design") -> str:
    words = re.findall(r"[A-Za-z0-9]+", (text or "").lower())
    while len(words) > 1 and words[0] in ("a", "an", "the"):
        words.pop(0)
    return "-".join(words[:6])[:48].strip("-") or fallback


def title_from(text: str, limit: int = 60) -> str:
    """A readable project title from a name or the first line of a prompt."""
    line = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    if len(line) > limit:
        line = line[:limit].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    return line[:1].upper() + line[1:]


def project_title(folder: Path) -> str | None:
    try:
        title = json.loads((folder / ".eitri" / "web.json").read_text(encoding="utf-8")).get("title")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    return str(title) if title else None


def set_project_title(folder: Path, title: str) -> None:
    f = folder / ".eitri" / "web.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data["title"] = title
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")


def model_stem(slug: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", slug).strip("_") or "model"
    return "part_" + stem if stem[0].isdigit() else stem


def is_project(folder: Path) -> bool:
    if (folder / "eitri.json").is_file():
        return True
    for f in folder.glob("*.py"):
        try:
            if "def build(" in f.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


class ProjectApp:
    """One project served under /p/<id>/: its Project, build lock, agent hub and request handler class."""

    def __init__(self, ws: "Workspace", pid: str, folder: Path):
        from ..agent.hub import AgentHub
        self.pid = pid
        self.project = Project(folder)
        if self.project.ensure_model():
            self.project.build(note="blank design", render=False)
        self.lock = threading.Lock()
        project = self.project
        self.hub = AgentHub(project, lambda on_event: ws.make_session(project, on_event), printer=ws.printer, history=True)
        self.handler_cls = make_handler(project, self.lock, self.hub, home="/", agent=lambda: ws.agent)

    def close(self) -> None:
        self.hub.close()


class Workspace:
    """The folder of projects the website shows (plus linked project folders kept elsewhere)."""

    def __init__(self, root: str | Path, make_session: SessionFactory, printer=None,
                 agent: dict | Callable[[], dict] | None = None, idle_close: float = 20 * 60):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.make_session = make_session
        self.printer = printer
        self._agent = agent or {"available": True, "label": "Claude"}
        self.idle_close = idle_close
        self.apps: dict[str, ProjectApp] = {}
        self._lock = threading.RLock()
        self.config_path = self.root / ".eitri-web.json"
        self._stop = threading.Event()
        threading.Thread(target=self._reaper, daemon=True).start()
        self._install_demos()

    # ------------------------------------------------------------------ demos
    DEMO_PREFIX = "demo-"

    def _install_demos(self) -> None:
        """Extract the bundled demo projects into <root>/.demos (only when missing or out of date)."""
        from ..demos import install, list_demos
        self.demos: dict[str, dict] = {}
        for d in list_demos():
            try:
                install(self.root, d["id"])
                self.demos[self.DEMO_PREFIX + d["id"]] = d
            except Exception:  # pragma: no cover - a broken bundle must not stop the site
                continue

    def demo_list(self) -> list[dict]:
        from ..demos import demo_folder
        out = []
        for pid, d in self.demos.items():
            folder = demo_folder(self.root, d["id"])
            app = self.apps.get(pid)
            out.append({"id": pid, "demo": d["id"], "title": d["title"], "blurb": d.get("blurb", ""),
                        "outcome": d.get("outcome", ""), "recorded": d.get("recorded", {}), "prompt": d.get("prompt", ""),
                        "thumb": self.thumb(pid, folder) is not None, "busy": bool(app and app.hub.busy),
                        "url": f"/p/{pid}/"})
        return out

    def replay(self, pid: str, speed: float = 1.0) -> dict:
        """Play the demo's recorded Claude session in its chat, stepping the model through its revisions.
        `speed` scales the (already compressed) pauses: 2 plays twice as fast; clamped to 0.25-8."""
        from ..agent.replay import ReplaySession, model_code
        from ..demos import demo_folder, install, is_modified, load_demo, reveal
        meta = self.demos.get(pid)
        if meta is None:
            raise KeyError(pid)
        app = self.app(pid)
        if app.hub.busy:
            raise ValueError("Claude (or a replay) is already working in this project")
        folder = demo_folder(self.root, meta["id"])
        if is_modified(folder):                           # edited since: back to the shipped demo first
            self.reset_demo(pid)
            app = self.app(pid)
        demo = load_demo(meta["id"])
        project = app.project
        rec = demo.get("recorded") or {}
        minutes = (rec.get("seconds") or 0) / 60
        app.hub._record("divider", {"text": f"Replay of a recorded session ({minutes:.0f} min, played compressed) · "
                                            "no API calls are made", "replay": True}, source="system")
        session = ReplaySession(demo, app.hub._on_event, on_revision=lambda n: reveal(project, n),
                                code_of=model_code(folder, demo["model"]), speed=min(max(float(speed), 0.25), 8.0))
        app.hub.send_async(demo["prompt"], source="replay", session=session)
        return {"ok": True, "replaying": True}

    def reset_demo(self, pid: str) -> dict:
        """Put the demo back exactly as shipped (edits and builds made in it are discarded)."""
        from ..demos import install
        meta = self.demos.get(pid)
        if meta is None:
            raise KeyError(pid)
        with self._lock:
            app = self.apps.pop(pid, None)
            if app is not None:
                app.hub.history_path = None           # a late event from a stopping replay must not write into the folder
                if app.hub.busy:
                    app.hub.interrupt()
                    deadline = time.time() + 15
                    while app.hub.busy and time.time() < deadline:
                        time.sleep(0.05)
                app.close()
            install(self.root, meta["id"], force=True)
        return {"ok": True}

    @property
    def agent(self) -> dict:
        """Whether Claude can run now (asked afresh: a key added on the page counts at once)."""
        return dict(self._agent() if callable(self._agent) else self._agent)

    # ------------------------------------------------------------------ config
    def _config(self) -> dict:
        try:
            cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
            return cfg if isinstance(cfg, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_config(self, cfg: dict) -> None:
        self.config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    def _links(self) -> dict[str, str]:
        links = self._config().get("links", {})
        return links if isinstance(links, dict) else {}

    # ------------------------------------------------------------------ projects
    def folders(self) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for d in sorted(self.root.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and _ID.match(d.name) and is_project(d):
                out[d.name] = d
        for pid, path in self._links().items():
            if _ID.match(pid) and Path(path).is_dir():
                out[pid] = Path(path)
        return out

    def folder_of(self, pid: str) -> Path | None:
        """A project's folder: a listed project or an installed demo."""
        if pid in getattr(self, "demos", {}):
            from ..demos import demo_folder
            return demo_folder(self.root, self.demos[pid]["id"])
        return self.folders().get(pid)

    def summary(self, pid: str, folder: Path) -> dict:
        try:
            state = json.loads((folder / "eitri.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        stem = Path(state.get("model") or "").stem
        model = folder / (state.get("model") or "")
        times = [p.stat().st_mtime for p in (model, folder / "eitri.json", folder / ".eitri" / "chat.jsonl") if p.is_file()]
        history = state.get("history") or []
        last_ok = next((h for h in reversed(history) if h.get("ok")), None)
        app = self.apps.get(pid)
        name = stem or folder.name
        fallback = folder.name if name in ("model", "") else name           # a generic model.py: the folder says more
        return {"id": pid, "name": name, "title": project_title(folder) or fallback.replace("_", " ").replace("-", " "), "folder": str(folder), "linked": pid in self._links(),
                "modified": max(times) if times else folder.stat().st_mtime, "revision": state.get("revision", 0),
                "note": (last_ok or {}).get("note", ""), "last_message": self._last_message(folder),
                "thumb": self.thumb(pid, folder) is not None, "busy": bool(app and app.hub.busy),
                "url": f"/p/{pid}/"}

    @staticmethod
    def _last_message(folder: Path) -> str:
        f = folder / ".eitri" / "chat.jsonl"
        try:
            with f.open("rb") as fh:
                fh.seek(max(0, f.stat().st_size - 65536))
                lines = fh.read().decode("utf-8", errors="ignore").splitlines()
        except OSError:
            return ""
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("kind") == "user":
                return str(e.get("data", {}).get("text", ""))[:140]
        return ""

    def list(self) -> list[dict]:
        out = [self.summary(pid, folder) for pid, folder in self.folders().items()]
        out.sort(key=lambda s: s["modified"], reverse=True)
        return out

    def thumb(self, pid: str, folder: Path | None = None) -> Path | None:
        folder = folder or self.folder_of(pid)
        if folder is None:
            return None
        t = folder / ".eitri" / "thumb.png"
        if t.is_file():
            return t
        try:
            stem = Path(json.loads((folder / "eitri.json").read_text(encoding="utf-8")).get("model") or "").stem
        except (OSError, json.JSONDecodeError):
            stem = ""
        png = folder / "out" / f"{stem}.png"
        return png if stem and png.is_file() else None

    def _new_folder(self, title: str) -> tuple[str, Path]:
        slug = slugify(title)
        base, k = slug, 2
        links = self._links()
        while (self.root / slug).exists() or slug in links:
            slug, k = f"{base}-{k}", k + 1
        return slug, self.root / slug

    def create(self, name: str | None = None, prompt: str | None = None) -> tuple[str, str | None]:
        """A new project (named after `name`, else the first words of `prompt`); with a prompt, Claude
        starts on it right away. Returns (id, a warning when Claude could not be started)."""
        prompt = (prompt or "").strip()
        with self._lock:
            pid, folder = self._new_folder((name or "").strip() or prompt or "design")
            project = Project(folder, name=model_stem(pid))
            project.write_model(BLANK)
            set_project_title(folder, title_from((name or "").strip() or prompt) or "Untitled")
            project.build(note="blank design", render=False)
        if not prompt:
            return pid, None
        agent = self.agent
        if not agent.get("available", True):
            return pid, f"Claude is not available, so it did not start: {agent.get('detail', '')}"
        self.app(pid).hub.send_async(prompt, source="viewer")
        return pid, None

    def import_file(self, filename: str, data: bytes) -> tuple[str, str]:
        """A new project from a file: a model .py becomes the model; a STEP / IGES / mesh file is copied in
        and inserted as a component (so direct edits and the agent can work on top of it). Returns
        (id, build note)."""
        name = Path(filename or "").name
        suffix = Path(name).suffix.lower()
        if suffix != ".py" and suffix not in IMPORT_KINDS:
            raise ValueError("import a STEP, IGES, STL, OBJ, 3MF or PLY file, or a eitri model (.py)")
        if not data or len(data) > MAX_IMPORT:
            raise ValueError("the file is empty or too large")
        if suffix == ".py":
            text = data.decode("utf-8", errors="replace")
            if "def build(" not in text:
                raise ValueError("that .py file is not a eitri model (it has no build(p) function)")
        with self._lock:
            pid, folder = self._new_folder(Path(name).stem)
            stem = model_stem(pid)
            project = Project(folder, name=stem)
            set_project_title(folder, title_from(Path(name).stem.replace("_", " ").replace("-", " ")) or stem)
            if suffix == ".py":
                project.write_model(text)
            else:
                safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or f"import{suffix}"
                src = folder / "inputs" / safe
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_bytes(data)
                project.write_model(BLANK)
                project.set_edits([{"op": "insert", "kind": IMPORT_KINDS[suffix], "path": str(src.resolve()), "name": stem}])
            res = project.build(note=f"imported {name}", render=False)
        if not res.ok:
            return pid, f"imported, but the build failed: {(res.error or '').strip().splitlines()[-1] if res.error else ''}"
        bad = [n.get("detail") for n in res.edit_notes if not n.get("ok")]
        return pid, ("imported, but: " + bad[0]) if bad else f"imported {name}"

    def link(self, path: str) -> str:
        folder = Path(path or "").expanduser()
        try:
            folder = folder.resolve()
        except OSError:
            raise ValueError(f"no such folder: {path}")
        if not folder.is_dir():
            raise ValueError(f"no such folder: {path}")
        if folder.parent == self.root and _ID.match(folder.name):
            if not is_project(folder):
                raise ValueError("that folder is not a eitri project (no eitri.json or model with build(p))")
            return folder.name
        if not is_project(folder):
            raise ValueError("that folder is not a eitri project (no eitri.json or model with build(p))")
        with self._lock:
            cfg = self._config()
            links = cfg.get("links") if isinstance(cfg.get("links"), dict) else {}
            for pid, p in links.items():
                if Path(p) == folder:
                    return pid
            pid = f"{slugify(folder.name)}-{hashlib.sha1(str(folder).encode()).hexdigest()[:6]}"
            links[pid] = str(folder)
            cfg["links"] = links
            self._save_config(cfg)
        return pid

    def rename(self, pid: str, title: str) -> str:
        folder = self.folders().get(pid)
        if folder is None:
            raise KeyError(pid)
        title = title_from(title, limit=80)
        if not title:
            raise ValueError("the name is empty")
        set_project_title(folder, title)
        return title

    def trash(self, pid: str) -> dict:
        if pid in self.demos:
            raise ValueError("a demo project cannot be moved to the trash; use Reset demo to undo changes to it")
        folder = self.folders().get(pid)
        if folder is None:
            raise KeyError(pid)
        with self._lock:
            app = self.apps.pop(pid, None)
            if app is not None:
                app.close()
            cfg = self._config()
            links = cfg.get("links") if isinstance(cfg.get("links"), dict) else {}
            if pid in links:                             # a folder kept elsewhere is only unlisted
                del links[pid]
                cfg["links"] = links
                self._save_config(cfg)
                return {"unlisted": str(folder)}
            dest = self.root / ".trash" / f"{pid}-{time.strftime('%Y%m%d-%H%M%S')}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder), str(dest))
            return {"trashed": str(dest)}

    def app(self, pid: str) -> ProjectApp:
        with self._lock:
            if pid in self.apps:
                return self.apps[pid]
            folder = self.folder_of(pid)
            if folder is None:
                raise KeyError(pid)
            if pid in getattr(self, "demos", {}):       # a demo whose install is missing or broken: put it back, never
                from ..demos import install, is_installed   # open it as an empty (blank) project
                if not is_installed(self.root, self.demos[pid]["id"]):
                    install(self.root, self.demos[pid]["id"], force=True)
            app = ProjectApp(self, pid, folder)
            self.apps[pid] = app
            return app

    def usage(self) -> dict | None:
        """The newest plan-usage report from any project's Claude session (remembered across restarts)."""
        seen = [a.hub.rate_limit for a in list(self.apps.values()) if a.hub.rate_limit]
        if seen:
            newest = max(seen, key=lambda u: u.get("seen", 0))
            cfg = self._config()
            if (cfg.get("usage") or {}).get("seen") != newest.get("seen"):
                cfg["usage"] = newest
                try:
                    self._save_config(cfg)
                except OSError:
                    pass
            return newest
        u = self._config().get("usage")
        return u if isinstance(u, dict) else None

    # ------------------------------------------------------------------ housekeeping
    def _reaper(self) -> None:
        while not self._stop.wait(60):
            for app in list(self.apps.values()):
                try:
                    app.hub.close_if_idle(self.idle_close)
                except Exception:
                    pass

    def close(self) -> None:
        self._stop.set()
        for app in list(self.apps.values()):
            app.close()


def make_web_handler(ws: Workspace):
    class WebHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def _delegate(self, method: str) -> None:
            raw, qs, query = self.path.partition("?")
            pid, sep, tail = raw[len("/p/"):].partition("/")
            pid = unquote(pid)
            if not sep:                                   # /p/<id> -> /p/<id>/ so the page's relative URLs resolve
                self._send(301, b"", "text/plain", {"Location": f"/p/{pid}/" + (qs + query if qs else "")})
                return
            try:
                app = ws.app(pid)
            except KeyError:
                self._send(404, b"no such project", "text/plain")
                return
            self.path = "/" + tail + (qs + query if qs else "")
            self.__class__ = app.handler_cls              # the project's own handler serves the rest of this request
            try:
                getattr(self, method)()
            finally:
                self.__class__ = WebHandler

        def do_GET(self):
            if not local_request(self, post=False):
                self._send(403, b"eitri only answers requests from this machine", "text/plain")
                return
            path = self.path.partition("?")[0]
            if path.startswith(("/vendor/", "/assets/")):
                from ..static import serve
                if not serve(self, path):
                    self._send(404, b"not found", "text/plain")
            elif path.startswith("/p/"):
                self._delegate("do_GET")
            elif path in ("/", "/index.html"):
                self._send(200, (_HERE / "home.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/projects":
                self._json({"projects": ws.list(), "demos": ws.demo_list(), "workspace": str(ws.root), "agent": ws.agent,
                            "usage": ws.usage()})
            elif path == "/api/setup":
                self._json(setup_status(ws))
            elif path.startswith("/thumb/"):
                f = ws.thumb(unquote(path[len("/thumb/"):]))
                if f is None:
                    self._send(404, b"no thumbnail yet", "text/plain")
                else:
                    self._send(200, f.read_bytes(), "image/png")
            elif path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if not local_request(self, post=True):
                self._send(403, b"eitri only answers requests from its own pages on this machine", "text/plain")
                return
            path = self.path.partition("?")[0]
            m = re.match(r"^/p/([^/]+)/(replay|demo/reset)$", path)
            if m and unquote(m.group(1)) in ws.demos:      # demo actions: the workspace owns the demo folders
                try:
                    pid = unquote(m.group(1))
                    if m.group(2) == "replay":
                        n = int(self.headers.get("Content-Length", "0") or 0)
                        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                        self._json(ws.replay(pid, speed=float(body.get("speed") or 1.0)))
                    else:
                        self._json(ws.reset_demo(pid))
                except (KeyError, ValueError, TypeError) as exc:
                    self._json({"ok": False, "error": str(exc)}, 400)
                return
            if path.startswith("/p/"):
                self._delegate("do_POST")                  # the project handler reads the body
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except json.JSONDecodeError:
                self._json({"ok": False, "error": "bad JSON"}, 400)
                return
            try:
                if path == "/api/projects":
                    pid, warning = ws.create(payload.get("name"), payload.get("prompt"))
                    self._json({"ok": True, "id": pid, "url": f"/p/{pid}/", "warning": warning})
                elif path == "/api/import":
                    data = base64.b64decode(str(payload.get("data") or "").split(",")[-1] or b"")
                    pid, note = ws.import_file(str(payload.get("filename") or ""), data)
                    self._json({"ok": True, "id": pid, "url": f"/p/{pid}/", "note": note})
                elif path == "/api/link":
                    pid = ws.link(str(payload.get("path") or ""))
                    self._json({"ok": True, "id": pid, "url": f"/p/{pid}/"})
                elif path == "/api/trash":
                    self._json({"ok": True, **ws.trash(str(payload.get("id") or ""))})
                elif path == "/api/setup":
                    self._json(save_key(ws, str(payload.get("key") or "")))
                elif path == "/api/setup/forget":
                    from ..config import forget_api_key
                    forget_api_key()
                    self._json({"ok": True, **setup_status(ws)})
                elif path == "/api/rename":
                    self._json({"ok": True, "title": ws.rename(str(payload.get("id") or ""), str(payload.get("title") or ""))})
                else:
                    self._send(404, b"not found", "text/plain")
            except KeyError as exc:
                self._json({"ok": False, "error": f"no such project: {exc}"}, 404)
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:  # pragma: no cover - reported to the page
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    return WebHandler


def setup_status(ws: Workspace) -> dict:
    """The key panel's state: is there a key (masked), where it lives, what requests cost."""
    from ..config import MEASURED_COST_PER_REQUEST_USD, api_key, config_path, key_source, mask
    return {"has_key": api_key() is not None, "source": key_source(), "masked": mask(api_key()),
            "config_path": str(config_path()), "cost_per_request": MEASURED_COST_PER_REQUEST_USD, "agent": ws.agent}


def save_key(ws: Workspace, key: str) -> dict:
    """Validate a pasted key with one free API call, then keep it in the user config folder."""
    from ..config import save_api_key, validate_api_key
    ok, message = validate_api_key(key)
    if not ok:
        return {"ok": False, "error": message, **setup_status(ws)}
    save_api_key(key)
    return {"ok": True, "message": message, **setup_status(ws)}


def start_web(ws: Workspace, port: int = 8765, background: bool = True) -> tuple[str, ThreadingHTTPServer]:
    """Serve the website; returns (url, server). background=False blocks until Ctrl-C."""
    if port:                                             # the asked port, else one of the next few (a stable URL), else any
        for candidate in range(port, port + 10):
            if _free_port(candidate) == candidate:
                port = candidate
                break
        else:
            port = _free_port(0)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_web_handler(ws))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if background:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return url, server
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ws.close()
    return url, server
