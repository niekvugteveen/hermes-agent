"""Cron job proposal helpers for A2A set_reminder requests."""

from __future__ import annotations

from typing import Any, Dict, Optional

from cron.jobs import create_job, parse_schedule


def parse_reminder_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an inbound set_reminder payload."""
    if not isinstance(payload, dict):
        return {"success": False, "error": "payload must be a JSON object"}

    message = str(payload.get("message") or "").strip()
    schedule = str(payload.get("schedule") or "").strip()
    if not message:
        return {"success": False, "error": "message is required"}
    if not schedule:
        return {"success": False, "error": "schedule is required"}

    recurrence = payload.get("recurrence")
    if recurrence and str(recurrence).strip():
        rec = str(recurrence).strip()
        if not schedule.lower().startswith("every "):
            schedule = f"every {rec}" if rec else schedule

    delivery_hint = str(payload.get("delivery_hint") or payload.get("deliver") or "").strip()
    timezone = str(payload.get("timezone") or "").strip()
    name = str(payload.get("name") or "").strip()

    return {
        "success": True,
        "message": message,
        "schedule": schedule,
        "delivery_hint": delivery_hint or None,
        "timezone": timezone or None,
        "name": name or None,
    }


def build_reminder_proposal(
    *,
    schedule: str,
    message: str,
    deliver: Optional[str] = None,
    name: Optional[str] = None,
    from_peer: str = "",
) -> Dict[str, Any]:
    """Validate schedule and build a create_job-ready proposal dict."""
    schedule = (schedule or "").strip()
    message = (message or "").strip()
    if not schedule:
        return {"success": False, "error": "schedule is required"}
    if not message:
        return {"success": False, "error": "message is required"}

    try:
        parsed = parse_schedule(schedule)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    deliver_value = (deliver or "").strip() or "local"
    job_name = (name or "").strip()
    if not job_name and from_peer:
        job_name = f"Reminder from {from_peer}"[:50]

    return {
        "success": True,
        "schedule": schedule,
        "schedule_display": parsed.get("display", schedule),
        "schedule_kind": parsed.get("kind"),
        "message": message,
        "deliver": deliver_value,
        "name": job_name or None,
        "parsed_schedule": parsed,
    }


def format_reminder_preview(
    proposal: Dict[str, Any],
    *,
    from_peer: str = "",
) -> str:
    """Human-readable preview for clarify approval."""
    lines = []
    if from_peer:
        lines.append(f"Peer '{from_peer}' wants to set a reminder on this Hermes.")
    else:
        lines.append("Create a reminder cron job?")
    lines.append(f"Schedule: {proposal.get('schedule_display') or proposal.get('schedule')}")
    lines.append(f"Message: {proposal.get('message')}")
    deliver = proposal.get("deliver") or "local"
    lines.append(f"Delivery: {deliver}")
    if proposal.get("name"):
        lines.append(f"Job name: {proposal['name']}")
    return "\n".join(lines)


def create_reminder_job(
    proposal: Dict[str, Any],
    *,
    from_peer: str = "",
) -> Dict[str, Any]:
    """Create the cron job from an approved proposal."""
    if not proposal.get("success", True):
        return proposal

    try:
        job = create_job(
            prompt=str(proposal.get("message") or ""),
            schedule=str(proposal.get("schedule") or ""),
            name=proposal.get("name"),
            deliver=proposal.get("deliver") or "local",
            origin={"platform": "a2a", "peer": from_peer} if from_peer else None,
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        return {"success": False, "error": f"Failed to create cron job: {exc}"}

    return {
        "success": True,
        "job_id": job["id"],
        "name": job.get("name"),
        "schedule": job.get("schedule_display"),
        "deliver": job.get("deliver"),
        "next_run_at": job.get("next_run_at"),
        "job": job,
    }
