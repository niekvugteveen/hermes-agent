"""Run the Hermes A2A MCP HTTP server."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_server_task: Optional[asyncio.Task] = None


def _load_serve_config() -> dict:
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        peers = cfg.get("peers") or {}
        serve = peers.get("serve") or {}
        if not isinstance(serve, dict):
            serve = {}
        if not isinstance(peers, dict):
            peers = {}
        return {
            "enabled": bool(serve.get("enabled")),
            "host": str(serve.get("host") or "0.0.0.0"),
            "port": int(serve.get("port") or 8765),
            "path": str(serve.get("path") or "/a2a/mcp"),
            "local_id": str(peers.get("local_id") or "").strip(),
        }
    except Exception:
        return {
            "enabled": os.getenv("HERMES_A2A_SERVE", "").lower() in {"1", "true", "yes"},
            "host": os.getenv("HERMES_A2A_HOST", "0.0.0.0"),
            "port": int(os.getenv("HERMES_A2A_PORT", "8765")),
            "path": os.getenv("HERMES_A2A_PATH", "/a2a/mcp"),
            "local_id": os.getenv("HERMES_A2A_LOCAL_ID", "").strip(),
        }


async def start_a2a_server(loop: Optional[asyncio.AbstractEventLoop] = None) -> bool:
    """Start the HTTP MCP server if enabled in config."""
    global _server_task

    cfg = _load_serve_config()
    if not cfg["enabled"]:
        return False
    if _server_task and not _server_task.done():
        return True

    from hermes_a2a.mcp_server import run_streamable_http_server

    loop = loop or asyncio.get_running_loop()
    _server_task = loop.create_task(
        run_streamable_http_server(
            host=cfg["host"],
            port=cfg["port"],
            path=cfg["path"],
            local_id=cfg["local_id"],
        )
    )
    logger.info(
        "A2A MCP server starting on http://%s:%s%s",
        cfg["host"],
        cfg["port"],
        cfg["path"],
    )
    return True


async def stop_a2a_server() -> None:
    global _server_task
    if _server_task and not _server_task.done():
        _server_task.cancel()
        try:
            await _server_task
        except asyncio.CancelledError:
            pass
    _server_task = None


def run_standalone(
    *,
    host: str = "0.0.0.0",
    port: int = 8765,
    path: str = "/a2a/mcp",
    local_id: str = "",
    verbose: bool = False,
) -> None:
    """Blocking entry for ``hermes peer serve``."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, stream=os.sys.stderr)

    from hermes_a2a.mcp_server import run_streamable_http_server

    asyncio.run(
        run_streamable_http_server(
            host=host,
            port=port,
            path=path,
            local_id=local_id,
        )
    )
