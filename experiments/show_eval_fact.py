"""按 chunk_id 查看评测语料内容与关联 case（Week 16 Day 8 辅助工具）。

用法：

    .venv/bin/python -m experiments.show_eval_fact
    .venv/bin/python -m experiments.show_eval_fact --chunk-id refund-policy
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.rag.answer_evaluation import load_rag_eval_cases_jsonl
from experiments.rag_eval_facts import FACT_CHUNKS

DEFAULT_CASES_PATH = Path("eval_data/rag_eval_cases.jsonl")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chunk-id",
        help="只查看指定 chunk_id；不传则列出全部 chunk_id",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help=f"eval cases jsonl path (default: {DEFAULT_CASES_PATH})",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    chunks_by_id = {chunk.id: chunk for chunk in FACT_CHUNKS}

    if args.chunk_id is not None:
        chunk = chunks_by_id.get(args.chunk_id)
        if chunk is None:
            print(f"chunk_id not found: {args.chunk_id}")
            print("available chunk ids:")
            for chunk_id in sorted(chunks_by_id):
                print(f"  {chunk_id}")
            raise SystemExit(1)

        print(f"chunk_id: {chunk.id}")
        print(f"document_id: {chunk.document_id}")
        print(f"source: {chunk.source}")
        print(f"content: {chunk.content}")
        print()

        cases = load_rag_eval_cases_jsonl(str(args.cases))
        related_cases = [
            case
            for case in cases
            if chunk.id in case.expected_chunk_ids
        ]
        print(f"related cases: {len(related_cases)}")
        for case in related_cases:
            print(
                f"  [{case.category.value}] {case.id}: {case.question} "
                f"answer_terms={list(case.expected_answer_contains)} "
                f"reviewed={case.reviewed}"
            )
        return

    for chunk in FACT_CHUNKS:
        print(f"{chunk.id}\t{chunk.content}")


if __name__ == "__main__":
    main()
