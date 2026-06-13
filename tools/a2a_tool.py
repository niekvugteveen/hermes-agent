"""Service-gated tool for proposing an outbound A2A response."""

from __future__ import annotations

import json
from typing import Callable, Optional

from hermes_a2a.constants import (
    APPROVE_ALWAYS,
    APPROVE_DENY,
    APPROVE_ONCE,
    REQUEST_KNOWLEDGE,
)
from hermes_a2a.context import get_a2a_from_peer, get_a2a_request_id, get_a2a_request_type
from hermes_a2a.registry import PeerRegistry
from hermes_a2a.store import RequestStore
from tools.clarify_tool import clarify_tool
from tools.registry import registry, tool_error

_store = RequestStore()
_registry = PeerRegistry()


def check_a2a_tool_requirements() -> bool:
    from hermes_a2a.context import a2a_context_active

    return a2a_context_active()


def _normalize_decision(raw: str) -> str:
    text = (raw or "").strip().lower()
    if text in {"send once", "once", "send", "approve", "yes", "allow-once", "allow once"}:
        return APPROVE_ONCE
    if text in {"always", "always from this peer", "allow-always", "allow always"}:
        return APPROVE_ALWAYS
    if text in {"deny", "no", "reject"}:
        return APPROVE_DENY
    return raw.strip()


def a2a_propose_response(
    proposed_response: str,
    summary_for_human: str = "",
    clarify_callback: Optional[Callable] = None,
) -> str:
    """Propose an outbound peer reply and obtain human approval before sending."""
    request_id = get_a2a_request_id()
    from_peer = get_a2a_from_peer()
    request_type = get_a2a_request_type() or REQUEST_KNOWLEDGE

    if not request_id:
        return tool_error("No active A2A request in this session.")

    proposed = (proposed_response or "").strip()
    if not proposed:
        return tool_error("proposed_response is required.")

    record = _store.get(request_id)
    if record is None:
        return tool_error(f"Unknown A2A request id: {request_id}")

    if _registry.trust_is_always(from_peer, request_type):
        _store.complete_response(request_id, answer=proposed, denied=False)
        return json.dumps(
            {
                "success": True,
                "auto_approved": True,
                "request_id": request_id,
                "status": "completed",
            },
            ensure_ascii=False,
        )

    question = summary_for_human.strip() or (
        f"Peer agent '{from_peer}' sent a {request_type.replace('_', ' ')}.\n\n"
        f"Proposed reply to send:\n{proposed}\n\n"
        "Send this response?"
    )
    choices = ["Send once", "Always from this peer", "Deny", "Other"]

    if clarify_callback is None:
        return tool_error(
            "Human approval is required but clarify is unavailable in this context."
        )

    raw_result = clarify_tool(question, choices, callback=clarify_callback)
    try:
        parsed = json.loads(raw_result)
    except json.JSONDecodeError:
        parsed = {"response": raw_result}
    if parsed.get("error"):
        return raw_result

    raw = str(parsed.get("user_response") or parsed.get("response") or "").strip()
    if not raw or raw.startswith("["):
        _store.complete_response(request_id, denied=True)
        return json.dumps(
            {
                "success": False,
                "denied": True,
                "reason": "no_human_response",
                "request_id": request_id,
            },
            ensure_ascii=False,
        )

    decision = _normalize_decision(raw)
    if decision == APPROVE_DENY:
        _store.complete_response(request_id, denied=True)
        return json.dumps(
            {
                "success": False,
                "denied": True,
                "request_id": request_id,
            },
            ensure_ascii=False,
        )
    if decision == APPROVE_ALWAYS:
        _registry.set_trust(from_peer, request_type, "always")
    elif decision not in {APPROVE_ONCE, APPROVE_ALWAYS}:
        proposed = raw.strip() or proposed

    _store.mark_awaiting_human(request_id, proposed)
    _store.complete_response(request_id, answer=proposed, denied=False)
    return json.dumps(
        {
            "success": True,
            "approved": True,
            "request_id": request_id,
            "status": "completed",
            "response": proposed,
        },
        ensure_ascii=False,
    )


registry.register(
    name="a2a_propose_response",
    toolset="a2a",
    schema={
        "name": "a2a_propose_response",
        "description": (
            "Propose the outbound reply for the active agent-to-agent request. "
            "The local human must approve (send once / always / deny) before the "
            "answer is returned to the calling peer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "proposed_response": {
                    "type": "string",
                    "description": "The exact text to send back to the peer agent if approved.",
                },
                "summary_for_human": {
                    "type": "string",
                    "description": "Optional approval prompt shown to the human.",
                },
            },
            "required": ["proposed_response"],
        },
    },
    handler=lambda args, **kw: a2a_propose_response(
        proposed_response=args.get("proposed_response", ""),
        summary_for_human=args.get("summary_for_human", ""),
        clarify_callback=kw.get("clarify_callback"),
    ),
    check_fn=check_a2a_tool_requirements,
)
