"""JarvisCore — the runtime that wires every subsystem together.

It owns the WebSocket fan-out, the approval round-trip, cancellation, the chat
turn lifecycle, and the background monitor/task engines. The FastAPI layer is a
thin shell over this object.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from jarvis.ai.orchestrator import Orchestrator
from jarvis.ai.provider import get_provider
from jarvis.config import settings
from jarvis.db.repositories import (
    ConversationRepo, MessageRepo, SettingsRepo, bind_settings_override,
)
from jarvis.memory.store import MemoryStore
from jarvis.security.audit import audit, get_logger
from jarvis.state import (
    AgentState, StateMachine, is_sleep_phrase, strip_wake_prefix,
)
from jarvis.system.monitor import SystemMonitor
from jarvis.tasks.engine import TaskEngine
from jarvis.tools.base import load_builtin_tools, registry

log = get_logger("core")

APPROVAL_TIMEOUT = 180  # seconds a pending approval waits before auto-declining


class JarvisCore:
    def __init__(self) -> None:
        self._clients: set[Any] = set()
        self._pending: dict[str, asyncio.Future] = {}
        self._approval_seq = 0
        self._chat_lock = asyncio.Lock()
        self._cancel_event = asyncio.Event()
        self.provider = None
        self.monitor: Optional[SystemMonitor] = None
        self.tasks: Optional[TaskEngine] = None
        self.memory = MemoryStore()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # central agent state machine — single source of truth for what JARVIS
        # is doing. Broadcasts every transition on the existing "state" event.
        self.state = StateMachine(self.broadcast)
        self._greeted = False

    # -- lifecycle ------------------------------------------------------ #
    async def startup(self) -> None:
        self._loop = asyncio.get_running_loop()
        load_builtin_tools()
        bind_settings_override()
        # discover and load user skills (adds their tools to the registry)
        from jarvis.skills import get_skill_manager
        skills = get_skill_manager().load_all()
        self.provider = get_provider()
        self.tasks = TaskEngine(self.broadcast)
        self.monitor = SystemMonitor(self.broadcast)
        self.monitor.start()
        audit("core_startup", provider=self.provider.name, tools=len(registry.all()),
              skills=get_skill_manager().loaded_count)
        log.info("JARVIS core online — provider=%s, %d tools, %d skills",
                 self.provider.name, len(registry.all()), get_skill_manager().loaded_count)

    async def shutdown(self) -> None:
        if self.monitor:
            await self.monitor.stop()
        if self.tasks:
            self.tasks.cancel_all()
        audit("core_shutdown")

    def reload_provider(self) -> str:
        self.provider = get_provider()
        return self.provider.name

    # -- websocket fan-out --------------------------------------------- #
    def register(self, ws: Any) -> None:
        self._clients.add(ws)

    def unregister(self, ws: Any) -> None:
        self._clients.discard(ws)

    async def broadcast(self, event: dict) -> None:
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    # -- approvals ------------------------------------------------------ #
    async def _request_approval(self, payload: dict) -> dict:
        self._approval_seq += 1
        approval_id = f"appr_{self._approval_seq}"
        future: asyncio.Future = self._loop.create_future()
        self._pending[approval_id] = future
        payload = {**payload, "approval_id": approval_id}
        await self.broadcast(payload)
        try:
            return await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            await self.broadcast({"type": "approval_timeout", "approval_id": approval_id})
            return {"approved": False, "remember": False}
        finally:
            self._pending.pop(approval_id, None)

    def resolve_approval(self, approval_id: str, approved: bool, remember: bool) -> bool:
        fut = self._pending.get(approval_id)
        if fut and not fut.done():
            # Audit the *grant* as well as the denial. An audit trail that only
            # records refusals cannot answer "who approved this?" (§42).
            audit("approval_resolved", approval_id=approval_id,
                  approved=bool(approved), remember=bool(remember))
            fut.set_result({"approved": approved, "remember": remember})
            # Tell *every* client the question is settled. JARVIS can be open on
            # more than one screen, and a modal still asking about an action that
            # already ran is a lie about the system's state.
            self._fire_and_forget(self.broadcast({
                "type": "approval_resolved", "approval_id": approval_id,
                "approved": bool(approved),
            }))
            return True
        # A verdict for an unknown/expired id is dropped, never applied to
        # whatever happens to be pending now.
        audit("approval_stale", approval_id=approval_id, approved=bool(approved))
        return False

    def _fire_and_forget(self, coro) -> None:
        """Schedule a broadcast from sync code without blocking the caller."""
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:  # no loop (e.g. direct call from a test)
            coro.close()

    # -- cancellation --------------------------------------------------- #
    def stop(self) -> None:
        self._cancel_event.set()
        if self.tasks:
            self.tasks.cancel_all()
        # decline any pending approvals
        for aid, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_result({"approved": False, "remember": False})

    # -- agent state / standby ----------------------------------------- #
    async def set_state(self, state: Any, **extra: Any) -> None:
        """Injected into the orchestrator so every state transition flows
        through the one central state machine."""
        await self.state.set(state, **extra)

    @property
    def is_standby(self) -> bool:
        return self.state.is_standby

    async def enter_standby(self) -> None:
        """Put the agent to sleep: stop any active turn, pause telemetry to save
        resources, and hold in STANDBY until a wake phrase arrives."""
        if self.state.is_standby:
            return
        self.stop()
        if self.monitor:
            await self.monitor.stop()   # event-driven standby — no polling asleep
        await self.state.set(AgentState.STANDBY)
        audit("standby_enter")
        log.info("entering standby")

    async def wake(self) -> bool:
        """Wake from standby. Returns True only if we were actually asleep."""
        if not self.state.is_standby:
            return False
        if self.monitor:
            self.monitor.start()
        await self.state.set(AgentState.IDLE)
        audit("standby_wake")
        log.info("waking from standby")
        return True

    async def on_client_ready(self) -> None:
        """Called once a UI client has connected and been greeted with hello.
        Optionally delivers the spoken boot greeting (opt-in via settings)."""
        if self._greeted or not settings.get("boot_greeting", False):
            return
        self._greeted = True
        await self._say(None, "", _boot_greeting_text())

    # -- chat turn ------------------------------------------------------ #
    async def handle_user_text(self, conversation_id: Optional[int], text: str) -> dict:
        """Front door for every user utterance.

        Handles the wake/sleep lifecycle *before* the AI brain is ever invoked,
        so standby genuinely suspends reasoning (not a fake UI state):
          * a wake phrase wakes the agent (and, said alone, just acknowledges);
          * a sleep phrase enters standby without calling the provider;
          * while in standby, non-wake input is declined until woken.
        Anything else is a normal turn handled by :meth:`handle_chat`.
        """
        original = (text or "").strip()
        if not original:
            return await self.handle_chat(conversation_id, original)

        wake_word = settings.get("wake_word", "hey jarvis")
        text = original

        rest = strip_wake_prefix(text, wake_word)
        if rest is not None:
            await self.wake()  # no-op if already awake
            if rest == "":
                return await self._say(conversation_id, original, "Yes, sir?")
            text = rest  # a command followed the wake word — process it below

        if is_sleep_phrase(text):
            await self.enter_standby()
            return await self._say(
                conversation_id, original,
                "Going into standby, sir. Say “Hey JARVIS” when you need me.")

        if self.state.is_standby:
            return await self._say(
                conversation_id, original,
                "I'm in standby. Say “Hey JARVIS” to wake me.")

        return await self.handle_chat(conversation_id, text)

    async def _say(self, conversation_id: Optional[int], user_text: str,
                   reply: str) -> dict:
        """Persist + broadcast an assistant reply *without* invoking the AI
        brain. Used for wake/sleep/standby acknowledgements so they are fast,
        deterministic, and never burn a provider call."""
        conv_repo = ConversationRepo()
        msg_repo = MessageRepo()
        if not conversation_id:
            title = (user_text[:40] + "…") if len(user_text) > 40 else (user_text or "Session")
            conversation_id = conv_repo.create(title)
        if user_text:
            msg_repo.add(conversation_id, "user", user_text)
        meta = {"provider": "core", "mode": "system"}
        msg_repo.add(conversation_id, "assistant", reply, meta=meta)
        result = {
            "type": "message", "role": "assistant", "content": reply,
            "conversation_id": conversation_id, "provider": "core",
            "mode": "system", "speak": True,
        }
        await self.broadcast(result)
        return result

    async def handle_chat(self, conversation_id: Optional[int], text: str) -> dict:
        conv_repo = ConversationRepo()
        msg_repo = MessageRepo()

        if not conversation_id:
            title = (text[:40] + "…") if len(text) > 40 else text
            conversation_id = conv_repo.create(title or "New conversation")

        msg_repo.add(conversation_id, "user", text)
        audit("user_message", conversation_id=conversation_id, text=text)

        # build prior text history (exclude the message we just added)
        history_rows = msg_repo.history(conversation_id)[:-1]
        history = [{"role": r["role"], "content": r["content"]}
                   for r in history_rows if r["role"] in ("user", "assistant")]

        self._cancel_event = asyncio.Event()  # fresh per turn
        orchestrator = Orchestrator(
            provider=self.provider,
            emit=self.broadcast,
            request_approval=self._request_approval,
            cancel_event=self._cancel_event,
            set_state=self.set_state,
        )

        async with self._chat_lock:
            response = await orchestrator.handle(history, text)

        meta = {"provider": response.provider, "mode": response.mode}
        msg_repo.add(conversation_id, "assistant", response.text, meta=meta)

        # opportunistically title a fresh conversation
        result = {
            "type": "message", "role": "assistant", "content": response.text,
            "conversation_id": conversation_id, "provider": response.provider,
            "mode": response.mode, "speak": True,
        }
        await self.broadcast(result)
        return result

    # -- diagnostics ---------------------------------------------------- #
    def diagnostics(self) -> dict:
        from jarvis.voice import get_stt, get_tts
        from jarvis.skills import get_skill_manager
        prov = self.provider
        return {
            "provider": {"name": prov.name if prov else None,
                         "mode": prov.mode if prov else None,
                         "available": bool(prov)},
            "state": self.state.state.value,
            "standby": self.state.is_standby,
            "tools": len(registry.all()),
            "skills": get_skill_manager().loaded_count,
            "monitor_running": bool(self.monitor and self.monitor._task
                                    and not self.monitor._task.done()),
            "clients": len(self._clients),
            "tts": {"available": get_tts().available()},
            "stt": get_stt().status(),
            "database": str(settings.db_path),
            "permission_mode": settings.get("permission_mode", "balanced"),
        }


core = JarvisCore()


def _boot_greeting_text() -> str:
    """Time-of-day boot greeting. Original wording (not film dialogue)."""
    import datetime
    hour = datetime.datetime.now().hour
    part = ("morning" if 5 <= hour < 12 else
            "afternoon" if 12 <= hour < 17 else
            "evening" if 17 <= hour < 22 else "night")
    return f"Good {part}, sir. All systems online and standing by."
