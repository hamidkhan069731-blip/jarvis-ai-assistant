"""Orchestrator: the reason -> tool-call -> permission -> execute loop.

Uses a scripted fake provider so the loop is deterministic, and exercises the
three behaviours that matter most for safety and correctness:
  * a SAFE tool auto-runs without ever asking for approval,
  * a CONFIRM tool is gated: declining does NOT execute it,
  * approving a CONFIRM tool executes it,
  * a pre-set cancel event stops the turn cleanly.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.ai.orchestrator import Orchestrator
from jarvis.ai.provider import AIProvider, AIResponse, ToolCall
from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import ToolResult, registry, tool


# A gated tool with an observable side effect, so we can prove whether it ran.
_STATE = {"ran": 0}


@tool("dummy_confirm", "test-only gated tool",
      permission_level=PermissionLevel.CONFIRM, risk_level=RiskLevel.HIGH)
def _dummy_confirm() -> ToolResult:
    _STATE["ran"] += 1
    return ToolResult.success(summary="did the gated thing")


class FakeProvider(AIProvider):
    """Returns a scripted sequence of AIResponses, one per complete() call."""
    name = "fake"
    mode = "cloud"

    def __init__(self, script: list[AIResponse]) -> None:
        self._script = list(script)
        self.calls = 0

    def available(self) -> bool:
        return True

    def complete(self, system, messages, tools) -> AIResponse:
        self.calls += 1
        return self._script.pop(0)


def _harness(provider, *, approve=None, cancel=False):
    """Run one orchestrator turn, capturing emitted events and approval calls."""
    events: list[dict] = []
    approvals: list[dict] = []

    async def emit(evt):
        events.append(evt)

    async def request_approval(payload):
        approvals.append(payload)
        return approve if approve is not None else {"approved": False, "remember": False}

    cancel_event = asyncio.Event()
    if cancel:
        cancel_event.set()

    orch = Orchestrator(provider=provider, emit=emit,
                        request_approval=request_approval, cancel_event=cancel_event)
    result = asyncio.run(orch.handle([], "go"))
    return result, events, approvals


# --------------------------------------------------------------------------- #
def test_safe_tool_runs_without_approval():
    provider = FakeProvider([
        AIResponse(tool_calls=[ToolCall("c1", "calculate", {"expression": "2+2"})],
                   finish_reason="tool_calls", provider="fake", mode="cloud"),
        AIResponse(text="That is 4.", provider="fake", mode="cloud"),
    ])
    result, events, approvals = _harness(provider)
    assert result.text == "That is 4."
    assert approvals == []                       # SAFE never asks
    statuses = [e for e in events if e["type"] == "tool_status"]
    assert any(e.get("status") == "done" for e in statuses)


def test_confirm_tool_declined_does_not_execute():
    _STATE["ran"] = 0
    provider = FakeProvider([
        AIResponse(tool_calls=[ToolCall("c1", "dummy_confirm", {})],
                   finish_reason="tool_calls", provider="fake", mode="cloud"),
        AIResponse(text="Okay, I won't.", provider="fake", mode="cloud"),
    ])
    result, events, approvals = _harness(provider, approve={"approved": False, "remember": False})
    assert len(approvals) == 1                   # it asked
    assert _STATE["ran"] == 0                     # ...and did NOT run
    assert result.text == "Okay, I won't."


def test_confirm_tool_approved_executes():
    _STATE["ran"] = 0
    provider = FakeProvider([
        AIResponse(tool_calls=[ToolCall("c1", "dummy_confirm", {})],
                   finish_reason="tool_calls", provider="fake", mode="cloud"),
        AIResponse(text="Done.", provider="fake", mode="cloud"),
    ])
    result, events, approvals = _harness(provider, approve={"approved": True, "remember": False})
    assert len(approvals) == 1
    assert _STATE["ran"] == 1                     # approval -> execution
    assert result.text == "Done."


def test_cancel_event_stops_turn():
    provider = FakeProvider([
        AIResponse(text="should not be reached", provider="fake", mode="cloud"),
    ])
    result, events, approvals = _harness(provider, cancel=True)
    assert result.text == "Stopped."
    assert provider.calls == 0                    # never even called the model


def test_provider_error_is_surfaced_gracefully():
    provider = FakeProvider([
        AIResponse(finish_reason="error", error="boom", provider="fake", mode="cloud"),
    ])
    result, events, approvals = _harness(provider)
    assert "error" in result.text.lower()
    assert result.error == "boom"
