"""The Messages-API backend's loop, with a scripted fake client (no network)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from eitri.agent import api_backend
from eitri.project import Project

PLATE = '''from eitri import *
PARAMS = dict(width=50.0)
def build(p):
    return Box(p.width, 30, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
'''


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeMessages:
    """First call: a build_model tool use. Second call: end_turn text."""

    def __init__(self):
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            content = [SimpleNamespace(type="text", text="Building."),
                       SimpleNamespace(type="tool_use", id="t1", name="build_model", input={"code": PLATE, "note": "first"})]
            return _Stream(SimpleNamespace(content=content, stop_reason="tool_use"))
        content = [SimpleNamespace(type="text", text="Done: a 50 x 30 x 4 plate.")]
        return _Stream(SimpleNamespace(content=content, stop_reason="end_turn"))


def test_api_session_runs_tools_and_returns_images(tmp_path, monkeypatch):
    fake = SimpleNamespace(messages=FakeMessages())
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake)
    project = Project(tmp_path / "proj", name="plate")
    events = []
    s = api_backend.APISession(project, "system", model="opus", effort="high", on_event=lambda k, d: events.append(k))
    res = s.send("make a plate")
    assert "Done" in res.text and res.tool_calls == 1
    assert project.last_ok_revision() is not None
    # second request carried the tool result with an image block
    second = fake.messages.calls[1]
    # the messages list is shared by reference: pick the user turn that carried the tool result
    user_turns = [m for m in second["messages"] if m["role"] == "user" and isinstance(m["content"], list)
                  and m["content"] and isinstance(m["content"][0], dict) and m["content"][0].get("type") == "tool_result"]
    tool_result = user_turns[-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "t1"
    kinds = [b["type"] for b in tool_result["content"]]
    assert kinds == ["text", "image"]
    assert "[revision 1]" in tool_result["content"][0]["text"]
    assert second["model"] == "claude-opus-5-5" and second["output_config"] == {"effort": "high"}
    assert second["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert events.count("tool_use") == 1 and events.count("tool_result") == 1
