"""Tests for /peer slash command handlers and hermes peer review UX."""

from __future__ import annotations

import importlib
import json

import pytest

from hermes_a2a.artifacts import bundle_skill
from hermes_a2a.store import RequestStore
from hermes_cli.a2a_commands import (
    approve_peer_request,
    deny_peer_request,
    format_pending_list,
    handle_peer_subcommand,
)


@pytest.fixture()
def a2a_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def cron_env(a2a_home):
    (a2a_home / "cron").mkdir(exist_ok=True)
    import cron.jobs

    importlib.reload(cron.jobs)
    return a2a_home


_SAMPLE_SKILL = """---
name: deploy-checklist
description: Deploy checklist.
---

# Deploy
"""


@pytest.fixture()
def skill_env(a2a_home):
    skill_dir = a2a_home / "skills" / "deploy-checklist"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_SAMPLE_SKILL, encoding="utf-8")
    import tools.skill_manager_tool as smt

    smt.HERMES_HOME = a2a_home
    smt.SKILLS_DIR = a2a_home / "skills"
    importlib.reload(smt)
    return a2a_home


def test_pending_lists_open_requests(a2a_home):
    store = RequestStore()
    store.create_request(
        request_type="knowledge_request",
        from_peer="alice",
        to_peer="bob",
        payload={"question": "Free Tuesday?"},
    )
    out = format_pending_list(store)
    assert "awaiting action" in out
    assert "alice" in out
    assert "/peer approve" in out


def test_deny_marks_request_denied(a2a_home):
    store = RequestStore()
    record = store.create_request(
        request_type="knowledge_request",
        from_peer="alice",
        to_peer="bob",
        payload={"question": "Free Tuesday?"},
    )
    msg = deny_peer_request(store, record["request_id"])
    assert "Denied" in msg
    updated = store.get(record["request_id"])
    assert updated["status"] == "denied"


def test_approve_knowledge_with_answer(a2a_home):
    store = RequestStore()
    record = store.create_request(
        request_type="knowledge_request",
        from_peer="alice",
        to_peer="bob",
        payload={"question": "Free Tuesday?"},
    )
    msg = approve_peer_request(store, record["request_id"], ["Tuesday", "works."])
    assert "Approved" in msg
    updated = store.get(record["request_id"])
    assert updated["status"] == "completed"
    assert updated["response"]["answer"] == "Tuesday works."


def test_approve_skill_installs(skill_env):
    store = RequestStore()
    bundled = bundle_skill("deploy-checklist", "summary")
    record = store.create_request(
        request_type="skill_share",
        from_peer="bob",
        to_peer="alice",
        payload={
            "skill_name": "shared-skill",
            "tier": "summary",
            "artifact": bundled["artifact"],
        },
    )
    msg = approve_peer_request(store, record["request_id"])
    assert "Installed skill" in msg
    updated = store.get(record["request_id"])
    assert updated["response"]["status"] == "installed"


def test_approve_reminder_creates_job(cron_env):
    store = RequestStore()
    record = store.create_request(
        request_type="set_reminder",
        from_peer="alice",
        to_peer="bob",
        payload={
            "message": "Prep for meeting",
            "schedule": "every 1h",
            "delivery_hint": "local",
        },
    )
    msg = approve_peer_request(store, record["request_id"])
    assert "Created reminder cron job" in msg
    updated = store.get(record["request_id"])
    assert updated["response"]["status"] == "created"
    assert updated["response"]["job_id"]

    from cron.jobs import list_jobs

    assert len(list_jobs()) == 1


def test_handle_peer_subcommand_dispatch(a2a_home):
    store = RequestStore()
    record = store.create_request(
        request_type="knowledge_request",
        from_peer="alice",
        to_peer="bob",
        payload={"question": "Hi"},
    )
    out = handle_peer_subcommand(["deny", record["request_id"]])
    assert out is not None
    assert "Denied" in out


def test_peer_command_registered_in_gateway_commands():
    from hermes_cli.commands import COMMAND_REGISTRY, GATEWAY_KNOWN_COMMANDS

    names = {cmd.name for cmd in COMMAND_REGISTRY}
    assert "peer" in names
    assert "peer" in GATEWAY_KNOWN_COMMANDS
