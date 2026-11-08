"""Updates no handler claims must leave a trace instead of vanishing.

python-telegram-bot models a fixed Bot API version; a newer message type (a Bot API 10.1
rich message) arrives with ``text`` unset and its payload in ``api_kwargs``, matches no
filter, and used to be discarded without a single log line.
"""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


def _adapter():
    return TelegramAdapter(PlatformConfig(enabled=True, token="***"))


def test_catch_all_is_registered_after_every_message_handler():
    adapter = _adapter()
    callbacks = []

    def _record(_flt, callback):
        callbacks.append(callback)
        return MagicMock()

    with patch("plugins.platforms.telegram.adapter.TelegramMessageHandler", _record):
        adapter._register_handlers(MagicMock())
    idx = callbacks.index(adapter._handle_unmatched_message)
    assert idx == len(callbacks) - 1
    for core in (adapter._handle_text_message, adapter._handle_command,
                 adapter._handle_location_message, adapter._handle_media_message):
        assert callbacks.index(core) < idx


def _update(msg):
    return SimpleNamespace(update_id=42, effective_message=msg, message=msg)


@pytest.mark.asyncio
async def test_unmodelled_fields_are_logged_at_warning(caplog):
    msg = SimpleNamespace(chat=SimpleNamespace(id=7), api_kwargs={"rich_message": {"blocks": []}})
    with caplog.at_level(logging.INFO):
        await _adapter()._handle_unmatched_message(_update(msg), None)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and "rich_message" in warnings[0].getMessage()
    assert "update_id=42" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_service_message_is_logged_at_info(caplog):
    msg = SimpleNamespace(chat=SimpleNamespace(id=7), api_kwargs={})
    with caplog.at_level(logging.INFO):
        await _adapter()._handle_unmatched_message(_update(msg), None)
    assert any(r.levelno == logging.INFO and "Ignoring unhandled" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_update_without_message_is_logged(caplog):
    with caplog.at_level(logging.INFO):
        await _adapter()._handle_unmatched_message(_update(None), None)
    assert any("no message payload" in r.getMessage() for r in caplog.records)
