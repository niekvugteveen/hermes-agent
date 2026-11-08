"""A rejected voice send must still deliver the audio, as a document.

``Voice_messages_forbidden`` (the recipient's "who can send me voice messages" privacy
setting) used to drop straight to the base adapter's text-only notice, losing the audio.
"""
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult
from plugins.platforms.telegram import adapter as telegram_mod
from plugins.platforms.telegram.adapter import TelegramAdapter


def _make_adapter() -> TelegramAdapter:
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***"))
    adapter._bot = MagicMock()
    adapter._bot.send_voice = AsyncMock(side_effect=Exception("Forbidden: Voice_messages_forbidden"))
    return adapter


@pytest.mark.asyncio
async def test_forbidden_voice_is_delivered_as_document(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram_mod, "_probe_voice_duration_seconds", lambda _p: 3)
    audio = tmp_path / "reply.ogg"
    audio.write_bytes(b"\x00" * 16)
    adapter = _make_adapter()
    adapter.send_document = AsyncMock(return_value=SendResult(success=True, message_id="9"))

    result = await adapter.send_voice("123", str(audio), caption="hi")

    assert result.success is True and result.message_id == "9"
    kwargs = adapter.send_document.await_args.kwargs
    assert kwargs["file_path"] == str(audio)
    assert kwargs["caption"] == "hi"


@pytest.mark.asyncio
async def test_transcoded_file_survives_until_the_document_upload(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram_mod, "_probe_voice_duration_seconds", lambda _p: 3)
    source = tmp_path / "reply.mp3"
    source.write_bytes(b"\x00" * 16)
    transcoded = tmp_path / "reply-transcoded.ogg"

    def _fake_transcode(_path):
        transcoded.write_bytes(b"\x00" * 16)
        return str(transcoded)

    import gateway.platforms.base as base_mod
    monkeypatch.setattr(base_mod, "transcode_to_ogg_opus", _fake_transcode)
    adapter = _make_adapter()
    seen = {}

    async def _send_document(**kwargs):
        seen["path"] = kwargs["file_path"]
        seen["existed"] = os.path.exists(kwargs["file_path"])
        return SendResult(success=True, message_id="9")

    adapter.send_document = _send_document

    result = await adapter.send_voice("123", str(source), is_voice=True)

    assert result.success is True
    assert seen == {"path": str(transcoded), "existed": True}
    assert not transcoded.exists()  # upstream's finally still cleans the transcode up


@pytest.mark.asyncio
async def test_document_failure_falls_back_to_base_adapter(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram_mod, "_probe_voice_duration_seconds", lambda _p: 3)
    audio = tmp_path / "reply.ogg"
    audio.write_bytes(b"\x00" * 16)
    adapter = _make_adapter()
    adapter.send_document = AsyncMock(side_effect=RuntimeError("boom"))
    base = AsyncMock(return_value=SendResult(success=False, error="notice"))
    monkeypatch.setattr(telegram_mod.BasePlatformAdapter, "send_voice", base)

    result = await adapter.send_voice("123", str(audio))

    assert result.error == "notice"
    base.assert_awaited_once()
