"""
compute_stats.py - Precomputes per-card and per-pair co-occurrence statistics.

Reads from decks.db and writes two tables:
  card_stats       - per-card frequency and inclusion rate, per format
  card_pair_stats  - co-occurrence metrics for every pair seen >= MIN_COOCCUR times

Usage:
    python src/analysis/compute_stats.py
    python src/analysis/compute_stats.py --min-cooccur 10
    python src/analysis/compute_stats.py --format commander
    python src/analysis/compute_stats.py --db path/to/decks.db
"""

import argparse
import itertools
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parents[1] / "data" / "decks.db"

# Boards treated as part of the constructed deck (excludes maybeboard / sideboard)
DEFAULT_BOARDS = frozenset({"mainboard", "commanders", "companions", "signatureSpells"})

DEFAULT_MIN_COOCCUR = 5


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

def init_stats_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS card_stats (
            card_name      TEXT    NOT NULL,
            format         TEXT    NOT NULL,
            deck_count     INTEGER NOT NULL,
            total_decks    INTEGER NOT NULL,
            inclusion_rate REAL    NOT NULL,
            avg_quantity   REAL    NOT NULL,
            PRIMARY KEY (card_name, format)
        );

        CREATE TABLE IF NOT EXISTS card_pair_stats (
            card_a              TEXT    NOT NULL,
            card_b              TEXT    NOT NULL,
            format              TEXT    NOT NULL,
            cooccurrence_count  INTEGER NOT NULL,
            lift                REAL    NOT NULL,
            pmi                 REAL    NOT NULL,
            jaccard             REAL    NOT NULL,
            confidence_a_to_b   REAL    NOT NULL,
            confidence_b_to_a   REAL    NOT NULL,
            PRIMARY KEY (card_a, card_b, format)
        );

        CREATE INDEX IF NOT EXISTS idx_pair_card_a  ON card_pair_stats(card_a, format);
        CREATE INDEX IF NOT EXISTS idx_pair_card_b  ON card_pair_stats(card_b, format);
        CREATE INDEX IF NOT EXISTS idx_pair_lift    ON card_pair_stats(lift    DESC);
        CREATE INDEX IF NOT EXISTS idx_pair_jaccard ON card_pair_stats(jaccard DESC);
        CREATE INDEX IF NOT EXISTS idx_pair_pmi     ON card_pair_stats(pmi     DESC);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_decks_for_format(
    conn: sqlite3.Connection,
    fmt: str,
    boards: frozenset[str],
) -> dict[str, dict[str, int]]:
    """
    Returns {deck_id: {card_name: quantity}} for a single format,
    restricted to the given boards.
    """
    placeholders = ",".join("?" * len(boards))
    rows = conn.execute(f"""
        SELECT dc.deck_id, c.card_name, SUM(dc.quantity) AS qty
        FROM deck_cards dc
        JOIN decks d ON dc.deck_id = d.public_id
        JOIN cards c ON dc.card_id = c.id
        WHERE d.format = ?
          AND dc.board IN ({placeholders})
        GROUP BY dc.deck_id, c.card_name
    """, [fmt, *boards]).fetchall()

    decks: dict[str, dict[str, int]] = defaultdict(dict)
    for deck_id, card_name, qty in rows:
        decks[deck_id][card_name] = qty
    return dict(decks)


def get_formats(conn: sqlite3.Connection, fmt_filter: str | None) -> list[str]:
    if fmt_filter:
        return [fmt_filter]
    return [r[0] for r in conn.execute("SELECT DISTINCT format FROM decks ORDER BY format")]


# ---------------------------------------------------------------------------
# Statistics computation
# ---------------------------------------------------------------------------

def compute_for_format(
    decks: dict[str, dict[str, int]],
    fmt: str,
    min_cooccur: int,
) -> tuple[list[dict], list[dict]]:
    """Returns (card_stat_rows, pair_stat_rows) for one format."""
    n_decks = len(decks)
    if n_decks == 0:
        return [], []

    card_deck_count: dict[str, int] = defaultdict(int)
    card_qty_sum:    dict[str, int] = defaultdict(int)
    pair_count: dict[tuple[str, str], int] = defaultdict(int)

    for cards in decks.values():
        unique_names = sorted(cards)
        for name in unique_names:
            card_deck_count[name] += 1
            card_qty_sum[name]    += cards[name]
        for a, b in itertools.combinations(unique_names, 2):
            pair_count[(a, b)] += 1

    card_rows = [
        {
            "card_name":      card,
            "format":         fmt,
            "deck_count":     card_deck_count[card],
            "total_decks":    n_decks,
            "inclusion_rate": card_deck_count[card] / n_decks,
            "avg_quantity":   card_qty_sum[card] / card_deck_count[card],
        }
        for card in card_deck_count
    ]

    pair_rows = []
    for (a, b), cooccur in pair_count.items():
        if cooccur < min_cooccur:
            continue

        count_a = card_deck_count[a]
        count_b = card_deck_count[b]

        p_ab = cooccur / n_decks
        p_a  = count_a / n_decks
        p_b  = count_b / n_decks

        expected = p_a * p_b
        lift    = p_ab / expected
        pmi     = math.log(p_ab / expected)
        jaccard = cooccur / (count_a + count_b - cooccur)
        conf_ab = cooccur / count_a
        conf_ba = cooccur / count_b

        pair_rows.append({
            "card_a":             a,
            "card_b":             b,
            "format":             fmt,
            "cooccurrence_count": cooccur,
            "lift":               lift,
            "pmi":                pmi,
            "jaccard":            jaccard,
            "confidence_a_to_b":  conf_ab,
            "confidence_b_to_a":  conf_ba,
        })

    return card_rows, pair_rows


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

def clear_format_stats(conn: sqlite3.Connection, fmt: str) -> None:
    conn.execute("DELETE FROM card_stats      WHERE format = ?", (fmt,))
    conn.execute("DELETE FROM card_pair_stats WHERE format = ?", (fmt,))


def write_card_stats(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany("""
        INSERT OR REPLACE INTO card_stats
            (card_name, format, deck_count, total_decks, inclusion_rate, avg_quantity)
        VALUES
            (:card_name, :format, :deck_count, :total_decks, :inclusion_rate, :avg_quantity)
    """, rows)


def write_pair_stats(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany("""
        INSERT OR REPLACE INTO card_pair_stats
            (card_a, card_b, format,
             cooccurrence_count, lift, pmi, jaccard,
             confidence_a_to_b, confidence_b_to_a)
        VALUES
            (:card_a, :card_b, :format,
             :cooccurrence_count, :lift, :pmi, :jaccard,
             :confidence_a_to_b, :confidence_b_to_a)
    """, rows)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(conn: sqlite3.Connection, formats: list[str], boards: frozenset[str], min_cooccur: int) -> None:
    init_stats_tables(conn)

    for fmt in formats:
        print(f"\n[{fmt}]")

        decks = load_decks_for_format(conn, fmt, boards)
        print(f"  {len(decks)} decks loaded", end=" ... ", flush=True)

        card_rows, pair_rows = compute_for_format(decks, fmt, min_cooccur)
        print(f"{len(card_rows)} cards, {len(pair_rows)} pairs (cooccur >= {min_cooccur})")

        clear_format_stats(conn, fmt)
        write_card_stats(conn, card_rows)
        write_pair_stats(conn, pair_rows)
        conn.commit()

        if pair_rows:
            _print_sample(pair_rows)


def _print_sample(pair_rows: list[dict]) -> None:
    top = sorted(pair_rows, key=lambda r: r["lift"], reverse=True)[:5]
    print("  top pairs by lift:")
    for r in top:
        print(
            f"    {r['card_a']!r:35s} + {r['card_b']!r:35s}"
            f"  cooccur={r['cooccurrence_count']:3d}"
            f"  lift={r['lift']:.2f}"
            f"  jaccard={r['jaccard']:.3f}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Compute card co-occurrence statistics from scraped deck data.")
    parser.add_argument("--format", "-f", dest="format", default=None,
                        help="Limit computation to one format (default: all formats in DB)")
    parser.add_argument("--min-cooccur", dest="min_cooccur", type=int,
                        default=DEFAULT_MIN_COOCCUR,
                        help=f"Minimum co-occurrence count to store a pair (default: {DEFAULT_MIN_COOCCUR})")
    parser.add_argument("--include-sideboard", dest="include_sideboard", action="store_true",
                        help="Also count sideboard cards when computing statistics")
    parser.add_argument("--db", dest="db_path", default=str(DB_PATH),
                        help=f"Path to SQLite database (default: {DB_PATH})")
    args = parser.parse_args()

    boards = DEFAULT_BOARDS | ({"sideboard"} if args.include_sideboard else set())

    conn = sqlite3.connect(args.db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    formats = get_formats(conn, args.format)
    if not formats:
        print("No matching formats found in the database.")
        return

    print(f"Computing stats for: {', '.join(formats)}")
    print(f"Boards: {', '.join(sorted(boards))}  |  min co-occurrence: {args.min_cooccur}")

    try:
        run(conn, formats, boards, args.min_cooccur)
    finally:
        conn.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
