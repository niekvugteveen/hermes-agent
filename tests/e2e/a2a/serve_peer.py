#!/usr/bin/env python3
"""Minimal A2A MCP HTTP server for bridge-network e2e smoke tests."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Hermes A2A peer HTTP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--path", default="/a2a/mcp")
    parser.add_argument("--local-id", default="bob")
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Immediately complete knowledge_request inbound envelopes (e2e only)",
    )
    args = parser.parse_args()

    token = os.environ.get("HERMES_A2A_INBOUND_TOKEN", "").strip()
    if not token:
        token = "e2e-shared-secret"
        os.environ["HERMES_A2A_INBOUND_TOKEN"] = token

    home = os.environ.get("HERMES_HOME", "").strip()
    if not home:
        home = str(Path("/tmp/hermes-a2a-e2e"))
        os.environ["HERMES_HOME"] = home
    Path(home).mkdir(parents=True, exist_ok=True)

    from hermes_a2a.registry import PeerRegistry

    registry = PeerRegistry()
    registry.set_local_id(args.local_id)
    registry.set_inbound_token(token)

    if args.auto_approve:
        from hermes_a2a.dispatch import register_inbound_handler
        from hermes_a2a.store import RequestStore

        store = RequestStore()

        def _auto_handler(envelope: dict) -> None:
            request_id = str(envelope.get("request_id") or "")
            if not request_id:
                return
            if str(envelope.get("type") or "") != "knowledge_request":
                return
            store.complete_response(
                request_id,
                answer="Tuesday after 2pm works.",
                denied=False,
            )

        register_inbound_handler(_auto_handler)

    from hermes_a2a.server import run_standalone

    run_standalone(
        host=args.host,
        port=args.port,
        path=args.path,
        local_id=args.local_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
