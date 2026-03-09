from __future__ import annotations

from psycopg2 import OperationalError
from psycopg2.pool import ThreadedConnectionPool

from app.core.settings import settings


class DBPool:
    def __init__(self) -> None:
        self._pool: ThreadedConnectionPool | None = None

    def reset(self) -> None:
        if self._pool is not None:
            try:
                self._pool.closeall()
            finally:
                self._pool = None

    def get_pool(self) -> ThreadedConnectionPool:
        if self._pool is None:
            self._pool = ThreadedConnectionPool(
                minconn=settings.db_minconn,
                maxconn=settings.db_maxconn,
                host=settings.db_host,
                port=settings.db_port,
                dbname=settings.db_name,
                user=settings.db_user,
                password=settings.db_password,
            )
        return self._pool

    def ping(self) -> bool:
        conn = None
        try:
            pool = self.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                _ = cur.fetchone()
            return True
        except OperationalError:
            return False
        except Exception:
            return False
        finally:
            if conn is not None and self._pool is not None:
                self._pool.putconn(conn)


db_pool = DBPool()
