"""Inbound Bot API 10.1 rich messages are answered by flattening them to markdown.

PTB has no model for ``rich_message`` yet, so it sits in ``api_kwargs`` with ``text`` unset and
no handler matches. The catch-all flattens it and routes it through ``_handle_text_message``.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram import adapter as telegram_mod
from plugins.platforms.telegram.adapter import TelegramAdapter

_TABLE = {
    "type": "table",
    "cells": [
        [{"text": "Item", "is_header": True}, {"text": "kcal", "is_header": True}],
        [{"text": "Bread | rye"}, {"text": "250"}],
        [{"text": "Egg"}],
    ],
}


def test_table_renders_as_markdown_with_escaped_pipes_and_padding():
    out = TelegramAdapter._flatten_rich_message({"blocks": [_TABLE, {"type": "paragraph", "text": "Totals?"}]})
    assert out == (
        "| Item | kcal |\n| --- | --- |\n| Bread \\| rye | 250 |\n| Egg |  |\n\nTotals?"
    )


def test_inline_nodes_lists_code_and_quotes():
    out = TelegramAdapter._flatten_rich_message({"blocks": [
        {"type": "paragraph", "text": [{"text": "hello "}, {"children": [{"text": "world"}]}]},
        {"type": "list", "items": [{"label": "1.", "blocks": [{"type": "paragraph", "text": "one"}]}]},
        {"type": "code", "text": "x = 1"},
        {"type": "blockquote", "text": "a\nb"},
    ]})
    assert out == "hello world\n\n1. one\n\n```\nx = 1\n```\n\n> a\n> b"


def test_container_blocks_recurse_and_unknown_blocks_leave_a_marker():
    out = TelegramAdapter._flatten_rich_message({"blocks": [
        {"type": "details", "blocks": [{"type": "paragraph", "text": "inside"}]},
        {"type": "math"},
    ]})
    assert out == "inside\n\n[unsupported rich block: math]"


@pytest.mark.parametrize("rich", [None, {}, {"blocks": "x"}, {"blocks": []}])
def test_nothing_to_flatten(rich):
    assert TelegramAdapter._flatten_rich_message(rich) is None


@pytest.mark.asyncio
async def test_rich_message_is_routed_through_the_text_handler():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***"))
    promoted = object()
    adapter._promote_rich_to_text = MagicMock(return_value=promoted)
    adapter._handle_text_message = AsyncMock()
    msg = SimpleNamespace(chat=SimpleNamespace(id=7), api_kwargs={"rich_message": {"blocks": [_TABLE]}})
    update = SimpleNamespace(update_id=1, effective_message=msg, message=msg)

    await adapter._handle_unmatched_message(update, "ctx")

    text = adapter._promote_rich_to_text.call_args.args[2]
    assert text.startswith("| Item | kcal |")
    adapter._handle_text_message.assert_awaited_once_with(promoted, "ctx")


def test_promote_rebuilds_a_text_message(monkeypatch):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***"))
    captured = {}

    class _Message:
        @staticmethod
        def de_json(data, bot):
            captured["data"] = data
            return "rebuilt"

    monkeypatch.setattr(telegram_mod, "Message", _Message)
    monkeypatch.setattr(telegram_mod, "Update", lambda **kw: kw)
    msg = MagicMock()
    msg.to_dict.return_value = {"message_id": 5, "chat": {"id": 7}, "rich_message": {"blocks": []}}

    out = adapter._promote_rich_to_text(SimpleNamespace(update_id=3), msg, "flat")

    assert out == {"update_id": 3, "message": "rebuilt"}
    assert captured["data"] == {"message_id": 5, "chat": {"id": 7}, "text": "flat"}
