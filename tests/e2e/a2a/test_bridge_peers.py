"""Bridge-network smoke tests for the Hermes A2A protocol."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.e2e.a2a.conftest import (
    COMPOSE_FILE,
    SHARED_TOKEN,
    pair_alice_to_bob,
    poll_peer_status,
    submit_knowledge_request,
    wait_for_port,
)

pytestmark = pytest.mark.e2e


@pytest.mark.e2e
def test_bridge_in_process_knowledge_roundtrip(bob_peer_server, tmp_path):
    """Alice submits knowledge_request to Bob over HTTP MCP; Bob auto-approves."""
    pytest.importorskip("mcp")

    bob_url, _bob_home = bob_peer_server
    alice_home = tmp_path / "alice"
    alice_home.mkdir()
    os.environ["HERMES_HOME"] = str(alice_home)

    pair_alice_to_bob(alice_home, bob_url)
    submit_data = submit_knowledge_request()
    request_id = submit_data["request_id"]
    assert submit_data.get("status") == "pending"

    status = poll_peer_status(request_id)
    assert status.get("status") == "completed"
    response = status.get("response") or {}
    assert response.get("status") == "answered"
    assert "Tuesday" in str(response.get("answer") or "")


@pytest.mark.e2e_docker
@pytest.mark.skipif(
    os.environ.get("HERMES_A2A_E2E_DOCKER", "").lower() not in {"1", "true", "yes"},
    reason="Set HERMES_A2A_E2E_DOCKER=1 to run docker-compose bridge smoke",
)
def test_docker_bridge_knowledge_roundtrip(tmp_path):
    """Optional docker-compose smoke: bob-hermes service on bridge network."""
    pytest.importorskip("mcp")
    if shutil.which("docker") is None:
        pytest.skip("docker not available")

    compose = ["docker", "compose", "-f", str(COMPOSE_FILE)]
    subprocess.run([*compose, "down", "--remove-orphans"], check=False)
    subprocess.run([*compose, "up", "--build", "-d", "bob-hermes"], check=True)
    try:
        wait_for_port("127.0.0.1", 18765, timeout=60.0)
        bob_url = "http://127.0.0.1:18765/a2a/mcp"

        alice_home = tmp_path / "alice-docker"
        alice_home.mkdir()
        os.environ["HERMES_HOME"] = str(alice_home)
        pair_alice_to_bob(alice_home, bob_url)

        submit_data = submit_knowledge_request("Docker bridge OK?")
        request_id = submit_data["request_id"]

        # Bob container runs with --auto-approve in compose CMD override... 
        # Dockerfile CMD includes --auto-approve
        status = poll_peer_status(request_id, attempts=40, delay=0.5)
        assert status.get("status") == "completed"
        answer = str((status.get("response") or {}).get("answer") or "")
        assert "Tuesday" in answer
    finally:
        subprocess.run([*compose, "down", "--remove-orphans"], check=False)


@pytest.mark.e2e
def test_compose_file_declares_bridge_network():
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "a2a-bridge" in text
    assert "bob-hermes" in text
    assert "alice-hermes" in text
    assert "driver: bridge" in text
