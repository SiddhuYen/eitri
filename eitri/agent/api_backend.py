"""Anthropic API backend: the same tools, driven by a manual tool-use loop.

Used with `--backend api` (or when no Claude Code binary is available): it needs only the Anthropic
API key eitri is configured with. Images from builds go back to the model as
image content blocks so it can review its own work.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from ..project import Project
from .claude_cli import TurnResult
from .tools import ToolResult, Tools

EventHandler = Callable[[str, dict], None]

log = logging.getLogger(__name__)

USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")

MODEL_ALIASES = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5",
                 "fable": "claude-fable-5-1"}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"name": "build_model", "description": Tools.build_model.__doc__,
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}, "note": {"type": "string"}},
                      "required": ["code"]}},
    {"name": "edit_model", "description": Tools.edit_model.__doc__,
     "input_schema": {"type": "object", "properties": {"old": {"type": "string"}, "new": {"type": "string"},
                                                       "note": {"type": "string"}}, "required": ["old", "new"]}},
    {"name": "set_params", "description": Tools.set_params.__doc__,
     "input_schema": {"type": "object", "properties": {"params": {"type": "object"}}, "required": ["params"]}},
    {"name": "read_model", "description": Tools.read_model.__doc__, "input_schema": {"type": "object", "properties": {}}},
    {"name": "inspect", "description": Tools.inspect.__doc__,
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "view", "description": Tools.view.__doc__,
     "input_schema": {"type": "object", "properties": {
         "views": {"type": "array", "items": {"type": "string"}}, "section": {"type": "string"},
         "exploded": {"type": "boolean"}, "hide": {"type": "array", "items": {"type": "string"}},
         "only": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "library", "description": Tools.library.__doc__,
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
    {"name": "export", "description": Tools.export.__doc__,
     "input_schema": {"type": "object", "properties": {"formats": {"type": "array", "items": {"type": "string"}}},
                      "required": ["formats"]}},
    {"name": "harness_reference", "description": Tools.harness_reference.__doc__,
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "mechanism_reference", "description": Tools.mechanism_reference.__doc__, "input_schema": {"type": "object", "properties": {}}},
    {"name": "pneumatics_reference", "description": Tools.pneumatics_reference.__doc__, "input_schema": {"type": "object", "properties": {}}},
    {"name": "firmware_reference", "description": Tools.firmware_reference.__doc__, "input_schema": {"type": "object", "properties": {}}},
    {"name": "design_notes", "description": Tools.design_notes.__doc__,
     "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
]


# list prices, USD per million tokens (input, output, cache read); a 5-minute cache write costs 1.25x input
PRICES = {"claude-opus-5-5": (4.00, 20.00, 0.20), "claude-sonnet-5-5": (2.00, 10.00, 0.20), "claude-haiku-5-5": (0.10, 0.50, 0.01),
          "claude-fable-5-1": (10.00, 50.00, 0.25), "claude-opus-5": (5.00, 25.00, 0.50), "claude-sonnet-5": (2.00, 10.00, 0.20)}


def usage_cost(model: str, usage: dict[str, int]) -> float:
    """Estimated USD cost of one response's usage at list prices (unknown models: priced as Opus 5.5)."""
    pin, pout, pread = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (usage.get("input_tokens", 0) * pin + usage.get("cache_creation_input_tokens", 0) * pin * 1.25
            + usage.get("cache_read_input_tokens", 0) * pread + usage.get("output_tokens", 0) * pout) / 1_000_000


def _image_block(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    media = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media,
                                        "data": base64.standard_b64encode(p.read_bytes()).decode()}}


class APISession:
    """A conversation with Claude over the Messages API, with the eitri tools."""

    def __init__(self, project: Project, system_prompt: str, model: str = "opus", effort: str | None = "high",
                 on_event: EventHandler | None = None, render_quality: float = 1.0, max_steps: int = 40,
                 api_key: str | None = None, allowed_tools: set[str] | None = None, fallbacks: bool = False,
                 on_usage: Callable[[dict[str, int], float], None] | None = None, usage_log: str | Path | None = None):
        """allowed_tools: the only tools Claude may call (default: every advertised one). on_usage(usage,
        cost) runs after each response; it may call interrupt() to end the turn (a budget ran out).
        fallbacks: the API's server-side refusal fallback (beta). usage_log: a JSONL file that gets one line per
        API response (tokens by kind, cost, step); default $EITRI_USAGE_LOG. Every record is also kept in
        self.step_log."""
        import anthropic
        from ..config import api_key as configured_key
        key = api_key or configured_key()
        self.client = anthropic.Anthropic(**({"api_key": key} if key else {}))
        advertised = {t["name"] for t in TOOL_SCHEMAS}
        self.allowed_tools = (allowed_tools & advertised) if allowed_tools is not None else advertised
        self.tool_schemas = [t for t in TOOL_SCHEMAS if t["name"] in self.allowed_tools]
        self.fallbacks = fallbacks
        self.on_usage = on_usage
        self.usage: dict[str, int] = {k: 0 for k in USAGE_KEYS}
        self.step_log: list[dict[str, Any]] = []
        self.usage_log = Path(usage_log) if usage_log else (Path(os.environ["EITRI_USAGE_LOG"]) if os.environ.get("EITRI_USAGE_LOG") else None)
        self._turn = 0
        self.project = project
        self.system_prompt = system_prompt
        self.model = MODEL_ALIASES.get(model, model)
        self.effort = effort
        self.on_event = on_event or (lambda kind, data: None)
        self.tools = Tools(project, render_quality=render_quality)
        self.messages: list[dict[str, Any]] = []
        self.max_steps = max_steps
        self.total_cost = 0.0

    def close(self) -> None:
        pass

    def _call_tool(self, name: str, inp: dict[str, Any]) -> ToolResult:
        fn = getattr(self.tools, name, None) if name in self.allowed_tools else None
        if fn is None or name.startswith("_"):
            return ToolResult(f"unknown tool {name}", is_error=True)
        try:
            return fn(**inp)
        except TypeError as exc:
            return ToolResult(f"bad arguments for {name}: {exc}", is_error=True)
        except Exception as exc:
            return ToolResult(f"{name} failed: {type(exc).__name__}: {exc}", is_error=True)

    def _images_in_history(self) -> int:
        """Image blocks the next request carries (in user turns and inside tool results)."""
        n = 0
        for m in self.messages:
            if m["role"] != "user" or not isinstance(m["content"], list):
                continue
            for b in m["content"]:
                if isinstance(b, dict):
                    n += b.get("type") == "image"
                    if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                        n += sum(1 for c in b["content"] if isinstance(c, dict) and c.get("type") == "image")
        return n

    def _account(self, response, step: int = 0, seconds: float = 0.0, images: int = 0) -> None:
        u = getattr(response, "usage", None)
        if u is None:
            return
        one = {k: int(getattr(u, k, 0) or 0) for k in USAGE_KEYS}
        for k, v in one.items():
            self.usage[k] += v
        cost = usage_cost(self.model, one)
        self.total_cost += cost
        prompt = one["input_tokens"] + one["cache_creation_input_tokens"] + one["cache_read_input_tokens"]
        rec = {"turn": self._turn, "step": step, **one, "prompt_tokens": prompt,
               "cache_read_share": round(one["cache_read_input_tokens"] / prompt, 4) if prompt else 0.0,
               "cost_usd": round(cost, 6), "seconds": round(seconds, 2), "images_in_history": images,
               "stop_reason": getattr(response, "stop_reason", None), "model": getattr(response, "model", self.model)}
        self.step_log.append(rec)
        log.info("api step %(turn)s.%(step)s: in %(input_tokens)s, cache write %(cache_creation_input_tokens)s, "
                 "cache read %(cache_read_input_tokens)s, out %(output_tokens)s, $%(cost_usd).4f", rec)
        if self.usage_log:
            try:
                with self.usage_log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
            except OSError as exc:
                log.warning("usage log not written: %s", exc)
        if self.on_usage:
            self.on_usage(one, cost)

    def interrupt(self) -> bool:
        """Stop the current turn at the next step boundary (the reply being generated finishes first)."""
        self._stop = True
        return True

    def send(self, text: str, images: list[str] | None = None, timeout: float = 3600.0) -> TurnResult:
        self._stop = False
        self._turn += 1
        t_turn, cost0, usage0 = time.time(), self.total_cost, dict(self.usage)
        content: list[dict[str, Any]] = []
        for img in images or []:
            block = _image_block(img)
            if block:
                content.append(block)
        content.append({"type": "text", "text": text})
        self.messages.append({"role": "user", "content": content})
        texts: list[str] = []
        tool_calls = 0
        steps = 0
        while True:
            steps += 1
            # Caching: an explicit breakpoint on the system prompt (tools + system, identical for every project of a
            # kind) plus top-level automatic caching, which puts a breakpoint on the last block of the history and
            # moves it forward each step, so every step reads the conversation so far at the cache-read price and
            # writes only what the last step appended. This needs the history to be append-only: self.messages is
            # never edited, and assistant turns (thinking blocks included) go back exactly as they arrived.
            kwargs: dict[str, Any] = dict(
                model=self.model, max_tokens=32000,
                system=[{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=self.messages, tools=self.tool_schemas, thinking={"type": "adaptive"},
                cache_control={"type": "ephemeral"},
            )
            if self.effort:
                kwargs["output_config"] = {"effort": self.effort}
            t_step, n_images = time.time(), self._images_in_history()
            if self.fallbacks:
                with self.client.beta.messages.stream(**kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default") as stream:
                    response = stream.get_final_message()
            else:
                with self.client.messages.stream(**kwargs) as stream:
                    response = stream.get_final_message()
            self._account(response, steps, time.time() - t_step, n_images)
            self.messages.append({"role": "assistant", "content": response.content})
            tool_uses = []
            for block in response.content:
                if block.type == "text" and block.text:
                    texts.append(block.text)
                    self.on_event("text", {"text": block.text})
                elif block.type == "tool_use":
                    tool_uses.append(block)
            if response.stop_reason == "refusal":
                texts.append("[the model declined this request]")
                break
            if not tool_uses or response.stop_reason != "tool_use":
                break
            if getattr(self, "_stop", False):               # keep the history valid: every tool use gets a result
                self.messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu.id, "content": "stopped by the user", "is_error": True} for tu in tool_uses]})
                texts.append("[stopped]")
                self.on_event("result", {"num_turns": steps, "duration_ms": 0, "total_cost_usd": None, "stopped": True})
                return TurnResult("\n".join(texts), turns=steps, tool_calls=tool_calls, stopped=True,
                                  **self._turn_totals(t_turn, cost0, usage0))
            results: list[dict[str, Any]] = []
            for tu in tool_uses:
                tool_calls += 1
                self.on_event("tool_use", {"name": tu.name, "input": tu.input, "id": tu.id})
                res = self._call_tool(tu.name, dict(tu.input))
                self.on_event("tool_result", {"id": tu.id, "text": res.text, "is_error": res.is_error})
                blocks: list[dict[str, Any]] = [{"type": "text", "text": res.text}]
                for img in res.images:
                    b = _image_block(img)
                    if b:
                        blocks.append(b)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": blocks,
                                "is_error": res.is_error})
            self.messages.append({"role": "user", "content": results})
            if steps >= self.max_steps:
                texts.append("[stopped: step limit reached]")
                break
        final = "\n".join(texts)
        self.on_event("result", {"num_turns": steps, "duration_ms": 0, "total_cost_usd": None})
        return TurnResult(final, turns=steps, tool_calls=tool_calls, **self._turn_totals(t_turn, cost0, usage0))

    def _turn_totals(self, t0: float, cost0: float, usage0: dict[str, int]) -> dict[str, Any]:
        """This turn's share of the session's cost and tokens, for TurnResult."""
        return {"cost_usd": self.total_cost - cost0, "duration_ms": int((time.time() - t0) * 1000),
                "usage": {k: self.usage[k] - usage0.get(k, 0) for k in USAGE_KEYS}}
