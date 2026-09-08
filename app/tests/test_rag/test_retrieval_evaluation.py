import math

import pytest

from app.rag.chunking import Chunk
from app.rag.evaluation import (
    EvalQuery,
    evaluate_retriever,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank_at_k,
)
from app.rag.retrieval import ChunkHit, Retriever


def make_chunk(chunk_id: str) -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id=f"doc-{chunk_id}",
        source=f"docs/{chunk_id}.md",
        content=f"content of {chunk_id}",
        metadata={},
        start=0,
        end=len(f"content of {chunk_id}"),
    )


def test_recall_at_k_counts_hits_over_total_relevant() -> None:
    assert recall_at_k(["a", "b", "c"], {"a"}, k=1) == 1.0
    assert recall_at_k(["a", "b", "c"], {"a", "d"}, k=3) == 0.5
    assert recall_at_k(["x", "a", "b"], {"a"}, k=1) == 0.0


def test_recall_at_k_does_not_double_count_duplicates() -> None:
    assert recall_at_k(["a", "a", "b"], {"a"}, k=2) == 1.0


def test_reciprocal_rank_at_k_known_values() -> None:
    assert reciprocal_rank_at_k(["a", "b"], {"a"}, k=2) == 1.0
    assert reciprocal_rank_at_k(["x", "a", "b"], {"a"}, k=3) == 0.5
    assert reciprocal_rank_at_k(["x", "y"], {"a"}, k=2) == 0.0


def test_ndcg_at_k_known_values() -> None:
    assert ndcg_at_k(["a", "b"], {"a"}, k=2) == pytest.approx(1.0)
    assert ndcg_at_k(["b", "a"], {"a"}, k=2) == pytest.approx(
        1.0 / math.log2(3)
    )
    assert ndcg_at_k(["x", "a"], {"a", "b"}, k=2) == pytest.approx(
        (1.0 / math.log2(3)) / (1.0 + 1.0 / math.log2(3))
    )


def test_metric_functions_fail_closed() -> None:
    with pytest.raises(ValueError, match="relevant_ids"):
        recall_at_k([], [], k=1)
    with pytest.raises((TypeError, ValueError), match="k"):
        recall_at_k([], {"a"}, k=0)
    with pytest.raises(ValueError, match="relevant_ids"):
        reciprocal_rank_at_k([], [], k=1)
    with pytest.raises(ValueError, match="relevant_ids"):
        ndcg_at_k([], [], k=1)


def test_eval_query_validates_state() -> None:
    EvalQuery(query="退款流程", relevant_chunk_ids=("c1",))

    with pytest.raises((TypeError, ValueError), match="query"):
        EvalQuery(query="", relevant_chunk_ids=("c1",))
    with pytest.raises(ValueError, match="relevant_chunk_ids"):
        EvalQuery(query="q", relevant_chunk_ids=())
    with pytest.raises(TypeError, match="tuple"):
        EvalQuery(query="q", relevant_chunk_ids=["c1"])  # type: ignore[arg-type]


class StubRetriever:
    def __init__(self, results_by_query: dict[str, list[str]]) -> None:
        self._results_by_query = results_by_query

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        metadata_filter: dict | None = None,
    ) -> list[ChunkHit]:
        ids = self._results_by_query.get(query, [])[:top_k]
        return [
            ChunkHit(chunk=make_chunk(chunk_id), score=1.0, rank=rank)
            for rank, chunk_id in enumerate(ids, start=1)
        ]


def test_evaluate_retriever_returns_average_report() -> None:
    stub = StubRetriever(
        {
            "q1": ["a", "b", "c"],
            "q2": ["x", "b", "a"],
        }
    )
    golden = [
        EvalQuery(query="q1", relevant_chunk_ids=("a",)),
        EvalQuery(query="q2", relevant_chunk_ids=("b",)),
    ]

    report = evaluate_retriever(stub, golden, k=2)

    assert report.k == 2
    assert report.query_count == 2
    assert report.mean_recall_at_k == pytest.approx(1.0)
    assert report.mean_reciprocal_rank == pytest.approx(0.75)
    assert report.mean_ndcg_at_k == pytest.approx(
        (1.0 + 1.0 / math.log2(3)) / 2
    )


def test_evaluate_retriever_is_deterministic() -> None:
    stub = StubRetriever({"q1": ["a", "b"]})
    golden = [EvalQuery(query="q1", relevant_chunk_ids=("a",))]

    first = evaluate_retriever(stub, golden, k=2)
    second = evaluate_retriever(stub, golden, k=2)

    assert first == second


def test_evaluate_retriever_rejects_invalid_arguments() -> None:
    stub = StubRetriever({"q1": ["a", "b"]})
    golden = [EvalQuery(query="q1", relevant_chunk_ids=("a",))]

    with pytest.raises(ValueError, match="queries"):
        evaluate_retriever(stub, [], k=2)
    with pytest.raises((TypeError, ValueError), match="k"):
        evaluate_retriever(stub, golden, k=0)


def test_evaluate_retriever_accepts_any_retriever_protocol_object() -> None:
    stub = StubRetriever({"q1": ["a"]})
    golden = [EvalQuery(query="q1", relevant_chunk_ids=("a",))]

    assert isinstance(stub, Retriever)
    assert evaluate_retriever(stub, golden, k=1).mean_recall_at_k == 1.0
