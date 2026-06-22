"""
compute_stats.py - Precomputes per-card and per-pair co-occurrence statistics.

All computation runs inside Postgres via the refresh_format_stats stored
procedure — no intermediate data is shipped to Python and back.
Writes results to {format}_card_stats and {format}_card_pair_stats.
Each deck is treated as an atomic unit: every card in {format}_deck_cards
is counted regardless of board.
Only regular (multi-card) formats are supported: pauper, standard, modern, vintage, legacy.

Usage:
    python src/analysis/compute_stats.py
    python src/analysis/compute_stats.py --format pauper
    python src/analysis/compute_stats.py --min-cooccur 10
    python src/analysis/compute_stats.py --format modern --min-cooccur 10

Reads DATABASE_URL from src/distributed scraper/.env automatically.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import psycopg2

from constants.env import load_env
from constants.moxfield import REGULAR_FORMATS

DEFAULT_MIN_COOCCUR = 5


def get_scraped_formats(fmt_filter: str | None) -> list[str]:
    if fmt_filter:
        if fmt_filter not in REGULAR_FORMATS:
            print(f"ERROR: '{fmt_filter}' is not a supported format.")
            print(f"  Supported: {', '.join(REGULAR_FORMATS)}")
            sys.exit(1)
        return [fmt_filter]
    return list(REGULAR_FORMATS)


_SAMPLE_LIMIT = 5


def _print_sample(conn, fmt: str, limit: int = _SAMPLE_LIMIT) -> None:
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT card_a, card_b, cooccurrence_count, lift, jaccard
            FROM   {fmt}_card_pair_stats
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

        with conn.cursor() as cur:
            cur.execute(
                "CALL refresh_format_stats(%s, %s)",
                (fmt, min_cooccur),
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    (SELECT COUNT(*) FROM {fmt}_card_stats),
                    (SELECT COUNT(*) FROM {fmt}_card_pair_stats)
            """)
            n_cards, n_pairs = cur.fetchone()

        print(f"  {n_cards:,} cards, {n_pairs:,} pairs (cooccur >= {min_cooccur})")
        _print_sample(conn, fmt)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute card co-occurrence statistics from scraped deck data."
    )
    parser.add_argument("--format", "-f", dest="format", default=None,
                        help=f"Limit to one format (default: all). Choices: {', '.join(REGULAR_FORMATS)}")
    parser.add_argument("--min-cooccur", dest="min_cooccur", type=int,
                        default=DEFAULT_MIN_COOCCUR,
                        help=f"Minimum co-occurrence count to store a pair (default: {DEFAULT_MIN_COOCCUR})")
    args = parser.parse_args()

    formats = get_scraped_formats(args.format)

    load_env()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL not set. Fill in src/distributed scraper/.env and retry.")
        sys.exit(1)

    conn = psycopg2.connect(pg_url)

    try:
        print(f"Computing stats for: {', '.join(formats)}")
        print(f"Min co-occurrence: {args.min_cooccur}")

        run(conn, formats, args.min_cooccur)
    finally:
        conn.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
