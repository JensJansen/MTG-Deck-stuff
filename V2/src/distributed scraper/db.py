"""
Pooled database connection helper for the V2 API.

A ThreadedConnectionPool is lazily initialised on first use and shared across
all FastAPI request threads. All SQL fully-qualifies tables with the `v2.`
schema, so no search_path tweaking is required.

Callers use the get_connection() context manager — it borrows a connection from
the pool and returns it (with an automatic rollback on error) when the block
exits.
"""

import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError(
                "DATABASE_URL environment variable is not set.\n"
                "Example: postgresql://user:pass@host:5432/dbname"
            )
        _pool = psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=80, dsn=url)
    return _pool


@contextmanager
def get_connection():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
