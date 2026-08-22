from pathlib import Path

import pytest

from app.core.exceptions import LLMConfigurationError, LLMInputTooLongError
from app.llm.context import ContextSelector
from app.llm.contracts import LLMMessage, LLMRole
from app.llm.deepseek_v4_tokenizer import (
    DEEPSEEK_V4_MODEL,
    DeepSeekV4TokenCounter,
    serialize_deepseek_v4_chat,
)
from app.llm.tokenization import ContextBudget
from app.tests.fakes import ContentLengthTokenCounter


def _message(role: LLMRole, content: str) -> LLMMessage:
    return LLMMessage(role=role, content=content)


SYSTEM = _message(LLMRole.SYSTEM, "s")
CURRENT = _message(LLMRole.USER, "5")
HISTORY = (
    _message(LLMRole.USER, "1"),
    _message(LLMRole.ASSISTANT, "2"),
    _message(LLMRole.USER, "3"),
    _message(LLMRole.ASSISTANT, "4"),
)


def _selector(max_input_tokens: int) -> ContextSelector:
    return ContextSelector(
        ContentLengthTokenCounter(),
        ContextBudget(
            context_window=max_input_tokens + 1,
            output_reserve=1,
        ),
    )


def test_selects_recent_complete_turn_and_restores_chronological_order() -> None:
    selection = _selector(4).select(
        system=SYSTEM,
        history=HISTORY,
        current=CURRENT,
    )

    assert [message.content for message in selection.messages] == ["s", "3", "4", "5"]
    assert selection.selected_turns == 1
    assert selection.dropped_turns == 1
    assert selection.usage.used_tokens == 4


def test_keeps_all_turns_when_they_fit_exactly() -> None:
    selection = _selector(6).select(
        system=SYSTEM,
        history=HISTORY,
        current=CURRENT,
    )

    assert selection.messages == (SYSTEM, *HISTORY, CURRENT)
    assert selection.usage.remaining_tokens == 0


def test_does_not_skip_non_fitting_latest_turn_to_take_an_older_one() -> None:
    history = (
        _message(LLMRole.USER, "1"),
        _message(LLMRole.ASSISTANT, "2"),
        _message(LLMRole.USER, "too-long"),
        _message(LLMRole.ASSISTANT, "reply"),
    )

    selection = _selector(4).select(
        system=SYSTEM,
        history=history,
        current=CURRENT,
    )

    assert selection.messages == (SYSTEM, CURRENT)
    assert selection.selected_turns == 0
    assert selection.dropped_turns == 2


def test_rejects_when_required_messages_exceed_budget() -> None:
    with pytest.raises(LLMInputTooLongError):
        _selector(1).select(
            system=SYSTEM,
            history=(),
            current=CURRENT,
        )


def test_ignores_incomplete_past_user_turn() -> None:
    selection = _selector(10).select(
        system=SYSTEM,
        history=(*HISTORY, _message(LLMRole.USER, "unfinished")),
        current=CURRENT,
    )

    assert _message(LLMRole.USER, "unfinished") not in selection.messages


def test_rejects_orphan_assistant_history() -> None:
    with pytest.raises(ValueError, match="no preceding user"):
        _selector(10).select(
            system=SYSTEM,
            history=(_message(LLMRole.ASSISTANT, "orphan"),),
            current=CURRENT,
        )


def test_v4_serializer_matches_non_thinking_contract_subset() -> None:
    prompt = serialize_deepseek_v4_chat((SYSTEM, *HISTORY[:2], CURRENT))

    assert prompt == (
        "<｜begin▁of▁sentence｜>s"
        "<｜User｜>1<｜Assistant｜></think>"
        "2<｜end▁of▁sentence｜>"
        "<｜User｜>5<｜Assistant｜></think>"
    )


def test_pinned_v4_counter_loads_checked_resource() -> None:
    counter = DeepSeekV4TokenCounter.from_resource(model=DEEPSEEK_V4_MODEL)

    assert counter.count_messages((SYSTEM, CURRENT)) > 0


def test_v4_counter_rejects_model_or_resource_mismatch(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigurationError, match="只与"):
        DeepSeekV4TokenCounter.from_resource(model="another-model")

    corrupt_resource = tmp_path / "tokenizer.json"
    corrupt_resource.write_text("{}", encoding="utf-8")
    with pytest.raises(LLMConfigurationError, match="校验失败"):
        DeepSeekV4TokenCounter.from_resource(
            model=DEEPSEEK_V4_MODEL,
            path=corrupt_resource,
        )
