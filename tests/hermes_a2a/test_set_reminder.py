"""Tests for A2A set_reminder proposal and approval flows."""

from __future__ import annotations

import importlib
import json
from unittest.mock import patch

import pytest

from hermes_a2a.context import reset_a2a_context, set_a2a_context
from hermes_a2a.cron_proposal import (
    build_reminder_proposal,
    create_reminder_job,
    format_reminder_preview,
    parse_reminder_payload,
)
from hermes_a2a.registry import PeerRegistry
from hermes_a2a.store import RequestStore
from tools.a2a_tool import a2a_propose_reminder


@pytest.fixture()
def cron_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "cron").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
    import cron.jobs

    importlib.reload(cron.jobs)
    return home


@pytest.fixture()
def reminder_env(cron_env):
    store = RequestStore()
    record = store.create_request(
        request_type="set_reminder",
        from_peer="alice",
        to_peer="bob",
        payload={
            "message": "Meeting with Alice — 30 min prep",
            "schedule": "2h",
            "delivery_hint": "telegram",
        },
    )
    tokens = set_a2a_context(
        request_id=record["request_id"],
        from_peer="alice",
        request_type="set_reminder",
    )
    yield record
    reset_a2a_context(tokens)


def test_parse_reminder_payload():
    parsed = parse_reminder_payload(
        {
            "message": "Standup",
            "schedule": "0 9 * * *",
            "delivery_hint": "telegram",
            "timezone": "America/Los_Angeles",
        }
    )
    assert parsed["success"] is True
    assert parsed["message"] == "Standup"
    assert parsed["schedule"] == "0 9 * * *"


def test_parse_reminder_applies_recurrence():
    parsed = parse_reminder_payload(
        {"message": "Ping", "schedule": "30m", "recurrence": "2h"}
    )
    assert parsed["success"] is True
    assert parsed["schedule"] == "every 2h"


def test_build_reminder_proposal_preview():
    proposal = build_reminder_proposal(
        schedule="every 1h",
        message="Check servers",
        deliver="telegram",
        from_peer="alice",
    )
    assert proposal["success"] is True
    preview = format_reminder_preview(proposal, from_peer="alice")
    assert "alice" in preview
    assert "Check servers" in preview
    assert "telegram" in preview


def test_create_reminder_job_writes_cron_store(cron_env):
    proposal = build_reminder_proposal(
        schedule="every 1h",
        message="Water plants",
        deliver="local",
    )
    result = create_reminder_job(proposal, from_peer="alice")
    assert result["success"] is True
    assert result["job_id"]

    from cron.jobs import get_job

    job = get_job(result["job_id"])
    assert job is not None
    assert job["prompt"] == "Water plants"
    assert job.get("origin", {}).get("peer") == "alice"


def test_propose_reminder_create_on_approval(reminder_env, cron_env):
    with patch("tools.a2a_tool.clarify_tool") as mock_clarify:
        mock_clarify.return_value = json.dumps({"user_response": "Create"})
        result = json.loads(
            a2a_propose_reminder(
                schedule="every 1h",
                message="Meeting with Alice — 30 min prep",
                deliver="telegram",
                clarify_callback=lambda q, c: "Create",
            )
        )
    assert result["success"] is True
    assert result["status"] == "created"
    assert result["job_id"]

    from cron.jobs import list_jobs

    jobs = list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["deliver"] == "telegram"


def test_propose_reminder_deny_creates_no_job(reminder_env, cron_env):
    with patch("tools.a2a_tool.clarify_tool") as mock_clarify:
        mock_clarify.return_value = json.dumps({"user_response": "Deny"})
        result = json.loads(
            a2a_propose_reminder(
                schedule="every 1h",
                message="Meeting with Alice — 30 min prep",
                clarify_callback=lambda q, c: "Deny",
            )
        )
    assert result["denied"] is True

    from cron.jobs import list_jobs

    assert list_jobs() == []


def test_propose_reminder_knowledge_trust_does_not_auto_approve(reminder_env, cron_env):
    registry = PeerRegistry()
    registry.set_trust("alice", "knowledge_request", "always")
    assert registry.trust_is_always("alice", "knowledge_request")
    assert not registry.trust_is_always("alice", "set_reminder")

    with patch("tools.a2a_tool.clarify_tool") as mock_clarify:
        mock_clarify.return_value = json.dumps({"user_response": "Deny"})
        result = json.loads(
            a2a_propose_reminder(
                schedule="every 1h",
                message="Meeting with Alice — 30 min prep",
                clarify_callback=lambda q, c: "Deny",
            )
        )
    assert result["denied"] is True
    mock_clarify.assert_called_once()


def test_propose_reminder_always_trust_auto_creates(reminder_env, cron_env):
    registry = PeerRegistry()
    registry.set_trust("alice", "set_reminder", "always")

    result = json.loads(
        a2a_propose_reminder(
            schedule="every 1h",
            message="Meeting with Alice — 30 min prep",
        )
    )
    assert result["success"] is True
    assert result.get("auto_approved") is True
    assert result["job_id"]

    from cron.jobs import list_jobs

    assert len(list_jobs()) == 1
