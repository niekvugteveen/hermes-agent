"""Shared constants for the Hermes A2A protocol."""

from __future__ import annotations

SCHEMA_VERSION = 1

REQUEST_KNOWLEDGE = "knowledge_request"
REQUEST_SKILL_SHARE = "skill_share"
REQUEST_REMINDER = "set_reminder"
REQUEST_ACK = "ack"

SUPPORTED_INBOUND_TYPES = frozenset({
    REQUEST_KNOWLEDGE,
    REQUEST_SKILL_SHARE,
    REQUEST_REMINDER,
})

STATUS_PENDING = "pending"
STATUS_AWAITING_HUMAN = "awaiting_human"
STATUS_COMPLETED = "completed"
STATUS_DENIED = "denied"
STATUS_EXPIRED = "expired"
STATUS_UNREACHABLE = "unreachable"

DEFAULT_EXPIRY_DAYS = 7

APPROVE_ONCE = "send once"
APPROVE_ALWAYS = "always from this peer"
APPROVE_DENY = "deny"
