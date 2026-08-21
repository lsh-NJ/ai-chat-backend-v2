import pytest

from app.llm.contracts import LLMMessage, LLMRole
from app.llm.tokenization import ContextBudget


class FixedCounter:
    def __init__(self, token_count: int) -> None:
        self.token_count = token_count

    def count_messages(self, messages) -> int:
        return self.token_count


MESSAGES = [LLMMessage(role=LLMRole.USER, content="内容不参与固定计数")]


def test_budget_derives_input_limit_from_reserved_capacity() -> None:
    budget = ContextBudget(
        context_window=100,
        output_reserve=20,
        safety_margin=5,
    )

    assert budget.max_input_tokens == 75


@pytest.mark.parametrize(
    ("used_tokens", "remaining_tokens", "fits"),
    [
        (74, 1, True),
        (75, 0, True),
        (76, -1, False),
    ],
)
def test_usage_reports_exact_budget_boundary(
    used_tokens: int,
    remaining_tokens: int,
    fits: bool,
) -> None:
    budget = ContextBudget(context_window=100, output_reserve=25)

    usage = budget.measure(FixedCounter(used_tokens), MESSAGES)

    assert usage.used_tokens == used_tokens
    assert usage.max_input_tokens == 75
    assert usage.remaining_tokens == remaining_tokens
    assert usage.fits is fits


@pytest.mark.parametrize(
    "kwargs",
    [
        {"context_window": 0, "output_reserve": 0},
        {"context_window": 100, "output_reserve": -1},
        {"context_window": 100, "output_reserve": 80, "safety_margin": 20},
        {"context_window": 100, "output_reserve": 100},
    ],
)
def test_budget_rejects_invalid_configuration(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        ContextBudget(**kwargs)


@pytest.mark.parametrize("bad_count", [-1, True, 1.5])
def test_budget_rejects_invalid_counter_results(bad_count) -> None:
    budget = ContextBudget(context_window=100, output_reserve=20)

    with pytest.raises((TypeError, ValueError)):
        budget.measure(FixedCounter(bad_count), MESSAGES)
