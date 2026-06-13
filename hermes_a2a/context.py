"""Per-task context for an active A2A request handler."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

_A2A_REQUEST_ID: ContextVar[str] = ContextVar("HERMES_A2A_REQUEST_ID", default="")
_A2A_FROM_PEER: ContextVar[str] = ContextVar("HERMES_A2A_FROM_PEER", default="")
_A2A_REQUEST_TYPE: ContextVar[str] = ContextVar("HERMES_A2A_REQUEST_TYPE", default="")
_A2A_SKIP_INBOUND: ContextVar[bool] = ContextVar("HERMES_A2A_SKIP_INBOUND", default=False)


def set_a2a_context(
    *,
    request_id: str,
    from_peer: str,
    request_type: str,
    skip_inbound_approval: bool = False,
) -> tuple[Token, Token, Token, Token]:
    """Bind A2A identifiers for the current task/thread."""
    return (
        _A2A_REQUEST_ID.set(request_id or ""),
        _A2A_FROM_PEER.set(from_peer or ""),
        _A2A_REQUEST_TYPE.set(request_type or ""),
        _A2A_SKIP_INBOUND.set(bool(skip_inbound_approval)),
    )


def reset_a2a_context(tokens: tuple[Token, Token, Token, Token]) -> None:
    """Restore context after an A2A handler finishes."""
    _A2A_REQUEST_ID.reset(tokens[0])
    _A2A_FROM_PEER.reset(tokens[1])
    _A2A_REQUEST_TYPE.reset(tokens[2])
    _A2A_SKIP_INBOUND.reset(tokens[3])


def get_a2a_request_id() -> str:
    return _A2A_REQUEST_ID.get()


def get_a2a_from_peer() -> str:
    return _A2A_FROM_PEER.get()


def get_a2a_request_type() -> str:
    return _A2A_REQUEST_TYPE.get()


def skip_inbound_approval() -> bool:
    return _A2A_SKIP_INBOUND.get()


def a2a_context_active() -> bool:
    return bool(get_a2a_request_id())
