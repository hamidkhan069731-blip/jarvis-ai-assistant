"""OpenAI-compatible provider.

Works with the OpenAI API and any compatible endpoint (Ollama's OpenAI-compat
layer, LM Studio, vLLM, LocalAI, ...). Uses the Chat Completions + tools schema
over plain httpx so no vendor SDK is required.
"""
from __future__ import annotations

import json
from typing import Any

from jarvis.ai.provider import AIProvider, AIResponse, ToolCall
from jarvis.config import settings
from jarvis.security.audit import get_logger

log = get_logger("ai.openai")


class OpenAIProvider(AIProvider):
    name = "openai"
    mode = "cloud"

    def available(self) -> bool:
        # A local endpoint may not need a key, but the default OpenAI host does.
        try:
            import httpx  # noqa: F401
        except Exception:
            return False
        is_local = "localhost" in settings.openai_base_url or "127.0.0.1" in settings.openai_base_url
        return bool(settings.openai_api_key) or is_local

    @staticmethod
    def _to_openai(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            role = m["role"]
            if role == "user":
                out.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{
                        "id": tc.id, "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    } for tc in m["tool_calls"]]
                out.append(msg)
            elif role == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                            "content": m["content"]})
        return out

    @staticmethod
    def _tools_to_openai(tools: list[dict]) -> list[dict]:
        return [{"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
        }} for t in tools]

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> AIResponse:
        import httpx
        headers = {"Content-Type": "application/json"}
        if settings.openai_api_key:
            headers["Authorization"] = f"Bearer {settings.openai_api_key}"
        payload: dict[str, Any] = {
            "model": settings.openai_model,
            "messages": self._to_openai(system, messages),
            "max_tokens": 2048,
        }
        if tools:
            payload["tools"] = self._tools_to_openai(tools)
            payload["tool_choice"] = "auto"
        try:
            with httpx.Client(timeout=60) as client:
                r = client.post(settings.openai_base_url.rstrip("/") + "/chat/completions",
                                headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
        except Exception as exc:  # noqa: BLE001
            log.error("OpenAI call failed: %s", exc)
            return AIResponse(provider=self.name, mode=self.mode,
                              finish_reason="error", error=str(exc))

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""),
                                       arguments=args))
        return AIResponse(
            text=(msg.get("content") or "").strip(),
            tool_calls=tool_calls,
            provider=self.name, mode=self.mode,
            finish_reason="tool_calls" if tool_calls else "stop",
        )
