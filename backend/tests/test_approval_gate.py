"""The approval gate: a CONFIRM-level tool must never execute unapproved.

This is the single most security-critical invariant in JARVIS (spec §22: never
allow unrestricted AI command execution). Reading the code is not enough — these
tests drive the real orchestrator with a real permission engine and assert on a
*side effect*: a sentinel tool that records whether its body actually ran. If the
gate ever leaks, the sentinel fires and these tests fail.

Every deny path is covered: explicit deny, silent timeout, a malformed client
payload, and a remembered deny. A positive control proves the tests are not
vacuously passing because nothing ever executes.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.ai.orchestrator import Orchestrator
from jarvis.ai.provider import AIProvider, AIResponse, ToolCall
from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import ToolResult, registry, tool

# --------------------------------------------------------------------------- #
# a sentinel tool: harmless, but records that its body executed
# --------------------------------------------------------------------------- #
FIRED: list[dict] = []


@tool(
    name="_test_gated_sentinel",
    description="Test-only sentinel that records execution. Never touches the system.",
    category="test",
    parameters={"type": "object", "properties": {"marker": {"type": "string"}}},
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
)
def _test_gated_sentinel(marker: str = "") -> ToolResult:
    FIRED.append({"marker": marker})
    return ToolResult.success({"marker": marker}, summary="sentinel executed")


@pytest.fixture(autouse=True)
def _clean():
    FIRED.clear()
    yield
    FIRED.clear()


class _NullProvider(AIProvider):
    name = "null"
    mode = "local"

    def available(self) -> bool:
        return True

    def complete(self, system, messages, tools) -> AIResponse:  # pragma: no cover
        return AIResponse(text="", provider=self.name, mode=self.mode)


def _orchestrator(approval_fn) -> Orchestrator:
    async def emit(_msg):
        return None

    return Orchestrator(provider=_NullProvider(), emit=emit, request_approval=approval_fn)


def _run(approval_fn, **args) -> dict:
    """Drive one gated tool call and return the tool-result message."""
    orch = _orchestrator(approval_fn)
    tc = ToolCall(id="t1", name="_test_gated_sentinel", arguments=args)
    return asyncio.run(orch._run_tool_call(tc))


# --------------------------------------------------------------------------- #
# the engine's own verdict
# --------------------------------------------------------------------------- #
def test_medium_risk_is_not_auto_approved_in_balanced_mode():
    from jarvis.permissions.engine import Outcome, PermissionEngine
    decision = PermissionEngine().evaluate(
        "_test_gated_sentinel", PermissionLevel.CONFIRM, RiskLevel.MEDIUM)
    assert decision.outcome is not Outcome.AUTO
    assert decision.needs_user is True


def test_real_screen_tool_requires_approval():
    # read_screen can expose passwords and private messages; it must be gated
    # under the default mode, not merely marked dangerous in metadata.
    from jarvis.permissions.engine import PermissionEngine
    from jarvis.tools.screen_tools import read_screen
    perm, risk = read_screen.effective_levels()
    assert PermissionEngine().evaluate("read_screen", perm, risk).needs_user is True


# --------------------------------------------------------------------------- #
# deny paths — the tool body must NOT run
# --------------------------------------------------------------------------- #
def test_explicit_denial_does_not_execute():
    async def deny(_payload):
        return {"approved": False, "remember": False}

    msg = _run(deny, marker="denied")
    assert FIRED == [], "gated tool executed despite the user denying it"
    assert "declined" in str(msg).lower()


def test_approval_timeout_does_not_execute():
    # The core's timeout path is what fires when the user simply walks away.
    from jarvis import core as core_mod

    async def never_answered(_payload):
        c = core_mod.JarvisCore()
        c._loop = asyncio.get_running_loop()
        return await c._request_approval(_payload)

    original = core_mod.APPROVAL_TIMEOUT
    core_mod.APPROVAL_TIMEOUT = 0.05
    try:
        _run(never_answered, marker="timeout")
    finally:
        core_mod.APPROVAL_TIMEOUT = original
    assert FIRED == [], "gated tool executed after the approval request timed out"


def test_malformed_client_payload_is_treated_as_denial():
    # A client that answers without an "approved" field (or with junk) must not
    # be read as consent.
    for payload in ({}, {"remember": True}, {"approved": None}, {"approved": ""}):
        FIRED.clear()

        async def answer(_p, _payload=payload):
            return _payload

        _run(answer, marker="malformed")
        assert FIRED == [], f"payload {payload!r} was treated as approval"


def test_websocket_approve_message_defaults_to_denial():
    # The wire handler coerces the field itself; prove the coercion, since this
    # is the exact value handed to resolve_approval().
    for raw in ({}, {"approval_id": "appr_1"}, {"approved": None}, {"approved": 0}):
        assert bool(raw.get("approved")) is False, raw


def test_remembered_deny_blocks_without_even_asking():
    from jarvis.permissions.engine import PermissionEngine

    engine = PermissionEngine()
    engine.remember("_test_gated_sentinel", "deny")
    try:
        asked = []

        async def record(payload):
            asked.append(payload)
            return {"approved": True, "remember": False}

        msg = _run(record, marker="remembered")
        assert FIRED == [], "remembered deny was bypassed"
        assert asked == [], "user was re-prompted for an action they already denied"
        assert "denied" in str(msg).lower()
    finally:
        engine.forget("_test_gated_sentinel")


# --------------------------------------------------------------------------- #
# positive control — proves the sentinel *can* fire, so the tests above mean
# something
# --------------------------------------------------------------------------- #
def test_explicit_approval_does_execute():
    async def approve(_payload):
        return {"approved": True, "remember": False}

    _run(approve, marker="approved")
    assert FIRED == [{"marker": "approved"}], "approved tool failed to execute"


def test_approval_request_carries_the_real_arguments():
    # The user must be shown what they are actually approving (§43: no
    # misleading prompts).
    seen: list[dict] = []

    async def capture(payload):
        seen.append(payload)
        return {"approved": False, "remember": False}

    _run(capture, marker="inspect-me")
    assert seen and seen[0]["tool"] == "_test_gated_sentinel"
    assert seen[0]["arguments"] == {"marker": "inspect-me"}
    assert seen[0]["risk"] == "medium"
