"""HTTP MCP client for outbound A2A calls to paired peers."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from hermes_a2a.registry import PeerRegistry

logger = logging.getLogger(__name__)


async def _async_call_remote(
    url: str,
    token: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        from mcp import ClientSession
    except ImportError as exc:
        raise ImportError("mcp package is required for A2A peer calls") from exc

    try:
        from mcp.client.streamable_http import streamable_http_client
    except ImportError:
        from mcp.client.streamable_http import streamablehttp_client as streamable_http_client  # type: ignore

    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30.0, read=120.0)) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _sid):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=arguments)
                if result.isError:
                    text = ""
                    if result.content:
                        for block in result.content:
                            text += getattr(block, "text", "") or ""
                    return {"success": False, "error": text or "remote tool error"}
                text = ""
                if result.content:
                    for block in result.content:
                        text += getattr(block, "text", "") or ""
                try:
                    return {"success": True, "data": json.loads(text) if text else {}}
                except json.JSONDecodeError:
                    return {"success": True, "data": {"raw": text}}


def call_remote_tool(
    peer_id: str,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call an MCP tool on a paired remote peer (sync wrapper)."""
    registry = PeerRegistry()
    remote = registry.get_remote(peer_id)
    if remote is None:
        return {"success": False, "error": f"Unknown peer '{peer_id}'. Run `hermes peer pair` first."}
    url = remote.get("url") or ""
    token = remote.get("token") or ""
    if not url:
        return {"success": False, "error": f"Peer '{peer_id}' has no URL configured."}
    try:
        return asyncio.run(_async_call_remote(url, token, tool_name, arguments or {}))
    except Exception as exc:
        logger.exception("A2A remote call to %s failed", peer_id)
        return {"success": False, "error": str(exc)}


def push_skill_share(
    peer_id: str,
    *,
    from_peer: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Deliver a skill_share payload to a remote peer via peer_push."""
    result = call_remote_tool(
        peer_id,
        "peer_push",
        {
            "from_peer": from_peer,
            "payload_json": json.dumps(payload, ensure_ascii=False),
        },
    )
    if not result.get("success"):
        return result
    data = result.get("data") or {}
    if isinstance(data, dict) and data.get("error"):
        return {"success": False, "error": data["error"]}
    return {"success": True, **(data if isinstance(data, dict) else {"data": data})}
