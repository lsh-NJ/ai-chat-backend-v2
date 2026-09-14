"""候选评测集机械检查（Week 16 Day 8）。

只做机器能稳定判断的检查：
- chunk id 是否存在；
- 期望答案关键词是否真的出现在期望 chunk；
- category / expected_refused 组合是否合法；
- forbidden 词是否反而出现在允许语料里；
- question 是否重复或高度相似。

不替代人工判断“问法是否自然”“负例是否语义合理”。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.rag.answer_evaluation import RagEvalCase, RagEvalCategory
from app.rag.chunking import Chunk

_REFUSAL_CATEGORIES = {
    RagEvalCategory.UNANSWERABLE,
    RagEvalCategory.PERMISSION_NEGATIVE,
}
_ANSWER_CATEGORIES = {
    RagEvalCategory.ANSWERABLE,
    RagEvalCategory.PARAPHRASE,
    RagEvalCategory.KEYWORD,
}


@dataclass(frozen=True, slots=True)
class ReviewIssue:
    """一条机械检查发现的问题。"""

    case_id: str
    code: str
    severity: str
    message: str

    def __post_init__(self) -> None:
        if self.severity not in {"error", "warning"}:
            raise ValueError("severity must be 'error' or 'warning'")


@dataclass(frozen=True, slots=True)
class ReviewReport:
    """评测集检查汇总。"""

    total_cases: int
    reviewed_cases: int
    issues: tuple[ReviewIssue, ...]

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0


def _normalize_question(text: str) -> str:
    return "".join(
        char.lower()
        for char in text
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )


def _chunk_map(corpus: Sequence[Chunk]) -> Mapping[str, Chunk]:
    return {chunk.id: chunk for chunk in corpus}


def review_cases(
    cases: Sequence[RagEvalCase],
    corpus: Sequence[Chunk],
) -> ReviewReport:
    """对候选评测集做机械检查，返回结构化报告。"""
    chunks_by_id = _chunk_map(corpus)
    issues: list[ReviewIssue] = []

    seen_questions: dict[str, str] = {}
    normalized_questions: list[tuple[str, str]] = []

    for case in cases:
        expected_chunks = [
            chunks_by_id[chunk_id]
            for chunk_id in case.expected_chunk_ids
            if chunk_id in chunks_by_id
        ]
        missing_chunk_ids = [
            chunk_id
            for chunk_id in case.expected_chunk_ids
            if chunk_id not in chunks_by_id
        ]
        for chunk_id in missing_chunk_ids:
            issues.append(
                ReviewIssue(
                    case_id=case.id,
                    code="missing_chunk_id",
                    severity="error",
                    message=f"expected chunk id not found: {chunk_id}",
                )
            )

        if case.category in _REFUSAL_CATEGORIES and not case.expected_refused:
            issues.append(
                ReviewIssue(
                    case_id=case.id,
                    code="category_refusal_mismatch",
                    severity="error",
                    message=(
                        f"{case.category.value} case must have "
                        "expected_refused=true"
                    ),
                )
            )
        if case.category in _ANSWER_CATEGORIES and case.expected_refused:
            issues.append(
                ReviewIssue(
                    case_id=case.id,
                    code="category_refusal_mismatch",
                    severity="error",
                    message=(
                        f"{case.category.value} case must have "
                        "expected_refused=false"
                    ),
                )
            )
        if case.expected_refused and case.expected_chunk_ids:
            issues.append(
                ReviewIssue(
                    case_id=case.id,
                    code="refusal_has_expected_chunks",
                    severity="warning",
                    message="refused case still declares expected_chunk_ids",
                )
            )

        if not case.expected_refused:
            if not case.expected_chunk_ids:
                issues.append(
                    ReviewIssue(
                        case_id=case.id,
                        code="answerable_without_chunks",
                        severity="warning",
                        message="answerable case has no expected_chunk_ids",
                    )
                )
            for term in case.expected_answer_contains:
                if expected_chunks and not any(
                    term in chunk.content for chunk in expected_chunks
                ):
                    issues.append(
                        ReviewIssue(
                            case_id=case.id,
                            code="answer_term_not_in_expected_chunks",
                            severity="error",
                            message=(
                                f"expected answer term not found in expected "
                                f"chunks: {term!r}"
                            ),
                        )
                    )

        if case.category is RagEvalCategory.PERMISSION_NEGATIVE:
            for term in case.forbidden_answer_contains:
                if any(term in chunk.content for chunk in corpus):
                    issues.append(
                        ReviewIssue(
                            case_id=case.id,
                            code="forbidden_term_in_allowed_corpus",
                            severity="warning",
                            message=(
                                "forbidden term also appears in allowed "
                                f"corpus: {term!r}"
                            ),
                        )
                    )
        for term in case.forbidden_answer_contains:
            if term in case.expected_answer_contains:
                issues.append(
                    ReviewIssue(
                        case_id=case.id,
                        code="forbidden_conflicts_with_expected",
                        severity="error",
                        message=(
                            "forbidden term is also an expected answer term: "
                            f"{term!r}"
                        ),
                    )
                )

        normalized = _normalize_question(case.question)
        if normalized in seen_questions:
            issues.append(
                ReviewIssue(
                    case_id=case.id,
                    code="duplicate_question",
                    severity="error",
                    message=(
                        "duplicate normalized question with "
                        f"{seen_questions[normalized]}"
                    ),
                )
            )
        else:
            seen_questions[normalized] = case.id
        normalized_questions.append((case.id, normalized))

    for index, (case_id, question) in enumerate(normalized_questions):
        for other_id, other_question in normalized_questions[index + 1 :]:
            if question == other_question:
                continue
            ratio = SequenceMatcher(None, question, other_question).ratio()
            if ratio >= 0.92:
                issues.append(
                    ReviewIssue(
                        case_id=case_id,
                        code="near_duplicate_question",
                        severity="warning",
                        message=(
                            f"question is {ratio:.0%} similar to {other_id}"
                        ),
                    )
                )

    return ReviewReport(
        total_cases=len(cases),
        reviewed_cases=sum(1 for case in cases if case.reviewed),
        issues=tuple(issues),
    )
