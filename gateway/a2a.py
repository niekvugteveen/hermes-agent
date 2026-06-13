"""Gateway integration for inbound A2A requests."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional, TYPE_CHECKING

from hermes_a2a.context import reset_a2a_context, set_a2a_context
from hermes_a2a.dispatch import register_inbound_handler
from hermes_a2a.store import RequestStore

if TYPE_CHECKING:
    from gateway.run import GatewayRunner

logger = logging.getLogger(__name__)

_store = RequestStore()


def install_a2a_handlers(runner: "GatewayRunner") -> None:
    """Register gateway-side inbound dispatch."""

    def _on_inbound(envelope: Dict[str, Any]) -> None:
        loop = getattr(runner, "_gateway_loop", None)
        if loop is None or not loop.is_running():
            logger.warning("A2A inbound received but gateway loop is not running")
            return
        asyncio.run_coroutine_threadsafe(
            process_a2a_inbound(runner, envelope),
            loop,
        )

    register_inbound_handler(_on_inbound)


async def start_a2a_on_gateway(runner: "GatewayRunner") -> None:
    from hermes_a2a.server import start_a2a_server

    install_a2a_handlers(runner)
    await start_a2a_server(loop=runner._gateway_loop)


async def stop_a2a_on_gateway() -> None:
    from hermes_a2a.server import stop_a2a_server

    await stop_a2a_server()


def _build_user_message(envelope: Dict[str, Any]) -> str:
    payload = envelope.get("payload") or {}
    question = str(payload.get("question") or "").strip()
    context = str(payload.get("context") or "").strip()
    lines = [
        f"[Agent-to-agent request from peer '{envelope.get('from_peer', '')}']",
        f"Type: {envelope.get('type', '')}",
        f"Request ID: {envelope.get('request_id', '')}",
        "",
    ]
    if question:
        lines.append(f"Question: {question}")
    if context:
        lines.append(f"Context: {context}")
    lines.extend(
        [
            "",
            "Investigate using your tools as needed. When ready, call "
            "`a2a_propose_response` with the exact text you want to send back. "
            "The human will approve before anything is delivered to the peer.",
        ]
    )
    return "\n".join(lines)


def _pick_notify_adapter(runner: "GatewayRunner"):
    from gateway.config import Platform

    skip = {Platform.LOCAL, Platform.API_SERVER, Platform.WEBHOOK}
    for platform, adapter in runner.adapters.items():
        if platform in skip:
            continue
        return adapter
    return None


def _resolve_notify_chat_id(runner: "GatewayRunner") -> str:
    try:
        for platform, platform_config in runner.config.platforms.items():
            if not platform_config.enabled:
                continue
            home = runner.config.get_home_channel(platform)
            if home and getattr(home, "chat_id", None):
                return str(home.chat_id)
    except Exception:
        pass
    return ""


async def process_a2a_inbound(runner: "GatewayRunner", envelope: Dict[str, Any]) -> None:
    """Run an isolated A2A agent turn for a knowledge_request."""
    request_id = str(envelope.get("request_id") or "")
    from_peer = str(envelope.get("from_peer") or "")
    request_type = str(envelope.get("type") or "")
    if not request_id:
        return

    session_key = f"agent:main:a2a:peer:{from_peer}"
    user_message = _build_user_message(envelope)
    loop = asyncio.get_running_loop()
    adapter = _pick_notify_adapter(runner)
    notify_chat_id = _resolve_notify_chat_id(runner)

    def _clarify_callback(question, choices):
        from tools import clarify_gateway as clarify_mod

        if adapter is None or not notify_chat_id:
            return "[clarify unavailable: no messaging adapter configured]"

        clarify_id = uuid.uuid4().hex[:10]
        clarify_mod.register(
            clarify_id=clarify_id,
            session_key=session_key,
            question=question,
            choices=list(choices) if choices else None,
        )
        fut = asyncio.run_coroutine_threadsafe(
            adapter.send_clarify(
                chat_id=notify_chat_id,
                question=question,
                choices=list(choices) if choices else None,
                clarify_id=clarify_id,
                session_key=session_key,
            ),
            loop,
        )
        try:
            result = fut.result(timeout=15)
            if not getattr(result, "success", True):
                clarify_mod.clear_session(session_key)
                return "[clarify prompt could not be delivered]"
        except Exception:
            clarify_mod.clear_session(session_key)
            return "[clarify prompt could not be delivered]"
        timeout = float(clarify_mod.get_clarify_timeout())
        response = clarify_mod.wait_for_response(clarify_id, timeout=timeout)
        return response or f"[user did not respond within {int(timeout / 60)}m]"

    def _run_sync() -> None:
        from run_agent import AIAgent

        tokens = set_a2a_context(
            request_id=request_id,
            from_peer=from_peer,
            request_type=request_type,
        )
        try:
            agent = AIAgent(
                quiet_mode=True,
                verbose_logging=False,
                enabled_toolsets=["a2a", "terminal", "file", "search", "web"],
                skip_memory=True,
                platform="a2a",
                gateway_session_key=session_key,
                clarify_callback=_clarify_callback,
            )
            agent.run_conversation(user_message=user_message, task_id=request_id)
        except Exception:
            logger.exception("A2A agent turn failed for %s", request_id)
            _store.complete_response(request_id, denied=True)
        finally:
            reset_a2a_context(tokens)

    await loop.run_in_executor(None, _run_sync)
