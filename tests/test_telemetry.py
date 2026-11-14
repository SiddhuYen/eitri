"""The two anonymous counts (eitri.telemetry): exactly the agreed payload, the off switches, once-per-id install, and nothing
sent from tests or sandboxes. No network here: the sender is replaced."""
from __future__ import annotations

import json
import sys
import uuid

import pytest

from eitri import __version__, telemetry as T

KEYS = {"event", "id", "version", "os", "agent"}


@pytest.fixture
def sent(monkeypatch):
    """Counts on (as outside tests), every POST captured instead of sent."""
    got: list[dict] = []
    monkeypatch.setattr(T, "_sender", lambda body: got.append(body))
    for v in ("EITRI_TELEMETRY", "EITRI_NO_PING", "DO_NOT_TRACK", "EITRI_SANDBOX_WORK"):
        monkeypatch.delenv(v, raising=False)
    real = T._env_off
    monkeypatch.setattr(T, "_env_off", lambda: None if real() == "running under tests" else real())
    return got


def _wait(got, n):
    import time
    for _ in range(100):
        if len(got) >= n:
            return
        time.sleep(0.02)


def test_off_by_default_under_tests():
    assert not T.enabled() and T.send("install") is None


def test_payload_is_exactly_the_contract(monkeypatch):
    monkeypatch.setenv("EITRI_HOST_AGENT", "Claude Code")
    p = T.payload("project_created", kind="harness")
    assert set(p) == KEYS | {"kind"} and p["kind"] == "wires" and p["agent"] == "claude-code"
    assert p["event"] == "project_created" and p["version"] == __version__ and p["os"] in ("darwin", "linux")
    assert str(uuid.UUID(p["id"])) == p["id"] and uuid.UUID(p["id"]).version == 4
    i = T.payload("install", kind="cad", agent="codex")                    # kind is never sent with install
    assert set(i) == KEYS and i["agent"] == "codex" and i["id"] == p["id"]  # one id per install
    assert len(json.dumps(i)) < 1024
    assert T.payload("other") is None and T.payload("project_created", kind="lathe") is None
    assert T.payload("project_created") is None                            # project_created always says its kind
    assert {T.payload("project_created", kind=k)["kind"] for k in ("cad", "mechanism", "pneumatics", "firmware", "electronics", "combined")} == \
        {"cad", "motion", "pneumatics", "firmware", "electronics", "combined"}
    for raw, want in (("Codex", "codex"), ("Cursor", "cursor"), ("", "unknown"), ("vim", "unknown")):
        assert T.agent_name(raw) == want
    monkeypatch.setattr(sys, "platform", "win32")
    assert T.payload("install") is None                                    # only darwin | linux


def test_send_posts_in_the_background(sent):
    t = T.send("project_created", kind="combined")
    t.join(3)
    assert len(sent) == 1 and sent[0]["kind"] == "combined" and set(sent[0]) == KEYS | {"kind"}


@pytest.mark.parametrize("var,val", [("EITRI_TELEMETRY", "off"), ("EITRI_NO_PING", "1"), ("DO_NOT_TRACK", "1"),
                                     ("EITRI_SANDBOX_WORK", "/work")])
def test_every_off_switch(sent, monkeypatch, var, val):
    monkeypatch.setenv(var, val)
    assert T.off_reason() and T.send("install") is None and T.send_install() is None and sent == []


def test_eitri_telemetry_off_is_saved(sent, capsys):
    assert T.main("off") == 0 and "no anonymous counts" in capsys.readouterr().out
    assert T.off_reason() == "turned off with `eitri telemetry off`" and T.send("install") is None
    assert T.main("on") == 0 and T.enabled()
    T.main("status")
    out = capsys.readouterr().out
    assert "anonymous counts: on" in out and "eitri telemetry off" in out


def test_install_is_counted_once_per_id(sent):
    assert T.send_install(agent="cursor") is not None
    assert T.send_install(agent="cursor") is None                          # a refresh after an update sends nothing
    assert [b["event"] for b in sent] == ["install"] and sent[0]["agent"] == "cursor"


def test_a_failing_post_never_raises(monkeypatch, sent):
    def boom(body):
        raise OSError("no network")
    monkeypatch.setattr(T, "_sender", boom)
    t = T.send("install")
    t.join(3)                                                               # the thread died quietly; nothing raised here
    monkeypatch.setattr(T, "URL", "http://127.0.0.1:9/api/ping")           # the real sender, nothing listening: swallowed
    T._post({"event": "install"})


def test_first_run_notice_once(sent, capsys):
    import io
    buf = io.StringIO()
    assert T.first_run_notice(buf) and "eitri telemetry off" in buf.getvalue()
    assert not T.first_run_notice(io.StringIO())


def test_the_servers_count_new_projects(sent, tmp_path, monkeypatch):
    """combined_new_project counts at once ("combined"); a single-domain new_project at its first build, with its kind."""
    monkeypatch.setenv("EITRI_NO_WARM", "1")
    from eitri.agent.combined_server import CombinedSession
    s = CombinedSession(root=tmp_path / "c", open_browser=False)
    s.ws.new_project("arm")
    _wait(sent, 1)
    assert [(b["event"], b["kind"]) for b in sent] == [("project_created", "combined")]
    from eitri.agent.plugin import PluginSession
    p = PluginSession(root=tmp_path / "s", open_browser=False)
    p.new_project("lead")
    assert len(sent) == 1                                                   # not yet: the kind is known at the first build
    (p.project.model_path).write_text("from eitri.harness import *\n\ndef build(p):\n    return Harness('x')\n", encoding="utf-8")
    p._on_event("build_start", {})
    p._on_event("build_start", {})                                          # once per project
    _wait(sent, 2)
    assert [(b["event"], b.get("kind")) for b in sent] == [("project_created", "combined"), ("project_created", "wires")]


def test_docs_say_what_is_sent():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for f in ("README.md", "AGENTS.md"):
        text = (root / f).read_text(encoding="utf-8")
        assert "eitri telemetry off" in text and "no telemetry" not in text.lower(), f
    sh = (root / "scripts" / "install-remote.sh").read_text(encoding="utf-8")
    assert "eitri.io/api/ping" in sh and "eitri telemetry off" in sh
