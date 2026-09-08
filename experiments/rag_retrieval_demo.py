"""Week 14 Day 5 实验：在同一语料 + golden set 上对比 BM25 与 dense。

运行方式（在项目根目录）：

    .venv/bin/python -m experiments.rag_retrieval_demo

输出固定语料、golden queries、每个 retriever 的 top-k，以及
Recall@k / MRR / nDCG@k 对比表。语料/golden/k 不变时结果应可复现。
"""

from __future__ import annotations

from app.rag.bm25 import InMemoryBM25Retriever
from app.rag.chunking import Chunk
from app.rag.dense import InMemoryDenseRetriever, hash_embed
from app.rag.evaluation import EvalQuery, evaluate_retriever
from app.rag.retrieval import Retriever

CORPUS = [
    Chunk(
        id="refund-policy",
        document_id="doc-refund",
        source="docs/refund.md",
        content="退款政策：用户可以在七天内无理由申请退款。",
        metadata={"tenant": "public", "lang": "zh"},
        start=0,
        end=len("退款政策：用户可以在七天内无理由申请退款。"),
    ),
    Chunk(
        id="refund-steps",
        document_id="doc-refund",
        source="docs/refund.md",
        content="退款流程：先提交申请，再等待审核，审核通过后退款到账。",
        metadata={"tenant": "public", "lang": "zh"},
        start=0,
        end=len("退款流程：先提交申请，再等待审核，审核通过后退款到账。"),
    ),
    Chunk(
        id="shipping",
        document_id="doc-shipping",
        source="docs/shipping.md",
        content="发货政策：订单通常在下单后两个工作日内发出。",
        metadata={"tenant": "public", "lang": "zh"},
        start=0,
        end=len("发货政策：订单通常在下单后两个工作日内发出。"),
    ),
    Chunk(
        id="contact",
        document_id="doc-contact",
        source="docs/contact.md",
        content="联系方式：客服邮箱 support@example.com，工作时间内回复。",
        metadata={"tenant": "public", "lang": "zh"},
        start=0,
        end=len("联系方式：客服邮箱 support@example.com，工作时间内回复。"),
    ),
]

GOLDEN_QUERIES = [
    EvalQuery(query="退款怎么申请", relevant_chunk_ids=("refund-steps", "refund-policy")),
    EvalQuery(query="发货需要多久", relevant_chunk_ids=("shipping",)),
    EvalQuery(query="怎么联系客服", relevant_chunk_ids=("contact",)),
]

K = 3


def _print_query_hits(name: str, retriever: Retriever) -> None:
    print(f"--- {name} top-{K} ---")
    for query in GOLDEN_QUERIES:
        hits = retriever.search(query.query, top_k=K)
        rows = ", ".join(
            f"{hit.chunk.id}({hit.score:.3f})" for hit in hits
        )
        print(f"  query: {query.query}")
        print(f"    -> {rows or '(no hits)'}")
    print()


def main() -> None:
    print("=== Week 14 检索对比 Demo ===")
    print(f"语料 chunk ids: {[chunk.id for chunk in CORPUS]}")
    print("golden queries:")
    for query in GOLDEN_QUERIES:
        print(f"  {query.query} -> {query.relevant_chunk_ids}")
    print()

    bm25 = InMemoryBM25Retriever(CORPUS)
    dense = InMemoryDenseRetriever(CORPUS, embedder=hash_embed)

    _print_query_hits("BM25", bm25)
    _print_query_hits("Dense (hash_embed toy)", dense)

    bm25_report = evaluate_retriever(bm25, GOLDEN_QUERIES, k=K)
    dense_report = evaluate_retriever(dense, GOLDEN_QUERIES, k=K)

    print("=== 评测对比 ===")
    print(f"{'Retriever':<25} {'Recall@3':>10} {'MRR':>8} {'nDCG@3':>10}")
    print(f"{'BM25':<25} {bm25_report.mean_recall_at_k:>10.3f} "
          f"{bm25_report.mean_reciprocal_rank:>8.3f} "
          f"{bm25_report.mean_ndcg_at_k:>10.3f}")
    print(f"{'Dense(hash_embed)':<25} {dense_report.mean_recall_at_k:>10.3f} "
          f"{dense_report.mean_reciprocal_rank:>8.3f} "
          f"{dense_report.mean_ndcg_at_k:>10.3f}")


if __name__ == "__main__":
    main()
