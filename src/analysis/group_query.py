"""
group_query.py - Co-occurrence stats for a group of cards treated as a unit.

Finds all decks in a format that contain every card in the group, then computes
lift, PMI, Jaccard, and confidence for every other card appearing in those decks.
Queries {format}_deck_cards directly; all metric computation runs in Postgres.
Each deck is treated as an atomic unit — all cards are counted regardless of board.
Only multi-card formats are supported (pauper, modern, vintage, legacy).

Usage:
    python src/analysis/group_query.py --format pauper
    python src/analysis/group_query.py --format modern --sort jaccard --limit 20

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
from query import SORT_CHOICES, resolve_card_name

DEFAULT_LIMIT = 30


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def _get_card_ids(conn, card_names: list[str]) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT card_name, id FROM cards WHERE card_name = ANY(%s)", (card_names,))
        return {r[0]: r[1] for r in cur.fetchall()}


def query_group_stats(
    conn,
    fmt: str,
    group: set[str],
    sort_by: str,
    limit: int,
    n_total: int,
) -> tuple[int, list[dict]]:
    """
    Returns (n_group, result_rows).

    n_total  = done deck count passed in from the caller (avoids a redundant scan).
    n_group  = decks containing every card in the group.
    result_rows is empty when n_group = 0 or no cards co-occur above threshold.
    """
    if sort_by not in SORT_CHOICES:
        raise ValueError(f"Invalid sort column: {sort_by!r}")

    name_to_id = _get_card_ids(conn, list(group))
    if len(name_to_id) < len(group):
        missing = group - set(name_to_id)
        print(f"  [warn] cards not found in DB: {sorted(missing)}")
        return 0, []

    group_ids = list(name_to_id.values())
    dc_table  = f"{fmt}_deck_cards"

    # Round-trip 1: n_group count only — n_total comes from the caller.
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT deck_id
                FROM {dc_table}
                WHERE card_id = ANY(%s)
                GROUP BY deck_id
                HAVING COUNT(DISTINCT card_id) = %s
            ) _gd
        """, (group_ids, len(group_ids)))
        n_group = cur.fetchone()[0]

    if n_group == 0:
        return 0, []

    # Round-trip 2: full co-occurrence stats computed in Postgres.
    with conn.cursor() as cur:
        cur.execute(f"""
            WITH
            group_decks AS (
                SELECT deck_id
                FROM {dc_table}
                WHERE card_id = ANY(%s)
                GROUP BY deck_id
                HAVING COUNT(DISTINCT card_id) = %s
            ),
            n_group AS (SELECT COUNT(*) AS n FROM group_decks),
            cooccur AS (
                SELECT dc.card_id, COUNT(DISTINCT dc.deck_id) AS cooccurrence_count
                FROM {dc_table} dc
                JOIN group_decks gd ON dc.deck_id = gd.deck_id
                WHERE NOT (dc.card_id = ANY(%s))
                GROUP BY dc.card_id
            ),
            card_totals AS (
                SELECT dc.card_id, COUNT(DISTINCT dc.deck_id) AS total_count
                FROM {dc_table} dc
                JOIN cooccur co ON dc.card_id = co.card_id
                GROUP BY dc.card_id
            )
            SELECT
                c.card_name,
                co.cooccurrence_count,
                (co.cooccurrence_count::float * nt.n) / (ng.n::float * ct.total_count)         AS lift,
                LN((co.cooccurrence_count::float * nt.n) / (ng.n::float * ct.total_count))     AS pmi,
                co.cooccurrence_count::float / (ng.n + ct.total_count - co.cooccurrence_count) AS jaccard,
                co.cooccurrence_count::float / ng.n                                             AS confidence
            FROM cooccur co
            JOIN card_totals ct ON co.card_id = ct.card_id
            JOIN cards c        ON co.card_id = c.id
            CROSS JOIN (SELECT %s::bigint AS n) nt
            CROSS JOIN n_group ng
            ORDER BY {sort_by} DESC
            LIMIT %s
        """, (
            group_ids, len(group_ids),   # group_decks
            group_ids,                   # cooccur exclusion
            n_total,                     # nt literal
            limit,
        ))
        cols = [d[0] for d in cur.description]
        return n_group, [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Interactive card input
# ---------------------------------------------------------------------------

def collect_group(conn, fmt: str) -> set[str]:
    """Interactively collect card names into a group. Returns the final set."""
    group: set[str] = set()

    print(f"\nEnter cards one at a time (format: {fmt}).")
    print("Press Enter on an empty line when done.\n")

    while True:
        try:
            raw = input(f"  Card [{len(group)} in group]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if raw == "":
            break

        resolved = resolve_card_name(conn, raw, fmt)
        if resolved is None:
            continue

        if resolved in group:
            print(f"  '{resolved}' is already in the group.")
            continue

        group.add(resolved)
        print(f"  Added. Group: {sorted(group)}")

    return group


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_results(
    rows: list[dict],
    group: set[str],
    fmt: str,
    n_group: int,
    n_total: int,
    sort_by: str,
) -> None:
    group_label = " + ".join(sorted(group))
    print(f"\n=== Group: [{group_label}]  [{fmt}] ===")
    print(f"    Decks containing full group: {n_group} / {n_total}  ({n_group/n_total:.1%})")
    print(f"    Sorted by: {sort_by}\n")

    if not rows:
        print("  No co-occurring cards found.")
        return

    print(
        f"  {'Card':<35} {'Cooccur':>7}  {'Lift':>6}  {'PMI':>6}  {'Jaccard':>7}  {'Conf':>6}"
    )
    print(f"  {'-'*35}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*6}")
    for r in rows:
        print(
            f"  {r['card_name']:<35} {r['cooccurrence_count']:>7}"
            f"  {r['lift']:>6.2f}  {r['pmi']:>6.2f}"
            f"  {r['jaccard']:>7.3f}  {r['confidence']:>6.2f}"
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(conn, fmt: str, sort_by: str, limit: int) -> None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {fmt}_decks WHERE status = 'done'")
        n_total = cur.fetchone()[0]
    if n_total == 0:
        print(f"No processed decks found for format '{fmt}'. Have you scraped data?")
        sys.exit(1)

    group = collect_group(conn, fmt)
    if not group:
        print("No cards entered. Exiting.")
        sys.exit(0)

    print(f"\nSearching for decks containing all {len(group)} card(s)...")
    n_group, rows = query_group_stats(conn, fmt, group, sort_by, limit, n_total)

    if n_group == 0:
        print(f"No decks in '{fmt}' contain all of: {sorted(group)}")
        sys.exit(0)

    print_results(rows, group, fmt, n_group, n_total, sort_by)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive group co-occurrence query against raw deck data."
    )
    parser.add_argument("--format", "-f", dest="format", required=True,
                        help=f"Format to query. Choices: {', '.join(REGULAR_FORMATS)}")
    parser.add_argument("--sort", "-s", dest="sort", default="lift",
                        choices=SORT_CHOICES,
                        help="Metric to sort results by (default: lift)")
    parser.add_argument("--limit", "-n", dest="limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Number of results to show (default: {DEFAULT_LIMIT})")
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
        run(conn, args.format, args.sort, args.limit)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
