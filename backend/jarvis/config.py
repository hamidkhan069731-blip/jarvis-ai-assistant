"""Configuration & settings management.

Settings resolve in three layers (highest priority first):
  1. Runtime overrides written to the DB `settings` table (editable from the UI)
  2. Environment variables / `.env` file
  3. Hard-coded safe defaults

The module exposes a process-wide :data:`settings` singleton. Values that a
user may change at runtime (voice, permission mode, proactive, ...) are read
through :meth:`Settings.get` so the DB layer can override them live.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# --------------------------------------------------------------------------- #
# .env loading (dependency-free)
# --------------------------------------------------------------------------- #
def _load_dotenv(path: Path) -> None:
    """Minimal .env loader; does not overwrite already-set env vars."""
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


def _project_root() -> Path:
    # backend/jarvis/config.py -> project root is three parents up
    return Path(__file__).resolve().parents[2]


_load_dotenv(_project_root() / ".env")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "").strip())
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "").strip())
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    """Immutable-at-boot configuration plus a live runtime override hook."""

    # paths
    project_root: Path = field(default_factory=_project_root)
    data_dir: Path = field(default_factory=lambda: Path(
        _env("JARVIS_DATA_DIR") or (_project_root() / "data")))
    frontend_dir: Path = field(default_factory=lambda: _project_root() / "frontend")

    # server
    host: str = field(default_factory=lambda: _env("JARVIS_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("JARVIS_PORT", 8787))

    # AI
    ai_provider: str = field(default_factory=lambda: _env("JARVIS_AI_PROVIDER", "local").lower())
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("JARVIS_ANTHROPIC_MODEL", "claude-opus-5"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str = field(default_factory=lambda: _env("JARVIS_OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_model: str = field(default_factory=lambda: _env("JARVIS_OPENAI_MODEL", "gpt-4o-mini"))

    # voice
    wake_word: str = field(default_factory=lambda: _env("JARVIS_WAKE_WORD", "hey jarvis").lower())
    tts_enabled: bool = field(default_factory=lambda: _env_bool("JARVIS_TTS_ENABLED", True))
    tts_rate: int = field(default_factory=lambda: _env_int("JARVIS_TTS_RATE", 175))
    tts_volume: float = field(default_factory=lambda: _env_float("JARVIS_TTS_VOLUME", 1.0))
    tts_voice: str = field(default_factory=lambda: _env("JARVIS_TTS_VOICE"))

    # security / permissions
    permission_mode: str = field(default_factory=lambda: _env("JARVIS_PERMISSION_MODE", "balanced").lower())
    enable_shell: bool = field(default_factory=lambda: _env_bool("JARVIS_ENABLE_SHELL", False))
    app_allowlist: list[str] = field(default_factory=lambda: [
        a.strip().lower() for a in _env(
            "JARVIS_APP_ALLOWLIST",
            "notepad,calc,explorer,chrome,msedge,code,cmd,powershell",
        ).split(",") if a.strip()
    ])

    # memory / privacy
    memory_enabled: bool = field(default_factory=lambda: _env_bool("JARVIS_MEMORY_ENABLED", True))
    proactive_enabled: bool = field(default_factory=lambda: _env_bool("JARVIS_PROACTIVE_ENABLED", True))
    # spoken boot greeting ("Good morning, sir. Systems online.") — opt-in so
    # startup never surprises the user with audio or an unrequested action.
    boot_greeting: bool = field(default_factory=lambda: _env_bool("JARVIS_BOOT_GREETING", False))

    # logging
    log_level: str = field(default_factory=lambda: _env("JARVIS_LOG_LEVEL", "INFO").upper())

    # runtime override provider, injected by the settings repository once the DB
    # is available. Signature: (key) -> Optional[str]
    _override: Optional[Callable[[str], Optional[Any]]] = field(default=None, repr=False)

    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.db_path = self.data_dir / "jarvis.db"
        self.logs_dir = self.data_dir / "logs"
        self.backups_dir = self.data_dir / "backups"
        self.knowledge_dir = self.data_dir / "knowledge"
        for d in (self.data_dir, self.logs_dir, self.backups_dir, self.knowledge_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    @property
    def file_roots(self) -> list[Path]:
        raw = _env("JARVIS_FILE_ROOTS")
        if raw:
            roots = [Path(p.strip()) for p in raw.split(";") if p.strip()]
        else:
            roots = [Path.home()]
        return [r.resolve() for r in roots if r.exists()]

    # ------------------------------------------------------------------ #
    def bind_override(self, fn: Callable[[str], Optional[Any]]) -> None:
        """Attach a callable that fetches live overrides from the DB."""
        self._override = fn

    def get(self, key: str, default: Any = None) -> Any:
        """Read a setting, preferring a live runtime override if present."""
        if self._override is not None:
            val = self._override(key)
            if val is not None:
                return val
        return getattr(self, key, default)

    def public_dict(self) -> dict[str, Any]:
        """Serializable view with secrets redacted — safe for the UI."""
        return {
            "version": _safe_version(),
            "ai_provider": self.get("ai_provider", self.ai_provider),
            "anthropic_model": self.anthropic_model,
            "openai_model": self.openai_model,
            "openai_base_url": self.openai_base_url,
            "has_anthropic_key": bool(self.anthropic_api_key),
            "has_openai_key": bool(self.openai_api_key),
            "wake_word": self.get("wake_word", self.wake_word),
            "tts_enabled": self.get("tts_enabled", self.tts_enabled),
            "tts_rate": self.get("tts_rate", self.tts_rate),
            "tts_volume": self.get("tts_volume", self.tts_volume),
            "tts_voice": self.get("tts_voice", self.tts_voice),
            "server_tts": self.get("server_tts", False),
            "permission_mode": self.get("permission_mode", self.permission_mode),
            "enable_shell": self.get("enable_shell", self.enable_shell),
            "app_allowlist": self.app_allowlist,
            "memory_enabled": self.get("memory_enabled", self.memory_enabled),
            "proactive_enabled": self.get("proactive_enabled", self.proactive_enabled),
            "boot_greeting": self.get("boot_greeting", self.boot_greeting),
            "log_level": self.log_level,
            "host": self.host,
            "port": self.port,
            "file_roots": [str(p) for p in self.file_roots],
        }


def _safe_version() -> str:
    try:
        from jarvis import __version__
        return __version__
    except Exception:
        return "1.0.0"


# process-wide singleton
settings = Settings()
