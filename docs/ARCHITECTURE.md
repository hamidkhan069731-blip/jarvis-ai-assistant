# Architecture

JARVIS is a single-user desktop assistant with a Python backend and a
zero-build vanilla-JS frontend, talking over a local WebSocket. This document
describes the layers, the runtime, the data model, and the wire protocol.

---

## 1. High-level shape

```
┌──────────────────────────────────────────────────────────────┐
│  Browser (frontend/, ES modules, no build step)              │
│  HUD console · arc-reactor orb · gauges · Web Speech STT/TTS  │
└───────────────▲───────────────────────────┬──────────────────┘
                │  WebSocket /ws  (JSON)     │  REST /api/*
                │  realtime events           │  snapshots & mutations
┌───────────────┴───────────────────────────▼──────────────────┐
│  FastAPI app  (jarvis/server.py)                              │
│  thin translation shell over the core                         │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  JarvisCore  (jarvis/core.py)                                 │
│  client fan-out · approval round-trip · chat lifecycle ·      │
│  cancellation · owns monitor + task engine                    │
└───┬───────────┬───────────┬───────────┬───────────┬───────────┘
    │           │           │           │           │
┌───▼───┐  ┌────▼────┐  ┌───▼────┐  ┌───▼────┐  ┌───▼─────┐
│  AI   │  │ Perms   │  │ Tools  │  │ Memory │  │ System  │
│ layer │  │ engine  │  │ (36)   │  │ store  │  │ monitor │
└───┬───┘  └─────────┘  └───┬────┘  └────────┘  └─────────┘
    │                       │
┌───▼───────────────────────▼──────────────────────────────────┐
│  SQLite (WAL, thread-safe)  ·  security guard  ·  audit log    │
└───────────────────────────────────────────────────────────────┘
```

---

## 2. The layers (spec mapping)

The master spec asks for 17+ layers. Here is where each lives:

| Layer | Module(s) |
|---|---|
| UI / HUD | `frontend/` |
| Voice (STT/TTS, wake word) | `frontend/js/voice.js`, `jarvis/voice/` |
| AI brain / providers | `jarvis/ai/provider.py`, `anthropic_provider.py`, `openai_provider.py`, `local_provider.py` |
| Reasoning + tool-calling | `jarvis/ai/orchestrator.py` |
| Memory (short/long term) | `jarvis/memory/store.py`, `jarvis/db/repositories.py` (`MemoryRepo`) |
| Tools / capabilities | `jarvis/tools/*` |
| Computer control | `jarvis/tools/app_tools.py`, `system_tools.py` |
| File management | `jarvis/tools/file_tools.py` |
| Browser / web | `jarvis/tools/web_tools.py` |
| Code / shell execution | `jarvis/tools/shell_tools.py` |
| Task automation / long-running agents | `jarvis/tasks/engine.py` |
| Notifications | monitor + task engine → `notification` events |
| System monitoring | `jarvis/system/monitor.py` |
| Security / permissions | `jarvis/permissions/engine.py`, `jarvis/security/guard.py` |
| Logging / audit | `jarvis/security/audit.py`, `audit_log` table |
| Settings | `jarvis/config.py` + `settings` table |
| Plugin / skill system | `jarvis/skills/__init__.py`, `skills/` |
| Persistence / backup | `jarvis/db/` (+ `data/backups/`) |

---

## 3. Runtime lifecycle

`JarvisCore.startup()` (invoked by FastAPI's lifespan) does, in order:

1. `load_builtin_tools()` — imports the tool modules so their `@tool`
   decorators self-register into the global `ToolRegistry`.
2. `bind_settings_override()` — wires live DB settings into `settings.get()`,
   so UI changes take effect without a restart.
3. `get_skill_manager().load_all()` — discovers and imports user skills,
   adding their tools to the same registry.
4. `get_provider()` — resolves the configured AI provider (falls back to Local
   if a cloud provider is selected but unavailable).
5. Starts the **system monitor** (2 s cadence) and the **task engine**.

`shutdown()` stops the monitor and cancels outstanding tasks.

### 3.1 Agent state machine

`jarvis/state.py` owns the single source of truth for what JARVIS is doing.
`StateMachine.set()` stores the new state and broadcasts it as a `state` event,
so the HUD always reflects real backend state rather than a UI guess.

| state | meaning |
|---|---|
| `idle` | awake, waiting for a command |
| `listening` | capturing microphone audio |
| `thinking` | provider is generating a response |
| `planning` | the model returned tool calls; deciding execution |
| `executing` | a tool is actually running |
| `waiting_approval` | blocked on a user decision |
| `speaking` | reading a reply aloud |
| `standby` | asleep — commands are refused until woken |
| `watching` | monitoring something on the user's behalf |
| `error` / `offline` | last turn failed / no provider reachable |

Unknown values coerce to `idle`, so a bad transition can never wedge the UI.

**Standby is real, not cosmetic.** `core.handle_user_text()` is the front door
for every user utterance and runs *before* the provider is invoked:

1. A wake phrase (the configured `wake_word`, or "wake up") exits standby. A
   bare wake gets "Yes, sir?"; a wake plus a command runs the command.
2. A sleep phrase ("go to sleep", "goodnight") enters standby: the current turn
   is stopped and the system monitor is paused, so an asleep JARVIS genuinely
   stops working rather than merely looking asleep.
3. While in standby any other text is refused with a reminder of the wake word —
   no provider call, no tool execution.

Device wording is deliberately excluded from sleep detection, so "put the
**computer** to sleep" is a `power_control` action while "go to sleep" is agent
standby. The same split keeps "restart chrome" an app action and "restart the
pc" a power action.

---

## 4. A chat turn, end to end

```
user text ──▶ server._handle_ws_message ──▶ core.handle_chat
                                              │
                                              ▼
                        Orchestrator.handle(history, text)   [jarvis/ai/orchestrator.py]
                                              │
          ┌───────────────────────────────────┼───────────────────────────────┐
          ▼                                    ▼                               ▼
   provider.complete()                 for each tool_call:              emit live events
   (in a worker thread)                _run_tool_call()                 (state, tool_status,
          │                                    │                          approval_request)
          │                          effective_levels(**args)
          │                                    │
          │                          PermissionEngine.evaluate()
          │                          ┌─────────┴──────────┐
          │                        AUTO              CONFIRM / ALWAYS_CONFIRM
          │                          │                    │
          │                          │        request_approval() ⇄ user
          │                          ▼                    ▼ (approved)
          │                    registry.run(name, **args)  ── executes tool in worker thread
          │                          │
          ▼                          ▼
   final assistant text  ◀── tool result fed back into the loop (up to MAX_ITERATIONS=8)
```

Key properties:

- **Providers and tools are synchronous** and always run via
  `asyncio.to_thread`, so the event loop serving the UI is never blocked.
- **One tool at a time**, result observed before the next step.
- **Fully cancellable** — a `cancel_event` is checked between steps; "stop"
  from the UI resolves it and declines any pending approval.
- The loop is bounded (`MAX_ITERATIONS`) to prevent runaway tool use.

---

## 5. Provider abstraction

All providers implement `AIProvider` (`available()`, `complete(system,
messages, tools) -> AIResponse`) and speak one normalized message format:

```
{"role": "user",      "content": "..."}
{"role": "assistant", "content": "...", "tool_calls": [ToolCall, ...]}
{"role": "tool",      "tool_call_id": "...", "name": "...", "content": "..."}
```

Each provider translates to/from its own wire format. `get_provider()` reads
`ai_provider` and returns Anthropic / OpenAI-compatible / Local, **falling back
to Local** when a cloud key or SDK is missing — so the app always works.

The **Local** provider (`local_provider.py`) is an ordered regex intent router.
It maps utterances to real `ToolCall`s (or a conversational reply). It is
explicitly *not* a fake LLM; it supports English and Roman Urdu (verb-first
*and* verb-final phrasing) and returns a helpful capability list when unmatched.

---

## 6. Tools & the registry

A `Tool` bundles: `name`, `description`, `parameters` (JSON schema),
`permission_level`, `risk_level`, `category`, `dangerous`, and an `execute()`
implementation. Simple tools use the `@tool(...)` decorator.

- `registry.run(name, /, **kwargs)` executes a tool, converts any exception
  into a `ToolResult.failure`, records the call to `tool_history`, and stamps
  the duration. (`name` is positional-only so a tool whose own argument is
  literally `name` — e.g. `open_application(name=...)` — can't collide.)
- `dynamic_permission(**kwargs)` lets a tool compute its permission/risk from
  the *actual arguments*. Example: launching an allowlisted app is `SAFE`, an
  unknown one is `CONFIRM`; a shell command matching a destructive pattern
  escalates to `ALWAYS_CONFIRM` / `CRITICAL`.

See [TOOLS.md](TOOLS.md) for the full catalogue.

---

## 7. Persistence

A single SQLite database (`data/jarvis.db`) in WAL mode, guarded by a
re-entrant lock so it's safe from FastAPI's threadpool and the task workers.
Tables: `conversations`, `messages`, `tasks`, `memory`, `settings`,
`permissions`, `audit_log`, `tool_history`, plus `documents` / `doc_chunks` /
`automations` for future use. Access goes through small typed repositories
(`jarvis/db/repositories.py`); no ORM.

---

## 8. WebSocket protocol

Realtime channel at `/ws`. All messages are JSON with a `type` field.

### Backend → Frontend

| type | payload | meaning |
|---|---|---|
| `hello` | `{diagnostics, settings}` | initial hydration snapshot |
| `state` | `{state}` | current agent state — see §3.1 |
| `message` | `{role, content, conversation_id, provider, mode, speak}` | an assistant reply |
| `tool_status` | `{tool, status, summary, arguments, duration_ms}` | `running` / `done` / `error` / `denied` / `skipped` |
| `approval_request` | `{approval_id, tool, category, risk, reason, arguments, rememberable, description}` | user decision needed |
| `approval_timeout` | `{approval_id}` | pending approval expired (auto-declined) |
| `notification` | `{level, source, message}` | info / warning / error toast |
| `system_stats` | `{stats}` | live metrics for the gauges (~every 2 s) |
| `task_update` | `{task, note}` | task queue changed |
| `settings_changed` | `{settings}` | settings were updated |

### Frontend → Backend

| type | payload | meaning |
|---|---|---|
| `chat` | `{text, conversation_id}` | user command/message |
| `approve` | `{approval_id, approved, remember}` | answer an approval request |
| `stop` | `{}` | cancel the current turn / tasks |
| `cancel_task` | `{task_id}` | cancel one background task |

### REST (snapshots & mutations)

`GET /api/health`, `/api/diagnostics`, `/api/settings` (+ `POST`),
`/api/tools`, `/api/skills`, `/api/conversations`, `/api/memory`
(+ `POST`/`DELETE`), `/api/tasks`, `/api/history`, `/api/audit`,
`/api/voices`, `POST /api/speak`. The UI hydrates from these and then streams
live updates over the WebSocket.

---

## 9. Frontend

No framework, no bundler — ES modules served straight from `frontend/`:

- `app.js` — entry point; wires the WebSocket handlers, composer, push-to-talk,
  approvals, tabs, and settings.
- `ws.js` — auto-reconnecting client with an outbound queue.
- `orb.js` — canvas arc-reactor that reflects state and audio level.
- `voice.js` — Web Speech API STT/TTS with honest capability flags and
  privacy-respecting mic metering.
- `ui.js` — DOM rendering (messages, gauges, feeds, modals).
- `markdown.js` — a small, safe Markdown subset renderer.

Because there's no build step, you can edit and refresh. See
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) if the browser serves stale assets.
