from app.rag.answer_evaluation import RagEvalCase, RagEvalCategory
from app.rag.chunking import Chunk
from app.rag.eval_review import review_cases


def _chunk(chunk_id: str, content: str) -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id=f"doc-{chunk_id}",
        source="docs/test.md",
        content=content,
        metadata={},
        start=0,
        end=len(content),
    )


def _case(
    case_id: str,
    *,
    question: str,
    category: RagEvalCategory = RagEvalCategory.ANSWERABLE,
    expected_refused: bool = False,
    expected_answer_contains: tuple[str, ...] = (),
    expected_chunk_ids: tuple[str, ...] = (),
    forbidden_answer_contains: tuple[str, ...] = (),
) -> RagEvalCase:
    return RagEvalCase(
        id=case_id,
        question=question,
        category=category,
        expected_refused=expected_refused,
        expected_answer_contains=expected_answer_contains,
        expected_chunk_ids=expected_chunk_ids,
        forbidden_answer_contains=forbidden_answer_contains,
    )


def test_review_accepts_valid_case() -> None:
    corpus = [_chunk("refund-policy", "退款政策：七天内无理由退款。")]
    cases = [
        _case(
            "refund-1",
            question="退款政策是什么？",
            expected_answer_contains=("七天内", "无理由"),
            expected_chunk_ids=("refund-policy",),
        )
    ]

    report = review_cases(cases, corpus)

    assert report.error_count == 0


def test_review_flags_missing_chunk_id() -> None:
    cases = [
        _case(
            "bad-chunk",
            question="退款政策是什么？",
            expected_answer_contains=("七天内",),
            expected_chunk_ids=("missing",),
        )
    ]

    report = review_cases(cases, [])

    assert report.error_count == 1
    assert report.issues[0].code == "missing_chunk_id"


def test_review_flags_answer_term_not_in_expected_chunk() -> None:
    corpus = [_chunk("refund-policy", "退款政策：七天内无理由退款。")]
    cases = [
        _case(
            "bad-term",
            question="退款政策是什么？",
            expected_answer_contains=("三十天",),
            expected_chunk_ids=("refund-policy",),
        )
    ]

    report = review_cases(cases, corpus)

    assert any(
        issue.code == "answer_term_not_in_expected_chunks"
        for issue in report.issues
    )


def test_review_flags_category_refusal_mismatch() -> None:
    cases = [
        _case(
            "bad-category",
            question="如何申请火星退款？",
            category=RagEvalCategory.UNANSWERABLE,
            expected_refused=False,
            expected_answer_contains=("火星",),
        )
    ]

    report = review_cases(cases, [])

    assert any(
        issue.code == "category_refusal_mismatch"
        for issue in report.issues
    )


def test_review_flags_forbidden_term_in_allowed_corpus() -> None:
    corpus = [_chunk("secret", "内部特批退款流程。")]
    cases = [
        _case(
            "permission-negative",
            question="内部特批退款流程是什么？",
            category=RagEvalCategory.PERMISSION_NEGATIVE,
            expected_refused=True,
            forbidden_answer_contains=("内部特批",),
        )
    ]

    report = review_cases(cases, corpus)

    assert any(
        issue.code == "forbidden_term_in_allowed_corpus"
        for issue in report.issues
    )


def test_review_flags_duplicate_question() -> None:
    corpus = [_chunk("refund-policy", "退款政策：七天内无理由退款。")]
    cases = [
        _case(
            "dup-1",
            question="退款政策是什么？",
            expected_answer_contains=("七天内",),
            expected_chunk_ids=("refund-policy",),
        ),
        _case(
            "dup-2",
            question="退款政策是什么？",
            expected_answer_contains=("七天内",),
            expected_chunk_ids=("refund-policy",),
        ),
    ]

    report = review_cases(cases, corpus)

    assert any(issue.code == "duplicate_question" for issue in report.issues)
