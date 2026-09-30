# J.A.R.V.I.S. — Just A Rather Very Intelligent System

A modular, permission-aware **personal AI operating system** for the Windows
desktop. Not a chatbot in a box: JARVIS understands natural-language commands
(English, Urdu, and Roman Urdu), and actually *operates your computer* through a
governed set of real tools — opening apps, managing files, watching system
health, searching the web, running (gated) shell commands, and more — behind a
futuristic HUD console.

> **Design principle #1 — no fake functionality.** Every button, gauge, and
> command in JARVIS is wired to a real implementation. Where a capability can't
> be delivered on your machine (e.g. no microphone, no API key), JARVIS says so
> plainly and degrades gracefully instead of pretending.

---

## Highlights

- **Real desktop control** — 33 built-in tools across apps, files, system, web,
  memory, and developer categories (36 including the bundled `notes` skill). See
  [docs/TOOLS.md](docs/TOOLS.md).
- **Permission engine you can trust** — every action is classified by
  *permission level* (safe / confirm / always-confirm) and *risk* (low →
  critical). Risky and irreversible actions are gated behind an explicit
  approval dialog and can never be silently auto-run. See
  [docs/SECURITY.md](docs/SECURITY.md).
- **Works fully offline** — the built-in **Local** provider is a genuine
  rule-based intent router (not a simulated LLM), so JARVIS is useful with no
  API key and no internet. Add an Anthropic or OpenAI-compatible key for
  open-ended reasoning. Provider is fully configurable — nothing is hard-coded.
- **Multilingual** — "open Chrome", "Chrome kholo", "Downloads organize karo"
  all route to the right action.
- **Live system HUD** — real CPU/RAM/disk/network/battery gauges, task queue,
  activity + audit feeds, and a reactive arc-reactor orb.
- **Voice** — browser Web Speech API for STT/TTS (primary) plus optional
  server-side TTS; wake word, push-to-talk, and barge-in interruption. The mic
  is only opened while actively listening.
- **Extensible** — drop-in [skills](docs/PLUGIN_DEVELOPMENT.md) add new tools
  without touching the core.
- **Auditable** — every tool call, approval, and provider turn is logged.

---

## Quick start

Requires **Python 3.11+** (tested on 3.14) on Windows 10/11.

```bash
cd backend
python -m pip install -r requirements.txt
python run.py
```

Then open **http://127.0.0.1:8787/** (the launcher opens it for you unless you
pass `--no-open`). No configuration is needed — JARVIS starts in offline
**Local** mode.

To enable a cloud brain, copy `.env.example` to `.env` and set a key:

```bash
# from the project root
cp .env.example .env
# edit .env: set JARVIS_AI_PROVIDER=anthropic and ANTHROPIC_API_KEY=sk-...
```

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for every setting.

---

## Try it

Type or speak any of these in the console:

| You say | JARVIS does |
|---|---|
| `what's my CPU and RAM` | Reads live system metrics |
| `open Chrome` / `Chrome kholo` | Launches the browser |
| `organize my Downloads` | Sorts loose files by type *(asks first)* |
| `find my PDFs` | Recursively searches your files |
| `search the web for rust vs go` | Web search |
| `note that ship the build` | Saves a note *(notes skill)* |
| `remember I prefer dark mode` | Stores a durable preference |
| `run command Get-Process` | Runs a shell command *(disabled by default; always confirmed)* |

---

## Documentation

| Doc | What's inside |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, layers, data flow, WebSocket protocol |
| [SECURITY.md](docs/SECURITY.md) | Permission model, sandboxing, secret handling, threat model |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Every environment variable and runtime setting |
| [TOOLS.md](docs/TOOLS.md) | The full tool catalogue with risk levels |
| [PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md) | Writing your own skills |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues and fixes |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Dev setup, tests, project layout, contributing |

---

## Project layout

```
Jarvis/
├── backend/
│   ├── run.py               # launcher (python run.py)
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── jarvis/              # the application package
│   │   ├── config.py        # settings (env + DB overrides)
│   │   ├── core.py          # runtime wiring everything together
│   │   ├── server.py        # FastAPI: WebSocket + REST + static UI
│   │   ├── ai/              # provider abstraction + orchestrator
│   │   ├── permissions/     # the permission engine
│   │   ├── security/        # guard rails, audit log
│   │   ├── tools/           # the 33 built-in tools
│   │   ├── memory/          # long-term memory store
│   │   ├── system/          # live system monitor
│   │   ├── tasks/           # async task engine
│   │   ├── voice/           # server-side TTS/STT
│   │   ├── skills/          # plugin loader
│   │   └── db/              # SQLite wrapper + repositories
│   └── tests/               # pytest suite (156 tests)
├── frontend/                # vanilla ES-module HUD (no build step)
├── skills/                  # drop-in skills (ships with an example: notes)
├── docs/
└── .env.example
```

---

## Safety & privacy at a glance

- Binds to `127.0.0.1` only. Do **not** expose it to a network without adding
  authentication.
- Shell/code execution is **disabled by default**.
- File tools are **sandboxed** to configured roots (your home directory by
  default).
- API keys live only in `.env` / server settings — they are **never** sent to
  the browser. `.env` is git-ignored; never commit real secrets.
- Long-term memory and proactive alerts each have an off switch.
- The microphone is opened only while actively listening, never continuously.

---

## License & status

Personal project scaffold — provided as-is. Review
[SECURITY.md](docs/SECURITY.md) before enabling shell execution or a cloud
provider.
