"""Helpers for A2E A2A bridge e2e tests."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, Tuple

import pytest

SERVE_SCRIPT = Path(__file__).resolve().parent / "serve_peer.py"
COMPOSE_FILE = Path(__file__).resolve().parent / "docker-compose.yml"
SHARED_TOKEN = "e2e-shared-secret"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(host: str, port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for {host}:{port}")


@pytest.fixture()
def bob_peer_server(tmp_path, monkeypatch) -> Iterator[Tuple[str, Path]]:
    """Start a local A2A MCP server with auto-approve handler."""
    pytest.importorskip("mcp")

    bob_home = tmp_path / "bob"
    bob_home.mkdir()
    port = free_port()
    monkeypatch.setenv("HERMES_HOME", str(bob_home))
    monkeypatch.setenv("HERMES_A2A_INBOUND_TOKEN", SHARED_TOKEN)

    import os

    env = os.environ.copy()
    env["HERMES_HOME"] = str(bob_home)
    env["HERMES_A2A_INBOUND_TOKEN"] = SHARED_TOKEN

    proc = subprocess.Popen(
        [
            sys.executable,
            str(SERVE_SCRIPT),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--local-id",
            "bob",
            "--auto-approve",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_port("127.0.0.1", port)
        yield f"http://127.0.0.1:{port}/a2a/mcp", bob_home
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def pair_alice_to_bob(alice_home: Path, bob_url: str) -> None:
    import os

    os.environ["HERMES_HOME"] = str(alice_home)
    from hermes_a2a.registry import PeerRegistry

    registry = PeerRegistry()
    registry.set_local_id("alice")
    registry.pair_remote("bob", bob_url, token=SHARED_TOKEN)


def submit_knowledge_request(question: str = "Free Tuesday?") -> dict:
    from hermes_a2a.client import call_remote_tool

    result = call_remote_tool(
        "bob",
        "peer_submit",
        {
            "request_type": "knowledge_request",
            "from_peer": "alice",
            "payload_json": json.dumps({"question": question, "context": ""}),
        },
    )
    assert result.get("success"), result
    return result["data"]


def poll_peer_status(request_id: str, *, attempts: int = 20, delay: float = 0.25) -> dict:
    from hermes_a2a.client import call_remote_tool

    last = {}
    for _ in range(attempts):
        result = call_remote_tool("bob", "peer_status", {"request_id": request_id})
        assert result.get("success"), result
        last = result["data"]
        if last.get("status") in {"completed", "denied", "expired"}:
            return last
        time.sleep(delay)
    return last
