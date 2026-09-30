# Writing Skills (Plugins)

A **skill** is a folder of real Python that adds new tools to JARVIS without
touching the core. Skills are loaded with `importlib` at startup, their tools
join the same registry as the built-ins, and they are governed by the same
permission engine — no special cases, no separate sandbox.

The bundled `skills/notes/` folder is a complete working reference. Copy it.

---

## 1. Anatomy

```
skills/
└── my_skill/
    ├── skill.json      # manifest (required)
    └── skill.py        # entry module (required; name set by "entry")
```

### Where skills are discovered

Loaded at startup, in this order:

1. `<project_root>/skills/` — the repo folder (version-controlled skills).
2. `<data_dir>/skills/` — per-machine skills you don't want in git.

A third directory is used if a `skills_dir` setting is present in the settings
store. Duplicate paths are de-duplicated, and folders without a `skill.json` are
ignored.

### `skill.json`

```json
{
  "name": "my_skill",
  "version": "1.0.0",
  "description": "One line shown in the UI and /api/skills.",
  "author": "you",
  "entry": "skill.py",
  "enabled": true
}
```

| Field | Required | Notes |
|---|---|---|
| `name` | no | Defaults to the folder name. Shown in the UI. |
| `version` | no | Defaults to `0.0.0`. |
| `description` | no | Shown in `/api/skills`. |
| `author` | no | Shown in `/api/skills`. |
| `entry` | no | Defaults to `skill.py`. Must exist, or the skill loads with `status: "error"`. |
| `enabled` | no | Set `false` to keep the folder but skip loading (`status: "disabled"`). |

---

## 2. A minimal skill

`skills/coffee/skill.py`:

```python
from jarvis.tools.base import ToolResult, registry, tool


@tool(
    "brew_coffee",
    "Report how much coffee is left. Use when the user asks about coffee.",
    category="kitchen",
    parameters={
        "type": "object",
        "properties": {
            "cups": {"type": "integer", "description": "How many cups.", "default": 1}
        },
    },
)
def brew_coffee(cups: int = 1) -> ToolResult:
    return ToolResult.success({"cups": cups}, summary=f"Brewing {cups} cup(s).")


def register(_registry=registry) -> None:
    """Optional. The @tool decorator already registers on import."""
    return None
```

That's it. Restart the server and `brew_coffee` appears in `/api/tools`, in the
Tools panel, and in the tool schemas sent to your AI provider.

---

## 3. The `@tool` decorator

```python
@tool(
    name,                      # unique tool id, snake_case
    description,               # written FOR THE MODEL — see §5
    *,
    category="general",        # groups the tool in the UI
    parameters=None,           # JSON Schema for the arguments
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
    dangerous=False,           # show the resolved arguments in the confirm dialog
    dynamic_permission=None,   # callable(**kwargs) -> (PermissionLevel, RiskLevel) | None
)
```

Your function receives the validated arguments as keyword arguments and must
return a `ToolResult`.

### `ToolResult`

```python
ToolResult.success(output=None, summary="", **meta)
ToolResult.failure(error, **meta)
```

- `output` — structured data (JSON-serializable). Goes back to the model and to
  the UI.
- `summary` — one short human sentence. This is what gets **spoken** and what
  the Local provider quotes verbatim as its reply, so write it in JARVIS's
  voice: `"Cleared 3 note(s)."`, not `"OK"`.
- `meta` — extra fields (the registry adds `duration_ms` automatically).

Raising an exception is safe: `registry.run()` catches it, converts it to a
`ToolResult.failure`, and logs it. Prefer returning an explicit `failure` with a
message the user can act on.

---

## 4. Choosing permission and risk

This is the most important design decision in a skill. Be honest — the gate is
what makes JARVIS trustworthy.

| Your tool… | Use |
|---|---|
| only reads, no side effects | `SAFE` / `LOW` |
| writes or modifies something recoverable | `CONFIRM` / `MEDIUM` |
| deletes data, kills processes, spends money, changes credentials | `CONFIRM` / `HIGH` |
| is irreversible or destructive enough that "remember my answer" would be wrong | `ALWAYS_CONFIRM` and/or `CRITICAL` |

`CRITICAL` and `ALWAYS_CONFIRM` are asked **every single time** and can never be
remembered-away — even in `trusted` mode. Use them for anything financial,
credential-related, security-affecting, or irreversible.

Set `dangerous=True` on anything gated so the confirmation dialog shows the
resolved arguments (the user sees exactly *which* path is about to be deleted).

`clear_notes` in the bundled skill shows the pattern:

```python
@tool(
    "clear_notes",
    "Delete ALL saved notes. This is irreversible.",
    category="notes",
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
    parameters={"type": "object", "properties": {}},
)
def clear_notes() -> ToolResult:
    ...
```

### Argument-aware gates

When the same tool is harmless for some inputs and risky for others, compute the
gate from the arguments:

```python
from jarvis.permissions.engine import PermissionLevel, RiskLevel

def _gate(**kwargs):
    target = (kwargs.get("target") or "").lower()
    if target == "production":
        return PermissionLevel.ALWAYS_CONFIRM, RiskLevel.CRITICAL
    return PermissionLevel.SAFE, RiskLevel.LOW

@tool("deploy", "...", dynamic_permission=_gate, parameters={...})
def deploy(target: str) -> ToolResult:
    ...
```

Return `None` to fall back to the static levels. An exception inside the hook is
swallowed and the static levels are used, so the call is never left ungated.

---

## 5. Writing descriptions the model can use

The `description` is a prompt. It's the only thing a cloud model sees when
deciding whether to call your tool.

- Say what it does **and when to use it**: *"List the user's most recent saved
  notes."* → good. *"Notes helper."* → useless.
- Mention the trigger phrases users actually say, including alternatives:
  *"Use when the user says 'note that…', 'add a note', or 'remind me to…'."*
- Disambiguate against neighbours: the notes skill explicitly says *"(as a note,
  not a scheduled alarm)"* so it doesn't get called for reminders.
- Describe every parameter in its schema — defaults included.

The **Local** (offline) provider does not read descriptions; it routes by regex
in `jarvis/ai/local_provider.py`. If you want your skill reachable offline, add
a rule there — that's an intentional core edit, not something skills can do
implicitly.

---

## 6. Using JARVIS services from a skill

Skills are ordinary Python and may import the app:

```python
from jarvis.config import settings                     # paths, live settings
from jarvis.security.guard import resolve_in_roots     # sandbox any user path
from jarvis.security.audit import get_logger           # structured logging
from jarvis.db.database import get_db                  # SQLite (WAL, thread-safe)
from jarvis.memory.store import MemoryStore            # long-term memory
```

Guidelines:

- **Store data under `settings.data_dir`**, never next to your code.
- **Always** pass user-supplied paths through `resolve_in_roots()` — don't
  re-implement the sandbox, and don't bypass it.
- Respect `settings.get("memory_enabled", True)` before persisting anything
  about the user.
- Tools are **synchronous**; the orchestrator runs them in a worker thread. Do
  blocking I/O normally, but keep it bounded (use timeouts) — a tool that hangs
  holds up the turn.
- Never print secrets to logs.

---

## 7. Loading, errors, and inspection

- Load happens once at startup (`JarvisCore.startup()` → `SkillManager.load_all()`).
  **Restart the server after editing a skill.**
- Failures are isolated: a skill that raises on import is recorded with
  `status: "error"` and the exception message; everything else still loads.
- A duplicate tool name overwrites the earlier one and logs a warning — prefix
  your tool names if you're worried about collisions.
- Inspect what loaded:

```bash
curl http://127.0.0.1:8787/api/skills
```

```json
{"skills": [{"name": "notes", "version": "1.0.0", "status": "loaded",
             "tools": ["add_note", "clear_notes", "list_notes"], "error": null}],
 "loaded": 1}
```

The startup log line is the fastest check:
`loaded skill 'notes' v1.0.0 (+3 tools)`.

---

## 8. Testing a skill

Follow `backend/tests/test_skills.py`. Call tools through the registry — the
permission gate lives in the orchestrator, so `registry.run()` executes directly,
which is what you want in a unit test:

```python
from jarvis.tools.base import registry

def test_note_roundtrip():
    assert registry.run("clear_notes").ok
    assert registry.run("add_note", text="ship it").ok
    result = registry.run("list_notes", limit=5)
    assert result.ok and "ship it" in result.summary
```

The test conftest redirects `JARVIS_DATA_DIR` to a temp folder, so tests never
touch your real notes. Run them with:

```bash
python -m pytest
```

---

## 9. Security expectations

Skills run with **the full privileges of the JARVIS process**. There is no
sandbox around skill code itself — only around the paths its tools touch, and
only if it uses the guard.

- Only install skills you have read.
- Declare gates honestly. A skill that mislabels a destructive action as
  `SAFE`/`LOW` defeats the entire permission model.
- Don't collect or transmit user data. Don't call out to the network unless the
  skill's whole purpose is that, and say so in the description.

See [SECURITY.md](SECURITY.md) for the model you're plugging into.

---

## 10. Checklist

- [ ] `skill.json` with a real `name`, `version`, `description`
- [ ] Tool names unique and snake_case
- [ ] `parameters` is valid JSON Schema, every property described
- [ ] Description tells the model **when** to use it
- [ ] Permission/risk honestly reflect the worst case
- [ ] `dangerous=True` on anything gated
- [ ] User paths go through `resolve_in_roots()`
- [ ] Data written under `settings.data_dir`
- [ ] `summary` reads well when spoken aloud
- [ ] A test in `backend/tests/`
- [ ] Server restarted and `/api/skills` shows `status: "loaded"`
