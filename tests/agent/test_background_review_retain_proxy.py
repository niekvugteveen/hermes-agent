"""The memory review may file facts in the external store through its retain tool only.

The fork is built with ``skip_memory=True`` so it never pushes the harness prompt into the
user's real memory namespace. ``_RetainOnlyMemoryProxy`` re-binds the parent's manager for one
purpose — an explicit ``*_retain`` call — and must leave every other hook (sync, prefetch,
teardown) unable to reach the parent's live provider.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import agent.background_review as bg

_RETAIN = {"name": "hindsight_retain", "description": "store a fact", "parameters": {"type": "object"}}
_RECALL = {"name": "hindsight_recall", "description": "search", "parameters": {"type": "object"}}


def _parent_manager():
    mm = MagicMock()
    mm.get_all_tool_schemas.return_value = [_RETAIN, {"type": "function", "function": _RECALL}]
    mm.has_tool.side_effect = lambda name: name in {"hindsight_retain", "hindsight_recall"}
    mm.handle_tool_call.return_value = '{"ok": true}'
    return mm


class TestProxySurface:
    def test_only_retain_is_exposed(self):
        proxy = bg._RetainOnlyMemoryProxy(_parent_manager())
        assert [s["name"] for s in proxy.get_all_tool_schemas()] == ["hindsight_retain"]
        assert proxy.get_all_tool_names() == {"hindsight_retain"}
        assert proxy.has_tool("hindsight_retain") is True
        assert proxy.has_tool("hindsight_recall") is False

    def test_retain_delegates_and_recall_is_refused(self):
        mm = _parent_manager()
        proxy = bg._RetainOnlyMemoryProxy(mm)
        assert proxy.handle_tool_call("hindsight_retain", {"content": "x"}, caller_gsk="") == '{"ok": true}'
        mm.handle_tool_call.assert_called_once_with("hindsight_retain", {"content": "x"}, caller_gsk="")
        refused = json.loads(proxy.handle_tool_call("hindsight_recall", {"query": "x"}))
        assert "not available" in refused["error"]
        assert mm.handle_tool_call.call_count == 1

    @pytest.mark.parametrize("hook", [
        "sync_all", "queue_prefetch_all", "prefetch_all", "on_turn_start", "on_session_end",
        "on_session_switch", "on_pre_compress", "notify_memory_tool_write", "release_soft",
        "shutdown_all", "claim_teardown", "flush_pending", "build_system_prompt",
        "some_hook_added_later",
    ])
    def test_every_other_hook_is_a_noop(self, hook):
        mm = _parent_manager()
        proxy = bg._RetainOnlyMemoryProxy(mm)
        assert getattr(proxy, hook)("a", key="b") is None
        getattr(mm, hook).assert_not_called()

    def test_detached_fork_shape(self):
        proxy = bg._RetainOnlyMemoryProxy(_parent_manager())
        assert list(proxy.providers) == []
        assert proxy.supports_pre_compress_checkpoint(2) is True
        with pytest.raises(AttributeError):
            proxy.__deepcopy__  # dunder lookups must not become no-ops


def _fork(tools):
    return SimpleNamespace(tools=tools, valid_tool_names={t["function"]["name"] for t in tools})


class TestBindReviewRetain:
    def test_same_model_whitelists_advertised_retain(self):
        parent = SimpleNamespace(_memory_manager=_parent_manager())
        fork = _fork([{"type": "function", "function": _RETAIN}])
        before = list(fork.tools)
        assert bg._bind_review_retain(parent, fork, routed=False) == {"hindsight_retain"}
        assert isinstance(fork._memory_manager, bg._RetainOnlyMemoryProxy)
        assert fork.tools == before  # tools[] stays byte-identical for cache parity

    def test_same_model_does_not_advertise_a_missing_tool(self):
        parent = SimpleNamespace(_memory_manager=_parent_manager())
        fork = _fork([])
        assert bg._bind_review_retain(parent, fork, routed=False) == set()
        assert fork.tools == []
        assert not hasattr(fork, "_memory_manager")

    def test_routed_fork_gets_the_schema_appended(self):
        parent = SimpleNamespace(_memory_manager=_parent_manager())
        fork = _fork([])
        assert bg._bind_review_retain(parent, fork, routed=True) == {"hindsight_retain"}
        assert [t["function"]["name"] for t in fork.tools] == ["hindsight_retain"]
        assert "hindsight_retain" in fork.valid_tool_names

    def test_no_external_provider_binds_nothing(self):
        fork = _fork([])
        assert bg._bind_review_retain(SimpleNamespace(), fork, routed=True) == set()
        assert not hasattr(fork, "_memory_manager")


class _Stop(Exception):
    pass


def _whitelist_for(review_memory):
    parent = SimpleNamespace(_memory_manager=_parent_manager())
    fork = SimpleNamespace(
        tools=[{"type": "function", "function": _RETAIN}], valid_tool_names={"hindsight_retain"},
        _memory_enabled=True, _user_profile_enabled=False,
    )
    captured = {}

    def _capture(whitelist, deny_msg_fmt=None):
        captured["whitelist"] = set(whitelist)
        captured["deny"] = deny_msg_fmt
        raise _Stop

    from hermes_cli import plugins as _plugins

    with patch.object(bg, "build_cache_parity_fork", return_value=(fork, {}, False)), \
         patch.object(_plugins, "set_thread_tool_whitelist", _capture):
        with pytest.raises(_Stop):
            bg._run_review_fork(parent, [], "p", None, None, bg._ReviewForkState(), review_memory)
    return captured, fork


def test_memory_review_whitelists_retain():
    captured, fork = _whitelist_for(review_memory=True)
    assert "hindsight_retain" in captured["whitelist"]
    assert "hindsight_retain" in captured["deny"]
    assert isinstance(fork._memory_manager, bg._RetainOnlyMemoryProxy)


def test_skill_only_review_gets_no_retain():
    captured, fork = _whitelist_for(review_memory=False)
    assert "hindsight_retain" not in captured["whitelist"]
    assert not hasattr(fork, "_memory_manager")


def test_routing_rule_is_in_both_memory_prompts():
    assert "_retain" in bg._MEMORY_REVIEW_PROMPT
    assert "_retain" in bg._COMBINED_REVIEW_PROMPT
    assert "_retain" not in bg._SKILL_REVIEW_PROMPT
