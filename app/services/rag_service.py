"""RAG 问答服务（Week 16 Day 1）。

服务层负责编排，不负责具体检索算法、prompt 文本或模型协议：
1. 调用 AsyncRetriever 获取证据；
2. 调用 RagContextBuilder 构造上下文与引用编号；
3. 调用 LLMProvider 生成回答；
4. 组装 RagAnswer。

Day 1 只做基础空证据拒答；更细的引用校验、低分拒答和权限负例在
Day 2/Day 3 增强。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.llm.contracts import LLMProvider
from app.rag.answer import RagAnswer
from app.rag.context_builder import RagContextBuilder
from app.rag.retrieval import (
    AsyncRetriever,
    validate_query,
    validate_top_k,
)

REFUSAL_ANSWER = "资料中没有足够信息来回答这个问题。"


class RagQueryService:
    """把检索、上下文构造和生成串成一条可测试的业务链路。"""

    def __init__(
        self,
        retriever: AsyncRetriever,
        provider: LLMProvider,
        context_builder: RagContextBuilder,
    ) -> None:
        if not isinstance(retriever, AsyncRetriever):
            raise TypeError("retriever must implement AsyncRetriever")
        if not isinstance(provider, LLMProvider):
            raise TypeError("provider must implement LLMProvider")
        if not isinstance(context_builder, RagContextBuilder):
            raise TypeError("context_builder must be a RagContextBuilder")

        self._retriever = retriever
        self._provider = provider
        self._context_builder = context_builder

    async def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> RagAnswer:
        """检索证据后生成带引用的回答；没有可用证据时拒答。"""
        validate_query(question)
        validate_top_k(top_k)
        if metadata_filter is not None and not isinstance(metadata_filter, Mapping):
            raise TypeError("metadata_filter must be a mapping")

        hits = await self._retriever.search(
            question,
            top_k=top_k,
            metadata_filter=metadata_filter,
        )
        normalized_hits = tuple(hits)

        if not normalized_hits:
            return RagAnswer(
                answer=REFUSAL_ANSWER,
                citations=(),
                retrieved_chunk_ids=(),
                refused=True,
            )

        context = self._context_builder.build(
            question=question,
            hits=normalized_hits,
        )
        if not context.citations:
            return RagAnswer(
                answer=REFUSAL_ANSWER,
                citations=(),
                retrieved_chunk_ids=context.retrieved_chunk_ids,
                refused=True,
            )

        answer = await self._provider.complete(context.messages)
        return RagAnswer(
            answer=answer,
            citations=context.citations,
            retrieved_chunk_ids=context.retrieved_chunk_ids,
            refused=False,
        )
