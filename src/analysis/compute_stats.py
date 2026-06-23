"""
compute_stats.py - Precomputes per-card and per-pair co-occurrence statistics.

All computation runs inside Postgres via stored procedures — no intermediate
data is shipped to Python and back.
Writes results to {format}_card_stats and {format}_card_pair_stats.

Regular formats (pauper, modern, vintage, legacy):
  Uses refresh_format_stats — reads from {format}_deck_cards.

Singleton formats (commander, highlanderCanadian):
  Uses refresh_singleton_format_stats — reads cards from the JSONB column
  on {format}_decks (mainboard + commanders + companions + signatureSpells).

Usage:
    python src/analysis/compute_stats.py
    python src/analysis/compute_stats.py --format pauper
    python src/analysis/compute_stats.py --format commander
    python src/analysis/compute_stats.py --min-cooccur 10

Reads DATABASE_URL from src/distributed scraper/.env automatically.
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import psycopg2

from constants.env import load_env
from constants.moxfield import ALL_FORMATS, REGULAR_FORMATS, SINGLETON_FORMATS

DEFAULT_MIN_COOCCUR = 5

_FORMAT_TABLE: dict[str, str] = {"highlanderCanadian": "canadian_highlander"}


def _table_prefix(fmt: str) -> str:
    return _FORMAT_TABLE.get(fmt, fmt)


def get_formats(fmt_filter: str | None) -> list[str]:
    if fmt_filter:
        if fmt_filter not in ALL_FORMATS:
            print(f"ERROR: '{fmt_filter}' is not a supported format.")
            print(f"  Supported: {', '.join(ALL_FORMATS)}")
            sys.exit(1)
        return [fmt_filter]
    return list(ALL_FORMATS)


_SAMPLE_LIMIT = 5


def _print_sample(conn, fmt: str, limit: int = _SAMPLE_LIMIT) -> None:
    table = _table_prefix(fmt) + "_card_pair_stats"
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT card_a, card_b, cooccurrence_count, lift, jaccard
            FROM   {table}
            ORDER  BY lift DESC
            LIMIT  %s
        """, (limit,))
        rows = cur.fetchall()
    if rows:
        print("  top pairs by lift:")
        for r in rows:
            print(
                f"    {r[0]:35s} + {r[1]:35s}"
                f"  cooccur={r[2]:3d}"
                f"  lift={r[3]:.2f}"
                f"  jaccard={r[4]:.3f}"
            )


def run(conn, formats: list[str], min_cooccur: int) -> None:
    for fmt in formats:
        print(f"\n[{fmt}]", flush=True)
        prefix = _table_prefix(fmt)

        with conn.cursor() as cur:
            if fmt in SINGLETON_FORMATS:
                cur.execute(
                    "CALL refresh_singleton_format_stats(%s, %s)",
                    (prefix, min_cooccur),
                )
            else:
                cur.execute(
                    "CALL refresh_format_stats(%s, %s)",
                    (fmt, min_cooccur),
                )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    (SELECT COUNT(*) FROM {prefix}_card_stats),
                    (SELECT COUNT(*) FROM {prefix}_card_pair_stats)
            """)
            n_cards, n_pairs = cur.fetchone()

        print(f"  {n_cards:,} cards, {n_pairs:,} pairs (cooccur >= {min_cooccur})")
        _print_sample(conn, fmt)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute card co-occurrence statistics from scraped deck data."
    )
    parser.add_argument(
        "--format", "-f", dest="format", default=None,
        help=f"Limit to one format (default: all). Choices: {', '.join(ALL_FORMATS)}",
    )
    parser.add_argument(
        "--min-cooccur", dest="min_cooccur", type=int, default=DEFAULT_MIN_COOCCUR,
        help=f"Minimum co-occurrence count to store a pair (default: {DEFAULT_MIN_COOCCUR})",
    )
    args = parser.parse_args()

    formats = get_formats(args.format)

    load_env()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL not set. Fill in src/distributed scraper/.env and retry.")
        sys.exit(1)

    conn = psycopg2.connect(pg_url)

    start = time.monotonic()
    start_time = datetime.now()
    print(f"Start: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        print(f"Computing stats for: {', '.join(formats)}")
        print(f"Min co-occurrence:   {args.min_cooccur}")

        run(conn, formats, args.min_cooccur)
    finally:
        conn.close()

    end_time = datetime.now()
    elapsed = time.monotonic() - start
    print(f"\nFinish:  {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Elapsed: {elapsed:.1f}s")
    print("\nDone.")


if __name__ == "__main__":
    main()
