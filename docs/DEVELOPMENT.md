# Development

How to set up, run, test, and extend JARVIS. For the *why* behind the structure,
read [ARCHITECTURE.md](ARCHITECTURE.md) first.

---

## 1. Setup

Requires **Python 3.11+** (developed on 3.14).

```bash
cd backend
python -m pip install -r requirements.txt
```

A virtual environment is recommended but not required:

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# POSIX:    source .venv/bin/activate
python -m pip install -r requirements.txt
```

There is **no frontend build step** — `frontend/` is served as-is.

---

## 2. Running

```bash
python run.py                 # start + open the browser
python run.py --no-open       # server only
python run.py --port 8901     # custom port
python run.py --reload        # auto-reload on code changes (dev)
```

`run.py` flags: `--host`, `--port`, `--no-open`, `--reload`. Defaults come from
your settings/`.env`. Then browse to `http://127.0.0.1:8787/`.

For a quick offline loop, leave `JARVIS_AI_PROVIDER=local`; set a cloud key in
`.env` when you need open-ended reasoning. See
[CONFIGURATION.md](CONFIGURATION.md).

---

## 3. Project layout

```
Jarvis/
├── backend/
│   ├── run.py                # launcher
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── jarvis/
│   │   ├── config.py         # Settings (env + .env + live DB overrides)
│   │   ├── core.py           # runtime: client fan-out, approvals, lifecycle
│   │   ├── server.py         # FastAPI: WebSocket + REST + static mounts
│   │   ├── ai/               # provider abstraction + orchestrator
│   │   │   ├── provider.py            # AIProvider, get_provider(), fallback
│   │   │   ├── anthropic_provider.py
│   │   │   ├── openai_provider.py
│   │   │   ├── local_provider.py      # offline regex intent router
│   │   │   └── orchestrator.py        # tool-calling loop
│   │   ├── permissions/engine.py      # the decision matrix
│   │   ├── security/         # guard.py (blocklist + sandbox), audit.py
│   │   ├── tools/            # base.py + one module per category
│   │   ├── memory/store.py
│   │   ├── system/monitor.py
│   │   ├── tasks/engine.py
│   │   ├── voice/            # server-side TTS/STT
│   │   ├── skills/__init__.py         # skill loader
│   │   └── db/               # database.py + repositories.py
│   └── tests/                # pytest suite
├── frontend/                 # vanilla ES-module HUD
├── skills/                   # drop-in skills (ships with: notes)
├── docs/
└── .env.example
```

---

## 4. Testing

The suite is plain `pytest` — no `pytest-asyncio`, no network, no real
filesystem writes outside a temp dir.

```bash
cd backend
python -m pytest                       # all tests (currently 156)
python -m pytest tests/test_tools.py    # one file
python -m pytest -k permission -v       # by keyword
```

### How the harness works

- **`tests/conftest.py`** sets `JARVIS_DATA_DIR`, `JARVIS_FILE_ROOTS`, provider,
  and permission env vars to safe temp values **before any `jarvis` import** —
  that ordering matters because `config.py` reads env at import time. A session
  fixture loads built-in tools, binds settings overrides, and loads skills; a
  per-test fixture clears the DB tables so tests are isolated.
- **Async orchestrator tests** call `asyncio.run(...)` directly rather than
  depending on a plugin.
- **API tests** use FastAPI's `TestClient`, which runs the real lifespan
  (`core.startup()`), so they exercise the true startup path.

### What's covered

| File | Focus |
|---|---|
| `test_permissions.py` | The full decision matrix + mode/remember invariants |
| `test_guard.py` | Dangerous-command blocklist + path sandbox |
| `test_tools.py` | Registry, positional-only `run`, `calculate` safety, dynamic escalation |
| `test_local_provider.py` | Offline routing incl. Roman Urdu, calculator, precedence |
| `test_orchestrator.py` | Tool-calling loop: auto/confirm/decline/cancel/error |
| `test_memory.py` | Remember/recall/search/clear + the privacy switch |
| `test_skills.py` | Skill loading, tool attribution, roundtrip persistence |
| `test_config.py` | Public settings shape + secret redaction |
| `test_api.py` | Health, diagnostics, settings, tools, skills, memory, editable allowlist |

**When you change behavior, add or update a test.** The permission engine and
the guard are the two invariants to protect — a change that lets a
`CRITICAL`/destructive action auto-run, or a path escape the roots, should fail
a test.

---

## 5. Common extension tasks

### Add a built-in tool

1. Pick the right module in `jarvis/tools/` (or make a new one and import it in
   `load_builtin_tools()` in `tools/base.py`).
2. Write a function decorated with `@tool(...)` returning a `ToolResult`.
3. Choose honest `permission_level` / `risk_level` (see
   [SECURITY.md](SECURITY.md) §1).
4. If it should work offline, add a routing rule in `local_provider.py`.
5. Add a test.

Prefer a **skill** over a built-in when the capability is optional or
third-party — see [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md).

### Add an AI provider

Implement `AIProvider` (`available()`, `complete(system, messages, tools) ->
AIResponse`) in a new module, translate to/from the normalized message format
(ARCHITECTURE §5), and wire it into `get_provider()` with a graceful
fallback-to-Local when unavailable. Never hard-code a provider anywhere else.

### Teach the offline router a new phrase

Add a `(regex, handler)` pair to `_RULES` in `local_provider.py`. Rules are
ordered — **first match wins** — so place more specific patterns before broad
ones. Add a case to `test_local_provider.py`.

### Add a runtime setting

Add the field to `Settings` in `config.py`, expose it in `public_dict()` if the
UI needs it, and add its key to `_EDITABLE` in `server.py` if it should be
settable at runtime. **Never** add a secret to `public_dict()` or `_EDITABLE`.

### Add a REST endpoint or WebSocket message

REST handlers live in `server.py`; WebSocket message types are dispatched in
`_handle_ws_message`. Keep `server.py` a thin shell — real logic belongs in
`core.py` or the relevant layer. Document new wire messages in ARCHITECTURE §8.

---

## 6. Conventions

- **Layers stay separated.** `server.py` translates HTTP/WS ↔ core; it holds no
  business logic. Tools touch the OS; the orchestrator decides; the engine gates.
- **Tools are synchronous** and run in a worker thread — don't `await` inside a
  tool; do keep blocking calls bounded with timeouts.
- **Never block the event loop.** Providers and tools go through
  `asyncio.to_thread`.
- **No fake functionality.** If a capability can't run on this machine, report it
  honestly (`ToolResult.failure` / an "unavailable" flag) — never fabricate
  output. This is the project's first rule.
- **Secrets never reach the client.** Redact in `public_dict()`; keep keys off
  `_EDITABLE`.
- **Sandbox every user path** through `resolve_in_roots()`; check the blocklist
  before executing commands.
- **DB access goes through repositories** (`db/repositories.py`), not raw SQL
  scattered around.
- Match the surrounding style: type hints, `from __future__ import annotations`,
  module docstrings explaining *why*.

---

## 7. Debugging tips

- Raise verbosity: `JARVIS_LOG_LEVEL=DEBUG` (env) — logs every tool
  registration and call.
- Inspect live state without the UI:
  - `GET /api/diagnostics` — provider, tool count, skill count.
  - `GET /api/tools` / `GET /api/skills` — what's loaded.
  - `GET /api/history` — recent tool calls with durations.
  - `GET /api/audit` — the audit trail.
- Frontend: browser dev-tools console + the Network tab's WS frames show the
  live protocol.
- `--reload` restarts the server on code changes (backend only; frontend is just
  a refresh).

---

## 8. Before you commit

- `python -m pytest` is green.
- New/changed behavior has a test.
- No secrets, no real `.env`, no `data/` artifacts staged.
- Docs updated if you changed a tool, setting, endpoint, or the wire protocol.
- Provider still falls back to Local cleanly (try with no key set).
