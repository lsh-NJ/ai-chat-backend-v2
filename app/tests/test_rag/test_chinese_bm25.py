import pytest

from app.rag.chinese_bm25 import (
    AsyncChineseBM25Retriever,
    ChineseBM25Retriever,
    tokenize_chinese,
)
from app.rag.chunking import Chunk


def _chunk(
    chunk_id: str,
    content: str,
    *,
    metadata: dict | None = None,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id="doc-1",
        source="docs/faq.md",
        content=content,
        metadata=metadata or {},
        start=0,
        end=len(content),
    )


def test_tokenize_chinese_keeps_chinese_words_and_filters_punctuation() -> None:
    tokens = tokenize_chinese("退款流程：先提交申请，support@example.com。")

    assert "退款" in tokens
    assert "流程" in tokens
    assert "：" not in tokens
    assert "support@example.com" in tokens or "support" in tokens


def test_chinese_bm25_ranks_matching_chunk_first() -> None:
    retriever = ChineseBM25Retriever(
        [
            _chunk("refund-steps", "退款流程：先提交申请，再等待审核。"),
            _chunk("shipping", "发货政策：订单通常在下单后两个工作日内发出。"),
            _chunk("contact", "联系方式：客服邮箱 support@example.com。"),
            _chunk("invoice", "发票申请需要提供订单号和抬头信息。"),
        ]
    )

    hits = retriever.search("退款怎么申请", top_k=1)

    assert [hit.chunk.id for hit in hits] == ["refund-steps"]
    assert hits[0].score > 0
    assert hits[0].rank == 1


def test_chinese_bm25_metadata_filter() -> None:
    retriever = ChineseBM25Retriever(
        [
            _chunk(
                "public",
                "退款流程：先提交申请。",
                metadata={"tenant": "public"},
            ),
            _chunk(
                "internal",
                "内部退款流程：先联系主管。",
                metadata={"tenant": "internal"},
            ),
            _chunk("shipping", "发货政策：两个工作日内发出。"),
            _chunk("contact", "客服邮箱 support@example.com。"),
        ]
    )

    hits = retriever.search(
        "主管",
        top_k=10,
        metadata_filter={"tenant": "internal"},
    )

    assert [hit.chunk.id for hit in hits] == ["internal"]


async def test_async_chinese_bm25_wrapper() -> None:
    retriever = AsyncChineseBM25Retriever(
        [
            _chunk("refund-steps", "退款流程：先提交申请。"),
            _chunk("shipping", "发货政策：两个工作日内发出。"),
            _chunk("contact", "客服邮箱 support@example.com。"),
            _chunk("invoice", "发票申请需要订单号。"),
        ]
    )

    hits = await retriever.search("退款申请", top_k=1)

    assert [hit.chunk.id for hit in hits] == ["refund-steps"]


def test_chinese_bm25_rejects_empty_query() -> None:
    retriever = ChineseBM25Retriever([_chunk("refund", "退款流程")])

    with pytest.raises(ValueError, match="empty"):
        retriever.search("   ")
