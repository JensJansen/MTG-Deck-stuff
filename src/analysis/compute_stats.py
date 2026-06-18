"""
compute_stats.py - Precomputes per-card and per-pair co-occurrence statistics.

All computation runs inside Postgres via the refresh_format_stats stored
procedure — no intermediate data is shipped to Python and back.

Usage:
    python src/analysis/compute_stats.py
    python src/analysis/compute_stats.py --min-cooccur 10
    python src/analysis/compute_stats.py --format commander

Reads DATABASE_URL from src/distributed scraper/.env automatically.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import psycopg2

from constants.env import load_env
from constants.moxfield import DEFAULT_BOARDS

DEFAULT_MIN_COOCCUR = 5


def get_scraped_formats(conn, fmt_filter: str | None) -> list[str]:
    if fmt_filter:
        return [fmt_filter]
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT format FROM decks ORDER BY format")
        return [r[0] for r in cur.fetchall()]


_SAMPLE_LIMIT = 5


def _print_sample(conn, fmt: str, limit: int = _SAMPLE_LIMIT) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT card_a, card_b, cooccurrence_count, lift, jaccard
            FROM   card_pair_stats
            WHERE  format = %s
            ORDER  BY lift DESC
            LIMIT  %s
        """, (fmt, limit))
        rows = cur.fetchall()
    if rows:
        print("  top pairs by lift:")
        for r in rows:
            print(
                f"    {r[0]!r:35s} + {r[1]!r:35s}"
                f"  cooccur={r[2]:3d}"
                f"  lift={r[3]:.2f}"
                f"  jaccard={r[4]:.3f}"
            )


def run(conn, formats: list[str], boards: frozenset[str], min_cooccur: int) -> None:
    boards_list = list(boards)
    for fmt in formats:
        print(f"\n[{fmt}]", flush=True)

        with conn.cursor() as cur:
            cur.execute(
                "CALL refresh_format_stats(%s, %s, %s)",
                (fmt, boards_list, min_cooccur),
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM card_stats      WHERE format = %s", (fmt,))
            n_cards = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM card_pair_stats WHERE format = %s", (fmt,))
            n_pairs = cur.fetchone()[0]

        print(f"  {n_cards:,} cards, {n_pairs:,} pairs (cooccur >= {min_cooccur})")
        _print_sample(conn, fmt)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute card co-occurrence statistics from scraped deck data."
    )
    parser.add_argument("--format", "-f", dest="format", default=None,
                        help="Limit computation to one format (default: all formats in DB)")
    parser.add_argument("--min-cooccur", dest="min_cooccur", type=int,
                        default=DEFAULT_MIN_COOCCUR,
                        help=f"Minimum co-occurrence count to store a pair (default: {DEFAULT_MIN_COOCCUR})")
    parser.add_argument("--include-sideboard", dest="include_sideboard", action="store_true",
                        help="Also count sideboard cards when computing statistics")
    args = parser.parse_args()

    load_env()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL not set. Fill in src/distributed scraper/.env and retry.")
        return

    boards = DEFAULT_BOARDS | ({"sideboard"} if args.include_sideboard else set())
    conn   = psycopg2.connect(pg_url)

    try:
        formats = get_scraped_formats(conn, args.format)
        if not formats:
            print("No matching formats found in the database.")
            return

        print(f"Computing stats for: {', '.join(formats)}")
        print(f"Boards: {', '.join(sorted(boards))}  |  min co-occurrence: {args.min_cooccur}")

        run(conn, formats, boards, args.min_cooccur)
    finally:
        conn.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
