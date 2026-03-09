from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.db.pool import db_pool

_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    name TEXT NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


@dataclass(frozen=True)
class MigrationFile:
    version: str
    name: str
    path: Path


@dataclass(frozen=True)
class MigrationResult:
    applied: int
    skipped: int


def _is_bootstrap_snapshot(migration: MigrationFile) -> bool:
    # `schema.sql` is the mutable bootstrap snapshot for fresh installs.
    # Only numbered patch migrations under `migrations/` are immutable.
    return migration.version == "0001" and migration.name == "base_schema"


def _migration_files() -> list[MigrationFile]:
    base_dir = Path(__file__).resolve().parent
    files: list[MigrationFile] = [
        MigrationFile(
            version="0001",
            name="base_schema",
            path=(base_dir / "schema.sql"),
        )
    ]
    patch_dir = base_dir / "migrations"
    if patch_dir.exists():
        for path in sorted(patch_dir.glob("*.sql")):
            stem = path.stem
            if "__" in stem:
                version, name = stem.split("__", 1)
            else:
                version, name = stem, "migration"
            files.append(
                MigrationFile(
                    version=version.strip(),
                    name=name.strip() or "migration",
                    path=path,
                )
            )
    files.sort(key=lambda m: (m.version, m.name, str(m.path)))
    return files


def run_db_migrations() -> MigrationResult:
    migrations = _migration_files()
    if not migrations:
        return MigrationResult(applied=0, skipped=0)

    pool = None
    conn = None
    applied = 0
    skipped = 0
    try:
        pool = db_pool.get_pool()
        conn = pool.getconn()
        with conn.cursor() as cur:
            cur.execute(_MIGRATIONS_TABLE_SQL)
            conn.commit()
            for migration in migrations:
                sql_text = migration.path.read_text(encoding="utf-8")
                checksum = sha256(sql_text.encode("utf-8")).hexdigest()
                cur.execute(
                    """
                    SELECT checksum
                    FROM schema_migrations
                    WHERE version = %s
                    LIMIT 1
                    """,
                    (migration.version,),
                )
                row = cur.fetchone()
                if row:
                    applied_checksum = str(row[0] or "")
                    if applied_checksum != checksum and not _is_bootstrap_snapshot(migration):
                        raise RuntimeError(
                            "migration checksum mismatch "
                            f"version={migration.version} path={migration.path}"
                        )
                    skipped += 1
                    continue

                cur.execute(sql_text)
                cur.execute(
                    """
                    INSERT INTO schema_migrations(version, name, checksum)
                    VALUES (%s, %s, %s)
                    """,
                    (migration.version, migration.name, checksum),
                )
                conn.commit()
                applied += 1
        return MigrationResult(applied=applied, skipped=skipped)
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if pool is not None and conn is not None:
            pool.putconn(conn)
