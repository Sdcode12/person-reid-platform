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


def main() -> int:
    parser = argparse.ArgumentParser(description='Sync capture metadata.jsonl into PostgreSQL capture_metadata table.')
    parser.add_argument('--scan-limit', type=int, default=5000, help='How many latest metadata rows to scan (1-200000)')
    parser.add_argument('--show-count', action='store_true', help='Print table count after sync')
    parser.add_argument(
        '--purge-local-images',
        dest='purge_local_images',
        action='store_true',
        default=True,
        help='Delete local image/sidecar files after successful DB write (default: enabled)',
    )
    parser.add_argument(
        '--keep-local-images',
        dest='purge_local_images',
        action='store_false',
        help='Keep local image/sidecar files after DB sync',
    )
    args = parser.parse_args()

    if not db_pool.ping():
        print('db_ping=false')
        return 2

    stats = capture_metadata_repo.sync_from_local(
        scan_limit=args.scan_limit,
        purge_local_images=bool(args.purge_local_images),
    )
    print(json.dumps({'db_ping': True, 'sync': stats}, ensure_ascii=False))

    if args.show_count:
        total = capture_metadata_repo.count_records()
        print(json.dumps({'capture_metadata_count': total}, ensure_ascii=False))

    return 0


if __name__ == '__main__':
    sys.exit(main())
