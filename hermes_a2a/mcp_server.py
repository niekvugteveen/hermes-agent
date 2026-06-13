"""Hermes A2A MCP server — peer_submit, peer_status, peer_capabilities."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from contextvars import ContextVar
from typing import Any, Dict, Optional

from hermes_a2a.constants import (
    REQUEST_KNOWLEDGE,
    REQUEST_REMINDER,
    REQUEST_SKILL_SHARE,
    SUPPORTED_INBOUND_TYPES,
)
from hermes_a2a.dispatch import dispatch_inbound
from hermes_a2a.registry import PeerRegistry
from hermes_a2a.store import RequestStore

logger = logging.getLogger(__name__)

_MCP_AVAILABLE = False
try:
    from mcp.server.fastmcp import FastMCP

    _MCP_AVAILABLE = True
except ImportError:
    FastMCP = None  # type: ignore[assignment,misc]

_inbound_auth_peer: ContextVar[str] = ContextVar("hermes_a2a_inbound_peer", default="")
_store = RequestStore()
_registry = PeerRegistry()


def _auth_error() -> str:
    return json.dumps({"error": "unauthorized"}, ensure_ascii=False)


def _require_auth() -> bool:
    return bool(_inbound_auth_peer.get())


def _handle_submit(request_type: str, from_peer: str, payload_json: str) -> str:
    if not _require_auth():
        return _auth_error()

    from_peer = (from_peer or _inbound_auth_peer.get() or "").strip().lower()
    if not from_peer:
        return json.dumps({"error": "from_peer is required"})

    try:
        payload = json.loads(payload_json) if payload_json else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid payload JSON: {exc}"})

    if not isinstance(payload, dict):
        return json.dumps({"error": "payload must be a JSON object"})

    if request_type not in SUPPORTED_INBOUND_TYPES:
        return json.dumps({"error": f"unsupported type: {request_type}"})

    local_id = _registry.get_local_id() or "hermes"
    record = _store.create_request(
        request_type=request_type,
        from_peer=from_peer,
        to_peer=local_id,
        payload=payload,
    )

    if request_type in {REQUEST_REMINDER}:
        return json.dumps(
            {
                "request_id": record["request_id"],
                "status": "not_implemented",
                "message": (
                    f"{request_type} is defined in the protocol but not implemented "
                    "in this build yet. knowledge_request and skill_share are supported."
                ),
            },
            indent=2,
        )

    envelope = {
        "request_id": record["request_id"],
        "type": request_type,
        "from_peer": from_peer,
        "to_peer": local_id,
        "payload": payload,
    }
    dispatch_inbound(envelope)
    return json.dumps(
        {
            "request_id": record["request_id"],
            "status": record["status"],
        },
        indent=2,
    )


def create_a2a_mcp_server(*, local_id: str = "") -> "FastMCP":
    if not _MCP_AVAILABLE:
        raise ImportError(
            "A2A MCP server requires the 'mcp' package. "
            "Install with: pip install 'mcp'"
        )

    if local_id:
        _registry.set_local_id(local_id)
    elif not _registry.get_local_id():
        _registry.set_local_id("hermes")

    instructions = (
        "Hermes agent-to-agent (A2A) protocol. Use peer_submit to send "
        "knowledge_request or set_reminder envelopes, then poll peer_status."
    )
    mcp = FastMCP("hermes-a2a", instructions=instructions)

    @mcp.tool()
    def peer_capabilities() -> str:
        """Return supported A2A request types and the local peer id."""
        if not _require_auth():
            return _auth_error()
        return json.dumps(
            {
                "schema_version": 1,
                "peer_id": _registry.get_local_id(),
                "supported_types": sorted(SUPPORTED_INBOUND_TYPES),
                "implemented_types": [REQUEST_KNOWLEDGE, REQUEST_SKILL_SHARE],
            },
            indent=2,
        )

    @mcp.tool()
    def peer_submit(
        request_type: str,
        from_peer: str,
        payload_json: str = "{}",
    ) -> str:
        """Submit a knowledge_request or set_reminder to this peer."""
        return _handle_submit(request_type, from_peer, payload_json)

    @mcp.tool()
    def peer_push(
        from_peer: str,
        payload_json: str = "{}",
    ) -> str:
        """Push a skill_share offer from a remote peer."""
        return _handle_submit(REQUEST_SKILL_SHARE, from_peer, payload_json)

    @mcp.tool()
    def peer_status(request_id: str) -> str:
        """Poll the status/result of a prior submit."""
        if not _require_auth():
            return _auth_error()
        record = _store.get(request_id)
        if record is None:
            return json.dumps({"error": "request not found"})
        body = {
            "request_id": record.get("request_id"),
            "status": record.get("status"),
            "type": record.get("type"),
            "response": record.get("response"),
        }
        return json.dumps(body, indent=2)

    return mcp


def _extract_bearer(headers: list) -> str:
    for key, value in headers:
        if key.decode("latin-1").lower() == "authorization":
            text = value.decode("latin-1")
            if text.lower().startswith("bearer "):
                return text[7:].strip()
    return ""


def _build_auth_middleware(app):
    """ASGI middleware validating Bearer tokens for A2A inbound calls."""

    async def middleware(scope, receive, send):
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return

        token = _extract_bearer(scope.get("headers") or [])
        if not token or not _registry.verify_inbound_token(token):
            body = b'{"error":"unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        # Token is global for v1; attribute inbound traffic to the peer id
        # declared in the MCP tool call (from_peer), not the bearer identity.
        _inbound_auth_peer.set("peer")
        try:
            await app(scope, receive, send)
        finally:
            _inbound_auth_peer.set("")

    return middleware


async def run_streamable_http_server(
    *,
    host: str,
    port: int,
    path: str,
    local_id: str,
) -> None:
    """Run the A2A MCP server until cancelled."""
    if not _MCP_AVAILABLE:
        raise ImportError("mcp package is required for A2A HTTP server")

    _registry.ensure_inbound_token()
    mcp = create_a2a_mcp_server(local_id=local_id)
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = path

    import uvicorn

    app = _build_auth_middleware(mcp.streamable_http_app())
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    except asyncio.CancelledError:
        server.should_exit = True
        raise
