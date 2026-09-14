"""候选评测集机械检查 CLI（Week 16 Day 8）。

运行：

    .venv/bin/python -m experiments.review_eval_cases

默认只打印报告，不因 warning/error 退出非 0。
CI 中可以使用 --strict，让 error 触发失败。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.rag.answer_evaluation import load_rag_eval_cases_jsonl
from app.rag.eval_review import ReviewReport, review_cases
from experiments.rag_eval_facts import FACT_CHUNKS

DEFAULT_CASES_PATH = Path("eval_data/rag_eval_cases.jsonl")
DEFAULT_REPORT_PATH = Path("eval_data/eval_review_report.json")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help=f"eval cases jsonl path (default: {DEFAULT_CASES_PATH})",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"review report path (default: {DEFAULT_REPORT_PATH})",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit with code 1 when mechanical errors are found",
    )
    return parser.parse_args()


def _print_report(report: ReviewReport) -> None:
    print(
        f"cases: {report.total_cases} "
        f"reviewed: {report.reviewed_cases} "
        f"errors: {report.error_count} "
        f"warnings: {report.warning_count}"
    )
    for issue in report.issues:
        print(
            f"[{issue.severity.upper()}] "
            f"{issue.case_id} {issue.code}: {issue.message}"
        )


def main() -> None:
    args = _parse_args()
    cases = load_rag_eval_cases_jsonl(str(args.cases))
    report = review_cases(cases, FACT_CHUNKS)

    _print_report(report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"report written to {args.report}")

    if args.strict and report.has_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
