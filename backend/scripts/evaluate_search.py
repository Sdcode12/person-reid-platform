from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.pool import db_pool
from app.services.search_eval import (
    build_report,
    evaluate_case,
    load_eval_cases,
    report_to_dict,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run offline search evaluation against a fixed dataset of query samples.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "data" / "search_eval" / "queries.template.json",
        help="Path to the evaluation dataset JSON file.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only specific case IDs. Can be provided multiple times.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to write the full evaluation report JSON.",
    )
    parser.add_argument(
        "--fail-on-miss",
        action="store_true",
        help="Exit with code 3 if any case misses Top10.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    dataset_path = args.dataset.resolve()
    if not dataset_path.exists():
        print(f"dataset_not_found path={dataset_path}", file=sys.stderr)
        return 2
    if not db_pool.ping():
        print("db_ping=false", file=sys.stderr)
        return 2

    dataset_name, cases = load_eval_cases(dataset_path)
    if args.case_id:
        wanted = {item.strip() for item in args.case_id if item.strip()}
        cases = [item for item in cases if item.case_id in wanted]
        if not cases:
            print("no_matching_cases", file=sys.stderr)
            return 2

    results = [evaluate_case(case, dataset_dir=dataset_path.parent) for case in cases]
    report = build_report(dataset_name, results)
    payload = report_to_dict(report)

    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print("")
    for item in payload["cases"]:
        status = "TOP10_HIT" if item["hit_top10"] else "MISS"
        print(
            f"[{status}] case={item['case_id']} rank={item['first_relevant_rank']} "
            f"top1={item['hit_top1']} top5={item['hit_top5']} top10={item['hit_top10']} "
            f"elapsed_ms={item['elapsed_ms']} results={item['result_count']} "
            f"matched_target={item['matched_target_key']} matched_track={item['matched_track_id']}"
        )

    if args.output_json is not None:
        output_path = args.output_json.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nreport_written path={output_path}")

    if args.fail_on_miss and any(not item.hit_top10 for item in report.cases):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
