"""The AI orchestrator: the reasoning + tool-calling loop.

Responsibilities:
  * assemble the system prompt (identity, tools, memory, mode)
  * drive the provider -> tool-call -> permission -> execute -> repeat loop
  * request user approval for anything above the auto threshold
  * emit live events (state, tool status, approvals) to the UI
  * remain fully cancellable ("JARVIS, stop")

Providers and tools are synchronous; they run in worker threads so the asyncio
event loop that serves the UI is never blocked.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from jarvis.ai.provider import AIProvider, AIResponse, ToolCall
from jarvis.config import settings
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.security.audit import audit, get_logger
from jarvis.tools.base import registry

log = get_logger("ai.orchestrator")

MAX_ITERATIONS = 8

EmitFn = Callable[[dict], Awaitable[None]]
ApprovalFn = Callable[[dict], Awaitable[dict]]

_IDENTITY = """You are J.A.R.V.I.S. (Just A Rather Very Intelligent System), a \
capable, precise desktop AI assistant that operates the user's Windows computer \
through a set of tools. You are calm, concise, and a little witty — never \
verbose.

Operating principles:
- Decide what the user wants, then use tools to actually do it. Prefer acting \
over explaining.
- Use exactly one tool at a time and wait for its result before the next step.
- Never claim something is done unless a tool result confirms it. If a tool \
fails, say so plainly and suggest a next step.
- Anything destructive or sensitive (delete, move, run commands, install, send) \
is gated by a permission system that asks the user; do not try to bypass it.
- For quick factual questions, answer directly without tools.
- Respect the user's language (English, Urdu, or Roman Urdu) and mirror it.
Keep replies short unless asked for detail."""


class Orchestrator:
    def __init__(
        self,
        provider: AIProvider,
        emit: EmitFn,
        request_approval: ApprovalFn,
        cancel_event: Optional[asyncio.Event] = None,
        set_state: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> None:
        self.provider = provider
        self.emit = emit
        self.request_approval = request_approval
        self.cancel_event = cancel_event or asyncio.Event()
        self.permissions = PermissionEngine()
        # When the core injects a state setter we route transitions through the
        # central state machine; otherwise we fall back to emitting the same
        # wire event directly (keeps the orchestrator usable standalone / in
        # tests without a core).
        self._set_state_cb = set_state

    async def _set_state(self, state: str, **extra) -> None:
        if self._set_state_cb is not None:
            await self._set_state_cb(state, **extra)
        else:
            await self.emit({"type": "state", "state": state, **extra})

    # ------------------------------------------------------------------ #
    def _system_prompt(self) -> str:
        parts = [_IDENTITY]
        mode = "LOCAL (offline rule-based routing)" if self.provider.mode == "local" \
            else f"CLOUD via {self.provider.name}"
        parts.append(f"\nCurrent brain: {mode}.")
        # inject memory context
        try:
            from jarvis.memory.store import MemoryStore
            ctx = MemoryStore().context_block()
            if ctx:
                parts.append("\nWhat you know about the user:\n" + ctx)
        except Exception:
            pass
        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    async def handle(self, history: list[dict], user_text: str) -> AIResponse:
        """Run one full turn. `history` is prior user/assistant text turns."""
        messages: list[dict] = list(history)
        messages.append({"role": "user", "content": user_text})

        system = self._system_prompt()
        tool_schemas = registry.schemas()
        final = AIResponse(text="", provider=self.provider.name, mode=self.provider.mode)

        for iteration in range(MAX_ITERATIONS):
            if self.cancel_event.is_set():
                await self._set_state("idle")
                return AIResponse(text="Stopped.", provider=self.provider.name,
                                  mode=self.provider.mode)

            await self._set_state("thinking")
            resp = await asyncio.to_thread(
                self.provider.complete, system, messages, tool_schemas)

            if resp.finish_reason == "error":
                await self._set_state("error")
                return AIResponse(
                    text=f"My {self.provider.name} brain hit an error: {resp.error}. "
                         "Check the provider/API key in Settings, or switch to Local mode.",
                    provider=self.provider.name, mode=self.provider.mode,
                    finish_reason="error", error=resp.error)

            if not resp.wants_tools:
                final = resp
                break

            # record the assistant's tool-calling turn
            messages.append({"role": "assistant", "content": resp.text,
                             "tool_calls": resp.tool_calls})

            # the model has settled on the next action(s): that's a plan
            await self._set_state("planning")
            for tc in resp.tool_calls:
                if self.cancel_event.is_set():
                    break
                tool_msg = await self._run_tool_call(tc)
                messages.append(tool_msg)
        else:
            final = AIResponse(
                text=(resp.text or "I've taken several steps but stopped to avoid "
                      "looping. Could you clarify the goal?"),
                provider=self.provider.name, mode=self.provider.mode)

        await self._set_state("idle")
        return final

    # ------------------------------------------------------------------ #
    async def _run_tool_call(self, tc: ToolCall) -> dict:
        tool = registry.get(tc.name)
        if tool is None:
            return self._tool_result(tc, ok=False,
                                     content=f"ERROR: unknown tool {tc.name}",
                                     summary=f"Unknown tool {tc.name}")

        perm_level, risk = tool.effective_levels(**tc.arguments)
        decision = self.permissions.evaluate(tc.name, perm_level, risk)

        if decision.outcome == Outcome.DENY:
            await self.emit({"type": "tool_status", "tool": tc.name,
                             "status": "denied", "summary": decision.reason})
            return self._tool_result(tc, ok=False,
                                     content=f"DENIED: {decision.reason}",
                                     summary=decision.reason)

        if decision.needs_user:
            await self._set_state("waiting_approval")
            verdict = await self.request_approval({
                "type": "approval_request",
                "tool": tc.name,
                "category": tool.category,
                "risk": risk.value,
                "reason": decision.reason,
                "arguments": tc.arguments,
                "rememberable": decision.rememberable,
                "description": tool.description,
            })
            if not verdict.get("approved"):
                await self.emit({"type": "tool_status", "tool": tc.name,
                                 "status": "skipped", "summary": "You declined."})
                if verdict.get("remember") and decision.rememberable:
                    self.permissions.remember(tc.name, "deny")
                audit("tool_denied_by_user", tool=tc.name, args=tc.arguments)
                return self._tool_result(
                    tc, ok=False,
                    content="The user declined this action. Do not retry it; "
                            "acknowledge and ask what they'd like instead.",
                    summary="Declined by user.")
            if verdict.get("remember") and decision.rememberable:
                self.permissions.remember(tc.name, "allow")

        # execute
        await self._set_state("executing")
        await self.emit({"type": "tool_status", "tool": tc.name, "status": "running",
                         "arguments": tc.arguments})
        audit("tool_execute", tool=tc.name, args=tc.arguments, risk=risk.value)
        result = await asyncio.to_thread(registry.run, tc.name, **tc.arguments)

        await self.emit({
            "type": "tool_status", "tool": tc.name,
            "status": "done" if result.ok else "error",
            "summary": result.summary or (result.error or ""),
            "duration_ms": result.meta.get("duration_ms"),
        })
        return self._tool_result(tc, ok=result.ok,
                                 content=result.to_model_string(),
                                 summary=result.summary or (result.error or ""))

    @staticmethod
    def _tool_result(tc: ToolCall, ok: bool, content: str, summary: str) -> dict:
        return {"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                "content": content, "summary": summary, "ok": ok}
