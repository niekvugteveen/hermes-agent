"""Persistent store for in-flight A2A requests."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_a2a.constants import (
    DEFAULT_EXPIRY_DAYS,
    SCHEMA_VERSION,
    STATUS_AWAITING_HUMAN,
    STATUS_COMPLETED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    STATUS_PENDING,
)
from hermes_constants import get_hermes_home


def _requests_dir() -> Path:
    path = get_hermes_home() / "peers" / "requests"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RequestStore:
    """File-backed request state under ``~/.hermes/peers/requests/``."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def _path(self, request_id: str) -> Path:
        safe = request_id.replace("/", "_")
        return _requests_dir() / f"{safe}.json"

    def create_request(
        self,
        *,
        request_type: str,
        from_peer: str,
        to_peer: str,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None,
        expiry_days: int = DEFAULT_EXPIRY_DAYS,
    ) -> Dict[str, Any]:
        request_id = uuid.uuid4().hex
        now = _utcnow()
        record = {
            "schema_version": SCHEMA_VERSION,
            "request_id": request_id,
            "correlation_id": correlation_id or request_id,
            "type": request_type,
            "from_peer": from_peer,
            "to_peer": to_peer,
            "payload": payload,
            "status": STATUS_PENDING,
            "created_at": _iso(now),
            "expires_at": _iso(now + timedelta(days=expiry_days)),
            "proposed_response": None,
            "response": None,
            "denial_message": None,
        }
        with self._lock:
            self._path(request_id).write_text(
                json.dumps(record, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return record

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            path = self._path(request_id)
            if not path.exists():
                return None
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        if not isinstance(record, dict):
            return None
        self._maybe_expire(record)
        return record

    def update(self, request_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self.get(request_id)
            if record is None:
                return None
            record.update(fields)
            self._path(request_id).write_text(
                json.dumps(record, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return record

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        with self._lock:
            for path in sorted(_requests_dir().glob("*.json")):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(record, dict):
                    continue
                self._maybe_expire(record)
                if record.get("status") == status:
                    out.append(record)
        return out

    def _maybe_expire(self, record: Dict[str, Any]) -> None:
        if record.get("status") in {STATUS_COMPLETED, STATUS_DENIED, STATUS_EXPIRED}:
            return
        expires_at = str(record.get("expires_at") or "")
        if not expires_at:
            return
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return
        if _utcnow() >= exp:
            record["status"] = STATUS_EXPIRED
            request_id = str(record.get("request_id") or "")
            if request_id:
                self._path(request_id).write_text(
                    json.dumps(record, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

    def complete_response(
        self,
        request_id: str,
        *,
        answer: Optional[str] = None,
        denied: bool = False,
        denial_message: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        fields: Dict[str, Any] = {
            "status": STATUS_DENIED if denied else STATUS_COMPLETED,
            "response": {
                "status": "denied" if denied else "answered",
                "answer": answer,
                "denial_message": denial_message,
            },
        }
        if extra:
            fields["response"].update(extra)
        if denied:
            fields["denial_message"] = denial_message
        else:
            fields["proposed_response"] = answer
        return self.update(request_id, **fields)

    def mark_awaiting_human(self, request_id: str, proposed: str) -> Optional[Dict[str, Any]]:
        return self.update(
            request_id,
            status=STATUS_AWAITING_HUMAN,
            proposed_response=proposed,
        )
