# Security & Privacy

JARVIS can operate your computer, so safety is a first-class feature, not an
afterthought. This document explains the permission model, the guard rails, how
secrets are handled, and the threat model — plus how to harden or loosen the
defaults.

---

## 1. The permission model

Every tool declares two independent properties:

**Permission level** (`jarvis/permissions/engine.py`):

| Level | Meaning |
|---|---|
| `SAFE` | May run automatically (subject to the risk ceiling below) |
| `CONFIRM` | Ask once; the user may choose to remember the decision |
| `ALWAYS_CONFIRM` | Ask **every** time; can never be remembered-away |

**Risk level:** `LOW` → `MEDIUM` → `HIGH` → `CRITICAL`.

### Permission modes

The active mode sets the *highest risk that may auto-run* for `SAFE` tools, and
whether confirmations can be remembered:

| Mode | Auto-runs up to | Remember decisions? |
|---|---|---|
| `strict` | LOW | **No** — every confirmable action is re-asked |
| `balanced` *(default)* | LOW | Yes |
| `trusted` | MEDIUM | Yes |

`strict` and `balanced` share the same auto-ceiling (LOW); what makes `strict`
stricter is that it ignores remembered "allow" decisions and marks confirmations
as non-rememberable, so you are prompted every single time. A remembered
**deny** is still honoured in every mode.

### The decision algorithm

`PermissionEngine.evaluate()` resolves each call in this order — **the stricter
outcome always wins**:

1. A remembered **deny** for the tool → `DENY` (blocked).
2. `CRITICAL` risk **or** `ALWAYS_CONFIRM` permission → `ALWAYS_CONFIRM`
   (never auto, never rememberable). This is the hard floor for dangerous
   actions.
3. A remembered **allow** (only for non-critical, confirm-level tools, and only
   outside `strict` mode) → `AUTO`.
4. `SAFE` and within the mode's risk ceiling → `AUTO`.
5. Otherwise → `CONFIRM` (one-time approval; rememberable except in `strict`).

**Consequences that matter:**

- A `CRITICAL` action can *never* be silently auto-approved, and "remember this"
  cannot downgrade it. Even in `trusted` mode, critical stays gated.
- "Remember" only ever applies to non-critical confirm-level tools.
- These invariants are covered by the test suite
  (`backend/tests/test_permissions.py`).

### Argument-aware escalation

Tools can raise or lower their own gate based on the actual arguments via
`dynamic_permission(**kwargs)`:

- **Apps** — launching an app on `JARVIS_APP_ALLOWLIST` is treated as low
  friction; launching an unknown app requires confirmation.
- **Shell** — a command matching a destructive pattern is escalated to
  `ALWAYS_CONFIRM` / `CRITICAL` and then *still* blocked outright by the guard
  (see below).

---

## 2. Guard rails (`jarvis/security/guard.py`)

Defence in depth, independent of the permission engine.

### Dangerous-command detection

`is_dangerous_command()` matches a blocklist of destructive patterns, including:
`rm -rf /` / `~`, `mkfs`, `dd if=`, Windows `format X:`, recursive `del /s`,
`Remove-Item -Recurse -Force C:\...`, fork bombs, `diskpart`, `bcdedit`,
`vssadmin delete`, `reg delete`, `net user ... /add`, `schtasks /create`,
`shutdown` / `Restart-Computer`, `curl ... | bash`, `Invoke-Expression` / `iex`,
and `Start-Process -Verb RunAs`. Matches are **refused**, not just confirmed.

### Filesystem sandbox

`resolve_in_roots()` resolves every path used by file tools and guarantees it
lives under a configured **file root** (`JARVIS_FILE_ROOTS`, default: your home
directory). Relative paths are anchored to the first root; anything resolving
outside — including `..` traversal — raises `PathAccessError` and the tool
fails. Covered by `backend/tests/test_guard.py`.

---

## 3. Shell & code execution

- **Disabled by default.** `run_shell` and `run_python` refuse to run unless
  `JARVIS_ENABLE_SHELL=true` (or the Settings toggle).
- Even when enabled: every invocation is at least `CONFIRM`; destructive
  commands are blocked; there's a hard timeout (max 120 s); output is captured
  and truncated; the working directory is restricted to the file roots.
- Enable this only if you understand the risk. It is the single most powerful —
  and most dangerous — capability in JARVIS.

---

## 4. Secret handling

- API keys are read from environment / `.env` / the server-side settings store
  **only**. They live in the backend process.
- `settings.public_dict()` — the *only* settings view sent to the browser —
  exposes booleans like `has_anthropic_key`, **never** the key itself. This is
  asserted in `backend/tests/test_config.py` and `test_api.py`.
- The runtime settings API (`POST /api/settings`) has an allowlist of editable
  keys (`_EDITABLE`); attempts to set `anthropic_api_key` (or any non-editable
  key) through it are ignored, not stored, and not echoed back.
- The audit log redacts sensitive values.
- `.env` is git-ignored. **Never commit real secrets.** Use `.env.example` as
  the template.

---

## 5. Privacy

- **Microphone:** opened only while actively listening (push-to-talk or an
  armed wake word). There is no continuous recording or background streaming of
  audio. Mic level metering runs only during a listening window.
- **Long-term memory:** set `JARVIS_MEMORY_ENABLED=false` to disable all
  memory writes; the prompt context digest then stays empty. Existing memories
  can be listed and deleted from the Memory panel or `DELETE /api/memory`.
- **Proactive alerts:** set `JARVIS_PROACTIVE_ENABLED=false` to silence the
  monitor's threshold notifications.
- **Cloud vs local:** in Local mode nothing leaves your machine. In cloud mode,
  your messages and tool results are sent to the selected provider — the UI
  labels each reply with its provider/mode so you always know.

---

## 6. Network exposure

- The server binds to `127.0.0.1` by default. It has **no authentication** —
  it assumes a single trusted local user.
- Do **not** bind to `0.0.0.0` or forward the port without putting an
  authenticating reverse proxy in front. Anyone who can reach the port can
  drive your desktop within the configured permission mode.

---

## 7. Threat model (what this does and does not defend against)

**In scope / mitigated:**

- Accidental destructive actions → permission gate + blocklist + sandbox.
- The AI proposing something dangerous → same gate; the model cannot bypass it.
- Leaking API keys to the browser → redacted public settings + editable-key
  allowlist.
- Path traversal by file tools → root sandbox.
- A misbehaving skill crashing the app → skills are error-isolated at load.

**Out of scope (by design, single-user local tool):**

- A malicious *local* user who already controls your OS session.
- A malicious **skill you choose to install** — skills run with the app's
  privileges. Only install skills you trust; every skill tool still passes
  through the permission engine, but a skill can do anything Python can.
- Network attackers, if you deliberately expose the port without auth.
- Prompt-injection content fetched from the web influencing a cloud model —
  the permission gate still stands between any suggestion and execution, so
  review confirmations for anything surprising.

---

## 8. Hardening checklist

- Keep `JARVIS_ENABLE_SHELL=false` unless you need it.
- Use `strict` permission mode for maximum friction.
- Narrow `JARVIS_FILE_ROOTS` to just the folders JARVIS should touch.
- Trim `JARVIS_APP_ALLOWLIST` to the apps you actually launch.
- Keep the bind address at `127.0.0.1`.
- Review the Audit tab periodically.

---

## 9. Reporting

This is a personal project scaffold. If you extend it and find a security
issue, treat the permission engine and the guard as the two invariants to
protect: no change should let a `CRITICAL`/destructive action run without an
explicit, per-occurrence confirmation, and no path should escape the roots.
