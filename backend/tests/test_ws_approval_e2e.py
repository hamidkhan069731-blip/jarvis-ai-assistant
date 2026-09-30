"""End-to-end approval gate over a real WebSocket, with no browser involved.

`test_approval_gate.py` proves the orchestrator honours a verdict. This proves
the *whole stack* does: a real client connects to the real ASGI app, sends a real
command that routes to a real CONFIRM-level tool, and then stays silent. Nothing
anywhere may execute that tool on the client's behalf.

This is the test that distinguishes "a human approved it" from "the software
approved itself" — the question a code read alone cannot answer.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import jarvis.core as core_mod
from jarvis.server import app

TIMEOUT = 2.0  # shortened approval window so the test is fast


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(core_mod, "APPROVAL_TIMEOUT", TIMEOUT)
    with TestClient(app) as c:
        yield c


def _drain(ws, *, until: set[str], limit: int = 60) -> list[dict]:
    """Collect messages until one of `until` types arrives (or `limit` msgs)."""
    seen: list[dict] = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if msg.get("type") in until:
            break
    return seen


def test_silence_is_not_consent(client):
    """The decisive case: request a gated tool, answer nothing, and prove the
    tool never runs."""
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "chat", "text": "read the screen"})

        seen = _drain(ws, until={"approval_request"})
        request = [m for m in seen if m.get("type") == "approval_request"]
        assert request, f"no approval was requested; got {[m.get('type') for m in seen]}"
        assert request[0]["tool"] == "read_screen"
        assert request[0]["risk"] == "medium"

        # Deliberately send nothing at all, then watch the turn finish.
        rest = _drain(ws, until={"message"})
        kinds = [m.get("type") for m in seen + rest]
        statuses = [(m.get("tool"), m.get("status"))
                    for m in seen + rest if m.get("type") == "tool_status"]

        assert "approval_timeout" in kinds, f"no timeout was signalled: {kinds}"
        assert ("read_screen", "running") not in statuses, \
            f"GATE BYPASS: read_screen executed without approval — {statuses}"
        assert ("read_screen", "done") not in statuses, \
            f"GATE BYPASS: read_screen completed without approval — {statuses}"
        assert ("read_screen", "skipped") in statuses, \
            f"expected the tool to be skipped, got {statuses}"


def test_denial_over_the_wire_blocks_execution(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "chat", "text": "read the screen"})

        seen = _drain(ws, until={"approval_request"})
        req = next(m for m in seen if m.get("type") == "approval_request")
        ws.send_json({"type": "approve", "approval_id": req["approval_id"],
                      "approved": False, "remember": False})

        rest = _drain(ws, until={"message"})
        statuses = [(m.get("tool"), m.get("status"))
                    for m in seen + rest if m.get("type") == "tool_status"]
        assert ("read_screen", "running") not in statuses, \
            f"GATE BYPASS: denied tool still ran — {statuses}"
        assert ("read_screen", "skipped") in statuses


def test_verdict_for_a_different_approval_id_is_ignored(client):
    """A stale or forged id must not release the pending approval."""
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "chat", "text": "read the screen"})

        seen = _drain(ws, until={"approval_request"})
        req = next(m for m in seen if m.get("type") == "approval_request")
        ws.send_json({"type": "approve", "approval_id": req["approval_id"] + "_wrong",
                      "approved": True, "remember": True})

        rest = _drain(ws, until={"message"})
        kinds = [m.get("type") for m in seen + rest]
        statuses = [(m.get("tool"), m.get("status"))
                    for m in seen + rest if m.get("type") == "tool_status"]
        assert "approval_timeout" in kinds, \
            f"a mismatched id released the approval: {kinds}"
        assert ("read_screen", "running") not in statuses, \
            f"GATE BYPASS via forged approval id — {statuses}"


def test_other_screens_are_told_the_question_was_settled(client):
    """JARVIS can be open in more than one window. When one answers, the others
    must stop asking — a modal about an action that already ran misrepresents
    the state of the system."""
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        assert a.receive_json()["type"] == "hello"
        assert b.receive_json()["type"] == "hello"

        a.send_json({"type": "chat", "text": "read the screen"})
        req = next(m for m in _drain(a, until={"approval_request"})
                   if m.get("type") == "approval_request")
        # the second screen is asked too
        assert any(m.get("type") == "approval_request"
                   for m in _drain(b, until={"approval_request"}))

        a.send_json({"type": "approve", "approval_id": req["approval_id"],
                     "approved": False, "remember": False})

        resolved = [m for m in _drain(b, until={"approval_resolved"})
                    if m.get("type") == "approval_resolved"]
        assert resolved, "the second screen was left showing a stale approval modal"
        assert resolved[0]["approval_id"] == req["approval_id"]
        assert resolved[0]["approved"] is False
