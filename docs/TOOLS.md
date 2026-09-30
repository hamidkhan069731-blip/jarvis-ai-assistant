# Tool Catalogue

Tools are the *only* way JARVIS affects your computer. Every call — whether it
came from a cloud model, the local router, or a skill — passes through the same
permission engine before it runs.

**36 tools ship out of the box:** 33 built-in plus 3 from the bundled `notes`
example skill.

Legend:

- **Perm** — `safe` (may auto-run within the mode's risk ceiling), `confirm`
  (approval required, rememberable outside `strict`), `always_confirm` (asked
  every time, never rememberable).
- **Risk** — `low` → `medium` → `high` → `critical`.
- `?` on an argument means optional.

The live, authoritative list is always `GET /api/tools` (and the **Tools** panel
in the UI). See [SECURITY.md](SECURITY.md) for how the gates are applied.

---

## apps — launching & closing things

| Tool | Args | Perm | Risk | What it does |
|---|---|---|---|---|
| `open_url` | `url` | safe | low | Open a web URL in the default browser ("open youtube"). |
| `open_application` | `name` | confirm | medium | Launch a desktop app by name ("Chrome", "VS Code", "Notepad"). Apps on `JARVIS_APP_ALLOWLIST` open immediately; unknown apps ask first. |
| `open_path` | `path` | confirm | medium | Open a file or folder with its default program / in File Explorer. |
| `close_application` | `name` | confirm | medium | Close all windows/processes of an application by name. |
| `list_windows` | — | safe | low | List open windows (title + owning process) — "what's open". |
| `focus_window` | `query` | safe | low | Bring a window to the foreground by title or app name ("switch to Chrome"). |
| `set_window_state` | `query`, `state` | safe | low | `minimize` / `maximize` / `restore` a window. |
| `is_app_running` | `name` | safe | low | Whether an app is running, with its PIDs and window titles. |
| `restart_application` | `name` | confirm | medium | Close an app and relaunch it. Confirmed — unsaved work may be lost. |

`open_application` is the clearest example of **argument-aware permissions**:
the same tool is low-friction for allowlisted apps and gated for anything else.

---

## files — search, inspect, organize *(sandboxed)*

Every path is resolved through the filesystem sandbox first — anything outside
`JARVIS_FILE_ROOTS` (your home directory by default) is refused, including `..`
traversal.

| Tool | Args | Perm | Risk | What it does |
|---|---|---|---|---|
| `list_directory` | `path?` | safe | low | List files and folders in a directory (non-recursive). |
| `search_files` | `query?`, `extension?`, `path?`, `limit?` | safe | low | Recursive search by name/extension — "find my PDFs", "files named invoice". |
| `read_text_file` | `path` | safe | low | Read a text file (max ~200 KB) — code, notes, logs, config. |
| `find_large_files` | `path?`, `min_mb?`, `limit?` | safe | low | Biggest files under a directory — "what's eating my disk". |
| `find_duplicate_files` | `path?`, `limit?` | safe | low | Find duplicates by **content hash**, not just name. |
| `analyze_folder` | `path` | safe | low | Count files, total size, and how they *would* group by type. |
| `create_folder` | `path` | safe | low | Create a folder plus any missing parents. |
| `write_text_file` | `path`, `content` | confirm | medium | Create or overwrite a text file. |
| `move_path` | `source`, `destination` | confirm | medium | Move or rename a file/folder. |
| `compress_to_zip` | `source`, `destination?` | confirm | medium | Compress a file or folder to `.zip`. |
| `organize_folder` | `path` | confirm | medium | Sort loose files into subfolders by type (Documents, Images, Videos, Installers, Archives, …). |
| `delete_path` | `path`, `recursive?` | confirm | **high** | Delete a file or folder. Always confirmed. |

**Pattern worth knowing:** `analyze_folder` is the safe *preview* for
`organize_folder`, so JARVIS can tell you exactly what will move before you
approve it. Similarly, nothing here deletes as a side effect — only
`delete_path` removes data, and it is high-risk by declaration.

---

## system — metrics, processes, session

| Tool | Args | Perm | Risk | What it does |
|---|---|---|---|---|
| `system_status` | — | safe | low | Live CPU %, RAM %, disk, battery, network, uptime. |
| `system_info` | — | safe | low | Static specs: OS, CPU model, cores, hostname, Python version. |
| `list_processes` | `sort_by?`, `limit?` | safe | low | Top processes by CPU or memory. |
| `control_volume` | `action`, `steps?` | safe | low | `up` / `down` in steps, or `mute` to toggle. |
| `clipboard_get` | — | safe | low | Read the clipboard's text contents. |
| `clipboard_set` | `text` | safe | low | Replace the clipboard contents. |
| `take_screenshot` | — | confirm | medium | Capture the screen to a PNG under `data/` and return the path. |
| `lock_workstation` | — | confirm | medium | Lock the Windows session. |
| `press_keys` | `combo` | confirm | medium | Send a keyboard shortcut to the active window (`ctrl+s`, `alt+tab`). |
| `power_control` | `action` | *varies* | *varies* | `shutdown` / `restart` / `sign_out` (**high**), `sleep` / `hibernate` (medium), `cancel` a pending shutdown (safe). Always confirmed except `cancel`. |
| `wake_on_lan` | `mac`, `broadcast?`, `port?` | safe | low | Send a Wake-on-LAN magic packet to another machine. |
| `kill_process` | `pid` | confirm | **high** | Terminate a process by PID. |
| `type_text` | `text` | confirm | **high** | Type text into the focused window (max 2000 chars). Confirmed — it goes wherever focus is. |

These read real values via `psutil` — the same source that feeds the HUD gauges.
Nothing here is simulated; on a machine with no battery, the battery field is
reported as unavailable rather than faked.

---

## web

| Tool | Args | Perm | Risk | What it does |
|---|---|---|---|---|
| `web_search` | `query`, `limit?` | safe | low | Top results (title, url, snippet) for current info, docs, prices, news. |
| `web_fetch` | `url`, `max_chars?` | safe | low | Fetch a page and return its readable text, HTML stripped. |

> Content fetched from the web is untrusted input. It can *inform* a reply, but
> it can never bypass the permission gate — any action it suggests still has to
> pass through the same approval flow.

---

## memory

| Tool | Args | Perm | Risk | What it does |
|---|---|---|---|---|
| `remember` | `kind`, `value`, `key?` | safe | low | Store a durable fact or preference. Used only when you explicitly ask. |
| `recall` | `query` | safe | low | Search long-term memory. |

Both become no-ops when `memory_enabled` is off. Stored items are listable and
deletable from the Memory panel — see [SECURITY.md](SECURITY.md) §5.

---

## utility

| Tool | Args | Perm | Risk | What it does |
|---|---|---|---|---|
| `get_time` | — | safe | low | Current local date and time. |
| `calculate` | `expression` | safe | low | Arithmetic only (`+ - * / // % **`). No variables, no functions. |

`calculate` is deliberately *not* `eval()`. Expressions are parsed and
restricted to numbers and operators; anything else (e.g. `__import__(...)`) is
rejected — pinned by `backend/tests/test_tools.py`.

---

## developer — shell & code *(disabled by default)*

| Tool | Args | Perm | Risk | What it does |
|---|---|---|---|---|
| `run_shell` | `command`, `shell?`, `cwd?`, `timeout?` | confirm | **high** | Run a PowerShell/CMD command and capture output. |
| `run_python` | `code`, `timeout?` | confirm | **high** | Run a short Python snippet in a separate process. |

Both refuse to run unless `enable_shell` is turned on. Even then:

- Destructive commands are **blocked outright** by the guard blocklist (not
  merely confirmed) — see [SECURITY.md](SECURITY.md) §2.
- A dangerous-looking command escalates to `always_confirm` / `critical`, which
  can never be auto-approved or remembered.
- There's a hard timeout, captured/truncated output, and the working directory
  is restricted to the file roots.

---

## notes — from the bundled `notes` skill

| Tool | Args | Perm | Risk | What it does |
|---|---|---|---|---|
| `add_note` | `text` | safe | low | Save a short personal note ("note that …"). |
| `list_notes` | `limit?` | safe | low | List the most recent notes. |
| `clear_notes` | — | confirm | medium | Delete **all** notes. Irreversible, so it's gated. |

These are not special-cased anywhere in the core — they arrive purely through
the skill loader and are governed identically to built-ins. That's the reference
example to copy: see [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md).

---

## Risk summary

| Risk | Tools |
|---|---|
| **high** | `delete_path`, `kill_process`, `run_shell`, `run_python` |
| **medium** | `open_application`, `open_path`, `close_application`, `write_text_file`, `move_path`, `compress_to_zip`, `organize_folder`, `take_screenshot`, `lock_workstation`, `clear_notes` |
| **low** | everything else (22 tools) |

No built-in tool declares `critical`. `critical` is reserved for *escalation* —
`run_shell` reaching it dynamically when a command matches a destructive
pattern — and for skills that need an unconditional, per-use confirmation.
