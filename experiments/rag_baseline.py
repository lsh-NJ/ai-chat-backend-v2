"""Week 16 真实 BGE + 中文 BM25 检索 baseline。

用途：
1. 用 `BAAI/bge-small-zh-v1.5` 把小型真实语料写入 PostgreSQL + pgvector；
2. 在 `eval_data/rag_eval_seed.jsonl` 的检索类样例上比较：
   - PostgreSQL 字符级 FTS（ts_rank_cd）
   - jieba + rank_bm25
   - BGE dense
   - FTS + dense Hybrid
   - BM25 + dense Hybrid
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from dotenv import load_dotenv

load_dotenv()

from app.db.session import AsyncSessionFactory  # noqa: E402
from app.rag.answer_evaluation import (  # noqa: E402
    load_rag_eval_cases_jsonl,
)
from app.rag.chinese_bm25 import AsyncChineseBM25Retriever  # noqa: E402
from app.rag.documents import Document  # noqa: E402
from app.rag.embedding import SentenceTransformerEmbedder  # noqa: E402
from app.rag.evaluation import (  # noqa: E402
    EvalQuery,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank_at_k,
)
from app.rag.hybrid import AsyncHybridRetriever  # noqa: E402
from app.rag.postgres_dense import PostgresDenseRetriever  # noqa: E402
from app.rag.postgres_fts import PostgresFullTextRetriever  # noqa: E402
from app.rag.postgres_store import (  # noqa: E402
    PostgresChunkStore,
    PostgresDocumentStore,
)
from app.rag.retrieval import AsyncRetriever  # noqa: E402
from app.rag.store import DuplicateContentError, DuplicateIdError  # noqa: E402
from experiments.rag_eval_facts import FACT_CHUNKS  # noqa: E402

TENANT_ID = "baseline-tenant-v2"
TOP_K = 5
CORPUS_CHUNKS = FACT_CHUNKS


async def seed_corpus(embedder: SentenceTransformerEmbedder) -> None:
    """幂等地写入 baseline 语料；重复运行不会产生重复行。"""
    for document_id in {chunk.document_id for chunk in CORPUS_CHUNKS}:
        chunks = tuple(
            chunk for chunk in CORPUS_CHUNKS if chunk.document_id == document_id
        )
        content = "\n".join(chunk.content for chunk in chunks)
        async with AsyncSessionFactory() as session:
            try:
                await PostgresDocumentStore(
                    session,
                    tenant_id=TENANT_ID,
                ).save(
                    Document(
                        id=document_id,
                        source=chunks[0].source,
                        content=content,
                        metadata={"version": 1, "lang": "zh"},
                    )
                )
            except (DuplicateContentError, DuplicateIdError):
                await session.rollback()
            else:
                await session.commit()

        async with AsyncSessionFactory() as session:
            await PostgresChunkStore(
                session,
                tenant_id=TENANT_ID,
            ).save_chunks(
                document_id,
                chunks,
                embedder=embedder.embed_query,
            )
            await session.commit()


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


async def evaluate_retriever(
    name: str,
    retriever: AsyncRetriever,
    cases: Sequence[EvalQuery],
) -> None:
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []

    print(f"--- {name} ---")
    for case in cases:
        hits = await retriever.search(case.query, top_k=TOP_K)
        retrieved_ids = [hit.chunk.id for hit in hits]
        recall = recall_at_k(retrieved_ids, case.relevant_chunk_ids, k=TOP_K)
        reciprocal_rank = reciprocal_rank_at_k(
            retrieved_ids,
            case.relevant_chunk_ids,
            k=TOP_K,
        )
        ndcg = ndcg_at_k(
            retrieved_ids,
            case.relevant_chunk_ids,
            k=TOP_K,
        )
        recalls.append(recall)
        reciprocal_ranks.append(reciprocal_rank)
        ndcgs.append(ndcg)
        print(
            f"  {case.query:<24} -> {retrieved_ids} "
            f"R@{TOP_K}={recall:.2f} RR={reciprocal_rank:.2f} nDCG={ndcg:.2f}"
        )

    print(
        f"  mean: Recall@{TOP_K}={_mean(recalls):.3f} "
        f"MRR={_mean(reciprocal_ranks):.3f} "
        f"nDCG@{TOP_K}={_mean(ndcgs):.3f}\n"
    )


async def main() -> None:
    embedder = SentenceTransformerEmbedder()
    print(f"embedding model: {embedder._model_name}")  # noqa: SLF001

    await seed_corpus(embedder)
    cases = load_rag_eval_cases_jsonl("eval_data/rag_eval_cases.jsonl")
    retrieval_cases = [
        EvalQuery(
            query=case.question,
            relevant_chunk_ids=case.expected_chunk_ids,
        )
        for case in cases
        if case.expected_chunk_ids
    ]
    reviewed_count = sum(1 for case in cases if case.reviewed)
    print(
        f"eval cases: total={len(cases)} retrieval={len(retrieval_cases)} "
        f"reviewed={reviewed_count}"
    )

    async with AsyncSessionFactory() as session:
        fts = PostgresFullTextRetriever(session, tenant_id=TENANT_ID)
        dense = PostgresDenseRetriever(
            session,
            tenant_id=TENANT_ID,
            embedder=embedder.embed_query,
        )
        bm25 = AsyncChineseBM25Retriever(CORPUS_CHUNKS)
        hybrid_fts = AsyncHybridRetriever(fts, dense)
        hybrid_bm25 = AsyncHybridRetriever(bm25, dense)

        await evaluate_retriever("PostgreSQL FTS", fts, retrieval_cases)
        await evaluate_retriever("Jieba BM25", bm25, retrieval_cases)
        await evaluate_retriever("BGE dense", dense, retrieval_cases)
        await evaluate_retriever(
            "Hybrid FTS + dense",
            hybrid_fts,
            retrieval_cases,
        )
        await evaluate_retriever(
            "Hybrid BM25 + dense",
            hybrid_bm25,
            retrieval_cases,
        )


if __name__ == "__main__":
    asyncio.run(main())
