import pytest

from app.core.exceptions import RagCitationError
from app.rag.answer import RagCitation
from app.rag.citation import extract_citation_labels, validate_answer_citations


def _citation(label: str, chunk_id: str) -> RagCitation:
    return RagCitation(
        label=label,
        chunk_id=chunk_id,
        document_id="doc-1",
        source="docs/faq.md",
        start=0,
        end=4,
        score=1.0,
    )


def test_extract_citation_labels_keeps_first_appearance_order() -> None:
    labels = extract_citation_labels("先看 [2]，再看 [1]，最后重复 [2]。")

    assert labels == ("2", "1")


def test_validate_answer_citations_returns_used_citations_only() -> None:
    first = _citation("1", "chunk-a")
    second = _citation("2", "chunk-b")

    used = validate_answer_citations(
        "退款需要申请 [2]，审核后到账 [1]。",
        (first, second),
    )

    assert used == (second, first)


def test_validate_answer_citations_returns_empty_when_no_labels() -> None:
    used = validate_answer_citations("资料中没有足够信息。", ())

    assert used == ()


def test_validate_answer_citations_rejects_unknown_label() -> None:
    with pytest.raises(RagCitationError, match=r"\[9\]"):
        validate_answer_citations(
            "依据 [9] 可以回答。",
            (_citation("1", "chunk-a"),),
        )
