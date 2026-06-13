"""Tests for hermes_a2a.store.RequestStore."""

import json

import pytest

from hermes_a2a.constants import STATUS_COMPLETED, STATUS_DENIED
from hermes_a2a.store import RequestStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    try:
        import hermes_constants

        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    except ImportError:
        pass
    return RequestStore()


def test_create_and_complete(store):
    record = store.create_request(
        request_type="knowledge_request",
        from_peer="alice",
        to_peer="bob",
        payload={"question": "Tuesday?"},
    )
    request_id = record["request_id"]
    updated = store.complete_response(request_id, answer="Tuesday afternoon works.")
    assert updated is not None
    assert updated["status"] == STATUS_COMPLETED
    assert updated["response"]["answer"] == "Tuesday afternoon works."


def test_deny_is_opaque(store):
    record = store.create_request(
        request_type="knowledge_request",
        from_peer="alice",
        to_peer="bob",
        payload={"question": "Tuesday?"},
    )
    updated = store.complete_response(record["request_id"], denied=True)
    assert updated["status"] == STATUS_DENIED
    assert updated["response"]["status"] == "denied"


def test_persisted_to_disk(store, tmp_path):
    record = store.create_request(
        request_type="knowledge_request",
        from_peer="alice",
        to_peer="bob",
        payload={},
    )
    path = tmp_path / "peers" / "requests" / f"{record['request_id']}.json"
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["from_peer"] == "alice"
