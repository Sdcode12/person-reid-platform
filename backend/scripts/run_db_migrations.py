from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.migrations import run_db_migrations


def main() -> int:
    result = run_db_migrations()
    print(
        json.dumps(
            {
                "ok": True,
                "applied": result.applied,
                "skipped": result.skipped,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

