"""Long-term memory store.

Wraps :class:`MemoryRepo` with a few conveniences the rest of the system uses:
  * :meth:`context_block` — a compact digest of preferences + top facts that the
    orchestrator injects into the system prompt.
  * :meth:`episodic` — record a short summary of each completed interaction.

Respects the ``memory_enabled`` privacy switch: when disabled, writes are
no-ops and the context block is empty.
"""
from __future__ import annotations

from typing import Any, Optional

from jarvis.config import settings
from jarvis.db.repositories import MemoryRepo


class MemoryStore:
    def __init__(self) -> None:
        self.repo = MemoryRepo()

    @property
    def enabled(self) -> bool:
        return bool(settings.get("memory_enabled", True))

    # -- writes --------------------------------------------------------- #
    def remember(self, kind: str, value: str, key: Optional[str] = None,
                 importance: int = 1) -> Optional[int]:
        if not self.enabled:
            return None
        return self.repo.add(kind, value, key=key, importance=importance)

    def set_preference(self, key: str, value: str) -> Optional[int]:
        return self.remember("preference", value, key=key, importance=3)

    def episodic(self, summary: str) -> Optional[int]:
        return self.remember("episodic", summary, importance=1)

    # -- reads ---------------------------------------------------------- #
    def list(self, kind: Optional[str] = None) -> list[dict[str, Any]]:
        return self.repo.list(kind=kind)

    def search(self, term: str) -> list[dict[str, Any]]:
        return self.repo.search(term)

    def context_block(self, max_items: int = 12) -> str:
        """A short, prompt-friendly digest of what JARVIS knows about the user."""
        if not self.enabled:
            return ""
        prefs = self.repo.list(kind="preference", limit=8)
        facts = self.repo.list(kind="fact", limit=8) + self.repo.list(kind="semantic", limit=4)
        lines: list[str] = []
        for p in prefs:
            label = f"{p['key']}: " if p.get("key") else ""
            lines.append(f"- Preference — {label}{p['value']}")
        for f in facts:
            lines.append(f"- Fact — {f['value']}")
        return "\n".join(lines[:max_items])

    # -- management ----------------------------------------------------- #
    def delete(self, memory_id: int) -> None:
        self.repo.delete(memory_id)

    def clear(self, kind: Optional[str] = None) -> None:
        self.repo.clear(kind)
