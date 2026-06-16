"""
query.py - Look up co-occurrence stats for a specific card.

Usage:
    python src/analysis/query.py "Lightning Bolt"
    python src/analysis/query.py "Lightning Bolt" --format pauper
    python src/analysis/query.py "Lightning Bolt" --sort jaccard --limit 20
    python src/analysis/query.py "Lightning Bolt" --format pauper --sort pmi
"""

import argparse
import difflib
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parents[1] / "data" / "decks.db"

SORT_CHOICES = ["lift", "pmi", "jaccard", "confidence", "cooccurrence_count"]


def get_canonical_name(conn: sqlite3.Connection, card_name: str) -> str | None:
    """Return the DB-canonical casing for card_name (case-insensitive lookup), or None."""
    row = conn.execute(
        "SELECT card_name FROM card_stats WHERE card_name = ? COLLATE NOCASE LIMIT 1",
        (card_name,)
    ).fetchone()
    return row[0] if row else None


def fuzzy_candidates(conn: sqlite3.Connection, card_name: str, n: int = 3) -> list[str]:
    all_names = [r[0] for r in conn.execute("SELECT DISTINCT card_name FROM card_stats")]
    return difflib.get_close_matches(card_name, all_names, n=n, cutoff=0.6)


def prompt_fuzzy_selection(candidates: list[str]) -> str | None:
    """Present up to 3 candidates and return the chosen name, or None to abort."""
    print(f"\nCard not found. Did you mean:")
    for i, name in enumerate(candidates, 1):
        print(f"  [{i}] {name}")
    print(f"  [0] Cancel")

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


def resolve_card_name(conn: sqlite3.Connection, card_name: str) -> str | None:
    """
    Return the canonical card name to use. If the input matches (case-insensitively),
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

    chosen = prompt_fuzzy_selection(candidates)
    return chosen


def card_stats(conn: sqlite3.Connection, card_name: str, fmt: str | None) -> list[dict]:
    query = """
        SELECT format, deck_count, total_decks, inclusion_rate, avg_quantity
        FROM card_stats
        WHERE card_name = ? COLLATE NOCASE
    """
    params = [card_name]
    if fmt:
        query += " AND format = ?"
        params.append(fmt)
    query += " ORDER BY inclusion_rate DESC"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def pair_stats(
    conn: sqlite3.Connection,
    card_name: str,
    fmt: str | None,
    sort_by: str,
    limit: int,
) -> list[dict]:
    sort_col = "confidence_a_to_b" if sort_by == "confidence" else sort_by
    query = f"""
        SELECT partner, format, cooccurrence_count, lift, pmi, jaccard, confidence
        FROM (
            SELECT
                CASE WHEN card_a = :name COLLATE NOCASE THEN card_b ELSE card_a END AS partner,
                format,
                cooccurrence_count,
                lift,
                pmi,
                jaccard,
                CASE WHEN card_a = :name COLLATE NOCASE
                     THEN confidence_a_to_b
                     ELSE confidence_b_to_a END AS confidence
            FROM card_pair_stats
            WHERE (card_a = :name COLLATE NOCASE OR card_b = :name COLLATE NOCASE)
            {"AND format = :fmt" if fmt else ""}
            ORDER BY cooccurrence_count DESC
            LIMIT :limit
        )
        ORDER BY {sort_col} DESC
    """
    params = {"name": card_name, "limit": limit}
    if fmt:
        params["fmt"] = fmt
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


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
        print(f"  No pairs found (threshold not met or stats not yet computed).")
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


def lookup(
    card_name: str,
    fmt: str | None = None,
    sort_by: str = "lift",
    limit: int = 30,
    db_path: Path = DB_PATH,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    resolved = resolve_card_name(conn, card_name)
    if resolved is None:
        conn.close()
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

    conn.close()


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
    parser.add_argument("--db", dest="db_path", default=str(DB_PATH))
    args = parser.parse_args()

    lookup(
        card_name=args.card,
        fmt=args.format,
        sort_by=args.sort,
        limit=args.limit,
        db_path=Path(args.db_path),
    )


if __name__ == "__main__":
    main()
