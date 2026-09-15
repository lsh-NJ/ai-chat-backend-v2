"""Week 16 真实 LLM 端到端 RAG baseline。

使用：
- BGE dense 检索（当前 82 条评测里 Recall@5 最高）；
- DeepSeek 真实模型生成；
- RagQueryService 的拒答、引用和 token 统计；
- RagEvaluator 计算答案/拒答/引用/召回指标。

先跑 smoke：

    HF_HOME=/tmp/hf-cache .venv/bin/python -m experiments.rag_answer_baseline --limit 5

全量：

    HF_HOME=/tmp/hf-cache .venv/bin/python -m experiments.rag_answer_baseline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("LLM_MAX_OUTPUT_TOKENS", "2048")
os.environ.setdefault("LLM_CONTEXT_WINDOW", "32768")
os.environ.setdefault("LLM_TOKEN_SAFETY_MARGIN", "256")
os.environ.setdefault("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
os.environ.setdefault("LLM_TEMPERATURE", "0")

from app.db.session import AsyncSessionFactory  # noqa: E402
from app.llm.deepseek_v4_tokenizer import DeepSeekV4TokenCounter  # noqa: E402
from app.llm.observability import TokenUsage  # noqa: E402
from app.llm.providers.deepseek import DeepSeekConfig, DeepSeekProvider  # noqa: E402
from app.llm.tokenization import ContextBudget  # noqa: E402
from app.rag.answer_evaluation import (  # noqa: E402
    RagEvalCase,
    RagEvalCategory,
    RagEvaluator,
    load_rag_eval_cases_jsonl,
)
from app.rag.context_builder import RagContextBuilder  # noqa: E402
from app.rag.embedding import SentenceTransformerEmbedder  # noqa: E402
from app.rag.postgres_dense import PostgresDenseRetriever  # noqa: E402
from app.rag.refusal import RagRefusalPolicy  # noqa: E402
from app.services.rag_service import RagQueryService  # noqa: E402
from experiments.rag_baseline import TENANT_ID, seed_corpus  # noqa: E402

DEFAULT_CASES_PATH = Path("eval_data/rag_eval_cases.jsonl")
DEFAULT_REPORT_PATH = Path("eval_data/rag_answer_baseline_report.json")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只跑前 N 条，用于 smoke test",
    )
    parser.add_argument(
        "--category",
        choices=[category.value for category in RagEvalCategory],
        default=None,
        help="只跑某个 category",
    )
    parser.add_argument(
        "--only-ids",
        default=None,
        help="逗号分隔的 case id，只跑这些 case",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
    )
    return parser.parse_args()


def _select_cases(
    cases: tuple[RagEvalCase, ...],
    *,
    limit: int | None,
    category: str | None,
    only_ids: str | None,
) -> tuple[RagEvalCase, ...]:
    selected = cases
    if only_ids is not None:
        wanted_ids = {
            case_id.strip()
            for case_id in only_ids.split(",")
            if case_id.strip()
        }
        selected = tuple(case for case in selected if case.id in wanted_ids)
    if category is not None:
        selected = tuple(
            case for case in selected if case.category.value == category
        )
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise ValueError("no eval cases selected")
    return selected


def _print_report(report) -> None:
    print(f"total_cases: {report.total_cases}")
    print(f"passed_cases: {report.passed_cases}")
    print(f"accuracy: {report.accuracy:.3f}")
    print(f"answer_accuracy: {report.answer_accuracy:.3f}")
    print(f"refusal_accuracy: {report.refusal_accuracy:.3f}")
    print(f"citation_accuracy: {report.citation_accuracy:.3f}")
    print(f"mean_retrieval_recall: {report.mean_retrieval_recall:.3f}")
    print(f"forbidden_leak_count: {report.forbidden_leak_count}")
    print(f"p50_latency_seconds: {report.p50_latency_seconds:.3f}")
    print(f"p95_latency_seconds: {report.p95_latency_seconds:.3f}")
    print(f"total_input_tokens: {report.total_input_tokens}")
    print(f"total_output_tokens: {report.total_output_tokens}")
    failures = [result for result in report.results if not result.passed]
    print(f"failed_cases: {len(failures)}")
    for result in failures[:20]:
        print(
            f"  {result.case_id}: refused={result.refusal_correct} "
            f"answer={result.answer_correct} citation={result.citation_correct} "
            f"forbidden={result.forbidden_content_found} "
            f"error={result.error}"
        )
        print(
            f"    answer={result.answer[:120]!r} "
            f"citations={result.citation_chunk_ids} "
            f"retrieved={result.retrieved_chunk_ids}"
        )


async def main() -> None:
    args = _parse_args()
    config = DeepSeekConfig.from_env()
    counter = DeepSeekV4TokenCounter.from_resource(model=config.model)
    budget = ContextBudget(
        context_window=int(os.environ["LLM_CONTEXT_WINDOW"]),
        output_reserve=config.max_tokens,
        safety_margin=int(os.environ["LLM_TOKEN_SAFETY_MARGIN"]),
    )
    context_builder = RagContextBuilder(counter, budget)
    embedder = SentenceTransformerEmbedder(
        os.environ["EMBEDDING_MODEL"],
    )
    cases = _select_cases(
        load_rag_eval_cases_jsonl(str(args.cases)),
        limit=args.limit,
        category=args.category,
        only_ids=args.only_ids,
    )
    print(
        f"model={config.model} cases={len(cases)} "
        f"reviewed={sum(1 for case in cases if case.reviewed)}"
    )

    await seed_corpus(embedder)

    def token_counter(messages, output: str) -> TokenUsage:
        return TokenUsage(
            input_tokens=counter.count_messages(messages),
            output_tokens=counter.count_text(output),
        )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10, read=120, write=30, pool=10),
        limits=httpx.Limits(
            max_connections=20,
            max_keepalive_connections=10,
        ),
    ) as client:
        provider = DeepSeekProvider(client, config)
        async with AsyncSessionFactory() as session:
            retriever = PostgresDenseRetriever(
                session,
                tenant_id=TENANT_ID,
                embedder=embedder.embed_query,
            )
            service = RagQueryService(
                retriever=retriever,
                provider=provider,
                context_builder=context_builder,
                refusal_policy=RagRefusalPolicy(),
                token_counter=token_counter,
            )
            evaluator = RagEvaluator(
                service,
                top_k=5,
                model=config.model,
            )
            report = await evaluator.evaluate(cases)

    _print_report(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(f"report written to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
