"""Fork behavior that must survive the upstream Telegram handler/voice changes."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult
from plugins.platforms.telegram import adapter as telegram_mod
from plugins.platforms.telegram.adapter import TelegramAdapter


def test_catch_all_follows_specific_handlers_on_every_application(monkeypatch):
    monkeypatch.setattr(
        telegram_mod, "TelegramMessageHandler",
        lambda filters, callback: SimpleNamespace(callback=callback),
    )
    for handler in ("CallbackQueryHandler", "InlineQueryHandler"):
        monkeypatch.setattr(telegram_mod, handler, lambda callback: SimpleNamespace(callback=callback))
    monkeypatch.setattr(
        telegram_mod, "TypeHandler",
        lambda update_type, callback: SimpleNamespace(callback=callback),
    )
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    for app in (MagicMock(), MagicMock()):
        adapter._register_handlers(app)
        callbacks = [call.args[0].callback for call in app.add_handler.call_args_list]
        assert callbacks.index(adapter._handle_unmatched_message) > callbacks.index(adapter._handle_media_message)
        assert callbacks.index(adapter._handle_unmatched_message) > callbacks.index(adapter._handle_text_message)
        assert adapter._handle_inline_query in callbacks
        assert adapter._on_platform_update in callbacks


@pytest.mark.asyncio
async def test_voice_forbidden_delivers_document_before_cleaning_transcode(tmp_path, monkeypatch):
    source = tmp_path / "reply.wav"
    source.write_bytes(b"original audio")
    converted = tmp_path / "reply.ogg"
    converted.write_bytes(b"converted audio")
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = SimpleNamespace(send_voice=AsyncMock(side_effect=RuntimeError("Voice_messages_forbidden")))
    monkeypatch.setattr("gateway.platforms.base.transcode_to_ogg_opus", lambda path: str(converted))
    monkeypatch.setattr(telegram_mod, "_probe_voice_duration_seconds", lambda path: 2)
    metadata = {"thread_id": "42"}

    async def send_document(**kwargs):
        assert kwargs["file_path"] == str(converted)
        assert converted.read_bytes() == b"converted audio"
        assert kwargs["reply_to"] == "7"
        assert kwargs["metadata"] == metadata
        return SendResult(success=True, message_id="8")

    adapter.send_document = AsyncMock(side_effect=send_document)
    result = await adapter.send_voice("123", str(source), caption="Audio", reply_to="7", metadata=metadata, is_voice=True)
    assert result.success
    adapter._bot.send_voice.assert_awaited()
    adapter.send_document.assert_awaited_once()
    assert not converted.exists()
    assert source.exists()
