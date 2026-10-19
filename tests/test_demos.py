"""The bundled demo projects: installing, resetting in the middle of a replay, and recovering a damaged
install (a demo must never turn into an empty project)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from eitri import demos
from eitri.demos import demo_folder, install, is_installed, load_demo
from eitri.web.app import Workspace

DEMO = "racket-holder"
PID = "demo-" + DEMO


def _shipped(folder: Path) -> None:
    """The folder holds the demo exactly as shipped: its model, its full history, the install marker."""
    meta = load_demo(DEMO, events=False)
    state = json.loads((folder / "eitri.json").read_text(encoding="utf-8"))
    assert state["model"] == meta["model"], state["model"]
    assert state["revision"] == meta["last_revision"]
    assert (folder / meta["model"]).is_file()
    assert (folder / ".eitri" / "thumb.png").is_file()
    assert is_installed(folder.parent.parent, DEMO)
    assert not [p for p in folder.parent.iterdir() if ".new-" in p.name or ".old-" in p.name]   # no temp folders left


def _no_session(project, on_event):
    raise AssertionError("no Claude session in these tests")


def test_failed_reinstall_keeps_the_installed_demo(tmp_path, monkeypatch):
    folder = install(tmp_path, DEMO)
    _shipped(folder)
    monkeypatch.setattr(demos, "load_demo", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError):
        install(tmp_path, DEMO, force=True)
    monkeypatch.undo()
    _shipped(folder)


def test_reset_during_a_replay_leaves_a_complete_demo(tmp_path):
    ws = Workspace(tmp_path, _no_session)
    try:
        assert ws.replay(PID, speed=0.25)["ok"]
        hub = ws.app(PID).hub
        deadline = time.time() + 30
        while not hub.busy and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(1.0)                                    # well into the replay: revisions are being revealed
        assert hub.busy
        ws.reset_demo(PID)
        time.sleep(0.5)                                    # anything the stopped replay still had to say
        folder = demo_folder(tmp_path, DEMO)
        _shipped(folder)
        project = ws.app(PID).project                      # opening it again: the demo, not a blank project
        assert project.model_path.name == load_demo(DEMO, events=False)["model"]
    finally:
        for a in list(ws.apps.values()):
            a.close()


def test_a_damaged_demo_is_reinstalled_not_opened_blank(tmp_path):
    ws = Workspace(tmp_path, _no_session)
    try:
        folder = demo_folder(tmp_path, DEMO)
        for p in list(folder.iterdir()):                   # what a failed install used to leave: almost nothing
            if p.name != ".eitri":
                (p.unlink() if p.is_file() else __import__("shutil").rmtree(p))
        (folder / ".eitri" / "demo.txt").unlink()
        project = ws.app(PID).project
        assert project.model_path.name == load_demo(DEMO, events=False)["model"]
        _shipped(folder)
    finally:
        for a in list(ws.apps.values()):
            a.close()
