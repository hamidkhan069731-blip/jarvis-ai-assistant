"""HTTP API surface, driven through FastAPI's TestClient.

Entering the client's context manager runs the app lifespan — i.e. the real
``core.startup()`` (tools, skills, monitor) — so these tests double as an
integration smoke test that the whole backend boots and serves.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.server import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_diagnostics_reports_local_provider(client):
    d = client.get("/api/diagnostics").json()
    assert d["provider"]["name"] == "local"
    assert d["tools"] > 0
    assert d["skills"] >= 1


def test_settings_endpoint_redacts_secrets(client):
    s = client.get("/api/settings").json()
    assert "has_anthropic_key" in s
    assert "anthropic_api_key" not in s          # the raw key is never serialized
    assert "openai_api_key" not in s


def test_tools_listing(client):
    body = client.get("/api/tools").json()
    assert isinstance(body["tools"], list) and body["tools"]
    assert isinstance(body["by_category"], dict)


def test_skills_listing_includes_notes(client):
    body = client.get("/api/skills").json()
    assert body["loaded"] >= 1
    assert any(s["name"] == "notes" for s in body["skills"])


def test_memory_crud_roundtrip(client):
    created = client.post("/api/memory", json={"kind": "fact", "value": "integration note"})
    assert created.status_code == 200
    mid = created.json()["id"]

    listed = client.get("/api/memory").json()["memory"]
    assert any(m["id"] == mid for m in listed)

    deleted = client.delete(f"/api/memory/{mid}")
    assert deleted.status_code == 200
    after = client.get("/api/memory").json()["memory"]
    assert all(m["id"] != mid for m in after)


def test_settings_update_ignores_non_editable_keys(client):
    # permission_mode is editable; api keys are NOT — an attempt to set one must
    # be silently ignored, never written, never echoed back.
    r = client.post("/api/settings", json={
        "permission_mode": "strict",
        "anthropic_api_key": "sk-should-be-ignored",
    })
    assert r.status_code == 200
    applied = r.json()["applied"]
    assert applied.get("permission_mode") == "strict"
    assert "anthropic_api_key" not in applied

    blob = r.text
    assert "sk-should-be-ignored" not in blob
