# Troubleshooting

Symptoms, causes, and fixes. If something isn't listed, check the server console
(it logs at the level set by `JARVIS_LOG_LEVEL`) and the browser dev-tools
console.

---

## Startup

### `python run.py` fails with `ModuleNotFoundError`

Dependencies aren't installed, or you're in the wrong directory.

```bash
cd backend
python -m pip install -r requirements.txt
python run.py
```

### `Address already in use` / port 8787 is taken

Another instance (or another app) holds the port. Either stop it or pick a new
port:

```bash
python run.py --port 8901
```

### Nothing opens in the browser

The launcher auto-opens the page unless you passed `--no-open`. Open it manually:
**http://127.0.0.1:8787/**. If it still won't load, the server didn't start —
read the console output.

### `python: command not found` / wrong Python

Requires Python 3.11+. Check with `python --version`. On Windows you may need
`py -3` instead of `python`.

---

## AI provider

### Replies are prefixed "Local" when I set a cloud provider

JARVIS fell back to Local because the cloud provider wasn't usable. Causes:

- The API key is missing or empty. Check `GET /api/settings` →
  `has_anthropic_key` / `has_openai_key`.
- The SDK isn't installed (e.g. `anthropic`). Re-run the pip install.
- The provider name is misspelled — it must be exactly `anthropic`, `openai`,
  or `local`.

The console prints a warning: `Anthropic selected but unavailable; falling back
to local.` This fallback is intentional — JARVIS stays usable rather than
erroring out.

### Cloud replies fail or time out

Check the key is valid and has credit, and that you have network access. For a
self-hosted OpenAI-compatible endpoint, confirm `JARVIS_OPENAI_BASE_URL` points
at the running server (e.g. `http://localhost:11434/v1` for Ollama) and
`JARVIS_OPENAI_MODEL` names a model it actually serves.

### "Local didn't match a command"

The offline router only recognizes known command shapes. Try phrasings from the
[README](../README.md#try-it) or type `what can you do`. For open-ended
questions, switch to a cloud provider. To teach the router a new phrase, add a
rule in `jarvis/ai/local_provider.py` (see [DEVELOPMENT.md](DEVELOPMENT.md)).

---

## Tools & permissions

### A tool keeps asking for confirmation every time

Expected for `ALWAYS_CONFIRM` / `CRITICAL` actions and for **everything** in
`strict` mode — `strict` never remembers "allow". Switch to `balanced` or
`trusted` and tick "remember" when you approve. Note that `CRITICAL` actions are
always re-asked regardless of mode by design.

### "Remember my choice" didn't stick

- You're in `strict` mode (remembered allows are ignored).
- The action is `CRITICAL` / `ALWAYS_CONFIRM` (never rememberable).
- You remembered a *deny* — that's honoured everywhere; clear it by approving
  again, or remove the row from the `permissions` table.

### A file tool says "outside the allowed roots" / `PathAccessError`

The path is outside `JARVIS_FILE_ROOTS` (default: your home directory). Either
move the target under a root, or add its folder:

```dotenv
JARVIS_FILE_ROOTS=C:\Users\me;D:\Projects
```

Restart after changing it. This sandbox is a safety feature — see
[SECURITY.md](SECURITY.md) §2.

### `run_shell` / `run_python` says it's disabled

Shell execution is off by default. Enable it in Settings or:

```dotenv
JARVIS_ENABLE_SHELL=true
```

If a specific command is *blocked* even with shell enabled, it matched the
destructive-command blocklist and will never run — that's deliberate.

### `psutil is not installed` on system tools / empty gauges

The system monitor and system tools need `psutil`:

```bash
python -m pip install psutil
```

The gauges show real data only when `psutil` is present; otherwise the tools
report unavailable rather than inventing numbers.

### `httpx is not installed; web search unavailable`

```bash
python -m pip install httpx
```

### Screenshot / lock / volume says "Windows only"

Those tools use Windows APIs. On other platforms they report unavailable instead
of pretending to work. The rest of JARVIS is cross-platform.

---

## Voice

### The mic never activates / speech isn't recognized

- Browser STT needs a Chromium-based browser (Chrome/Edge) and microphone
  permission for `127.0.0.1`. Click the mic icon in the address bar and allow it.
- The mic is opened **only** while listening (push-to-talk or after the wake
  word) — by design, not a bug. See [SECURITY.md](SECURITY.md) §5.

### No spoken replies

- `tts_enabled` may be off — check Settings.
- Browser TTS depends on OS voices being installed.
- For server-side TTS, set `server_tts` on and install `pyttsx3`
  (`pip install pyttsx3`); list available voices at `GET /api/voices`.

### Wake word doesn't trigger

Confirm `wake_word` (default `hey jarvis`) matches what you say, and that STT is
working at all (try push-to-talk first).

---

## UI / WebSocket

### "Disconnected" banner, or the orb sits idle

The WebSocket dropped. The client auto-reconnects with a queue; if it doesn't,
the server likely stopped — check the console. A hard refresh (`Ctrl+F5`)
re-establishes the connection.

### I edited a frontend file but see the old version

There's no build step, but the browser caches ES modules aggressively. Hard
refresh (`Ctrl+F5`) or disable cache in dev-tools. If it persists, confirm you
edited the file under `frontend/` that's actually being served.

### Settings changes don't take effect

Only the keys in the editable allowlist (`_EDITABLE`) apply live; everything else
requires an `.env` change and a restart. API keys can **only** be set via `.env`
— the settings API ignores them by design. See
[CONFIGURATION.md](CONFIGURATION.md) §3.

---

## Data & reset

### Where is my data?

Under `data/` in the project (or `JARVIS_DATA_DIR`): `jarvis.db` (SQLite),
`logs/`, `backups/`, `screenshots/`, `notes.txt`.

### Start completely fresh

Stop the server and delete the database (this wipes conversations, memory,
settings, remembered permissions, notes):

```bash
rm backend/data/jarvis.db
```

To clear just one thing, use the UI (Memory panel) or the API
(`DELETE /api/memory`) instead.

### The database is locked

SQLite runs in WAL mode behind a re-entrant lock, so this is rare. It usually
means two server instances are pointing at the same `data/` dir — run only one.

---

## Tests

### `pytest` not found

```bash
python -m pip install pytest
```

### Tests touch my real data / notes

They shouldn't — `backend/tests/conftest.py` redirects `JARVIS_DATA_DIR` and
`JARVIS_FILE_ROOTS` to a temp folder before any `jarvis` import. If you see real
data being used, you're likely running a script outside pytest; run via
`python -m pytest` from `backend/`.

---

## Still stuck?

Raise `JARVIS_LOG_LEVEL=DEBUG`, reproduce, and read the console — most failures
log a clear cause. The [ARCHITECTURE.md](ARCHITECTURE.md) data-flow section shows
exactly where in a turn things happen.
