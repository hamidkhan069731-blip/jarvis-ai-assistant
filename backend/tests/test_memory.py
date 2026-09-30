"""Long-term memory store: persistence, de-duplication, search, the prompt
context digest, and the privacy switch (memory_enabled=false disables writes and
empties the context block)."""
from __future__ import annotations

from jarvis.memory.store import MemoryStore
from jarvis.db.repositories import SettingsRepo


def test_remember_and_list():
    store = MemoryStore()
    mid = store.remember("fact", "The user's name is Tony.")
    assert mid
    facts = store.list(kind="fact")
    assert any("Tony" in f["value"] for f in facts)


def test_keyed_memory_is_deduplicated():
    store = MemoryStore()
    first = store.remember("preference", "dark", key="theme", importance=3)
    second = store.remember("preference", "light", key="theme", importance=3)
    assert first == second                        # same row updated, not duplicated
    prefs = [p for p in store.list(kind="preference") if p.get("key") == "theme"]
    assert len(prefs) == 1
    assert prefs[0]["value"] == "light"


def test_search_matches_value():
    store = MemoryStore()
    store.remember("fact", "Favourite language is Python.")
    hits = store.search("python")
    assert hits and any("Python" in h["value"] for h in hits)


def test_delete_and_clear():
    store = MemoryStore()
    mid = store.remember("fact", "ephemeral")
    store.delete(mid)
    assert all(f["id"] != mid for f in store.list(kind="fact"))
    store.remember("fact", "a")
    store.remember("episodic", "b")
    store.clear("fact")
    assert store.list(kind="fact") == []
    assert store.list(kind="episodic")            # other kinds untouched


def test_context_block_includes_prefs_and_facts():
    store = MemoryStore()
    store.set_preference("tone", "concise")
    store.remember("fact", "Lives in Karachi.")
    block = store.context_block()
    assert "concise" in block
    assert "Karachi" in block


def test_privacy_switch_disables_memory():
    SettingsRepo().set("memory_enabled", False)
    try:
        store = MemoryStore()
        assert store.enabled is False
        assert store.remember("fact", "should not persist") is None
        assert store.context_block() == ""
    finally:
        SettingsRepo().set("memory_enabled", True)
