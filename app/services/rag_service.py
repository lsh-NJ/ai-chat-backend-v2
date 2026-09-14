"""RAG 问答服务（Week 16 Day 1-3）。

服务层负责编排，不负责具体检索算法、prompt 文本或模型协议：
1. 调用 AsyncRetriever 获取证据；
2. 用 RagRefusalPolicy 做生成前的确定性守门；
3. 调用 RagContextBuilder 构造上下文与引用编号；
4. 调用 LLMProvider 生成回答；
5. 识别模型拒答、校验引用并组装 RagAnswer。

Day 1 做基础空证据拒答；Day 2 增加引用解析与未知编号校验；
Day 3 增加低分策略、模型拒答识别和“非拒答回答必须有引用”的校验。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.llm.contracts import LLMProvider
from app.rag.answer import RagAnswer
from app.rag.citation import validate_answer_citations
from app.rag.context_builder import RagContextBuilder
from app.rag.refusal import (
    REFUSAL_ANSWER,
    RagRefusalPolicy,
    RefusalReason,
)
from app.rag.retrieval import (
    AsyncRetriever,
    validate_query,
    validate_top_k,
)


class RagQueryService:
    """把检索、拒答守门、上下文构造和生成串成一条可测试的业务链路。"""

    def __init__(
        self,
        retriever: AsyncRetriever,
        provider: LLMProvider,
        context_builder: RagContextBuilder,
        refusal_policy: RagRefusalPolicy | None = None,
    ) -> None:
        if not isinstance(retriever, AsyncRetriever):
            raise TypeError("retriever must implement AsyncRetriever")
        if not isinstance(provider, LLMProvider):
            raise TypeError("provider must implement LLMProvider")
        if not isinstance(context_builder, RagContextBuilder):
            raise TypeError("context_builder must be a RagContextBuilder")
        if refusal_policy is not None and not isinstance(
            refusal_policy,
            RagRefusalPolicy,
        ):
            raise TypeError("refusal_policy must be a RagRefusalPolicy")

        self._retriever = retriever
        self._provider = provider
        self._context_builder = context_builder
        self._refusal_policy = refusal_policy or RagRefusalPolicy()

    async def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> RagAnswer:
        """检索证据后生成带引用的回答；证据不足时按策略拒答。"""
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

        retrieval_refusal = self._refusal_policy.check_retrieval(normalized_hits)
        if retrieval_refusal is not None:
            return self._refusal(
                reason=retrieval_refusal,
                retrieved_chunk_ids=(),
            )

        context = self._context_builder.build(
            question=question,
            hits=normalized_hits,
        )
        context_refusal = self._refusal_policy.check_context_citations(
            context.citations,
        )
        if context_refusal is not None:
            return self._refusal(
                reason=context_refusal,
                retrieved_chunk_ids=context.retrieved_chunk_ids,
            )

        answer = await self._provider.complete(context.messages)

        # 模型自己判断资料不足时，统一返回固定拒答文本，方便评测。
        if self._refusal_policy.is_model_refusal(answer):
            return self._refusal(
                reason=RefusalReason.MODEL_REFUSED,
                retrieved_chunk_ids=context.retrieved_chunk_ids,
            )

        used_citations = validate_answer_citations(
            answer,
            context.citations,
        )

        # 非拒答回答必须引用证据，否则这次生成结果不可信。
        if self._refusal_policy.should_refuse_missing_citation(
            answer=answer,
            citations=used_citations,
        ):
            return self._refusal(
                reason=RefusalReason.MISSING_CITATION,
                retrieved_chunk_ids=context.retrieved_chunk_ids,
            )

        return RagAnswer(
            answer=answer,
            citations=used_citations,
            retrieved_chunk_ids=context.retrieved_chunk_ids,
            refused=False,
        )

    @staticmethod
    def _refusal(
        *,
        reason: RefusalReason,
        retrieved_chunk_ids: Sequence[str],
    ) -> RagAnswer:
        return RagAnswer(
            answer=REFUSAL_ANSWER,
            citations=(),
            retrieved_chunk_ids=tuple(retrieved_chunk_ids),
            refused=True,
            refusal_reason=reason.value,
        )
