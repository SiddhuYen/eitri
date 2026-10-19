"""Package recorded eitri sessions as the demo projects shipped in eitri/demos/.

Each demo is a real project and the real conversation that made it: the user's prompt, Claude's text,
every tool call and result, with their original timestamps (eitri/demos/<id>/demo.json), plus every
revision the conversation built (eitri/demos/<id>/project.tar.xz). The revisions that built are
rebuilt here from their exact model code with the current kernel, so their geometry, topology and
check results load in this version of the viewer; the revisions that failed stay failed, with the
recorded error, because a replay must show what happened. Absolute paths of the machine the session
was recorded on are replaced.

    .venv/bin/python scripts/make_demos.py            # all demos
    .venv/bin/python scripts/make_demos.py racket-holder
    .venv/bin/python scripts/make_demos.py --thumbs   # only refresh the home-page thumbnails

Thumbnails are made by the editor itself: each demo is opened in headless Chromium (Playwright) and the
picture the page saves is put into the bundle, so the home page shows what the editor shows.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eitri.project import Project  # noqa: E402

PARTS = Path(os.environ.get("EITRI_DEMO_SOURCES", Path.home() / "parts"))
DEMOS = [
    {"id": "v8-engine", "source": "build-me-a-really-cool-mini", "last": 11, "order": 0,
     "title": "V8 engine kit",
     "blurb": "A hand-cranked, see-through V8 you print and assemble: 43 parts, cross-plane crank, pistons that move.",
     "outcome": "Claude worked for 23 min; stopped by the user with two thin walls still flagged."},
    {"id": "racket-holder", "source": "tennis-racket-holder-to-clip-onto", "last": 7, "order": 1,
     "title": "Wall racket holder",
     "blurb": "A wall hook for a tennis racket. Watch the thin-wall check fail, Claude find the cause, and every check go green.",
     "outcome": "3 failed builds and a 0.1 mm sliver, then all checks pass (3 min 17 s).", "keep_thumb": False},
    {"id": "marble-maze", "source": "i-want-to-3d-print-a", "last": 12, "order": 2,
     "title": "Marble maze",
     "blurb": "A tilt labyrinth that generates its own maze, with a sliding clear lid and a marble.",
     "outcome": "Claude chased a 0.03 mm sliver through 11 revisions until all checks pass (5 min)."},
]
PLACEHOLDER = "{PROJECT}"
KEEP_KINDS = {"user", "text", "thinking", "tool_use", "tool_result", "result", "stopping", "error"}


def _scrub(text: str, src: Path) -> str:
    text = text.replace(str(src) + "/", "").replace(str(src), ".")
    return re.sub(r"/Users/[^/\s]+/", "~/", text)


def _scrub_obj(obj, src: Path):
    if isinstance(obj, str):
        return _scrub(obj, src)
    if isinstance(obj, list):
        return [_scrub_obj(x, src) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub_obj(v, src) for k, v in obj.items()}
    return obj


def transcript(src: Path) -> tuple[list[dict], dict]:
    """The first conversation turn of the project's chat log, scrubbed, with times relative to its start."""
    events, t0 = [], None
    for line in (src / ".eitri" / "chat.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["kind"] not in KEEP_KINDS:
            continue
        if e["kind"] == "user" and events:
            break                                     # one turn per demo
        t0 = t0 if t0 is not None else e["ts"]
        events.append({"t": round(e["ts"] - t0, 3), "kind": e["kind"], "data": _scrub_obj(e["data"], src)})
        if e["kind"] == "result":
            break
    result = next((e["data"] for e in events if e["kind"] == "result"), {})
    return events, result


def package(spec: dict, out_root: Path) -> dict:
    src = PARTS / spec["source"]
    original = json.loads((src / "eitri.json").read_text(encoding="utf-8"))
    stem = Path(original["model"]).stem
    history = [h for h in original["history"] if h["number"] <= spec["last"]]
    with tempfile.TemporaryDirectory(prefix="eitri-demo-") as tmp:
        folder = (Path(tmp) / spec["id"]).resolve()
        project = Project(folder, name=stem)
        for h in history:
            code = (src / h["model"]).read_text(encoding="utf-8")
            if h["ok"]:
                project.write_model(code)
                res = project.build(note=h["note"], render=(h["number"] == spec["last"]))
                if not res.ok:
                    raise SystemExit(f"{spec['id']}: revision {h['number']} built when recorded but fails now:\n{res.error}")
                rev_dir = folder / "revisions" / f"{h['number']:03d}"
                shutil.copy2(project.out_dir / "result.json", rev_dir / "result.json")
            else:                                     # recorded as failed: keep it failed, with the recorded error
                project.reload()
                n = int(project.state.get("revision", 0)) + 1
                assert n == h["number"], (n, h["number"])
                rev_dir = folder / "revisions" / f"{n:03d}"
                rev_dir.mkdir(parents=True, exist_ok=True)
                (rev_dir / f"{stem}.py").write_text(code, encoding="utf-8")
                project.state["revision"] = n
                project.state.setdefault("history", []).append({
                    "number": n, "model": f"revisions/{n:03d}/{stem}.py", "note": h["note"], "ok": False,
                    "created": h["created"], "summary": _scrub(h.get("summary") or "build failed", src),
                    "outputs": {}, "params": h.get("params", {})})
                project._save()
            project.reload()
            project.state["history"][-1]["created"] = h["created"]      # the recorded time, not packaging time
            project._save()
        project.write_model((src / history[-1]["model"]).read_text(encoding="utf-8"))
        thumb = src / ".eitri" / "thumb.png"
        if thumb.is_file() and spec.get("keep_thumb", True):
            (folder / ".eitri").mkdir(exist_ok=True)
            shutil.copy2(thumb, folder / ".eitri" / "thumb.png")
        # absolute paths of this temporary folder -> a placeholder filled in at install time
        state = json.loads((folder / "eitri.json").read_text(encoding="utf-8"))
        text = json.dumps(state, indent=1).replace(str(folder), PLACEHOLDER).replace(str(Path(tmp) / spec["id"]), PLACEHOLDER)
        (folder / "eitri.json").write_text(text, encoding="utf-8")
        for f in list(folder.rglob("*.step")) + list(folder.rglob("*.stl")) + list(folder.rglob("progress.json")):
            f.unlink()
        out = out_root / spec["id"]
        out.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:xz", preset=9) as tar:
            for f in sorted(folder.rglob("*")):
                if f.is_file():
                    info = tar.gettarinfo(str(f), arcname=str(f.relative_to(folder)))
                    info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
                    with f.open("rb") as fh:
                        tar.addfile(info, fh)
        (out / "project.tar.xz").write_bytes(buf.getvalue())
    events, result = transcript(src)
    demo = {k: spec[k] for k in ("id", "title", "blurb", "outcome", "order")}
    demo.update({"model": f"{stem}.py", "last_revision": spec["last"], "prompt": events[0]["data"]["text"],
                 "recorded": {"seconds": round(events[-1]["t"], 1), "cost_usd": result.get("turn_cost_usd"),
                              "stopped": bool(result.get("stopped")), "turns": result.get("num_turns"),
                              "model": "Claude Opus (Claude Code, effort high)", "date": "2026-10"},
                 "events": events})
    (out / "demo.json").write_text(json.dumps(demo, indent=1), encoding="utf-8")
    return {"id": spec["id"], "bytes": (out / "project.tar.xz").stat().st_size, "events": len(events)}


def capture_thumbs(ids: list[str]) -> None:
    """Open each demo in the editor (headless Chromium), wait for the thumbnail it saves, and put it into
    the demo's bundle."""
    import time
    from playwright.sync_api import sync_playwright
    from eitri.web.app import Workspace, start_web
    with tempfile.TemporaryDirectory(prefix="eitri-thumbs-") as tmp:
        ws = Workspace(Path(tmp) / "ws", lambda project, on_event: None)
        url, server = start_web(ws, port=0)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader"])
                for demo_id in ids:
                    thumb = Path(tmp) / "ws" / ".demos" / demo_id / ".eitri" / "thumb.png"
                    thumb.unlink(missing_ok=True)
                    page = browser.new_page(viewport={"width": 1280, "height": 860})
                    page.goto(f"{url}p/demo-{demo_id}/")
                    deadline = time.time() + 90
                    while not thumb.is_file() and time.time() < deadline:
                        time.sleep(0.5)
                    page.close()
                    if not thumb.is_file():
                        raise SystemExit(f"{demo_id}: the editor saved no thumbnail")
                    _put_in_bundle(ROOT / "eitri" / "demos" / demo_id / "project.tar.xz", ".eitri/thumb.png", thumb.read_bytes())
                    print({"id": demo_id, "thumb": thumb.stat().st_size}, flush=True)
                browser.close()
        finally:
            server.shutdown()
            ws.close()


def _put_in_bundle(bundle: Path, name: str, data: bytes) -> None:
    with tarfile.open(bundle, mode="r:xz") as tar:
        members = [(m, tar.extractfile(m).read() if m.isfile() else None) for m in tar.getmembers() if m.name != name]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz", preset=9) as out:
        for m, content in members:
            out.addfile(m, io.BytesIO(content) if content is not None else None)
        info = tarfile.TarInfo(name)
        info.size, info.mtime = len(data), 0
        out.addfile(info, io.BytesIO(data))
    bundle.write_bytes(buf.getvalue())


def main(argv: list[str]) -> int:
    out_root = ROOT / "eitri" / "demos"
    thumbs_only = "--thumbs" in argv
    argv = [a for a in argv if a != "--thumbs"]
    chosen = [spec for spec in DEMOS if not argv or spec["id"] in argv]
    if not thumbs_only:
        for spec in chosen:
            print(package(spec, out_root), flush=True)
    capture_thumbs([spec["id"] for spec in chosen])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
