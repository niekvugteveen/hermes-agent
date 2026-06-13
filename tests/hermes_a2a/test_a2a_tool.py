"""Tests for A2A propose-response approval behavior."""

import json
from unittest.mock import patch

import pytest

from hermes_a2a.context import reset_a2a_context, set_a2a_context
from hermes_a2a.store import RequestStore
from tools.a2a_tool import a2a_propose_response


@pytest.fixture()
def a2a_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    try:
        import hermes_constants

        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    except ImportError:
        pass
    store = RequestStore()
    record = store.create_request(
        request_type="knowledge_request",
        from_peer="alice",
        to_peer="bob",
        payload={"question": "Free Tuesday?"},
    )
    tokens = set_a2a_context(
        request_id=record["request_id"],
        from_peer="alice",
        request_type="knowledge_request",
    )
    yield record
    reset_a2a_context(tokens)


def test_propose_requires_clarify(a2a_env):
    result = json.loads(a2a_propose_response("Tuesday works."))
    assert result.get("error")


def test_propose_send_once_completes(a2a_env):
    with patch("tools.a2a_tool.clarify_tool") as mock_clarify:
        mock_clarify.return_value = json.dumps({"user_response": "Send once"})
        result = json.loads(
            a2a_propose_response(
                "Tuesday works.",
                clarify_callback=lambda q, c: "Send once",
            )
        )
    assert result["success"] is True
    assert result["status"] == "completed"


def test_propose_deny(a2a_env):
    with patch("tools.a2a_tool.clarify_tool") as mock_clarify:
        mock_clarify.return_value = json.dumps({"user_response": "Deny"})
        result = json.loads(
            a2a_propose_response(
                "Tuesday works.",
                clarify_callback=lambda q, c: "Deny",
            )
        )
    assert result["denied"] is True
