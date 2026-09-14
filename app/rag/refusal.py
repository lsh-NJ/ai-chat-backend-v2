"""RAG 拒答策略（Week 16 Day 3）。

拒答不是让模型随便决定，而是分层策略：
1. 检索层确定性守门：没有命中，或最高分低于明确配置的阈值；
2. 上下文层确定性守门：token 预算导致一个证据都放不下；
3. 模型层自判断：资料相关但没有答案时，模型按 prompt 输出拒答文本；
4. 生成后校验：非拒答的回答必须引用本次检索结果。

`min_top_score` 默认是 None，因为 BM25、余弦相似度和 RRF 的分数尺度不同，
不能拍脑袋设置一个通用阈值。只有评测证明某个检索方案下阈值合理时才启用。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.rag.answer import RagCitation
from app.rag.retrieval import ChunkHit

REFUSAL_ANSWER = "资料中没有足够信息来回答这个问题。"


class RefusalReason(StrEnum):
    """拒答原因，供评测和 bad-case 分类使用。"""

    NO_RETRIEVAL_HITS = "no_retrieval_hits"
    LOW_EVIDENCE_SCORE = "low_evidence_score"
    NO_CONTEXT_EVIDENCE = "no_context_evidence"
    MODEL_REFUSED = "model_refused"
    MISSING_CITATION = "missing_citation"


@dataclass(frozen=True, slots=True)
class RagRefusalPolicy:
    """可配置的拒答规则，默认只拒绝真正没有证据的情况。"""

    min_evidence: int = 1 # 调用前检查 CHunkHit 数量
    min_top_score: float | None = None
    require_citation: bool = True # 调用后检查 LLM 使用 ChunkHit 情况

    def __post_init__(self) -> None:
        if isinstance(self.min_evidence, bool) or not isinstance(
            self.min_evidence,
            int,
        ):
            raise TypeError("min_evidence must be an integer")
        if self.min_evidence <= 0:
            raise ValueError("min_evidence must be positive")
        if self.min_top_score is not None:
            if isinstance(self.min_top_score, bool) or not isinstance(
                self.min_top_score,
                (int, float),
            ):
                raise TypeError("min_top_score must be a number or None")
            value = float(self.min_top_score)
            if not math.isfinite(value):
                raise ValueError("min_top_score must be finite")
            object.__setattr__(self, "min_top_score", value)
        if not isinstance(self.require_citation, bool):
            raise TypeError("require_citation must be a boolean")

    def check_retrieval(
        self,
        hits: Sequence[ChunkHit],
    ) -> RefusalReason | None:
        """生成前的确定性守门：检查命中数量和可选的最低分。"""
        if any(not isinstance(hit, ChunkHit) for hit in hits):
            raise TypeError("hits must contain ChunkHit values")
        if len(hits) < self.min_evidence:
            return RefusalReason.NO_RETRIEVAL_HITS
        if self.min_top_score is not None:
            top_score = max(hit.score for hit in hits)
            if top_score < self.min_top_score:
                return RefusalReason.LOW_EVIDENCE_SCORE
        return None

    def check_context_citations(
        self,
        citations: Sequence[RagCitation],
    ) -> RefusalReason | None:
        """上下文构造后没有留下任何引用时，直接拒答，不调用 LLM。"""
        if not citations:
            return RefusalReason.NO_CONTEXT_EVIDENCE
        return None

    @staticmethod
    def is_model_refusal(answer: str) -> bool:
        """识别模型是否按 prompt 返回了固定拒答文本。"""
        if not isinstance(answer, str):
            raise TypeError("answer must be a string")
        normalized = answer.strip().rstrip("。.!！")
        expected = REFUSAL_ANSWER.rstrip("。")
        return expected in normalized

    def should_refuse_missing_citation(
        self,
        *,
        answer: str,
        citations: Sequence[RagCitation],
    ) -> bool:
        """要求非拒答回答必须有引用；否则拒绝这次生成结果。"""
        if not self.require_citation:
            return False
        if citations:
            return False
        return not self.is_model_refusal(answer)
