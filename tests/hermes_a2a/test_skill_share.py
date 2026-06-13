"""Tests for A2A skill_share artifact bundling and approval flows."""

from __future__ import annotations

import base64
import importlib
import json
from unittest.mock import patch

import pytest

from hermes_a2a.artifacts import (
    TIER_FULL,
    TIER_REFERENCE,
    TIER_SUMMARY,
    artifact_preview,
    bundle_skill,
    install_skill_artifact,
    verify_artifact_checksum,
)
from hermes_a2a.context import reset_a2a_context, set_a2a_context
from hermes_a2a.registry import PeerRegistry
from hermes_a2a.store import RequestStore
from tools.a2a_tool import a2a_accept_skill, a2a_share_skill

_SAMPLE_SKILL = """---
name: deploy-checklist
description: Deploy checklist for releases.
version: 1.0.0
---

# Deploy Checklist

Run tests before deploy.
"""


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    try:
        import hermes_constants

        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    except ImportError:
        pass
    skills_dir = tmp_path / "skills" / "deploy-checklist"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(_SAMPLE_SKILL, encoding="utf-8")
    scripts = skills_dir / "scripts"
    scripts.mkdir()
    (scripts / "deploy.sh").write_text("#!/bin/sh\necho deploy\n", encoding="utf-8")
    yield tmp_path


def _reload_skill_manager(monkeypatch, home):
    import hermes_constants
    import tools.skill_manager_tool as smt

    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
    monkeypatch.setattr(smt, "HERMES_HOME", home)
    monkeypatch.setattr(smt, "SKILLS_DIR", home / "skills")
    importlib.reload(smt)
    monkeypatch.setattr(smt, "HERMES_HOME", home)
    monkeypatch.setattr(smt, "SKILLS_DIR", home / "skills")
    return smt


def test_bundle_summary_tier(hermes_home):
    result = bundle_skill("deploy-checklist", TIER_SUMMARY)
    assert result["success"] is True
    assert result["tier"] == TIER_SUMMARY
    files = result["artifact"]["files"]
    assert len(files) == 1
    assert files[0]["path"] == "SKILL.md"
    assert verify_artifact_checksum(result["artifact"])


def test_bundle_full_tier_includes_scripts(hermes_home):
    result = bundle_skill("deploy-checklist", TIER_FULL)
    assert result["success"] is True
    paths = {f["path"] for f in result["artifact"]["files"]}
    assert "SKILL.md" in paths
    assert "scripts/deploy.sh" in paths


def test_bundle_reference_tier_has_no_files(hermes_home):
    result = bundle_skill("deploy-checklist", TIER_REFERENCE)
    assert result["success"] is True
    assert result["artifact"]["files"] == []
    assert result["artifact"]["reference"]["description"]


def test_bundle_excludes_env_files(hermes_home):
    skill_dir = hermes_home / "skills" / "deploy-checklist"
    (skill_dir / ".env").write_text("SECRET=1\n", encoding="utf-8")
    result = bundle_skill("deploy-checklist", TIER_FULL)
    paths = {f["path"] for f in result["artifact"]["files"]}
    assert ".env" not in paths


def test_install_skill_roundtrip(hermes_home, monkeypatch):
    smt = _reload_skill_manager(monkeypatch, hermes_home)
    bundled = bundle_skill("deploy-checklist", TIER_FULL)
    install_name = "imported-checklist"
    artifact = bundled["artifact"]
    result = install_skill_artifact(install_name, artifact)
    assert result["success"] is True
    assert result["installed"] is True
    found = smt._find_skill(install_name)
    assert found is not None
    assert (found["path"] / "scripts" / "deploy.sh").is_file()


def test_install_rejects_checksum_tamper(hermes_home, monkeypatch):
    _reload_skill_manager(monkeypatch, hermes_home)
    bundled = bundle_skill("deploy-checklist", TIER_SUMMARY)
    artifact = dict(bundled["artifact"])
    artifact["checksum"] = "sha256:deadbeef"
    result = install_skill_artifact("bad-import", artifact)
    assert result["success"] is False
    assert "checksum" in result["error"].lower()


@pytest.fixture()
def inbound_skill_env(hermes_home):
    store = RequestStore()
    bundled = bundle_skill("deploy-checklist", TIER_SUMMARY)
    record = store.create_request(
        request_type="skill_share",
        from_peer="bob",
        to_peer="alice",
        payload={
            "skill_name": "shared-deploy",
            "tier": TIER_SUMMARY,
            "artifact": bundled["artifact"],
            "message": "Try this skill.",
        },
    )
    tokens = set_a2a_context(
        request_id=record["request_id"],
        from_peer="bob",
        request_type="skill_share",
    )
    yield record
    reset_a2a_context(tokens)


def test_accept_skill_installs_with_approval(inbound_skill_env, hermes_home, monkeypatch):
    _reload_skill_manager(monkeypatch, hermes_home)
    with patch("tools.a2a_tool.clarify_tool") as mock_clarify:
        mock_clarify.return_value = json.dumps({"user_response": "Install"})
        result = json.loads(
            a2a_accept_skill(clarify_callback=lambda q, c: "Install")
        )
    assert result["success"] is True
    assert result["installed"] is True


def test_accept_skill_decline(inbound_skill_env):
    with patch("tools.a2a_tool.clarify_tool") as mock_clarify:
        mock_clarify.return_value = json.dumps({"user_response": "Decline"})
        result = json.loads(
            a2a_accept_skill(clarify_callback=lambda q, c: "Decline")
        )
    assert result["denied"] is True


def test_accept_skill_auto_on_pull_response(inbound_skill_env, hermes_home, monkeypatch):
    _reload_skill_manager(monkeypatch, hermes_home)
    tokens = set_a2a_context(
        request_id=inbound_skill_env["request_id"],
        from_peer="bob",
        request_type="skill_share",
        skip_inbound_approval=True,
    )
    try:
        result = json.loads(a2a_accept_skill())
    finally:
        reset_a2a_context(tokens)
    assert result["success"] is True
    assert result.get("auto_approved") is True


@pytest.fixture()
def paired_peer(hermes_home):
    registry = PeerRegistry()
    registry.set_local_id("alice")
    registry.pair_remote("bob", "http://bob-hermes:8765/a2a/mcp", token="secret")
    return registry


def test_share_skill_denied_without_send(paired_peer, hermes_home):
    with patch("tools.a2a_tool.push_skill_share") as mock_push:
        with patch("tools.a2a_tool.clarify_tool") as mock_clarify:
            mock_clarify.return_value = json.dumps({"user_response": "Deny"})
            result = json.loads(
                a2a_share_skill(
                    peer_id="bob",
                    skill_name="deploy-checklist",
                    tier=TIER_SUMMARY,
                    clarify_callback=lambda q, c: "Deny",
                )
            )
        mock_push.assert_not_called()
    assert result["denied"] is True


def test_share_skill_pushes_on_approval(paired_peer, hermes_home):
    with patch("tools.a2a_tool.push_skill_share") as mock_push:
        mock_push.return_value = {"success": True, "request_id": "remote-1", "status": "pending"}
        with patch("tools.a2a_tool.clarify_tool") as mock_clarify:
            mock_clarify.return_value = json.dumps({"user_response": "Send once"})
            result = json.loads(
                a2a_share_skill(
                    peer_id="bob",
                    skill_name="deploy-checklist",
                    tier=TIER_SUMMARY,
                    clarify_callback=lambda q, c: "Send once",
                )
            )
        mock_push.assert_called_once()
        call_payload = mock_push.call_args.kwargs["payload"]
        assert call_payload["skill_name"] == "deploy-checklist"
        assert verify_artifact_checksum(call_payload["artifact"])
    assert result["success"] is True
    assert result["remote_request_id"] == "remote-1"


def test_artifact_preview_lists_files(hermes_home):
    bundled = bundle_skill("deploy-checklist", TIER_FULL)
    preview = artifact_preview(
        {
            "skill_name": "deploy-checklist",
            "tier": TIER_FULL,
            "artifact": bundled["artifact"],
            "message": "For you",
        }
    )
    assert "deploy-checklist" in preview
    assert "scripts/deploy.sh" in preview
    assert "For you" in preview
