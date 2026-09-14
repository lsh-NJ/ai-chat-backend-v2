from pathlib import Path

from app.rag.answer_evaluation import (
    RagEvalCase,
    RagEvalCategory,
    load_rag_eval_cases_jsonl,
)
from experiments.review_eval_cases_interactive import (
    case_to_record,
    save_cases,
)


def _case(reviewed: bool) -> RagEvalCase:
    return RagEvalCase(
        id="refund-1",
        question="退款政策是什么？",
        category=RagEvalCategory.ANSWERABLE,
        expected_refused=False,
        expected_answer_contains=("七天内", "无理由"),
        expected_chunk_ids=("refund-policy",),
        forbidden_answer_contains=(),
        reviewed=reviewed,
    )


def test_case_to_record_roundtrip_fields() -> None:
    record = case_to_record(_case(reviewed=True))

    assert record == {
        "id": "refund-1",
        "question": "退款政策是什么？",
        "category": "answerable",
        "expected_refused": False,
        "expected_answer_contains": ["七天内", "无理由"],
        "expected_chunk_ids": ["refund-policy"],
        "forbidden_answer_contains": [],
        "reviewed": True,
    }


def test_save_cases_preserves_reviewed_flag(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    original = [_case(reviewed=True), _case(reviewed=False)]
    original[1] = RagEvalCase(
        id="refund-2",
        question="退款流程是什么？",
        category=RagEvalCategory.ANSWERABLE,
        expected_refused=False,
        expected_answer_contains=("提交申请",),
        expected_chunk_ids=("refund-steps",),
        reviewed=False,
    )

    save_cases(path, original)
    loaded = load_rag_eval_cases_jsonl(str(path))

    assert [case.reviewed for case in loaded] == [True, False]
    assert loaded[0].expected_chunk_ids == ("refund-policy",)
