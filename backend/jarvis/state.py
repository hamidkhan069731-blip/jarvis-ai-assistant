"""Central agent state machine — the single source of truth for what JARVIS
is *doing* right now.

Before this module, UI state was emitted ad-hoc from inside the orchestrator.
Now one :class:`StateMachine` owns the current :class:`AgentState`, broadcasts
every transition on the existing ``{"type": "state", "state": ...}`` wire
format (so the frontend needs no protocol change), and exposes the helpers the
core uses to reason about standby vs. active.

This module also holds the *wake / sleep* phrase detection used to drive the
STANDBY lifecycle. Detection is deliberately conservative: telling JARVIS to
"go to sleep" puts the agent into standby, but "put the *computer* to sleep"
is a power action (a real tool), never confused with agent standby.
"""
from __future__ import annotations

import enum
import re
from typing import Any, Awaitable, Callable, Optional

from jarvis.security.audit import get_logger

log = get_logger("state")

BroadcastFn = Callable[[dict], Awaitable[None]]


class AgentState(str, enum.Enum):
    """Every state JARVIS can be in. String-valued so it serializes directly to
    the WebSocket wire and compares cleanly against frontend strings."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    SPEAKING = "speaking"
    STANDBY = "standby"
    WATCHING = "watching"
    ERROR = "error"
    OFFLINE = "offline"

    @classmethod
    def coerce(cls, value: Any) -> "AgentState":
        """Best-effort convert a value (enum or wire string) to an AgentState,
        defaulting to IDLE for anything unrecognized so a stray string can never
        crash a transition."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError:
            return cls.IDLE


# States in which JARVIS is actively engaged in a turn (used by the UI to decide
# whether the "stop" affordance is shown, and by the core to avoid overlap).
_BUSY = frozenset({
    AgentState.LISTENING, AgentState.THINKING, AgentState.PLANNING,
    AgentState.EXECUTING, AgentState.WAITING_APPROVAL, AgentState.SPEAKING,
})


class StateMachine:
    """Holds the current agent state and broadcasts transitions.

    Intentionally tiny: it does not enforce a transition graph (the orchestrator
    and core drive legal sequences). Its job is to be the *one* place the state
    lives and the *one* place it is announced from.
    """

    def __init__(self, broadcast: BroadcastFn) -> None:
        self._broadcast = broadcast
        self.state: AgentState = AgentState.IDLE

    async def set(self, state: Any, **extra: Any) -> AgentState:
        state = AgentState.coerce(state)
        self.state = state
        await self._broadcast({"type": "state", "state": state.value, **extra})
        return state

    @property
    def is_standby(self) -> bool:
        return self.state is AgentState.STANDBY

    @property
    def is_busy(self) -> bool:
        return self.state in _BUSY

    def snapshot(self) -> dict:
        return {"state": self.state.value, "standby": self.is_standby}


# --------------------------------------------------------------------------- #
# Wake / sleep phrase detection
# --------------------------------------------------------------------------- #
# "Go to sleep", "goodnight", "standby" → put the AGENT into standby.
_SLEEP_RE = re.compile(
    r"\b("
    r"go(?:ing)?\s+to\s+sleep|"
    r"good\s*night|goodnight|"
    r"go\s+(?:in)?to\s+standby|enter\s+standby|standby\s+mode|"
    r"take\s+(?:a\s+)?(?:break|nap)|"
    r"so\s+ja(?:o|na|iye)?"          # Roman-Urdu "so jao" (you sleep)
    r")\b",
    re.I,
)

# Device words mean the user is talking about the *machine*, not the agent — so
# "put the computer to sleep" is a power action, never agent standby.
_DEVICE_RE = re.compile(
    r"\b(pc|computer|laptop|machine|system|screen|display|monitor)\b", re.I)


def is_sleep_phrase(text: str) -> bool:
    """True when the user is telling JARVIS *itself* to go into standby.

    Guarded against device phrasing so "sleep the PC" routes to the power tool
    rather than putting the agent to sleep.
    """
    t = text or ""
    return bool(_SLEEP_RE.search(t)) and not _DEVICE_RE.search(t)


def _wake_pattern(wake_word: str) -> "re.Pattern[str]":
    ww = re.escape((wake_word or "hey jarvis").strip().lower())
    return re.compile(
        rf"\b({ww}|wake up|jarvis wake|are you (?:there|awake)|you awake)\b", re.I)


def strip_wake_prefix(text: str, wake_word: str) -> Optional[str]:
    """If *text* contains a wake phrase, return the remaining command with the
    wake token removed (an empty string when the wake phrase was said alone).
    Returns ``None`` when there is no wake phrase at all.

    Examples (wake_word="hey jarvis"):
        "hey jarvis"                -> ""            (bare wake)
        "hey jarvis, open chrome"   -> "open chrome" (wake + command)
        "open chrome"               -> None          (no wake phrase)
    """
    m = _wake_pattern(wake_word).search(text or "")
    if not m:
        return None
    rest = (text[:m.start()] + " " + text[m.end():]).strip(" ,.!?—-")
    return re.sub(r"\s{2,}", " ", rest)
