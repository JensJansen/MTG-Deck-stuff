"""
Shared database connection helper.

Set the DATABASE_URL environment variable to a standard PostgreSQL connection
string before running any node:

    export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
"""

import os

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
