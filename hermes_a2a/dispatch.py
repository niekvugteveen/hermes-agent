"""Inbound dispatch hooks from the A2A MCP server to the gateway."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

_inbound_handlers: List[Callable[[Dict[str, Any]], None]] = []
_lock = threading.Lock()


def register_inbound_handler(handler: Callable[[Dict[str, Any]], None]) -> None:
    with _lock:
        if handler not in _inbound_handlers:
            _inbound_handlers.append(handler)


def dispatch_inbound(envelope: Dict[str, Any]) -> None:
    with _lock:
        handlers = list(_inbound_handlers)
    for handler in handlers:
        try:
            handler(envelope)
        except Exception:
            logger.exception("A2A inbound handler failed")
