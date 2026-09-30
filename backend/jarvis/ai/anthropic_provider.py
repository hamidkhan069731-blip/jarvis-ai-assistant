"""Anthropic (Claude) provider."""
from __future__ import annotations

import json
from typing import Any

from jarvis.ai.provider import AIProvider, AIResponse, ToolCall
from jarvis.config import settings
from jarvis.security.audit import get_logger

log = get_logger("ai.anthropic")


class AnthropicProvider(AIProvider):
    name = "anthropic"
    mode = "cloud"

    def __init__(self) -> None:
        self._client = None

    def available(self) -> bool:
        if not settings.anthropic_api_key:
            return False
        try:
            import anthropic  # noqa: F401
            return True
        except Exception:
            return False

    def _ensure_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    # -- message conversion -------------------------------------------- #
    @staticmethod
    def _to_anthropic(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            role = m["role"]
            if role == "user":
                out.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc.id,
                                   "name": tc.name, "input": tc.arguments})
                out.append({"role": "assistant", "content": blocks or ""})
            elif role == "tool":
                out.append({"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                }]})
        return out

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> AIResponse:
        try:
            client = self._ensure_client()
            resp = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=2048,
                system=system,
                messages=self._to_anthropic(messages),
                tools=tools or [],
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Anthropic call failed: %s", exc)
            return AIResponse(provider=self.name, mode=self.mode,
                              finish_reason="error", error=str(exc))

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=args))
        return AIResponse(
            text="".join(text_parts).strip(),
            tool_calls=tool_calls,
            provider=self.name, mode=self.mode,
            finish_reason="tool_calls" if tool_calls else "stop",
        )
