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


def _usage(inp, write, read, out):
    return SimpleNamespace(input_tokens=inp, cache_creation_input_tokens=write, cache_read_input_tokens=read, output_tokens=out)


class ThreeStepMessages:
    """build -> view -> end_turn, each response with usage and a thinking block (as Opus 5.5 returns them).
    Records a JSON snapshot of every request, because the session shares its messages list by reference."""

    def __init__(self):
        self.calls, self.snapshots, self.thinking = [], [], []

    def stream(self, **kwargs):
        import json
        self.calls.append(kwargs)
        self.snapshots.append(json.dumps(kwargs["messages"], default=lambda o: vars(o), sort_keys=True))
        n = len(self.calls)
        think = SimpleNamespace(type="thinking", thinking="", signature=f"sig-{n}")
        self.thinking.append(think)
        if n == 1:
            content = [think, SimpleNamespace(type="tool_use", id="t1", name="build_model", input={"code": PLATE})]
            stop = "tool_use"
        elif n == 2:
            content = [think, SimpleNamespace(type="tool_use", id="t2", name="view", input={"views": ["iso"]})]
            stop = "tool_use"
        else:
            content = [think, SimpleNamespace(type="text", text="Done.")]
            stop = "end_turn"
        return _Stream(SimpleNamespace(content=content, stop_reason=stop, model="claude-opus-5-5",
                                       usage=_usage(100 * n, 2000, 20000 * (n > 1), 300)))


def test_history_is_cached_append_only_and_usage_is_logged_per_step(tmp_path, monkeypatch):
    fake = SimpleNamespace(messages=ThreeStepMessages())
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake)
    project = Project(tmp_path / "proj", name="plate")
    log_file = tmp_path / "usage.jsonl"
    s = api_backend.APISession(project, "system", model="opus", effort="high", usage_log=log_file)
    res = s.send("make a plate")
    calls = fake.messages.calls
    assert len(calls) == 3
    for kw in calls:
        # automatic caching moves a breakpoint onto the newest block every step; the system prompt keeps its own
        assert kw["cache_control"] == {"type": "ephemeral"}
        assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    # append-only: each request's history starts with the previous request's history, byte for byte
    snaps = fake.messages.snapshots
    for a, b in zip(snaps, snaps[1:]):
        assert b.startswith(a[:-1]), "an earlier turn was edited"
    # thinking blocks go back exactly as they arrived (same objects, signatures intact)
    sent = [blk for m in s.messages if m["role"] == "assistant" for blk in m["content"] if getattr(blk, "type", "") == "thinking"]
    assert sent == fake.messages.thinking and [t.signature for t in sent] == ["sig-1", "sig-2", "sig-3"]
    # one usage record per response, in memory and in the JSONL file
    import json
    rows = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert rows == s.step_log and [r["step"] for r in rows] == [1, 2, 3]
    assert rows[1]["cache_read_input_tokens"] == 20000 and rows[1]["prompt_tokens"] == 200 + 2000 + 20000
    assert rows[0]["images_in_history"] == 0 and rows[1]["images_in_history"] == 1 and rows[2]["images_in_history"] == 2
    assert abs(res.cost_usd - sum(r["cost_usd"] for r in rows)) < 1e-6 and res.usage["output_tokens"] == 900
    assert abs(res.cost_usd - api_backend.usage_cost("claude-opus-5-5", res.usage)) < 1e-6


class TwoTurnMessages(ThreeStepMessages):
    """ThreeStepMessages, plus a beta namespace: per-message effort changes need the beta endpoint."""

    def __init__(self):
        super().__init__()
        self.betas: list[list[str] | None] = []

    def stream(self, betas=None, **kwargs):
        self.betas.append(betas)
        n = len(self.calls) + 1
        if n >= 4:      # second turn: one text reply
            import json
            self.calls.append(kwargs)
            self.snapshots.append(json.dumps(kwargs["messages"], default=lambda o: vars(o), sort_keys=True))
            content = [SimpleNamespace(type="text", text="Edited.")]
            return _Stream(SimpleNamespace(content=content, stop_reason="end_turn", model="claude-opus-5-5",
                                           usage=_usage(3, 500, 25000, 100)))
        return super().stream(**kwargs)


def test_followup_effort_is_a_per_message_change_that_keeps_the_cached_history(tmp_path, monkeypatch):
    msgs = TwoTurnMessages()
    fake = SimpleNamespace(messages=msgs, beta=SimpleNamespace(messages=msgs))
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake)
    project = Project(tmp_path / "proj", name="plate")
    s = api_backend.APISession(project, "system", model="opus", effort="high", followup_effort="low")
    s.send("make a plate")
    # the first turn is exactly today's request: top-level effort, no per-message change, no beta
    assert all(kw["output_config"] == {"effort": "high"} for kw in msgs.calls[:3]) and msgs.betas[:3] == [None] * 3
    assert not any(m["role"] == "system" for m in s.messages)
    s.send("make it 70 wide")
    req = msgs.calls[3]
    # the top-level effort never changes (that would restart the messages cache) ...
    assert req["output_config"] == {"effort": "high"}
    # ... the follow-up's effort rides on an effort-only system message right after its user turn, under the beta
    import json
    sent = json.loads(msgs.snapshots[3])          # the request as sent (the session shares its list by reference)
    assert sent[-2]["role"] == "user" and sent[-2]["content"][-1]["text"] == "make it 70 wide"
    assert sent[-1] == {"role": "system", "content": [], "output_config": {"effort": "low"}}
    assert msgs.betas[3] == [api_backend.EFFORT_BETA]
    # and the history is still append-only: the follow-up request extends the first turn's last request
    assert msgs.snapshots[3].startswith(msgs.snapshots[2][:-1])
    # a third turn at the same follow-up level adds nothing; an explicit per-turn effort adds one more change
    n_sys = sum(m["role"] == "system" for m in s.messages)
    s.send("thanks")
    assert sum(m["role"] == "system" for m in s.messages) == n_sys
    s.send("now a hard one", effort="xhigh")
    assert s.messages[-2] == {"role": "system", "content": [], "output_config": {"effort": "xhigh"}}
    # a resumed chat (new session, project already built): its first turn is a follow-up edit
    msgs2 = TwoTurnMessages()
    msgs2.calls = [None] * 3                       # answer with the one-reply second-turn script
    monkeypatch.setattr(anthropic, "Anthropic", lambda: SimpleNamespace(messages=msgs2, beta=SimpleNamespace(messages=msgs2)))
    s2 = api_backend.APISession(project, "system", model="opus", effort="high", followup_effort="low")
    s2.send("make it 80 wide")
    assert msgs2.betas == [[api_backend.EFFORT_BETA]]
    assert s2.messages[1] == {"role": "system", "content": [], "output_config": {"effort": "low"}}


def test_without_followup_effort_requests_are_unchanged(tmp_path, monkeypatch):
    msgs = TwoTurnMessages()
    fake = SimpleNamespace(messages=msgs, beta=SimpleNamespace(messages=msgs))
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake)
    s = api_backend.APISession(Project(tmp_path / "proj", name="plate"), "system", model="opus", effort="high")
    s.send("make a plate")
    s.send("make it 70 wide")
    assert msgs.betas == [None] * 4 and not any(m["role"] == "system" for m in s.messages)
    assert all(kw["output_config"] == {"effort": "high"} for kw in msgs.calls)
