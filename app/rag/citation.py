"""RAG 引用解析与校验（Week 16 Day 2）。

模型回答中的 `[1]`、`[2]` 只是文本。服务层必须把文本标签映射回本次
检索得到的 `RagCitation`，并拒绝不存在的编号，不能让伪造引用进入响应。

职责边界：
- 只解析文本和校验标签；
- 不调用 LLM、不查数据库、不判断答案是否正确。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.core.exceptions import RagCitationError
from app.rag.answer import RagCitation

_CITATION_LABEL_RE = re.compile(r"\[(\d+)\]")


def extract_citation_labels(answer: str) -> tuple[str, ...]:
    """按首次出现顺序提取回答中的引用编号，并去重。"""
    if not isinstance(answer, str):
        raise TypeError("answer must be a string")

    labels: list[str] = []
    seen: set[str] = set()
    for match in _CITATION_LABEL_RE.finditer(answer):
        label = match.group(1)
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return tuple(labels)


def validate_answer_citations(
    answer: str,
    available_citations: Sequence[RagCitation],
) -> tuple[RagCitation, ...]:
    """返回回答实际引用的 citation；出现未知编号时 fail-closed。"""
    if isinstance(available_citations, (str, bytes)) or not isinstance(
        available_citations,
        Sequence,
    ):
        raise TypeError("available_citations must be a sequence")
    citations_by_label: dict[str, RagCitation] = {}
    for citation in available_citations:
        if not isinstance(citation, RagCitation):
            raise TypeError("available_citations must contain RagCitation values")
        if citation.label in citations_by_label:
            raise ValueError("available citation labels must be unique")
        citations_by_label[citation.label] = citation

    used: list[RagCitation] = []
    unknown: list[str] = []
    for label in extract_citation_labels(answer):
        citation = citations_by_label.get(label)
        if citation is None:
            unknown.append(label)
            continue
        used.append(citation)

    if unknown:
        raise RagCitationError(
            "model answer cited unknown citation labels: "
            + ", ".join(f"[{label}]" for label in unknown)
        )
    return tuple(used)
