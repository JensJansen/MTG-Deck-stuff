"""
query.py - Look up co-occurrence stats for a specific card.

Usage:
    python src/analysis/query.py "Lightning Bolt"
    python src/analysis/query.py "Lightning Bolt" --format pauper
    python src/analysis/query.py "Lightning Bolt" --sort jaccard --limit 20
    python src/analysis/query.py "Lightning Bolt" --format pauper --sort pmi

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

SORT_CHOICES = ["lift", "pmi", "jaccard", "confidence", "cooccurrence_count"]

_SORT_COL_SAFE: dict[str, str] = {c: c for c in SORT_CHOICES}


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------

def get_canonical_name(conn, card_name: str) -> str | None:
    """Return the DB-canonical casing for card_name (case-insensitive), or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT card_name FROM card_stats WHERE lower(card_name) = lower(%s) LIMIT 1",
            (card_name,)
        )
        row = cur.fetchone()
    return row[0] if row else None


def fuzzy_candidates(conn, card_name: str, n: int = 3) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT card_name FROM card_stats")
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


def resolve_card_name(conn, card_name: str) -> str | None:
    """
    Return the canonical card name to use. If the input matches case-insensitively,
    return the DB-canonical casing. Otherwise offer fuzzy candidates and return the
    selection, or None if the user cancels / no candidates exist.
    """
    canonical = get_canonical_name(conn, card_name)
    if canonical:
        return canonical

    candidates = fuzzy_candidates(conn, card_name)
    if not candidates:
        print(f"No card matching '{card_name}' found, and no close matches exist.")
        print("Have you run compute_stats.py with enough data scraped?")
        return None

    return prompt_fuzzy_selection(candidates)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def card_stats(conn, card_name: str, fmt: str | None) -> list[dict]:
    params: list = [card_name]
    fmt_clause = ""
    if fmt:
        fmt_clause = "AND format = %s"
        params.append(fmt)

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT format, deck_count, total_decks, inclusion_rate, avg_quantity
            FROM card_stats
            WHERE card_name = %s
            {fmt_clause}
            ORDER BY inclusion_rate DESC
        """, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def pair_stats(
    conn,
    card_name: str,
    fmt: str | None,
    sort_by: str,
    limit: int,
) -> list[dict]:
    fmt_clause = "AND format = %s" if fmt else ""

    # card_name repeated 4x: two CASE expressions + two WHERE conditions
    params: list = [card_name, card_name, card_name, card_name]
    if fmt:
        params.append(fmt)
    params.append(limit)

    col = _SORT_COL_SAFE[sort_by]
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                CASE WHEN card_a = %s THEN card_b ELSE card_a END AS partner,
                format,
                cooccurrence_count,
                lift,
                pmi,
                jaccard,
                CASE WHEN card_a = %s
                     THEN confidence_a_to_b
                     ELSE confidence_b_to_a END AS confidence
            FROM card_pair_stats
            WHERE (card_a = %s OR card_b = %s)
            {fmt_clause}
            ORDER BY {col} DESC
            LIMIT %s
        """, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_card_stats(rows: list[dict], card_name: str) -> None:
    if not rows:
        print(f"  No stats found for '{card_name}'. Have you run compute_stats.py?")
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
        f"  {'Partner':<35} {'Fmt':<16} {'Cooccur':>7}"
        f"  {'Lift':>6}  {'PMI':>6}  {'Jaccard':>7}  {'Conf':>6}"
    )
    print(f"  {'-'*35}  {'-'*16}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*6}")
    for r in rows:
        print(
            f"  {r['partner']:<35} {r['format']:<16} {r['cooccurrence_count']:>7}"
            f"  {r['lift']:>6.2f}  {r['pmi']:>6.2f}  {r['jaccard']:>7.3f}  {r['confidence']:>6.2f}"
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def lookup(
    conn,
    card_name: str,
    fmt: str | None = None,
    sort_by: str = "lift",
    limit: int = 30,
) -> None:
    resolved = resolve_card_name(conn, card_name)
    if resolved is None:
        sys.exit(1)

    if resolved != card_name:
        print(f"Using '{resolved}'.")

    print(f"\n=== {resolved}{f'  [{fmt}]' if fmt else ''} ===\n")

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
    parser.add_argument("--format", "-f", dest="format", default=None,
                        help="Filter to a specific format")
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

    conn = psycopg2.connect(pg_url)
    try:
        lookup(conn, args.card, args.format, args.sort, args.limit)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
