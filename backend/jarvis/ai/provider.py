"""Provider abstraction: a common interface over Anthropic, OpenAI-compatible
endpoints, and a built-in offline rule-based provider.

The orchestrator speaks a single *normalized* message format and each provider
translates to/from its own wire format:

    {"role": "user",      "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [ToolCall, ...]}
    {"role": "tool",      "tool_call_id": "...", "name": "...", "content": "..."}
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional

from jarvis.config import settings
from jarvis.security.audit import get_logger

log = get_logger("ai")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AIResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = "local"
    mode: str = "local"          # cloud | local
    finish_reason: str = "stop"  # stop | tool_calls | error
    error: Optional[str] = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class AIProvider(abc.ABC):
    name: str = "base"
    mode: str = "local"

    @abc.abstractmethod
    def available(self) -> bool:
        ...

    @abc.abstractmethod
    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> AIResponse:
        ...


# --------------------------------------------------------------------------- #
def get_provider(name: Optional[str] = None) -> AIProvider:
    """Resolve the configured provider, falling back to local when a cloud
    provider is selected but unavailable (missing key/SDK)."""
    from jarvis.ai.anthropic_provider import AnthropicProvider
    from jarvis.ai.local_provider import LocalProvider
    from jarvis.ai.openai_provider import OpenAIProvider

    choice = (name or settings.get("ai_provider", "local")).lower()
    if choice == "anthropic":
        p = AnthropicProvider()
        if p.available():
            return p
        log.warning("Anthropic selected but unavailable; falling back to local.")
    elif choice == "openai":
        p = OpenAIProvider()
        if p.available():
            return p
        log.warning("OpenAI selected but unavailable; falling back to local.")
    return LocalProvider()
