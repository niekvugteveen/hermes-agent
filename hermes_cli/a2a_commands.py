"""Shared handlers for ``/peer`` slash commands and ``hermes peer`` review UX."""

from __future__ import annotations

import json
from typing import List, Optional

from hermes_a2a.constants import (
    REQUEST_KNOWLEDGE,
    REQUEST_REMINDER,
    REQUEST_SKILL_SHARE,
    STATUS_AWAITING_HUMAN,
    STATUS_COMPLETED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    STATUS_PENDING,
)
from hermes_a2a.cron_proposal import build_reminder_proposal, create_reminder_job
from hermes_a2a.store import RequestStore


def _terminal_statuses() -> frozenset[str]:
    return frozenset({STATUS_COMPLETED, STATUS_DENIED, STATUS_EXPIRED})


def _summarize_record(record: dict) -> str:
    request_id = str(record.get("request_id") or "")
    short_id = request_id[:8] if request_id else "?"
    rtype = str(record.get("type") or "")
    from_peer = str(record.get("from_peer") or "")
    status = str(record.get("status") or "")
    payload = record.get("payload") or {}
    hint = ""
    if rtype == REQUEST_KNOWLEDGE:
        hint = str(payload.get("question") or "")[:60]
    elif rtype == REQUEST_SKILL_SHARE:
        hint = str(payload.get("skill_name") or "")[:60]
    elif rtype == REQUEST_REMINDER:
        hint = str(payload.get("message") or "")[:60]
    proposed = str(record.get("proposed_response") or "").strip()
    if proposed and not hint:
        hint = proposed[:60]
    suffix = f" — {hint}" if hint else ""
    return f"  {request_id}  [{status}] {rtype} from {from_peer}{suffix}"


def format_pending_list(store: Optional[RequestStore] = None) -> str:
    """List inbound A2A requests still awaiting resolution."""
    store = store or RequestStore()
    open_statuses = {STATUS_PENDING, STATUS_AWAITING_HUMAN}
    records: List[dict] = []
    for status in sorted(open_statuses):
        records.extend(store.list_by_status(status))
    if not records:
        return "No A2A requests awaiting human approval."
    lines = [f"A2A requests awaiting action ({len(records)}):"]
    for record in records:
        lines.append(_summarize_record(record))
    lines.extend(
        [
            "",
            "Approve: /peer approve <request_id> [answer]",
            "Deny:    /peer deny <request_id>",
            "Detail:  /peer status <request_id>",
        ]
    )
    return "\n".join(lines)


def format_request_status(store: RequestStore, request_id: str) -> str:
    record = store.get(request_id)
    if record is None:
        return f"Request not found: {request_id}"
    return json.dumps(record, indent=2, ensure_ascii=False)


def deny_peer_request(store: RequestStore, request_id: str) -> str:
    record = store.get(request_id)
    if record is None:
        return f"Request not found: {request_id}"
    if record.get("status") in _terminal_statuses():
        return f"Request already {record.get('status')}."
    store.complete_response(request_id, denied=True, extra={"status": "denied"})
    return f"Denied A2A request {request_id}."


def approve_peer_request(
    store: RequestStore,
    request_id: str,
    extra_args: Optional[List[str]] = None,
) -> str:
    record = store.get(request_id)
    if record is None:
        return f"Request not found: {request_id}"
    if record.get("status") in _terminal_statuses():
        return f"Request already {record.get('status')}."

    rtype = str(record.get("type") or "")
    from_peer = str(record.get("from_peer") or "")
    payload = record.get("payload") or {}
    tail = list(extra_args or [])

    if rtype == REQUEST_KNOWLEDGE:
        answer = " ".join(tail).strip() or str(record.get("proposed_response") or "").strip()
        if not answer:
            return (
                "Provide the reply text: /peer approve <request_id> <answer>"
            )
        store.complete_response(request_id, answer=answer, denied=False)
        return f"Approved knowledge reply for request {request_id}."

    if rtype == REQUEST_SKILL_SHARE:
        from hermes_a2a.artifacts import install_skill_artifact

        skill_name = str(payload.get("skill_name") or "").strip()
        artifact = payload.get("artifact") or {}
        if not skill_name or not artifact:
            return "Request has no skill artifact to install."
        install_result = install_skill_artifact(skill_name, artifact)
        if not install_result.get("success"):
            store.complete_response(
                request_id,
                denied=True,
                denial_message=str(install_result.get("error") or "install failed"),
            )
            return f"Install failed: {install_result.get('error')}"
        store.complete_response(
            request_id,
            answer=json.dumps({"status": "installed", "skill_name": skill_name}),
            denied=False,
            extra={"status": "installed", "skill_name": skill_name},
        )
        return f"Installed skill '{skill_name}' from peer request {request_id}."

    if rtype == REQUEST_REMINDER:
        schedule = str(payload.get("schedule") or "").strip()
        message = str(payload.get("message") or "").strip()
        if tail:
            message = " ".join(tail).strip() or message
        proposal = build_reminder_proposal(
            schedule=schedule,
            message=message,
            deliver=str(payload.get("delivery_hint") or payload.get("deliver") or "").strip() or None,
            from_peer=from_peer,
        )
        if not proposal.get("success"):
            return str(proposal.get("error") or "Invalid reminder proposal.")
        create_result = create_reminder_job(proposal, from_peer=from_peer)
        if not create_result.get("success"):
            store.complete_response(
                request_id,
                denied=True,
                denial_message=str(create_result.get("error") or "create failed"),
            )
            return f"Failed to create cron job: {create_result.get('error')}"
        job_id = str(create_result.get("job_id") or "")
        store.complete_response(
            request_id,
            answer=json.dumps({"status": "created", "job_id": job_id}),
            denied=False,
            extra={"status": "created", "job_id": job_id},
        )
        return f"Created reminder cron job {job_id} for request {request_id}."

    return f"Unsupported A2A request type: {rtype}"


def handle_peer_subcommand(args: List[str]) -> Optional[str]:
    """Dispatch ``/peer`` or ``hermes peer`` review subcommands."""
    store = RequestStore()

    if not args:
        return format_pending_list(store)

    sub = args[0].lower()
    if sub in {"pending", "list", "ls"}:
        return format_pending_list(store)

    if sub == "status":
        if len(args) < 2:
            return "Usage: /peer status <request_id>"
        return format_request_status(store, args[1])

    if sub in {"deny", "reject"}:
        if len(args) < 2:
            return "Usage: /peer deny <request_id>"
        return deny_peer_request(store, args[1])

    if sub in {"approve", "accept"}:
        if len(args) < 2:
            return "Usage: /peer approve <request_id> [answer]"
        return approve_peer_request(store, args[1], args[2:])

    return (
        "Unknown /peer subcommand. Use: pending, approve <id> [answer], "
        "deny <id>, status <id>."
    )
