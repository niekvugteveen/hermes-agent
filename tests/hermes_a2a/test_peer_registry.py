"""Tests for hermes_a2a.registry.PeerRegistry."""

from pathlib import Path

import pytest

from hermes_a2a.registry import PeerRegistry, _hash_token


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    try:
        import hermes_constants

        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    except ImportError:
        pass
    return PeerRegistry(path=tmp_path / "peers" / "registry.yaml")


def test_pair_and_list_remote(registry):
    token = registry.pair_remote("bob", "http://bob-hermes:8765/a2a/mcp", token="shared-secret")
    assert token == "shared-secret"
    remotes = registry.list_remotes()
    assert len(remotes) == 1
    assert remotes[0]["peer_id"] == "bob"
    assert remotes[0]["url"].endswith("/a2a/mcp")


def test_verify_inbound_token(registry):
    registry.set_inbound_token("inbound-secret")
    assert registry.verify_inbound_token("inbound-secret")
    assert not registry.verify_inbound_token("wrong")


def test_trust_always_scoped_per_type(registry):
    registry.set_trust("alice", "knowledge_request", "always")
    assert registry.trust_is_always("alice", "knowledge_request")
    assert not registry.trust_is_always("alice", "set_reminder")


def test_revoke_remote(registry):
    registry.pair_remote("bob", "http://bob:8765/a2a/mcp", token="t")
    assert registry.revoke_remote("bob")
    assert registry.list_remotes() == []


def test_hash_token_stable():
    assert _hash_token("abc") == _hash_token("abc")
    assert _hash_token("abc") != _hash_token("def")
