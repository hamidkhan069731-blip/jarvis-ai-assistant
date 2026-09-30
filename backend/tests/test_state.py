"""Agent state machine + wake/sleep phrase detection (Phase 1).

Covers the invariants the standby lifecycle depends on:
  * every transition is broadcast on the ``state`` wire event,
  * an unrecognized state coerces to IDLE (never crashes a transition),
  * busy/standby flags reflect the current state,
  * "go to sleep" means the AGENT sleeps, but "sleep the computer" does not
    (that's a power action), and wake phrases strip cleanly to the command.
"""
from __future__ import annotations

import asyncio

from jarvis.state import (
    AgentState, StateMachine, is_sleep_phrase, strip_wake_prefix,
)


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# StateMachine
# --------------------------------------------------------------------------- #
def test_transitions_are_broadcast_in_order():
    events: list[dict] = []

    async def bc(evt):
        events.append(evt)

    async def go():
        sm = StateMachine(bc)
        assert sm.state is AgentState.IDLE
        await sm.set(AgentState.THINKING)
        await sm.set("executing")          # accepts wire strings too
        return sm

    sm = _run(go())
    assert sm.state is AgentState.EXECUTING
    assert [e["type"] for e in events] == ["state", "state"]
    assert [e["state"] for e in events] == ["thinking", "executing"]


def test_unknown_state_coerces_to_idle():
    events: list[dict] = []

    async def bc(evt):
        events.append(evt)

    async def go():
        sm = StateMachine(bc)
        await sm.set("teleporting")        # not a real state
        return sm

    sm = _run(go())
    assert sm.state is AgentState.IDLE
    assert events[-1]["state"] == "idle"


def test_busy_and_standby_flags():
    async def bc(evt):
        pass

    async def go():
        sm = StateMachine(bc)
        await sm.set(AgentState.PLANNING)
        assert sm.is_busy and not sm.is_standby
        await sm.set(AgentState.STANDBY)
        assert sm.is_standby and not sm.is_busy
        assert sm.snapshot() == {"state": "standby", "standby": True}
        await sm.set(AgentState.IDLE)
        assert not sm.is_busy and not sm.is_standby

    _run(go())


def test_extra_fields_pass_through():
    events: list[dict] = []

    async def bc(evt):
        events.append(evt)

    _run(StateMachine(bc).set(AgentState.WATCHING, target="clock"))
    assert events[-1] == {"type": "state", "state": "watching", "target": "clock"}


# --------------------------------------------------------------------------- #
# Sleep-phrase detection (agent standby, NOT the power tool)
# --------------------------------------------------------------------------- #
def test_agent_sleep_phrases_detected():
    for phrase in ["go to sleep", "I'm going to sleep", "goodnight",
                   "good night JARVIS", "enter standby", "go into standby",
                   "so jao"]:
        assert is_sleep_phrase(phrase), phrase


def test_device_sleep_is_not_agent_standby():
    # These are power actions ("sleep the PC"), never agent standby.
    for phrase in ["put the computer to sleep", "sleep the laptop",
                   "shut down the pc", "restart the system"]:
        assert not is_sleep_phrase(phrase), phrase


def test_ordinary_text_is_not_sleep():
    for phrase in ["what time is it", "open chrome", "find my pdfs"]:
        assert not is_sleep_phrase(phrase), phrase


# --------------------------------------------------------------------------- #
# Wake-phrase stripping
# --------------------------------------------------------------------------- #
def test_bare_wake_returns_empty_string():
    assert strip_wake_prefix("Hey JARVIS", "hey jarvis") == ""
    assert strip_wake_prefix("wake up", "hey jarvis") == ""


def test_wake_plus_command_returns_command():
    assert strip_wake_prefix("hey jarvis, open chrome", "hey jarvis") == "open chrome"
    assert strip_wake_prefix("Hey JARVIS what's my CPU", "hey jarvis") == "what's my CPU"


def test_no_wake_phrase_returns_none():
    assert strip_wake_prefix("open chrome", "hey jarvis") is None
    assert strip_wake_prefix("", "hey jarvis") is None


def test_custom_wake_word():
    assert strip_wake_prefix("computer, status report", "computer") == "status report"
    # generic wake phrases work regardless of the configured word
    assert strip_wake_prefix("wake up", "computer") == ""
    # the configured word replaces the default: "hey jarvis" no longer wakes
    assert strip_wake_prefix("hey jarvis", "computer") is None
