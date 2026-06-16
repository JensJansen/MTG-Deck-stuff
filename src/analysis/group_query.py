"""
group_query.py - Co-occurrence stats for a group of cards treated as a unit.

Finds all decks (within a format) that contain every card in the group, then
computes lift, PMI, Jaccard, and confidence for every other card that appears
in those decks.

Usage:
    python src/analysis/group_query.py --format pauper
    python src/analysis/group_query.py --format commander --db path/to/decks.db
"""

import argparse
import math
import sqlite3
import sys
from pathlib import Path

from query import DB_PATH as DEFAULT_DB_PATH, resolve_card_name

# Boards treated as part of the constructed deck
DEFAULT_BOARDS = frozenset({"mainboard", "commanders", "companions", "signatureSpells"})

DISPLAY_LIMIT = 30
CHUNK_SIZE    = 500


def _get_card_ids(conn: sqlite3.Connection, card_names: list[str]) -> dict[str, int]:
    """Return {card_name: id}, chunked to stay within SQLite's variable limit."""
    result: dict[str, int] = {}
    for i in range(0, len(card_names), CHUNK_SIZE):
        chunk = card_names[i:i + CHUNK_SIZE]
        phs = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT card_name, id FROM cards WHERE card_name IN ({phs})",
            chunk
        ).fetchall()
        for name, card_id in rows:
            result[name] = card_id
    return result


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------

def get_total_decks(conn: sqlite3.Connection, fmt: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM decks WHERE format = ?", (fmt,)
    ).fetchone()[0]


def find_group_deck_ids(
    conn: sqlite3.Connection,
    fmt: str,
    group: set[str],
    boards: frozenset[str],
) -> list[str]:
    """Return deck IDs (in the given format) that contain every card in group."""
    name_to_id = _get_card_ids(conn, list(group))
    if len(name_to_id) < len(group):
        return []
    card_ids = list(name_to_id.values())

    board_phs = ",".join("?" * len(boards))
    id_phs    = ",".join("?" * len(card_ids))

    rows = conn.execute(f"""
        SELECT dc.deck_id
        FROM deck_cards dc
        JOIN decks d ON dc.deck_id = d.public_id
        WHERE d.format = ?
          AND dc.board IN ({board_phs})
          AND dc.card_id IN ({id_phs})
        GROUP BY dc.deck_id
        HAVING COUNT(DISTINCT dc.card_id) = ?
    """, [fmt, *boards, *card_ids, len(card_ids)]).fetchall()

    return [r[0] for r in rows]


def cooccurrence_counts(
    conn: sqlite3.Connection,
    group_deck_ids: list[str],
    exclude: set[str],
    boards: frozenset[str],
) -> dict[str, int]:
    """For each card (not in exclude) that appears in any group deck, return count of group decks it appears in."""
    if not group_deck_ids:
        return {}

    excl_ids   = set(_get_card_ids(conn, list(exclude)).values())
    board_phs  = ",".join("?" * len(boards))
    excl_phs   = ",".join("?" * len(excl_ids)) if excl_ids else None

    counts: dict[str, int] = {}

    for i in range(0, len(group_deck_ids), CHUNK_SIZE):
        chunk    = group_deck_ids[i:i + CHUNK_SIZE]
        deck_phs = ",".join("?" * len(chunk))

        params: list = [*chunk, *boards]
        if excl_ids:
            params.extend(sorted(excl_ids))

        excl_clause = f"AND dc.card_id NOT IN ({excl_phs})" if excl_ids else ""

        rows = conn.execute(f"""
            SELECT c.card_name, COUNT(DISTINCT dc.deck_id) AS cooccur
            FROM deck_cards dc
            JOIN cards c ON dc.card_id = c.id
            WHERE dc.deck_id IN ({deck_phs})
              AND dc.board IN ({board_phs})
              {excl_clause}
            GROUP BY dc.card_id
        """, params).fetchall()

        for name, cnt in rows:
            counts[name] = counts.get(name, 0) + cnt

    return counts


def per_card_deck_counts(
    conn: sqlite3.Connection,
    fmt: str,
    card_names: list[str],
    boards: frozenset[str],
) -> dict[str, int]:
    """Return total deck count per card across the whole format."""
    if not card_names:
        return {}

    name_to_id = _get_card_ids(conn, card_names)
    id_to_name = {v: k for k, v in name_to_id.items()}
    card_ids   = list(name_to_id.values())

    board_phs = ",".join("?" * len(boards))
    counts: dict[str, int] = {}

    for i in range(0, len(card_ids), CHUNK_SIZE):
        chunk  = card_ids[i:i + CHUNK_SIZE]
        id_phs = ",".join("?" * len(chunk))

        rows = conn.execute(f"""
            SELECT dc.card_id, COUNT(DISTINCT dc.deck_id) AS cnt
            FROM deck_cards dc
            JOIN decks d ON dc.deck_id = d.public_id
            WHERE d.format = ?
              AND dc.board IN ({board_phs})
              AND dc.card_id IN ({id_phs})
            GROUP BY dc.card_id
        """, [fmt, *boards, *chunk]).fetchall()

        for card_id, cnt in rows:
            name = id_to_name.get(card_id)
            if name:
                counts[name] = cnt

    return counts


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def compute_group_stats(
    cooccur: dict[str, int],
    card_totals: dict[str, int],
    n_group_decks: int,
    n_total_decks: int,
) -> list[dict]:
    p_group = n_group_decks / n_total_decks
    rows = []

    for card, cooccur_count in cooccur.items():
        card_total = card_totals.get(card, 0)
        if card_total == 0:
            continue

        p_card  = card_total  / n_total_decks
        p_joint = cooccur_count / n_total_decks

        lift       = p_joint / (p_group * p_card)
        pmi        = math.log(p_joint / (p_group * p_card))
        jaccard    = cooccur_count / (n_group_decks + card_total - cooccur_count)
        confidence = cooccur_count / n_group_decks

        rows.append({
            "card_name":          card,
            "cooccurrence_count": cooccur_count,
            "lift":               lift,
            "pmi":                pmi,
            "jaccard":            jaccard,
            "confidence":         confidence,
        })

    top_by_cooccur = sorted(rows, key=lambda r: r["cooccurrence_count"], reverse=True)[:DISPLAY_LIMIT]
    return sorted(top_by_cooccur, key=lambda r: r["lift"], reverse=True)


# ---------------------------------------------------------------------------
# Interactive card input
# ---------------------------------------------------------------------------

def collect_group(conn: sqlite3.Connection, fmt: str) -> set[str]:
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

        resolved = resolve_card_name(conn, raw)
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

def print_results(rows: list[dict], group: set[str], fmt: str, n_group_decks: int, n_total: int) -> None:
    group_label = " + ".join(sorted(group))
    print(f"\n=== Group: [{group_label}]  [{fmt}] ===")
    print(f"    Decks containing full group: {n_group_decks} / {n_total}  ({n_group_decks/n_total:.1%})\n")

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

def run(fmt: str, boards: frozenset[str], db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    n_total = get_total_decks(conn, fmt)
    if n_total == 0:
        print(f"No decks found for format '{fmt}'. Have you scraped data for this format?")
        conn.close()
        sys.exit(1)

    group = collect_group(conn, fmt)

    if not group:
        print("No cards entered. Exiting.")
        conn.close()
        sys.exit(0)

    print(f"\nSearching for decks containing all {len(group)} card(s)...")
    group_deck_ids = find_group_deck_ids(conn, fmt, group, boards)

    if not group_deck_ids:
        print(f"No decks in '{fmt}' contain all of: {sorted(group)}")
        conn.close()
        sys.exit(0)

    cooccur = cooccurrence_counts(conn, group_deck_ids, exclude=group, boards=boards)
    card_totals = per_card_deck_counts(conn, fmt, list(cooccur.keys()), boards)

    results = compute_group_stats(cooccur, card_totals, len(group_deck_ids), n_total)

    print_results(results, group, fmt, len(group_deck_ids), n_total)
    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive group co-occurrence query against raw deck data."
    )
    parser.add_argument("--format", "-f", dest="format", required=True,
                        help="Format to query (required)")
    parser.add_argument("--include-sideboard", dest="include_sideboard", action="store_true",
                        help="Also count sideboard cards")
    parser.add_argument("--db", dest="db_path", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()

    boards = DEFAULT_BOARDS | ({"sideboard"} if args.include_sideboard else set())
    run(args.format, boards, Path(args.db_path))


if __name__ == "__main__":
    main()
