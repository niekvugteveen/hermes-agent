"""Gateway wiring tests for /peer slash commands."""

from __future__ import annotations

from pathlib import Path


def test_gateway_run_dispatches_peer_command():
    source = Path("gateway/run.py").read_text(encoding="utf-8")
    assert 'canonical == "peer"' in source
    assert "_handle_peer_command" in source


def test_gateway_peer_handler_exists():
    from gateway.slash_commands import GatewaySlashCommandsMixin

    assert hasattr(GatewaySlashCommandsMixin, "_handle_peer_command")
