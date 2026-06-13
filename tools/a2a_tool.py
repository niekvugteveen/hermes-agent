"""Service-gated tools for agent-to-agent (A2A) peer communication."""

from __future__ import annotations

import json
from typing import Callable, Optional

from hermes_a2a.artifacts import artifact_preview, bundle_skill, install_skill_artifact
from hermes_a2a.client import push_skill_share
from hermes_a2a.constants import (
    APPROVE_ALWAYS,
    APPROVE_DENY,
    APPROVE_ONCE,
    REQUEST_KNOWLEDGE,
    REQUEST_REMINDER,
    REQUEST_SKILL_SHARE,
)
from hermes_a2a.cron_proposal import (
    build_reminder_proposal,
    create_reminder_job,
    format_reminder_preview,
)
from hermes_a2a.context import (
    get_a2a_from_peer,
    get_a2a_request_id,
    get_a2a_request_type,
    skip_inbound_approval,
)
from hermes_a2a.registry import PeerRegistry
from hermes_a2a.store import RequestStore
from tools.clarify_tool import clarify_tool
from tools.registry import registry, tool_error

_store = RequestStore()
_registry = PeerRegistry()


def check_a2a_tool_requirements() -> bool:
    from hermes_a2a.context import a2a_context_active

    return a2a_context_active()


def check_a2a_accept_requirements() -> bool:
    from hermes_a2a.context import a2a_context_active

    return a2a_context_active() and get_a2a_request_type() == REQUEST_SKILL_SHARE


def check_a2a_reminder_requirements() -> bool:
    from hermes_a2a.context import a2a_context_active

    return a2a_context_active() and get_a2a_request_type() == REQUEST_REMINDER


def check_a2a_share_requirements() -> bool:
    return bool(_registry.list_remotes())


def _normalize_decision(raw: str) -> str:
    text = (raw or "").strip().lower()
    if text in {
        "send once", "once", "send", "approve", "yes", "allow-once", "allow once",
        "install", "install once", "accept",
        "create", "create job", "create once",
    }:
        return APPROVE_ONCE
    if text in {"always", "always from this peer", "allow-always", "allow always"}:
        return APPROVE_ALWAYS
    if text in {"deny", "no", "reject", "decline"}:
        return APPROVE_DENY
    return raw.strip()


def _run_clarify(
    question: str,
    choices: list,
    clarify_callback: Optional[Callable],
) -> tuple[str, bool]:
    if clarify_callback is None:
        return "", False
    raw_result = clarify_tool(question, choices, callback=clarify_callback)
    try:
        parsed = json.loads(raw_result)
    except json.JSONDecodeError:
        parsed = {"response": raw_result}
    if parsed.get("error"):
        return "", False
    raw = str(parsed.get("user_response") or parsed.get("response") or "").strip()
    if not raw or raw.startswith("["):
        return "", False
    return raw, True


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

    raw, ok = _run_clarify(question, choices, clarify_callback)
    if not ok:
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


def a2a_share_skill(
    peer_id: str,
    skill_name: str,
    tier: str = "summary",
    message: str = "",
    responding_to_pull: bool = False,
    summary_for_human: str = "",
    clarify_callback: Optional[Callable] = None,
) -> str:
    """Bundle a local skill and push it to a paired peer after human approval."""
    peer_id = (peer_id or "").strip().lower()
    skill_name = (skill_name or "").strip()
    if not peer_id:
        return tool_error("peer_id is required.")
    if not skill_name:
        return tool_error("skill_name is required.")
    if _registry.get_remote(peer_id) is None:
        return tool_error(f"Unknown peer '{peer_id}'.")

    bundled = bundle_skill(skill_name, tier)
    if not bundled.get("success"):
        return json.dumps(bundled, ensure_ascii=False)

    payload = {
        "skill_name": skill_name,
        "tier": bundled.get("tier"),
        "artifact": bundled.get("artifact"),
        "message": (message or "").strip(),
        "responding_to_pull": bool(responding_to_pull),
    }
    preview = artifact_preview(payload)
    request_type = REQUEST_SKILL_SHARE

    if not _registry.trust_is_always(peer_id, request_type):
        question = summary_for_human.strip() or (
            f"Share skill with peer '{peer_id}'?\n\n{preview}\n\nSend this skill?"
        )
        choices = ["Send once", "Always from this peer", "Deny", "Other"]
        raw, ok = _run_clarify(question, choices, clarify_callback)
        if not ok:
            return json.dumps(
                {"success": False, "denied": True, "reason": "no_human_response"},
                ensure_ascii=False,
            )
        decision = _normalize_decision(raw)
        if decision == APPROVE_DENY:
            inbound_id = get_a2a_request_id()
            if inbound_id and responding_to_pull:
                _store.complete_response(inbound_id, denied=True)
            return json.dumps(
                {"success": False, "denied": True, "peer_id": peer_id},
                ensure_ascii=False,
            )
        if decision == APPROVE_ALWAYS:
            _registry.set_trust(peer_id, request_type, "always")

    local_id = _registry.get_local_id() or "hermes"
    push_result = push_skill_share(peer_id, from_peer=local_id, payload=payload)
    if not push_result.get("success"):
        return json.dumps(push_result, ensure_ascii=False)

    inbound_id = get_a2a_request_id()
    if inbound_id and responding_to_pull:
        _store.complete_response(
            inbound_id,
            answer=json.dumps({"status": "shared", "skill_name": skill_name}),
            denied=False,
            extra={"status": "shared", "skill_name": skill_name},
        )

    return json.dumps(
        {
            "success": True,
            "peer_id": peer_id,
            "skill_name": skill_name,
            "remote_request_id": push_result.get("request_id"),
            "status": push_result.get("status", "pending"),
        },
        ensure_ascii=False,
    )


def a2a_accept_skill(
    summary_for_human: str = "",
    clarify_callback: Optional[Callable] = None,
) -> str:
    """Approve (or decline) installing a skill_share offer from a peer."""
    request_id = get_a2a_request_id()
    from_peer = get_a2a_from_peer()
    if not request_id:
        return tool_error("No active A2A request in this session.")

    record = _store.get(request_id)
    if record is None:
        return tool_error(f"Unknown A2A request id: {request_id}")

    payload = record.get("payload") or {}
    skill_name = str(payload.get("skill_name") or "").strip()
    artifact = payload.get("artifact") or {}
    if not skill_name or not artifact:
        return tool_error("Active request is not a skill_share with an artifact.")

    if skip_inbound_approval() or _registry.trust_is_always(from_peer, REQUEST_SKILL_SHARE):
        install_result = install_skill_artifact(skill_name, artifact)
        if not install_result.get("success"):
            _store.complete_response(request_id, denied=True, denial_message=install_result.get("error"))
            return json.dumps(install_result, ensure_ascii=False)
        _store.complete_response(
            request_id,
            answer=json.dumps({"status": "installed", "skill_name": skill_name}),
            denied=False,
            extra={"status": "installed", "skill_name": skill_name},
        )
        return json.dumps(
            {
                "success": True,
                "auto_approved": True,
                "installed": install_result.get("installed", True),
                "request_id": request_id,
            },
            ensure_ascii=False,
        )

    preview = artifact_preview(payload)
    question = summary_for_human.strip() or (
        f"Peer '{from_peer}' wants to share a skill.\n\n{preview}\n\nInstall this skill?"
    )
    choices = ["Install", "Always from this peer", "Decline", "Other"]

    if clarify_callback is None:
        return tool_error(
            "Human approval is required but clarify is unavailable in this context."
        )

    raw, ok = _run_clarify(question, choices, clarify_callback)
    if not ok:
        _store.complete_response(request_id, denied=True)
        return json.dumps(
            {"success": False, "denied": True, "reason": "no_human_response", "request_id": request_id},
            ensure_ascii=False,
        )

    decision = _normalize_decision(raw)
    if decision == APPROVE_DENY:
        _store.complete_response(
            request_id,
            denied=True,
            extra={"status": "declined"},
        )
        return json.dumps(
            {"success": False, "denied": True, "status": "declined", "request_id": request_id},
            ensure_ascii=False,
        )
    if decision == APPROVE_ALWAYS:
        _registry.set_trust(from_peer, REQUEST_SKILL_SHARE, "always")

    install_result = install_skill_artifact(skill_name, artifact)
    if not install_result.get("success"):
        _store.complete_response(request_id, denied=True, denial_message=install_result.get("error"))
        return json.dumps(install_result, ensure_ascii=False)

    _store.complete_response(
        request_id,
        answer=json.dumps({"status": "installed", "skill_name": skill_name}),
        denied=False,
        extra={"status": "installed", "skill_name": skill_name},
    )
    return json.dumps(
        {
            "success": True,
            "installed": True,
            "skill_name": skill_name,
            "request_id": request_id,
            "status": "installed",
        },
        ensure_ascii=False,
    )


def a2a_propose_reminder(
    schedule: str,
    message: str,
    deliver: str = "",
    name: str = "",
    summary_for_human: str = "",
    clarify_callback: Optional[Callable] = None,
) -> str:
    """Propose a cron reminder job for human approval, then create it on approve."""
    request_id = get_a2a_request_id()
    from_peer = get_a2a_from_peer()
    request_type = get_a2a_request_type() or REQUEST_REMINDER

    if not request_id:
        return tool_error("No active A2A request in this session.")

    record = _store.get(request_id)
    if record is None:
        return tool_error(f"Unknown A2A request id: {request_id}")

    inbound = record.get("payload") or {}
    deliver_value = (deliver or "").strip() or str(
        inbound.get("delivery_hint") or inbound.get("deliver") or ""
    ).strip()
    proposal = build_reminder_proposal(
        schedule=schedule,
        message=message,
        deliver=deliver_value or None,
        name=(name or "").strip() or None,
        from_peer=from_peer,
    )
    if not proposal.get("success"):
        return json.dumps(proposal, ensure_ascii=False)

    if _registry.trust_is_always(from_peer, request_type):
        create_result = create_reminder_job(proposal, from_peer=from_peer)
        if not create_result.get("success"):
            _store.complete_response(request_id, denied=True, denial_message=create_result.get("error"))
            return json.dumps(create_result, ensure_ascii=False)
        job_id = str(create_result.get("job_id") or "")
        _store.complete_response(
            request_id,
            answer=json.dumps({"status": "created", "job_id": job_id}),
            denied=False,
            extra={"status": "created", "job_id": job_id},
        )
        return json.dumps(
            {
                "success": True,
                "auto_approved": True,
                "request_id": request_id,
                "status": "created",
                "job_id": job_id,
            },
            ensure_ascii=False,
        )

    preview = format_reminder_preview(proposal, from_peer=from_peer)
    question = summary_for_human.strip() or f"{preview}\n\nCreate this cron job?"
    choices = ["Create", "Always from this peer", "Deny", "Other"]

    if clarify_callback is None:
        return tool_error(
            "Human approval is required but clarify is unavailable in this context."
        )

    raw, ok = _run_clarify(question, choices, clarify_callback)
    if not ok:
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
        _store.complete_response(
            request_id,
            denied=True,
            extra={"status": "denied"},
        )
        return json.dumps(
            {
                "success": False,
                "denied": True,
                "status": "denied",
                "request_id": request_id,
            },
            ensure_ascii=False,
        )
    if decision == APPROVE_ALWAYS:
        _registry.set_trust(from_peer, request_type, "always")
    elif decision not in {APPROVE_ONCE, APPROVE_ALWAYS}:
        # "Other" — allow editing schedule/message from free text is out of scope;
        # proceed with the original proposal unless the user typed a full override
        # in the clarify response (same pattern as knowledge_request).
        pass

    _store.mark_awaiting_human(request_id, preview)
    create_result = create_reminder_job(proposal, from_peer=from_peer)
    if not create_result.get("success"):
        _store.complete_response(request_id, denied=True, denial_message=create_result.get("error"))
        return json.dumps(create_result, ensure_ascii=False)

    job_id = str(create_result.get("job_id") or "")
    _store.complete_response(
        request_id,
        answer=json.dumps({"status": "created", "job_id": job_id}),
        denied=False,
        extra={"status": "created", "job_id": job_id},
    )
    return json.dumps(
        {
            "success": True,
            "approved": True,
            "request_id": request_id,
            "status": "created",
            "job_id": job_id,
            "schedule": create_result.get("schedule"),
            "deliver": create_result.get("deliver"),
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

registry.register(
    name="a2a_share_skill",
    toolset="a2a",
    schema={
        "name": "a2a_share_skill",
        "description": (
            "Bundle a local skill and push it to a paired peer via peer_push. "
            "The local human must approve before anything is sent. Use "
            "responding_to_pull=true when fulfilling an inbound skill pull request."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "peer_id": {"type": "string", "description": "Target peer id from `hermes peer list`."},
                "skill_name": {"type": "string", "description": "Local skill directory name to share."},
                "tier": {
                    "type": "string",
                    "description": "Artifact tier: reference, summary, or full.",
                    "enum": ["reference", "summary", "full"],
                },
                "message": {"type": "string", "description": "Optional note included with the offer."},
                "responding_to_pull": {
                    "type": "boolean",
                    "description": "True when replying to an inbound pull request.",
                },
                "summary_for_human": {
                    "type": "string",
                    "description": "Optional approval prompt shown to the human.",
                },
            },
            "required": ["peer_id", "skill_name"],
        },
    },
    handler=lambda args, **kw: a2a_share_skill(
        peer_id=args.get("peer_id", ""),
        skill_name=args.get("skill_name", ""),
        tier=args.get("tier", "summary"),
        message=args.get("message", ""),
        responding_to_pull=bool(args.get("responding_to_pull", False)),
        summary_for_human=args.get("summary_for_human", ""),
        clarify_callback=kw.get("clarify_callback"),
    ),
    check_fn=check_a2a_share_requirements,
)

registry.register(
    name="a2a_accept_skill",
    toolset="a2a",
    schema={
        "name": "a2a_accept_skill",
        "description": (
            "Install (or decline) a skill_share offer from the active inbound A2A "
            "request. The local human must approve before the skill is written to disk."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary_for_human": {
                    "type": "string",
                    "description": "Optional approval prompt shown to the human.",
                },
            },
        },
    },
    handler=lambda args, **kw: a2a_accept_skill(
        summary_for_human=args.get("summary_for_human", ""),
        clarify_callback=kw.get("clarify_callback"),
    ),
    check_fn=check_a2a_accept_requirements,
)

registry.register(
    name="a2a_propose_reminder",
    toolset="a2a",
    schema={
        "name": "a2a_propose_reminder",
        "description": (
            "Propose a cron reminder job for the active set_reminder request. "
            "The local human must approve before the job is created."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "schedule": {
                    "type": "string",
                    "description": "Schedule string (30m, every 2h, cron expr, or ISO timestamp).",
                },
                "message": {
                    "type": "string",
                    "description": "Reminder message delivered when the job fires.",
                },
                "deliver": {
                    "type": "string",
                    "description": "Delivery target (local, telegram, origin, etc.).",
                },
                "name": {
                    "type": "string",
                    "description": "Optional friendly cron job name.",
                },
                "summary_for_human": {
                    "type": "string",
                    "description": "Optional approval prompt shown to the human.",
                },
            },
            "required": ["schedule", "message"],
        },
    },
    handler=lambda args, **kw: a2a_propose_reminder(
        schedule=args.get("schedule", ""),
        message=args.get("message", ""),
        deliver=args.get("deliver", ""),
        name=args.get("name", ""),
        summary_for_human=args.get("summary_for_human", ""),
        clarify_callback=kw.get("clarify_callback"),
    ),
    check_fn=check_a2a_reminder_requirements,
)
