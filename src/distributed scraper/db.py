"""
Shared database connection helper.

Set the DATABASE_URL environment variable to a standard PostgreSQL connection
string before running any node:

    export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
"""

import os
from pathlib import Path

import psycopg2
import psycopg2.extras


def get_connection() -> psycopg2.extensions.connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set.\n"
            "Example: postgresql://user:pass@host:5432/dbname"
        )
    conn = psycopg2.connect(url)
    conn.autocommit = False
    return conn


def apply_schema(conn: psycopg2.extensions.connection) -> None:
    """Create all tables and indexes if they do not already exist."""
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
