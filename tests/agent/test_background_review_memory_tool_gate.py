"""A skill-only background review must not receive the memory tool.

With memory.nudge_interval: 0 the memory review never fires, but the skill
review (skills.creation_nudge_interval) still does. It used to get the memory
tool regardless, so it kept staging personal facts into MEMORY.md.
"""

from unittest.mock import MagicMock, patch

import agent.background_review as br


def _bare_agent():
    agent = MagicMock()
    del agent._COMBINED_REVIEW_PROMPT
    del agent._MEMORY_REVIEW_PROMPT
    del agent._SKILL_REVIEW_PROMPT
    return agent


def _include_memory_tool_for(review_memory, review_skills):
    with patch.object(br, "_run_review_in_thread") as run:
        target, _prompt = br.spawn_background_review_thread(
            _bare_agent(), [], review_memory=review_memory, review_skills=review_skills
        )
        target()
    return run.call_args.kwargs["include_memory_tool"]


def test_skill_only_review_gets_no_memory_tool():
    assert _include_memory_tool_for(False, True) is False


def test_memory_review_keeps_memory_tool():
    assert _include_memory_tool_for(True, False) is True
    assert _include_memory_tool_for(True, True) is True
