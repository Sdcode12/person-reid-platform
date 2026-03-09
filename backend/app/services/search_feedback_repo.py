from __future__ import annotations

from typing import Any

from app.db.pool import db_pool

_INSERT_FEEDBACK_SQL = """
INSERT INTO search_feedback (
    query_id,
    track_id,
    verdict,
    note,
    created_by
)
VALUES (%s, %s, %s, %s, %s)
RETURNING feedback_id, created_at
"""


class SearchFeedbackRepo:
    def insert_feedback(
        self,
        *,
        query_id: str,
        track_id: int,
        verdict: str,
        note: str | None,
        created_by: str,
    ) -> dict[str, Any]:
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_FEEDBACK_SQL,
                    (
                        query_id.strip(),
                        int(track_id),
                        verdict.strip().lower(),
                        note.strip() if isinstance(note, str) and note.strip() else None,
                        created_by.strip(),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

        return {
            "feedback_id": int(row[0]) if row else None,
            "created_at": row[1].isoformat() if row and row[1] is not None else None,
        }


search_feedback_repo = SearchFeedbackRepo()
