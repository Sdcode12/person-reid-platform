from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.pool import db_pool
from app.services.capture_metadata_repo import capture_metadata_repo
from app.services.search_service import extract_search_backfill_features


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill search rerank features for existing capture_metadata rows.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="How many rows to process per batch (1-2000).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum number of rows to process in this run (1-200000).",
    )
    parser.add_argument(
        "--start-meta-id",
        type=int,
        default=0,
        help="Only process rows with meta_id greater than this value.",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        default=True,
        help="Process only rows missing search features (default: enabled).",
    )
    parser.add_argument(
        "--all",
        dest="only_missing",
        action="store_false",
        help="Process all rows regardless of whether features already exist.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run feature extraction without writing back to the database.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not db_pool.ping():
        print("db_ping=false", file=sys.stderr)
        return 2

    batch_size = max(1, min(int(args.batch_size), 2000))
    remaining = max(1, min(int(args.limit), 200000))
    next_meta_id = max(0, int(args.start_meta_id))
    only_missing = bool(args.only_missing)

    scanned = 0
    updated = 0
    skipped = 0
    errors = 0
    last_meta_id = next_meta_id

    while remaining > 0:
        fetch_limit = min(batch_size, remaining)
        rows = capture_metadata_repo.list_search_feature_backfill_candidates(
            limit=fetch_limit,
            after_meta_id=next_meta_id,
            only_missing=only_missing,
        )
        if not rows:
            break

        for row in rows:
            meta_id = int(row["meta_id"])
            last_meta_id = meta_id
            next_meta_id = meta_id
            scanned += 1
            try:
                features = extract_search_backfill_features(row, use_face=True)
                if not features:
                    skipped += 1
                    continue
                if args.dry_run:
                    updated += 1
                    continue
                did_update = capture_metadata_repo.update_search_features(
                    meta_id=meta_id,
                    upper_color=features.get("upper_color"),
                    lower_color=features.get("lower_color"),
                    upper_color_conf=features.get("upper_color_conf"),
                    lower_color_conf=features.get("lower_color_conf"),
                    upper_embedding=features.get("upper_embedding") or [],
                    lower_embedding=features.get("lower_embedding") or [],
                    face_embedding=features.get("face_embedding") or [],
                    face_confidence=features.get("face_confidence"),
                    quality_score=features.get("quality_score"),
                    person_area_ratio=features.get("person_area_ratio"),
                    image_mode=features.get("image_mode"),
                )
                if did_update:
                    updated += 1
                else:
                    skipped += 1
            except Exception:
                errors += 1
            remaining -= 1
            if remaining <= 0:
                break

    print(
        json.dumps(
            {
                "db_ping": True,
                "dry_run": bool(args.dry_run),
                "only_missing": only_missing,
                "scanned": scanned,
                "updated": updated,
                "skipped": skipped,
                "errors": errors,
                "last_meta_id": last_meta_id,
            },
            ensure_ascii=False,
        )
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
