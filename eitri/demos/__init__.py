"""The demo projects: real eitri projects and the real Claude sessions that made them, playable without
an API key.

Each demo is <id>/demo.json (title, the recorded conversation with its timestamps) and <id>/project.tar.xz
(every revision the conversation built: model code, viewer geometry, check results). See
scripts/make_demos.py for how they are made. A demo is installed (extracted) into the workspace's
hidden .demos folder the first time it is opened, so it can be edited like any project; "reset"
puts it back as shipped. A replay steps the project through its recorded revisions (reveal()) while
the conversation plays back in the chat (eitri.agent.replay).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from pathlib import Path

HERE = Path(__file__).parent
PLACEHOLDER = "{PROJECT}"
STATE_COPY = "demo-state.json"          # in <project>/.eitri: the full recorded history, for reveal()


def _ids() -> list[str]:
    return sorted(d.name for d in HERE.iterdir() if (d / "demo.json").is_file() and (d / "project.tar.xz").is_file())


def load_demo(demo_id: str, events: bool = True) -> dict:
    if demo_id not in _ids():
        raise KeyError(demo_id)
    data = json.loads((HERE / demo_id / "demo.json").read_text(encoding="utf-8"))
    if not events:
        data.pop("events", None)
    return data


def list_demos() -> list[dict]:
    out = [load_demo(i, events=False) for i in _ids()]
    out.sort(key=lambda d: (d.get("order", 99), d["id"]))
    return out


def _bundle_hash(demo_id: str) -> str:
    h = hashlib.sha1()
    for name in ("project.tar.xz", "demo.json"):
        h.update((HERE / demo_id / name).read_bytes())
    return h.hexdigest()[:16]


def demo_folder(root: Path, demo_id: str) -> Path:
    return Path(root) / ".demos" / demo_id


def install(root: Path, demo_id: str, force: bool = False) -> Path:
    """Extract the demo into <root>/.demos/<id> (again when the bundle changed or force)."""
    folder = demo_folder(root, demo_id)
    marker = folder / ".eitri" / "demo.txt"
    version = _bundle_hash(demo_id)
    if folder.is_dir() and not force and marker.is_file() and marker.read_text(encoding="utf-8").strip() == version:
        return folder
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    with tarfile.open(HERE / demo_id / "project.tar.xz", mode="r:xz") as tar:
        try:
            tar.extractall(folder, filter="data")
        except TypeError:                                   # Python without extraction filters
            for m in tar.getmembers():
                if m.name.startswith(("/", "..")) or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
                    raise ValueError(f"unsafe path in the demo bundle: {m.name}")
            tar.extractall(folder)
    state_file = folder / "eitri.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    for h in state.get("history", []):               # every output re-anchored in this folder, whatever the bundle said
        h["outputs"] = {k: _anchor(folder, v) for k, v in (h.get("outputs") or {}).items()}
    text = json.dumps(state, indent=1)
    state_file.write_text(text, encoding="utf-8")
    (folder / ".eitri").mkdir(exist_ok=True)
    (folder / ".eitri" / STATE_COPY).write_text(text, encoding="utf-8")
    demo = load_demo(demo_id, events=False)
    (folder / ".eitri" / "web.json").write_text(json.dumps({"title": demo["title"], "demo": demo_id}, indent=2), encoding="utf-8")
    marker.write_text(version, encoding="utf-8")
    return folder


def _anchor(folder: Path, path: str) -> str:
    """A recorded output path, moved into this folder (from its revisions/... or out/... part)."""
    p = str(path).replace("\\", "/")
    for mark in ("/revisions/", "/out/"):
        if mark in p:
            return str(folder / (mark.strip("/") + "/" + p.rsplit(mark, 1)[1]))
    return str(folder / Path(p).name)


def is_modified(folder: Path) -> bool:
    """Whether the installed demo was edited (built past its recorded revisions)."""
    try:
        full = json.loads((folder / ".eitri" / STATE_COPY).read_text(encoding="utf-8"))
        now = json.loads((folder / "eitri.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    return int(now.get("revision", 0)) > int(full.get("revision", 0))


def reveal(project, upto: int | None) -> None:
    """Show the demo as it stood after revision `upto` (None: all of it): the history up to there, that
    revision's model code, and the check results of the last revision that built."""
    folder = project.folder
    full = json.loads((folder / ".eitri" / STATE_COPY).read_text(encoding="utf-8"))
    history = full.get("history", [])
    if upto is None:
        upto = int(full.get("revision", 0))
    shown = [h for h in history if h["number"] <= upto]
    state = dict(full, revision=upto, history=shown)
    project.state = state
    project._save()
    current = shown[-1] if shown else None
    if current is not None:
        src = folder / current["model"]
        if src.is_file():
            shutil.copy2(src, project.model_path)
    ok = [h for h in shown if h.get("ok")]
    result = folder / "revisions" / f"{ok[-1]['number']:03d}" / "result.json" if ok else None
    out = project.out_dir
    out.mkdir(exist_ok=True)
    (out / "preview.json").unlink(missing_ok=True)       # never a stale "build in progress" geometry
    if result is not None and result.is_file():
        shutil.copy2(result, out / "result.json")
    else:
        (out / "result.json").unlink(missing_ok=True)
