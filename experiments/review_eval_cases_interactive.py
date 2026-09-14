"""交互式人工复核评测集（Week 16 Day 8 辅助工具）。

运行：

    .venv/bin/python -m experiments.review_eval_cases_interactive

逐条显示 case、期望答案、关联 chunk 内容，然后选择：
    y  标记 reviewed=True
    n  标记 reviewed=False
    s  跳过，保持当前值
    q  保存并退出
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.rag.answer_evaluation import (
    RagEvalCase,
    load_rag_eval_cases_jsonl,
)
from experiments.rag_eval_facts import FACT_CHUNKS

DEFAULT_CASES_PATH = Path("eval_data/rag_eval_cases.jsonl")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help=f"eval cases jsonl path (default: {DEFAULT_CASES_PATH})",
    )
    return parser.parse_args()


def case_to_record(case: RagEvalCase) -> dict[str, Any]:
    """把领域模型转回 JSONL 记录。"""
    return {
        "id": case.id,
        "question": case.question,
        "category": case.category.value,
        "expected_refused": case.expected_refused,
        "expected_answer_contains": list(case.expected_answer_contains),
        "expected_chunk_ids": list(case.expected_chunk_ids),
        "forbidden_answer_contains": list(case.forbidden_answer_contains),
        "reviewed": case.reviewed,
    }


def save_cases(path: Path, cases: list[RagEvalCase]) -> None:
    """把已复核/未复核状态写回 JSONL。"""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(case_to_record(case), ensure_ascii=False)
            for case in cases
        )
        + "\n",
        encoding="utf-8",
    )


def _with_reviewed(case: RagEvalCase, reviewed: bool) -> RagEvalCase:
    record = asdict(case)
    record["reviewed"] = reviewed
    return RagEvalCase(**record)


def _print_case(case: RagEvalCase, chunks_by_id: dict[str, Any]) -> None:
    print("=" * 80)
    print(f"id: {case.id}")
    print(f"category: {case.category.value}")
    print(f"question: {case.question}")
    print(f"expected_refused: {case.expected_refused}")
    print(f"expected_answer_contains: {list(case.expected_answer_contains)}")
    print(f"expected_chunk_ids: {list(case.expected_chunk_ids)}")
    print(f"forbidden_answer_contains: {list(case.forbidden_answer_contains)}")
    print(f"reviewed: {case.reviewed}")
    print("-" * 80)
    for chunk_id in case.expected_chunk_ids:
        chunk = chunks_by_id.get(chunk_id)
        if chunk is None:
            print(f"[chunk missing] {chunk_id}")
            continue
        print(f"[{chunk_id}] {chunk.content}")
    print("-" * 80)


def _ask_action() -> str:
    while True:
        action = input("标记 reviewed? [y/n/s/q]: ").strip().lower()
        if action in {"y", "n", "s", "q"}:
            return action
        print("请输入 y / n / s / q")


def main() -> None:
    args = _parse_args()
    cases = list(load_rag_eval_cases_jsonl(str(args.cases)))
    chunks_by_id = {chunk.id: chunk for chunk in FACT_CHUNKS}

    reviewed_count = 0
    for index, case in enumerate(cases, start=1):
        print(f"\n[{index}/{len(cases)}]")
        _print_case(case, chunks_by_id)
        action = _ask_action()
        if action == "q":
            break
        if action == "y":
            cases[index - 1] = _with_reviewed(case, True)
            reviewed_count += 1
        elif action == "n":
            cases[index - 1] = _with_reviewed(case, False)
        save_cases(args.cases, cases)

    total_reviewed = sum(1 for case in cases if case.reviewed)
    print(
        f"\n本 session 标记 reviewed=True: {reviewed_count}\n"
        f"当前 reviewed=True 总数: {total_reviewed}/{len(cases)}"
    )
    print(f"已写回: {args.cases}")


if __name__ == "__main__":
    main()
