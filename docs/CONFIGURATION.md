# Configuration

Everything in JARVIS is configurable — the AI provider is never hard-coded. This
document lists every environment variable and every runtime setting.

Configuration is resolved in this order (later wins):

1. Built-in defaults (`jarvis/config.py`).
2. Environment variables / `.env` file (read at startup).
3. Runtime settings stored in the database (editable from the Settings panel or
   `POST /api/settings`) — these override env for the keys in the editable
   allowlist, and take effect **without a restart**.

Secrets (API keys) are only ever read from env / `.env` / the settings store and
never sent to the browser. See [SECURITY.md](SECURITY.md).

---

## 1. Quick start with `.env`

Copy the template and edit it:

```bash
cp .env.example .env
```

`.env` lives at the project root and is git-ignored. You only need to set what
you want to change — every variable has a sensible default.

---

## 2. Environment variables

### AI provider

| Variable | Default | Notes |
|---|---|---|
| `JARVIS_AI_PROVIDER` | `local` | `local`, `anthropic`, or `openai` (OpenAI-compatible). Falls back to `local` if the chosen provider isn't available. |
| `ANTHROPIC_API_KEY` | *(empty)* | Required for `anthropic`. Never committed, never sent to the browser. |
| `JARVIS_ANTHROPIC_MODEL` | `claude-opus-4-8` | Model id for the Anthropic provider. |
| `OPENAI_API_KEY` | *(empty)* | Required for `openai`. |
| `JARVIS_OPENAI_BASE_URL` | `https://api.openai.com/v1` | Point at any OpenAI-compatible endpoint (LM Studio, Ollama, vLLM, Together, …). |
| `JARVIS_OPENAI_MODEL` | `gpt-4o-mini` | Model id for the OpenAI-compatible provider. |

> **Local mode needs no key.** It's a real rule-based router, not a stub — see
> [ARCHITECTURE.md](ARCHITECTURE.md) §5.

### Server

| Variable | Default | Notes |
|---|---|---|
| `JARVIS_HOST` | `127.0.0.1` | **Keep this local.** Binding elsewhere exposes an unauthenticated desktop-control server. |
| `JARVIS_PORT` | `8787` | HTTP + WebSocket port. |
| `JARVIS_LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error`. |

### Permissions & execution

| Variable | Default | Notes |
|---|---|---|
| `JARVIS_PERMISSION_MODE` | `balanced` | `strict`, `balanced`, or `trusted`. Sets the auto-run risk ceiling and whether confirmations can be remembered — see [SECURITY.md](SECURITY.md) §1. |
| `JARVIS_ENABLE_SHELL` | `false` | Master switch for `run_shell` / `run_python`. Off by default. |
| `JARVIS_FILE_ROOTS` | *(home dir)* | Semicolon (`;`)-separated list of folders file tools may touch. Non-existent paths are dropped; everything outside the roots is refused. |
| `JARVIS_APP_ALLOWLIST` | `notepad,calc,explorer,chrome,msedge,code,cmd,powershell` | Comma-separated apps that launch without extra confirmation. |

### Voice & TTS

| Variable | Default | Notes |
|---|---|---|
| `JARVIS_WAKE_WORD` | `hey jarvis` | Spoken wake word for hands-free activation (browser STT). |
| `JARVIS_BOOT_GREETING` | `false` | Speak a greeting ("Good morning, sir. All systems online…") when the console first connects. Opt-in. |
| `JARVIS_TTS_ENABLED` | `true` | Master switch for spoken replies. |
| `JARVIS_TTS_RATE` | `175` | Server TTS words-per-minute. |
| `JARVIS_TTS_VOLUME` | `1.0` | Server TTS volume, `0.0`–`1.0`. |
| `JARVIS_TTS_VOICE` | *(system default)* | Server TTS voice id (see `GET /api/voices`). |

> Browser-side speech synthesis is the default. Server-side TTS (`pyttsx3`) is a
> **runtime** setting (`server_tts`, see §3), not an env variable.

### Memory & proactivity

| Variable | Default | Notes |
|---|---|---|
| `JARVIS_MEMORY_ENABLED` | `true` | When `false`, no long-term memories are written and the prompt digest stays empty. |
| `JARVIS_PROACTIVE_ENABLED` | `true` | When `false`, the monitor won't push threshold alerts (high CPU/RAM/disk/battery). |

### Storage

| Variable | Default | Notes |
|---|---|---|
| `JARVIS_DATA_DIR` | `<project_root>/data` | SQLite DB, backups, logs, knowledge. Redirected to a temp dir during tests. |

Skills are loaded from `<project_root>/skills` and `<data_dir>/skills`
automatically — no variable required. See
[PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md).

---

## 3. Runtime-editable settings

These can be changed live from the **Settings** panel (or `POST /api/settings`)
and persist in the DB. This is the exact editable allowlist (`_EDITABLE` in
`server.py`) — anything not on it is ignored by the settings API, including all
API-key fields:

| Setting | Type | Effect |
|---|---|---|
| `ai_provider` | enum | Switch provider live (falls back to Local if unavailable). |
| `permission_mode` | enum | `strict` / `balanced` / `trusted`. |
| `enable_shell` | bool | Toggle shell/code execution. |
| `tts_enabled` | bool | Toggle spoken replies. |
| `tts_rate` | int | Server TTS speed. |
| `tts_volume` | float | Server TTS volume. |
| `tts_voice` | string | Server TTS voice id. |
| `server_tts` | bool | Browser vs server TTS. |
| `wake_word` | string | Wake word. |
| `boot_greeting` | bool | Speak a greeting on first connect. |
| `memory_enabled` | bool | Long-term memory on/off. |
| `proactive_enabled` | bool | Proactive alerts on/off. |

Trying to set anything else (e.g. `anthropic_api_key`, `host`, `data_dir`)
through the API is a no-op: it is not stored and not echoed back. Change those
via `.env` and restart.

---

## 4. Inspecting the live configuration

- **UI:** the Settings panel shows current values; the diagnostics header shows
  the active provider/mode.
- **API:** `GET /api/settings` returns the redacted public view
  (`has_anthropic_key` / `has_openai_key` booleans, never the keys);
  `GET /api/diagnostics` reports the resolved provider, tool count, and skill
  count.

---

## 5. Example `.env`

```dotenv
# --- AI provider ---
JARVIS_AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here
JARVIS_ANTHROPIC_MODEL=claude-opus-4-8

# --- server ---
JARVIS_HOST=127.0.0.1
JARVIS_PORT=8787
JARVIS_LOG_LEVEL=info

# --- safety ---
JARVIS_PERMISSION_MODE=balanced
JARVIS_ENABLE_SHELL=false

# --- voice ---
JARVIS_WAKE_WORD=jarvis
JARVIS_TTS_ENABLED=true

# --- memory ---
JARVIS_MEMORY_ENABLED=true
JARVIS_PROACTIVE_ENABLED=true
```

Never commit your real `.env`. Share `.env.example` instead.
