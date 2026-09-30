"""Configuration & settings serialization.

The public settings view is sent to the browser, so it must never carry secrets.
This is a hard requirement from the spec ("Never expose API keys in frontend
code"), so we assert both the shape and the redaction explicitly.
"""
from __future__ import annotations

import json

from jarvis.config import settings


def test_public_dict_has_expected_shape():
    pub = settings.public_dict()
    for key in ("version", "ai_provider", "permission_mode", "wake_word",
                "tts_enabled", "memory_enabled", "has_anthropic_key",
                "has_openai_key", "file_roots"):
        assert key in pub


def test_public_dict_never_leaks_api_keys(monkeypatch):
    # inject fake secrets directly on the settings object...
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-SECRET-VALUE")
    monkeypatch.setattr(settings, "openai_api_key", "sk-oai-SECRET-VALUE")
    pub = settings.public_dict()
    blob = json.dumps(pub)

    # ...the presence is reported as a boolean, but the value is redacted
    assert pub["has_anthropic_key"] is True
    assert pub["has_openai_key"] is True
    assert "SECRET-VALUE" not in blob
    assert "sk-ant" not in blob
    assert "sk-oai" not in blob


def test_data_dir_is_redirected_to_temp():
    # conftest points JARVIS_DATA_DIR at an isolated temp tree
    assert "jarvis_test_" in str(settings.data_dir)
    assert settings.db_path.parent == settings.data_dir


def test_runtime_override_takes_precedence():
    from jarvis.db.repositories import SettingsRepo
    SettingsRepo().set("permission_mode", "trusted")
    assert settings.get("permission_mode") == "trusted"
