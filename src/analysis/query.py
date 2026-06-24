"""
query.py - Look up per-format co-occurrence stats for a specific card.

Queries {format}_card_stats for inclusion rates and {format}_card_pair_stats
for co-occurrence metrics. --format is required; only multi-card formats are
supported (pauper, modern, vintage, legacy).

Usage:
    python src/analysis/query.py "Lightning Bolt" --format pauper
    python src/analysis/query.py "Lightning Bolt" --format modern --sort jaccard --limit 20
    python src/analysis/query.py "Lightning Bolt" --format legacy --sort pmi

Reads DATABASE_URL from src/distributed scraper/.env automatically.
"""

import argparse
import difflib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import psycopg2

from constants.env import load_env
from constants.mtg import REGULAR_FORMATS, format_to_table_prefix

SORT_CHOICES = ["lift", "pmi", "jaccard", "confidence", "cooccurrence_count"]

_VALID_SORT_COLS: frozenset[str] = frozenset(SORT_CHOICES)


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------

def get_canonical_name(conn, card_name: str, fmt: str) -> str | None:
    """Return the DB-canonical casing for card_name in fmt (case-insensitive), or None."""
    prefix = format_to_table_prefix(fmt)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT card_name FROM {prefix}_card_stats WHERE lower(card_name) = lower(%s) LIMIT 1",
            (card_name,)
        )
        row = cur.fetchone()
    return row[0] if row else None


def fuzzy_candidates(conn, card_name: str, fmt: str, n: int = 3) -> list[str]:
    prefix = format_to_table_prefix(fmt)
    with conn.cursor() as cur:
        cur.execute(f"SELECT card_name FROM {prefix}_card_stats")
        all_names = [r[0] for r in cur.fetchall()]
    return difflib.get_close_matches(card_name, all_names, n=n, cutoff=0.6)


def prompt_fuzzy_selection(candidates: list[str]) -> str | None:
    print("\nCard not found. Did you mean:")
    for i, name in enumerate(candidates, 1):
        print(f"  [{i}] {name}")
    print("  [0] Cancel")

    while True:
        try:
            raw = input("\nSelect an option: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw == "0":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(candidates):
            return candidates[int(raw) - 1]
        print(f"  Please enter a number between 0 and {len(candidates)}.")


def resolve_card_name(conn, card_name: str, fmt: str) -> str | None:
    """
    Return the canonical card name to use within fmt. If the input matches
    case-insensitively, return the DB-canonical casing. Otherwise offer fuzzy
    candidates from that format's stats and return the selection, or None if
    the user cancels or no candidates exist.
    """
    canonical = get_canonical_name(conn, card_name, fmt)
    if canonical:
        return canonical

    candidates = fuzzy_candidates(conn, card_name, fmt)
    if not candidates:
        print(f"No card matching '{card_name}' found in {fmt}, and no close matches exist.")
        print("Have you run refresh_stats.py for this format?")
        return None

    return prompt_fuzzy_selection(candidates)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def card_stats(conn, card_name: str, fmt: str) -> list[dict]:
    prefix = format_to_table_prefix(fmt)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT deck_count, total_decks, inclusion_rate, avg_quantity
            FROM {prefix}_card_stats
            WHERE card_name = %s
        """, (card_name,))
        row = cur.fetchone()
    if not row:
        return []
    return [{"format": fmt, "deck_count": row[0], "total_decks": row[1],
             "inclusion_rate": row[2], "avg_quantity": row[3]}]


def pair_stats(
    conn,
    card_name: str,
    fmt: str,
    sort_by: str,
    limit: int,
) -> list[dict]:
    if sort_by not in _VALID_SORT_COLS:
        raise ValueError(f"Invalid sort column: {sort_by!r}")
    prefix = format_to_table_prefix(fmt)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                CASE WHEN card_a = %s THEN card_b ELSE card_a END AS partner,
                cooccurrence_count, lift, pmi, jaccard,
                CASE WHEN card_a = %s THEN confidence_a_to_b ELSE confidence_b_to_a END AS confidence
            FROM {prefix}_card_pair_stats
            WHERE (card_a = %s OR card_b = %s)
            ORDER BY {sort_by} DESC
            LIMIT %s
        """, [card_name, card_name, card_name, card_name, limit])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_card_stats(rows: list[dict], card_name: str) -> None:
    if not rows:
        print(f"  No stats found for '{card_name}'. Have you run refresh_stats.py?")
        return
    print(f"  {'Format':<18} {'Decks':>6}  {'Total':>6}  {'Inclusion':>10}  {'Avg qty':>8}")
    print(f"  {'-'*18}  {'-'*6}  {'-'*6}  {'-'*10}  {'-'*8}")
    for r in rows:
        print(
            f"  {r['format']:<18} {r['deck_count']:>6}  {r['total_decks']:>6}"
            f"  {r['inclusion_rate']:>9.1%}  {r['avg_quantity']:>8.2f}"
        )


def print_pair_stats(rows: list[dict], card_name: str, sort_by: str) -> None:
    if not rows:
        print("  No pairs found (threshold not met or stats not yet computed).")
        return
    print(
        f"  {'Partner':<35} {'Cooccur':>7}"
        f"  {'Lift':>6}  {'PMI':>6}  {'Jaccard':>7}  {'Conf':>6}"
    )
    print(f"  {'-'*35}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*6}")
    for r in rows:
        print(
            f"  {r['partner']:<35} {r['cooccurrence_count']:>7}"
            f"  {r['lift']:>6.2f}  {r['pmi']:>6.2f}  {r['jaccard']:>7.3f}  {r['confidence']:>6.2f}"
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def lookup(
    conn,
    card_name: str,
    fmt: str,
    sort_by: str = "lift",
    limit: int = 30,
) -> None:
    resolved = resolve_card_name(conn, card_name, fmt)
    if resolved is None:
        sys.exit(1)

    if resolved != card_name:
        print(f"Using '{resolved}'.")

    print(f"\n=== {resolved}  [{fmt}] ===\n")

    print("Inclusion stats:")
    cstats = card_stats(conn, resolved, fmt)
    print_card_stats(cstats, resolved)

    print(f"\nTop {limit} by co-occurrence, sorted by {sort_by}:")
    pstats = pair_stats(conn, resolved, fmt, sort_by, limit)
    print_pair_stats(pstats, resolved, sort_by)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Look up co-occurrence stats for a card.")
    parser.add_argument("card", help="Card name to look up (case-insensitive)")
    parser.add_argument("--format", "-f", dest="format", required=True,
                        help="Format to query (required — pair stats are per-format only)")
    parser.add_argument("--sort", "-s", dest="sort", default="lift",
                        choices=SORT_CHOICES,
                        help="Metric to sort co-occurring cards by (default: lift)")
    parser.add_argument("--limit", "-n", dest="limit", type=int, default=30,
                        help="Number of pairs to show (default: 30)")
    args = parser.parse_args()

    load_env()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL not set. Fill in src/distributed scraper/.env and retry.")
        sys.exit(1)

    if args.format not in REGULAR_FORMATS:
        print(f"ERROR: '{args.format}' is not a supported format.")
        print(f"  Supported: {', '.join(REGULAR_FORMATS)}")
        sys.exit(1)

    conn = psycopg2.connect(pg_url)
    try:
        lookup(conn, args.card, args.format, args.sort, args.limit)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
