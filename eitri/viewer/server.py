"""A tiny local HTTP server for the viewer page.

GET  /                page            GET /view.glb      pickable model (mm, Z up)
GET  /state.json      project state   GET /topology.json what every face/edge/hole is
GET  /model.glb       plain GLB       GET /image.png     the agent's review sheet
GET  /events?since=N  agent events    GET /revisions     build history
GET  /interference    overlapping part pairs + a GLB of the overlap volumes
GET  /download?fmt=step|stl|3mf|glb|obj|iges|svg|dxf|pdf|csv   the current model as a file (svg/dxf/pdf: an engineering drawing, &style=iso|ansi &projection=third|first; csv: the bill of materials;
     &views=front,top,right,iso &sheet=A4 &inline=1 to show rather than download)
POST /set {params}    rebuild         POST /edit {edit}  add a direct edit and rebuild
POST /edits/update {index, edit}      replace one edit (edit feature / suppress)
POST /edits/move {index, to}          reorder the timeline
POST /edits/remove {index}            POST /edits/clear  POST /restore {number}
POST /undo            back to the previous different model      POST /redo  forward again
POST /measure {ids}   distance / angle between two entities
POST /sketch/solve {sketch}            solve a 2D sketch's constraints; returns status + preview polylines
POST /sketch/op {sketch, op, args}     apply a sketch editing op (trim, offset, fillet, constrain, dimension, ...)
POST /chat {text, selection, images}  send a message (with the selection and uploaded images) to the agent
POST /stop            stop the agent's turn (the conversation is kept)    POST /newchat  start a new conversation
POST /upload {name, data}             save a base64 image into the project's inputs/ (to attach to a message)
POST /thumb {data}    the project's thumbnail (a PNG the page renders)     GET /file?path=  an image inside the project
A change to EDITS that fails to build is rolled back and the error returned. Every URL the page uses is
relative, so the same page works at / (one project) and under /p/<id>/ (the local website, eitri web).
Requests must come from this machine: a Host other than localhost, or a POST from another origin, is refused.

The page polls state.json; the model reloads when the revision changes, so the
agent's builds and your own edits appear live.
"""
from __future__ import annotations

import json
from urllib.parse import quote, unquote
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..project import Project

_HERE = Path(__file__).parent


def _state(project: Project, hub=None, home: str | None = None) -> dict:
    revision, history = project.snapshot()
    mine = project.revisions_of(history=history)            # the current model file's builds (a project may hold several files)
    ok_hist = [h for h in mine if h.ok]
    rev = ok_hist[-1] if ok_hist else None
    summary = ""
    parts: list[dict] = []
    report = None
    edit_notes: list = []
    joints: list = []
    anchors: list = []
    harness: dict | None = None
    domain: dict | None = None
    groups: dict = {}
    sheets: dict = {}
    flats: dict = {}
    forms: dict = {}
    if rev is not None:
        summary = rev.summary
        data = project.build_result(rev, history)
        if data is not None:
            try:
                report = data.get("report")
                edit_notes = data.get("edit_notes", [])
                joints = data.get("joints", []) or []
                anchors = data.get("anchors", []) or []
                groups = data.get("groups", {}) or {}
                sheets = data.get("sheets", {}) or {}
                flats = data.get("flats", {}) or {}
                forms = data.get("forms", {}) or {}
                harness = data.get("harness") if data.get("kind") == "harness" else None
                domain = data.get(data["kind"]) if data.get("kind") not in (None, "cad") else None
            except Exception:
                report = None
        if report:
            parts = [{"name": p["name"], "color": p["color"], "volume": p["volume"], "size": p["size"],
                      "valid": p["valid"], "material": p.get("material"), "mass_g": p.get("mass_g"), "solids": p.get("solids"), "group": p.get("group") or groups.get(p["name"])} for p in report.get("parts", [])]
    last = mine[-1] if mine else None
    from ..edits import describe
    edits = project.edits()
    checks = (report or {}).get("checks", []) if report else []
    return {
        "name": project.name,
        "file": project.model_path.name,
        "models": _models(project, history, checks),
        "kind": project.kind,
        "harness": harness,
        "domain": domain,
        "net_stats": (report or {}).get("nets", []) if harness else [],
        "revision": revision,
        "ok_revision": rev.number if rev else 0,
        "last_ok": bool(last and last.ok),
        "last_note": last.note if last else "",
        "last_error": (None if (last is None or last.ok) else (last.summary or "build failed")[:600]),
        "params": project.current_params(),
        "configs": list(project.configs()),
        "parts": parts,
        "summary": summary,
        "checks": checks,
        "issues": ((report or {}).get("issues", []) + [i for p in (report or {}).get("parts", []) for i in p.get("issues", [])]) if report else [],
        "glb": f"view.glb?r={rev.number if rev else 0}",
        "topology": f"topology.json?r={rev.number if rev else 0}",
        "outputs": rev.outputs if rev else {},
        "anchors": anchors,
        "edits": [{"index": i, "description": describe(e), "op": e.get("op"), "part": e.get("part")} for i, e in enumerate(edits)],
        "edit_notes": edit_notes,
        "edits_full": edits,
        "joints": joints,
        "groups": groups,
        "sheets": sheets,
        "flats": flats,
        "forms": forms,
        "bom": (report or {}).get("bom", []) if report else [],
        "materials": _materials(),
        "bearings": _bearings(),
        "has_agent": hub is not None,
        "preview": _preview(project, history),
        "building": _build_progress(project),
        "home": home,
        "title": _title(project),
        "busy": bool(hub.busy) if hub is not None else False,
        "demo": _demo_info(project),
        "needs_key": _needs_key(),
        "engine": _engine(),
        "replaying": bool(hub is not None and getattr(hub, "_guest", None) is not None),
        "last_event": hub.last_event_id if hub is not None else 0,
        "exports": _exports(project.kind),
        "version": _version(),
    }


MODEL_ORDER = ("cad", "harness", "mechanism", "pneumatics", "firmware")
_RESULT_CHECKS: dict[str, tuple[int, list]] = {}          # result.json path -> (mtime, its checks): read once per build


def _models(project: Project, history, current_checks: list) -> list[dict]:
    """Every model file of the project: the file, its domain, a name to show, its last good revision and that build's
    check counts. One entry for a project with one model; a host may switch between several (EITRI_SHELL.switchModel)."""
    from ..domains import kind_of_source
    current = project.model_path.name
    out = []
    for f in project.model_files():
        try:
            code = (project.folder / f).read_text(encoding="utf-8")
        except OSError:
            code = None
        ok = [h for h in project.revisions_of(f, history) if h.ok]
        rev = ok[-1] if ok else None
        checks = current_checks if f == current else _checks_of(project, rev, history)
        out.append({"file": f, "kind": kind_of_source(code), "name": Path(f).stem, "current": f == current,
                    "ok_revision": rev.number if rev else 0, "checks": _tally(checks)})
    out.sort(key=lambda m: (MODEL_ORDER.index(m["kind"]) if m["kind"] in MODEL_ORDER else len(MODEL_ORDER), m["file"]))
    return out


def _checks_of(project: Project, rev, history) -> list:
    if rev is None:
        return []
    f = project.folder / Path(rev.model).parent / "result.json"
    try:
        mtime = f.stat().st_mtime_ns
    except OSError:
        return ((project.build_result(rev, history) or {}).get("report") or {}).get("checks", [])
    hit = _RESULT_CHECKS.get(str(f))
    if hit is None or hit[0] != mtime:
        hit = (mtime, ((project.build_result(rev, history) or {}).get("report") or {}).get("checks", []))
        if len(_RESULT_CHECKS) > 256:
            _RESULT_CHECKS.clear()
        _RESULT_CHECKS[str(f)] = hit
    return hit[1]


def _tally(checks: list) -> dict:
    """pass / warn / fail, counted as the editor counts them (a warning is a check that passed with severity "warn")."""
    fail = sum(1 for c in checks if not c.get("ok"))
    warn = sum(1 for c in checks if c.get("ok") and c.get("severity") == "warn")
    return {"pass": len(checks) - fail - warn, "warn": warn, "fail": fail}


def _exports(kind: str | None) -> list[dict]:
    from ..exports import export_formats
    return export_formats(kind)


def _version() -> str:
    from .. import __version__
    return __version__


HARNESS_DOWNLOADS = {                      # /download?fmt=...: a harness build's shop outputs
    "svg": ("svg", "image/svg+xml", ".harness.svg"), "png": ("png", "image/png", ".png"),
    "bom": ("csv", "text/csv", ".bom.csv"), "bom_md": ("bom_md", "text/markdown", ".bom.md"),
    "cutlist": ("cutlist_csv", "text/csv", ".cutlist.csv"), "cutlist_md": ("cutlist_md", "text/markdown", ".cutlist.md"),
    "wireviz": ("wireviz", "text/yaml", ".wireviz.yaml"),
}


def _downloads(kind: str) -> dict:
    """/download?fmt=... of a non-CAD domain: what its editor offers (eitri.domains)."""
    from ..domains import module
    return module(kind).DOWNLOADS


def _bearings() -> list[str]:
    from ..lib.bearings import BEARINGS
    return list(BEARINGS)


def _materials() -> list[str]:
    from ..measure import DENSITY
    return [m for m in DENSITY if m != "aluminum"]


def make_handler(project: Project, lock: threading.Lock, hub=None, home: str | None = None, agent=None, policy=None):
    """The editor's request handler for one project. `policy` (a hosted deployment) replaces the local-only
    host check (policy.host_ok(handler, post)), refuses routes (policy.blocked(method, path) -> a message
    or None) and adds to the state (policy.state(st))."""
    from ..printexport import ProjectPrinter
    printer = ProjectPrinter(project)          # the print-export dialog's plan, kept for its export
    redo_stack: list[int] = []                 # revisions undone, newest last

    def _st() -> dict:
        st = _state(project, hub, home)
        if agent is not None:                  # the website knows whether Claude can run (a key, a binary)
            st["needs_key"] = bool(agent().get("needs_key"))
        if policy is None:                     # running locally: the status bar shows where the project lives (never sent by a hosted policy)
            st["folder"] = str(project.folder)
        else:
            st = policy.state(st)
        return st

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str, filename: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def _build_edits(self, old_edits: list, note: str, check: int | None) -> dict:
            """Rebuild after an EDITS change (already written); roll the change back when the
            build fails, when the edit at `check` could not be applied, or when an edit that
            applied before now fails (e.g. after a reorder). Call with the lock held."""
            res = project.build(note=note, render=False)
            notes = res.edit_notes
            bad = None
            if not res.ok:
                bad = res.error
            elif check is not None and 0 <= check < len(notes) and not notes[check].get("ok"):
                bad = notes[check].get("detail", "edit failed")
            elif check is None:
                failed = [n for n in notes if not n.get("ok")]
                if failed:
                    bad = failed[0].get("detail", "an edit failed after the change")
            if bad:
                project.set_edits(old_edits)
                project.build(note="viewer: undo failed edit", render=False)
                return {"ok": False, "error": bad, "state": _st()}
            return {"ok": True, "notes": notes, "state": _st()}

        def _file_from_rev(self, key: str, ctype: str) -> None:
            rev = project.last_ok_revision()
            f = Path(rev.outputs[key]) if rev and rev.outputs.get(key) else None
            if f and f.is_file():
                self._send(200, f.read_bytes(), ctype)
            else:
                self._send(404, b"not built yet", "text/plain")

        def _refused(self, method: str) -> bool:
            if policy is None:
                if not local_request(self, post=method == "POST"):
                    self._send(403, b"eitri only answers requests from this machine", "text/plain")
                    return True
                return False
            if not policy.host_ok(self, method == "POST"):
                self._send(403, b"forbidden", "text/plain")
                return True
            msg = policy.blocked(method, self.path.partition("?")[0])
            if msg:
                self._json({"ok": False, "error": msg}, 403)
                return True
            return False

        def do_GET(self):
            if self._refused("GET"):
                return
            path, _, query = self.path.partition("?")
            params = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
            if path.startswith(("/vendor/", "/assets/")):
                from ..static import serve
                if not serve(self, path):
                    self._send(404, b"not found", "text/plain")
                return
            if path in ("/", "/index.html"):
                if hub is not None and hasattr(hub, "prewarm"):
                    hub.prewarm()                    # Claude loads while the page does: the first reply comes sooner
                self._send(200, (_HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/state.json":
                self._json(_st())
            elif path == "/view.glb":
                self._file_from_rev("view_glb", "model/gltf-binary")
            elif path == "/topology.json":
                self._file_from_rev("topology", "application/json")
            elif path == "/model.glb":
                self._file_from_rev("glb", "model/gltf-binary")
            elif path == "/image.png":
                self._file_from_rev("png", "image/png")
            elif path in ("/harness.svg", "/domain.svg"):      # the diagram a domain editor draws (harness, mechanism, ...)
                self._file_from_rev("svg", "image/svg+xml")
            elif path == "/model.py":
                self._send(200, (project.read_model() or "").encode(), "text/plain; charset=utf-8")
            elif path == "/download" and project.kind != "cad" and (params.get("fmt") or "").lower() in _downloads(project.kind):
                key, ctype, suffix = _downloads(project.kind)[(params.get("fmt") or "").lower()]
                rev = project.last_ok_revision()
                f = Path(rev.outputs[key]) if rev and rev.outputs.get(key) else None
                if f and f.is_file():
                    self._send(200, f.read_bytes(), ctype, filename=f"{project.name}{suffix}")
                else:
                    self._send(404, b"not built yet", "text/plain")
            elif path == "/download":
                fmt = (params.get("fmt") or "step").lower()
                if fmt in ("gcode", "cut", "bed"):                  # manufacturing outputs written by /cam, cut files, the print plate
                    from ..model import load_model
                    pname = params.get("part") or ""
                    if fmt == "gcode":
                        f = project.out_dir / f"{pname}.gcode"
                        if not f.exists():
                            self._json({"ok": False, "error": "no toolpaths yet: generate them first"}, 400); return
                        self._send(200, f.read_bytes(), "text/plain", filename=f.name); return
                    if fmt == "cut":
                        from ..cam import cut_file
                        kind = (params.get("kind") or "dxf").lower()
                        with lock:
                            built = load_model(project.model_path).build()
                        if pname not in built.names:
                            self._json({"ok": False, "error": f"no part {pname!r}"}, 400); return
                        f = project.out_dir / f"{pname}.cut.{kind}"
                        cut_file(built[pname], f, kerf=float(params.get("kerf") or 0), fmt=kind)
                        self._send(200, f.read_bytes(), "application/dxf" if kind == "dxf" else "image/svg+xml", filename=f.name); return
                    f = project.out_dir / f"{project.name}.bed.{(params.get('kind') or 'stl').lower()}"
                    if not f.exists():
                        self._json({"ok": False, "error": "no print plate yet: run print prep first"}, 400); return
                    self._send(200, f.read_bytes(), "model/stl" if f.suffix == ".stl" else "model/3mf", filename=f.name); return
                if fmt == "flat":                                   # the flat pattern of a sheet-metal part as DXF
                    from ..model import load_model
                    from ..sheetmetal import flat_dxf
                    pname = params.get("part") or ""
                    with lock:
                        built = load_model(project.model_path).build()
                    states = getattr(built, "sheet_states", {})
                    if pname not in states:
                        self._json({"ok": False, "error": f"{pname!r} is not a sheet-metal part"}, 400); return
                    out = project.out_dir / f"{project.name}_{pname}_flat.dxf"
                    flat_dxf(states[pname], out)
                    self._send(200, out.read_bytes(), "application/dxf", filename=out.name); return
                ctypes = {"step": "application/step", "stl": "model/stl", "3mf": "model/3mf", "glb": "model/gltf-binary",
                          "svg": "image/svg+xml", "dxf": "application/dxf", "csv": "text/csv", "pdf": "application/pdf", "obj": "model/obj", "iges": "model/iges"}
                if fmt not in ctypes:
                    self._send(404, b"unknown format", "text/plain")
                    return
                try:
                    from ..kernel import export_parts
                    from ..model import load_model
                    with lock:
                        parts = load_model(project.model_path).build()
                        if fmt in ("svg", "dxf", "pdf"):
                            from ..drawing import drawing_dxf, drawing_pdf, drawing_svg
                            dopts = {"style": params.get("style", "iso"), "projection": params.get("projection", "third")}
                            mat = next(((p.get("material"), p.get("mass_g")) for p in _st()["parts"] if p.get("material")), (None, None))
                            views = tuple((params.get("views") or "front,top,right,iso").split(","))
                            if fmt == "svg":
                                body = drawing_svg(parts, project.name, views=views, sheet=params.get("sheet", "A4"), material=mat[0], mass_g=mat[1], **dopts).encode("utf-8")
                            elif fmt == "pdf":
                                svg = drawing_svg(parts, project.name, views=views, sheet=params.get("sheet", "A4"), material=mat[0], mass_g=mat[1], **dopts)
                                body = drawing_pdf(svg, project.out_dir / f"{project.name}.drawing.pdf", params.get("sheet", "A4")).read_bytes()
                            else:
                                body = drawing_dxf(parts, project.name, project.out_dir / f"{project.name}.dxf", views=tuple(v for v in views if v != "iso"), sheet=params.get("sheet", "A4")).read_bytes()
                            self._send(200, body, ctypes[fmt], filename=None if params.get("inline") else f"{project.name}.{fmt}")
                            return
                        out = export_parts(parts, project.out_dir, project.name, (fmt,))
                    self._send(200, Path(out[fmt]).read_bytes(), ctypes[fmt], filename=f"{project.name}.{fmt}")
                except Exception as exc:
                    self._send(500, f"export failed: {type(exc).__name__}: {exc}".encode(), "text/plain; charset=utf-8")
            elif path == "/interference":
                import time as _time
                from .. import measure
                from ..model import Part, Parts, load_model
                from ..viewglb import write_view_glb
                try:
                    with lock:
                        plist = list(load_model(project.model_path).build())
                        pairs, overlaps = [], []
                        for i in range(len(plist)):
                            for j in range(i + 1, len(plist)):
                                a, b = plist[i], plist[j]
                                try:
                                    ov = measure.overlap_volume(a.shape, b.shape)
                                except Exception:
                                    continue
                                if ov > 1e-6:
                                    pairs.append({"a": a.name, "b": b.name, "volume": round(ov, 3)})
                                    try:
                                        inter = a.shape & b.shape
                                        if inter.solids():
                                            overlaps.append(Part(f"{a.name} x {b.name}", inter, "#E53935"))
                                    except Exception:
                                        pass
                        glb = None
                        if overlaps:
                            gp = project.out_dir / "interference.glb"
                            write_view_glb(Parts(overlaps), gp, None)
                            glb = f"interference.glb?t={_time.time():.0f}"
                    self._json({"ok": True, "pairs": pairs, "glb": glb})
                except Exception as exc:
                    self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
            elif path == "/interference.glb":
                f = project.out_dir / "interference.glb"
                self._send(200, f.read_bytes(), "model/gltf-binary") if f.is_file() else self._send(404, b"none", "text/plain")
            elif path in ("/preview.glb", "/preview_topology.json"):
                try:
                    named = Path(unquote(params.get("f", ""))).name  # a file the preview named (see _preview)
                    if named and named.endswith((".view.glb", ".partial.glb", ".topology.json")):
                        f = project.out_dir / named
                    else:
                        pv = json.loads((project.out_dir / "preview.json").read_text(encoding="utf-8"))
                        f = project.out_dir / Path(pv["glb" if path == "/preview.glb" else "topology"]).name
                    body = f.read_bytes()
                except (OSError, ValueError, KeyError):
                    self._send(404, b"no preview", "text/plain")
                    return
                self._send(200, body, "model/gltf-binary" if path == "/preview.glb" else "application/json")
            elif path == "/events":
                since = int(params.get("since", "0") or 0)
                if hub is None:
                    self._json({"events": [], "busy": False, "last": 0, "has_agent": False})
                else:
                    st = hub.status() if hasattr(hub, "status") else {"busy": hub.busy}
                    if st.get("running"):
                        st["build"] = _build_progress(project)
                    self._json({"events": hub.events_since(since), "last": hub.last_event_id, "has_agent": True, **st})
            elif path == "/file":                      # an image inside the project (review renders, attachments)
                f = _project_file(project, unquote(params.get("path", "")))
                if f is None:
                    self._send(404, b"no such image in this project", "text/plain")
                else:
                    self._send(200, f.read_bytes(), _IMAGE_TYPES[f.suffix.lower()])
            elif path == "/print/file":
                f = printer.file(unquote(params.get("path", "")))
                if f is None:
                    self._send(404, b"no such file", "text/plain")
                else:
                    ctype = {".png": "image/png", ".stl": "model/stl", ".3mf": "model/3mf", ".zip": "application/zip",
                             ".json": "application/json"}.get(f.suffix.lower(), "application/octet-stream")
                    self._send(200, f.read_bytes(), ctype, filename=f.name if f.suffix.lower() != ".png" else None)
            elif path == "/print_prep":
                from .. import additive as A
                from ..kernel import export_parts
                from ..model import Part, Parts, load_model
                try:
                    w, _, d = (params.get("bed") or "220x220").lower().partition("x")
                    bed = (float(w), float(d or w))
                    with lock:
                        built = load_model(project.model_path).build()
                        report = A.print_report(built, bed, layer=float(params.get("layer") or 0.2))
                        placed, placements, skipped = A.arrange(built, bed, orient=True)
                        plate = Parts([Part(n, s, next(p.color for p in built if p.name == n)) for n, s in placed])
                        out = export_parts(plate, project.out_dir, f"{project.name}.bed", ("stl", "3mf")) if len(plate) else {}
                    self._json({"ok": True, "report": report, "placements": [p.__dict__ for p in placements], "skipped": skipped, "files": out})
                except Exception as exc:
                    self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
            elif path == "/compare":
                try:
                    a = int(params["a"]) if params.get("a") else None
                    b = int(params["b"]) if params.get("b") else None
                    with lock:
                        res = project.compare(a, b)
                    res["glb"] = f"compare.glb?a={res['a']}"
                    self._json(res)
                except Exception as exc:
                    self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
            elif path == "/compare.glb":
                from ..kernel import export_parts
                from ..model import load_model
                try:
                    a = int(params.get("a") or 0)
                    rev = next(h for h in project.history if h.number == a and h.ok)
                    with lock:
                        parts = load_model(project.folder / rev.model).build()
                        out = export_parts(parts, project.out_dir, f"compare_{a}", ("glb",))
                    self._send(200, Path(out["glb"]).read_bytes(), "model/gltf-binary")
                except Exception as exc:
                    self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
            elif path == "/revisions":
                self._json([{"number": h.number, "ok": h.ok, "note": h.note, "created": h.created,
                             "summary": (h.summary or "").splitlines()[0] if h.summary else ""} for h in project.revisions_of()])
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self._refused("POST"):
                return
            path = self.path.split("?")[0]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                self._json({"ok": False, "error": "bad JSON"}, 400)
                return
            try:
                if path == "/set":
                    with lock:
                        if payload.get("config"):
                            project.apply_config(str(payload["config"]))
                            res = project.build(note=f"viewer: configuration {payload['config']}", render=False)
                        else:
                            project.set_params(payload)
                            res = project.build(note="viewer: " + ", ".join(f"{k}={v}" for k, v in payload.items()), render=False)
                    self._json({"ok": res.ok, "error": res.error, "state": _st()})
                elif path in ("/print/plan", "/print/export"):
                    from ..printexport import settings_from
                    st = settings_from(payload)
                    with lock:
                        if path == "/print/plan":
                            self._json({"ok": True, **printer.plan_json(st)})
                        else:
                            fmts = tuple(f for f in (payload.get("formats") or ["stl", "3mf"]) if f in ("stl", "3mf")) or ("stl", "3mf")
                            self._json(printer.export(st, fmts))
                elif path == "/edit":
                    from ..edits import describe
                    edit = payload.get("edit", payload)
                    if "op" not in edit:
                        self._json({"ok": False, "error": "an edit needs an op"}, 400)
                        return
                    with lock:
                        old = project.edits()
                        new = project.add_edit(edit)
                        self._json(self._build_edits(old, "viewer: " + describe(edit), len(new) - 1))
                elif path in ("/harness/layout", "/domain/layout"):   # things dragged in a domain editor: layout hints
                    positions = payload.get("positions") or {}
                    if not isinstance(positions, dict) or not positions:
                        self._json({"ok": False, "error": "send positions: {name: [x, y]}"}, 400)
                        return
                    with lock:
                        project.set_layout(positions)
                        res = project.build(note="viewer: move " + ", ".join(positions), render=False)
                    self._json({"ok": res.ok, "error": res.error, "state": _st()})
                elif path == "/edits/update":
                    from ..edits import describe
                    index, edit = int(payload["index"]), dict(payload["edit"])
                    edit.pop("by", None)                      # changed by hand now: the timeline shows it as yours
                    with lock:
                        old = project.edits()
                        project.update_edit(index, edit)
                        self._json(self._build_edits(old, "viewer: edit feature: " + describe(edit), index))
                elif path == "/edits/move":
                    index, to = int(payload["index"]), int(payload["to"])
                    with lock:
                        old = project.edits()
                        project.move_edit(index, to)
                        self._json(self._build_edits(old, f"viewer: move edit {index + 1} to {to + 1}", None))
                elif path == "/edits/remove":
                    with lock:
                        project.remove_edit(int(payload.get("index", -1)))
                        res = project.build(note="viewer: remove edit", render=False)
                    self._json({"ok": res.ok, "error": res.error, "state": _st()})
                elif path == "/edits/clear":
                    with lock:
                        project.clear_edits()
                        res = project.build(note="viewer: clear edits", render=False)
                    self._json({"ok": res.ok, "error": res.error, "state": _st()})
                elif path == "/restore":
                    with lock:
                        project.restore(int(payload["number"]))
                        res = project.build(note=f"viewer: restore revision {payload['number']}", render=False)
                    self._json({"ok": res.ok, "error": res.error, "state": _st()})
                elif path == "/redo":
                    with lock:
                        mine = {h.number for h in project.revisions_of()}
                        while redo_stack and redo_stack[-1] not in mine:   # undone in another model file
                            redo_stack.pop()
                        if not redo_stack:
                            self._json({"ok": False, "error": "nothing to redo"})
                            return
                        number = redo_stack.pop()
                        project.restore(number)
                        res = project.build(note=f"viewer: redo -> revision {number}", render=False)
                    self._json({"ok": res.ok, "error": res.error, "state": _st()})
                elif path == "/undo":
                    with lock:
                        prev = project.last_ok_revision(before=project.revision)
                        # the previous *different* model: skip revisions whose file equals the current one
                        current = project.read_model()
                        hist = [h for h in project.revisions_of() if h.ok]
                        target = None
                        for h in reversed(hist[:-1] if hist and hist[-1].ok else hist):
                            f = project.folder / h.model
                            if f.exists() and f.read_text(encoding="utf-8") != current:
                                target = h
                                break
                        if target is None:
                            self._json({"ok": False, "error": "nothing to undo"})
                            return
                        current_rev = project.last_ok_revision()
                        if current_rev is not None:
                            redo_stack.append(current_rev.number)
                        project.restore(target.number)
                        res = project.build(note=f"viewer: undo -> revision {target.number}", render=False)
                    self._json({"ok": res.ok, "error": res.error, "state": _st()})
                elif path == "/measure":
                    from ..model import load_model
                    from .. import measure as M
                    ids = payload.get("ids") or []
                    with lock:
                        parts = load_model(project.model_path).build()
                    def pick(sid):
                        if ":" not in sid:
                            return parts[sid]
                        pname, rest = sid.split(":", 1)
                        shape = parts[pname]
                        idx = int(rest[1:])
                        return list(shape.faces())[idx] if rest[0] == "f" else list(shape.edges())[idx]
                    try:
                        objs = [pick(s) for s in ids]
                    except Exception as exc:
                        self._json({"ok": False, "error": f"cannot resolve {ids}: {exc}"}, 400)
                        return
                    out = {"ok": True}
                    if len(objs) == 1:
                        o = objs[0]
                        if hasattr(o, "area") and ":f" in ids[0]:
                            out.update(area=round(o.area, 3), center=list(_t3(o.center())))
                        elif ":e" in ids[0]:
                            out.update(length=round(o.length, 3))
                        else:
                            out.update(volume=round(abs(o.volume), 3), bbox=[list(_t3(o.bounding_box().min)), list(_t3(o.bounding_box().max))])
                    elif len(objs) == 2:
                        out["distance"] = round(M.min_distance(objs[0], objs[1]), 4)
                        try:
                            if all(":f" in s for s in ids):
                                n1, n2 = objs[0].normal_at(), objs[1].normal_at()
                                out["angle"] = round(n1.get_angle(n2), 2)
                        except Exception:
                            pass
                    self._json(out)
                elif path == "/cam":
                    from .. import cam as C
                    from ..model import load_model
                    try:
                        with lock:
                            built = load_model(project.model_path).build()
                        pname = payload.get("part") or built.names[0]
                        if pname not in built.names:
                            self._json({"ok": False, "error": f"no part {pname!r}"}, 400); return
                        prt = next(p for p in built if p.name == pname)
                        job = C.plan(built[pname], pname, payload.get("ops") or ["face", {"op": "contour", "tabs": 3}, "pocket", "drill"],
                                     post=payload.get("post") or "grbl", material=payload.get("material") or getattr(prt, "material", None), safe_z=float(payload.get("safe_z") or 5))
                        project.out_dir.mkdir(parents=True, exist_ok=True)
                        (project.out_dir / f"{pname}.gcode").write_text(C.gcode(job), encoding="utf-8")
                        res = C.toolpath_json(job); res["ok"] = True; res["gcode"] = f"download?fmt=gcode&part={pname}"
                        self._json(res)
                    except Exception as exc:
                        self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
                elif path == "/sketch/solve":
                    from .. import sketch as S
                    sk = payload.get("sketch") or {}
                    try:
                        st = S.solve(sk) if (sk.get("constraints") or sk.get("dims")) else {"ok": True, "residual": 0.0, "dof": None, "conflicts": []}
                        self._json({"ok": True, "sketch": sk, "status": st, "preview": _sketch_preview(sk)})
                    except Exception as exc:
                        self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}", "sketch": sk}, 400)
                elif path == "/sketch/op":
                    from .. import sketch as S
                    sk = payload.get("sketch") or {}
                    op, args = payload.get("op"), payload.get("args") or {}
                    try:
                        result = _sketch_op(project, sk, op, args)
                        st = S.solve(sk) if (sk.get("constraints") or sk.get("dims")) else {"ok": True, "residual": 0.0, "dof": None, "conflicts": []}
                        self._json({"ok": True, "sketch": sk, "result": result, "status": st, "preview": _sketch_preview(sk)})
                    except Exception as exc:
                        self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}", "sketch": sk}, 400)
                elif path == "/rebuild":
                    with lock:
                        res = project.build(note="viewer: rebuild", render=False)
                    self._json({"ok": res.ok, "error": res.error, "state": _st()})
                elif path == "/chat":
                    text = (payload.get("text") or "").strip()
                    if not text:
                        self._json({"ok": False, "error": "empty message"}, 400)
                        return
                    if hub is None:
                        self._json({"ok": False, "error": "no agent here: start eitri with `eitri web` (or `eitri <project>`), not `eitri serve`"}, 400)
                        return
                    if hub.busy:
                        self._json({"ok": False, "error": "Claude is still working on the previous message"}, 409)
                        return
                    images = []
                    for rel in payload.get("images") or []:
                        f = _project_file(project, str(rel))
                        if f is None:
                            self._json({"ok": False, "error": f"no such attachment: {rel}"}, 400)
                            return
                        images.append(str(f))
                    hub.send_async(text, images=images or None, source="viewer", selection=payload.get("selection") or None)
                    self._json({"ok": True, "last": hub.last_event_id})
                elif path == "/stop":
                    if hub is None or not hub.busy:
                        self._json({"ok": False, "error": "Claude is not working on anything"}, 409)
                        return
                    self._json({"ok": bool(hub.interrupt())})
                elif path == "/newchat":
                    if hub is None:
                        self._json({"ok": False, "error": "no agent here"}, 400)
                        return
                    hub.reset()
                    self._json({"ok": True, "last": hub.last_event_id})
                elif path == "/upload":
                    import base64 as _b64
                    import re as _re
                    name = Path(str(payload.get("name") or "image.png")).name
                    stem, suffix = Path(name).stem, Path(name).suffix.lower()
                    if suffix not in _IMAGE_TYPES or suffix == ".svg":
                        self._json({"ok": False, "error": "attach a PNG, JPEG, GIF or WebP image"}, 400)
                        return
                    data = _b64.b64decode(str(payload.get("data") or "").split(",")[-1] or b"")
                    if not data or len(data) > 15 * 1024 * 1024:
                        self._json({"ok": False, "error": "the image is empty or larger than 15 MB"}, 400)
                        return
                    folder = project.folder / "inputs"
                    folder.mkdir(parents=True, exist_ok=True)
                    stem = _re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._") or "image"
                    target, k = folder / f"{stem}{suffix}", 2
                    while target.exists():
                        target, k = folder / f"{stem}_{k}{suffix}", k + 1
                    target.write_bytes(data)
                    self._json({"ok": True, "path": str(target.relative_to(project.folder))})
                elif path == "/thumb":
                    import base64 as _b64
                    data = _b64.b64decode(str(payload.get("data") or "").split(",")[-1] or b"")
                    if not data.startswith(b"\x89PNG") or len(data) > 3 * 1024 * 1024:
                        self._json({"ok": False, "error": "send a PNG under 3 MB"}, 400)
                        return
                    thumb = project.folder / ".eitri" / "thumb.png"
                    thumb.parent.mkdir(parents=True, exist_ok=True)
                    thumb.write_bytes(data)
                    self._json({"ok": True})
                else:
                    self._send(404, b"not found", "text/plain")
            except Exception as exc:
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)

    return Handler


def _sketch_preview(sk: dict) -> dict:
    """Polylines (sketch-plane coordinates) for every entity, plus the closed profiles' outlines."""
    from .. import sketch as S
    import build123d as bd
    ents = {}
    for e in sk.get("entities", []):
        pls = []
        if e["type"] == "text":
            try:
                for f in S.text_faces(e):
                    for w in [f.outer_wire()] + list(f.inner_wires()):
                        pls.append([[round(p.X, 4), round(p.Y, 4)] for p in [w.position_at(i / 48) for i in range(49)]])
            except Exception:
                pass
        elif e["type"] == "point":
            pls.append([[e["at"][0], e["at"][1]]])
        else:
            try:
                for ed in S.entity_edges(e):
                    n = 2 if ed.geom_type == bd.GeomType.LINE else 40
                    pls.append([[round(q.X, 4), round(q.Y, 4)] for q in [ed.position_at(i / n) for i in range(n + 1)]])
            except Exception:
                pass
        ents[e["id"]] = pls
    closed = 0
    try:
        faces, chains = S.profile_faces(sk)
        closed = len(faces)
        open_count = len(chains)
    except Exception:
        open_count = 0
    return {"entities": ents, "profiles": closed, "open": open_count}


def _sketch_op(project: Project, sk: dict, op: str, args: dict):
    """Apply one kernel editing operation to a sketch (the viewer's and the agent's single path)."""
    from .. import sketch as S
    if op == "add":
        return S.add(sk, args["entity"])["id"]
    if op == "remove":
        S.remove(sk, args["ids"]); return None
    if op == "move":
        S.move(sk, args["ids"], args["delta"]); return None
    if op == "rotate":
        S.rotate(sk, args["ids"], args["centre"], float(args["angle"])); return None
    if op == "copy":
        return S.copy(sk, args["ids"], args.get("delta") or (0, 0))
    if op == "mirror":
        return S.mirror(sk, args["ids"], args["a"], args["b"])
    if op == "pattern_rect":
        return S.pattern_rect(sk, args["ids"], int(args.get("nx", 2)), int(args.get("ny", 1)), float(args.get("dx", 10)), float(args.get("dy", 10)))
    if op == "pattern_circular":
        return S.pattern_circular(sk, args["ids"], int(args.get("n", 4)), args.get("centre") or (0, 0), float(args.get("angle", 360)))
    if op == "split":
        return S.split(sk, args["id"], args["at"])
    if op == "trim":
        return S.trim(sk, args["id"], args["near"])
    if op == "extend":
        return S.extend(sk, args["id"], args.get("end", "b"))
    if op == "offset":
        return S.offset(sk, args["ids"], float(args["distance"]))
    if op == "fillet":
        return S.fillet_corner(sk, args["a"], args["b"], float(args["r"]))
    if op == "chamfer":
        return S.chamfer_corner(sk, args["a"], args["b"], float(args["d"]))
    if op == "construction":
        for e in sk.get("entities", []):
            if e["id"] in args["ids"]:
                e["construction"] = bool(args.get("on", True))
        return None
    if op == "constrain":
        sk.setdefault("constraints", []).append(args["constraint"]); return len(sk["constraints"]) - 1
    if op == "unconstrain":
        sk["constraints"] = [c for i, c in enumerate(sk.get("constraints", [])) if i != int(args["index"])]; return None
    if op == "dimension":
        d = dict(args["dim"])
        if d.get("value") is None:
            d["value"] = round(S.measure(sk, d), 4)
        sk.setdefault("dims", []).append(d); return len(sk["dims"]) - 1
    if op == "undimension":
        sk["dims"] = [d for i, d in enumerate(sk.get("dims", [])) if i != int(args["index"])]; return None
    if op == "set_dim":
        sk["dims"][int(args["index"])]["value"] = float(args["value"]); return None
    if op == "project":
        from ..model import load_model
        from ..edits import face_frame, resolve_edges, resolve_face
        parts = load_model(project.model_path).build()
        part = parts[args["part"]] if args.get("part") in parts.names else None
        if part is None:
            raise ValueError("no such part")
        edges = resolve_edges(part, args["edges"]) if args.get("edges") else []
        if args.get("face"):
            f = resolve_face(part, args["face"])
            if f is not None:
                edges += list(f.edges())
        plane = face_frame(args["normal"], args.get("origin") or (0, 0, 0))
        return S.project_edges(sk, edges, plane, construction=bool(args.get("construction", True)))
    if op == "svg":
        return S.import_svg(sk, args["path"], float(args.get("scale", 1.0)))
    if op == "dxf":                                     # a DXF file's entities merged into this sketch
        from ..meshio import dxf_to_sketch
        new = dxf_to_sketch(args["path"], args.get("layer"), float(args.get("scale", 1.0)))
        ids = []
        for e in new["entities"]:
            e["id"] = S.new_id(sk, e["type"][0])
            sk.setdefault("entities", []).append(e)
            ids.append(e["id"])
        return ids
    if op == "from_simple":
        new = S.from_simple(args["profile"])
        sk.clear(); sk.update(new); return None
    if op == "add_simple":                              # merge a decomposed rect/circle/polygon/slot/text into this sketch
        new = S.from_simple(args["profile"])
        mapping: dict[str, str] = {}
        for e in new["entities"]:
            old_id = e["id"]
            e["id"] = S.new_id(sk, e["type"][0])
            mapping[old_id] = e["id"]
            sk.setdefault("entities", []).append(e)

        def ren(ref):
            if not isinstance(ref, str):
                return ref
            eid, _, rest = ref.partition(".")
            return mapping.get(eid, eid) + ("." + rest if rest else "")
        for c in new.get("constraints", []):
            sk.setdefault("constraints", []).append({k: (ren(v) if k in ("a", "b", "line") else v) for k, v in c.items()})
        for d in new.get("dims", []):
            sk.setdefault("dims", []).append({k: (ren(v) if k in ("a", "b") else v) for k, v in d.items()})
        return list(mapping.values())
    raise ValueError(f"unknown sketch op {op!r}")


_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}


def local_request(handler: BaseHTTPRequestHandler, post: bool) -> bool:
    """Only this machine may use the server: the Host must be local (no DNS rebinding), and a POST must
    come from one of our own pages (an Origin, when the browser sends one, of this host and port), so
    another website open in the browser cannot drive the agent or change projects."""
    host = (handler.headers.get("Host") or "").strip()
    if host.startswith("["):                                   # [::1]:8765
        hostname = host[1:host.index("]")] if "]" in host else host
    else:
        hostname = host.rsplit(":", 1)[0] if ":" in host else host
    if host and hostname not in _LOCAL_HOSTS:
        return False
    if post:
        origin = handler.headers.get("Origin")
        if origin is not None:
            from urllib.parse import urlparse
            u = urlparse(origin)
            port = handler.server.server_address[1]
            if u.hostname not in ("127.0.0.1", "localhost", "::1") or (u.port or 80) != port:
                return False
        if (handler.headers.get("Sec-Fetch-Site") or "same-origin") not in ("same-origin", "none"):
            return False
    return True


def _build_progress(project: Project) -> dict | None:
    """What a build that is still running is doing (out/progress.json, written by the runner)."""
    try:
        pg = json.loads((project.out_dir / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if pg.get("done") or not pg.get("stage"):
        return None
    import time as _time
    return {"stage": str(pg["stage"]), "elapsed": round(max(0.0, _time.time() - float(pg.get("ts") or 0)), 1)}


def _needs_key() -> bool:
    from ..config import api_key
    return api_key() is None


def _engine() -> dict:
    from ..kernel import engine_status
    return engine_status()


def _demo_info(project: Project) -> dict | None:
    """For a bundled demo project: what it is and what its recorded session was (no events)."""
    try:
        meta = json.loads((project.folder / ".eitri" / "web.json").read_text(encoding="utf-8"))
        demo_id = meta.get("demo") if isinstance(meta, dict) else None
        if not demo_id:
            return None
        from ..demos import load_demo
        d = load_demo(demo_id, events=False)
        return {k: d.get(k) for k in ("id", "title", "blurb", "outcome", "prompt", "recorded")}
    except Exception:
        return None


def _preview(project: Project, history) -> dict | None:
    """The geometry of a build still running (out/preview.json, newer than every recorded revision): the
    page shows it before the build is checked, exported and rendered."""
    try:
        pv = json.loads((project.out_dir / "preview.json").read_text(encoding="utf-8"))
        ts = float(pv["ts"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    newest = max((h.created for h in history), default=0.0)
    if ts <= newest:
        return None
    # the exact files are named in the URLs: a newer preview (the next batch of an assembly's parts) cannot pair
    # one batch's geometry with another's topology
    g, t = quote(Path(str(pv.get("glb", ""))).name), quote(Path(str(pv.get("topology", ""))).name)
    out = {"glb": f"preview.glb?f={g}&t={ts:.3f}", "topology": f"preview_topology.json?f={t}&t={ts:.3f}", "ts": ts}
    if pv.get("parts_total"):
        out.update(parts_done=int(pv.get("parts_done") or 0), parts_total=int(pv["parts_total"]))
    return out


def _title(project: Project) -> str | None:
    """The readable name the local website keeps for a project (None outside it)."""
    try:
        title = json.loads((project.folder / ".eitri" / "web.json").read_text(encoding="utf-8")).get("title")
    except (OSError, ValueError, AttributeError):
        title = None
    if not title and project.name == "model":                # a generic model.py: the folder says more
        title = project.folder.name
    return str(title) if title else None


def _project_file(project: Project, rel: str) -> Path | None:
    """An image file inside the project folder (relative, or absolute but inside); None otherwise."""
    if not rel:
        return None
    try:
        base = project.folder.resolve()
        f = Path(rel)
        f = (f if f.is_absolute() else base / f).resolve()
        f.relative_to(base)
    except (ValueError, OSError):
        return None
    if f.suffix.lower() not in _IMAGE_TYPES or not f.is_file():
        return None
    return f


def _t3(v) -> tuple[float, float, float]:
    return (round(v.X, 4), round(v.Y, 4), round(v.Z, 4))


def _free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # like the server: a just-closed port is free
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return 0


def start_viewer(project: Project, port: int = 0, background: bool = True, hub=None) -> str:
    """Start the viewer server; returns its URL. background=False blocks."""
    port = port or _free_port()
    lock = threading.Lock()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(project, lock, hub))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if background:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return url
    print(f"viewer: {url}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return url
