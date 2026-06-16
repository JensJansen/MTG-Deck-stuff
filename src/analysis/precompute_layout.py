"""
precompute_layout.py - Compute UMAP 2D layout for card visualization.

Builds a sparse co-occurrence matrix (cards × cards, weighted by Jaccard),
runs UMAP to project to 2D, and stores (x, y, color_identity) in card_layout.

Usage:
    python src/analysis/precompute_layout.py --format pauper
    python src/analysis/precompute_layout.py
    python src/analysis/precompute_layout.py --min-decks 10 --min-cooccur 10
"""

import argparse
import sqlite3
from pathlib import Path

import umap
from scipy.sparse import csr_matrix

DB_PATH = Path(__file__).parents[1] / "data" / "decks.db"

_COLOR_BITS = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}

DEFAULT_MIN_DECKS   = 5
DEFAULT_MIN_COOCCUR = 20


def decode_colors(mask: int) -> str:
    if not mask:
        return ""
    return ",".join(c for c, bit in _COLOR_BITS.items() if mask & bit)


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def init_layout_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS card_layout (
            card_name      TEXT NOT NULL,
            format         TEXT NOT NULL,
            x              REAL NOT NULL,
            y              REAL NOT NULL,
            color_identity TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (card_name, format)
        );
    """)
    conn.commit()


def get_formats(conn: sqlite3.Connection, fmt_filter: str | None) -> list[str]:
    if fmt_filter:
        return [fmt_filter]
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT format FROM card_stats ORDER BY format"
    )]


def load_cards(conn: sqlite3.Connection, fmt: str, min_decks: int) -> dict[str, int]:
    """
    Returns {card_name: deck_count} for cards that are both above the deck
    threshold AND legal in the given format. Falls back to no legality filter
    if the format has no corresponding legal_{fmt} column.
    """
    legal_col = f"legal_{fmt}"
    has_legal = legal_col in {r[1] for r in conn.execute("PRAGMA table_info(cards)")}

    if has_legal:
        rows = conn.execute(f"""
            SELECT cs.card_name, cs.deck_count
            FROM card_stats cs
            JOIN cards c ON cs.card_name = c.card_name COLLATE NOCASE
            WHERE cs.format = ?
              AND cs.deck_count >= ?
              AND c.{legal_col} = 'legal'
        """, (fmt, min_decks)).fetchall()
    else:
        rows = conn.execute("""
            SELECT card_name, deck_count
            FROM card_stats
            WHERE format = ? AND deck_count >= ?
        """, (fmt, min_decks)).fetchall()

    return {r[0]: r[1] for r in rows}


def load_pairs(
    conn: sqlite3.Connection,
    fmt: str,
    card_set: set[str],
    min_cooccur: int,
) -> list[tuple[str, str, float]]:
    """Returns (card_a, card_b, jaccard) for pairs within card_set."""
    rows = conn.execute("""
        SELECT card_a, card_b, jaccard
        FROM card_pair_stats
        WHERE format = ? AND cooccurrence_count >= ?
    """, (fmt, min_cooccur)).fetchall()
    return [
        (a, b, jac)
        for a, b, jac in rows
        if a in card_set and b in card_set
    ]


def get_color_identities(conn: sqlite3.Connection, card_set: set[str]) -> dict[str, str]:
    """Return the decoded color identity string for each card."""
    placeholders = ",".join("?" * len(card_set))
    rows = conn.execute(
        f"SELECT card_name, ci_mask FROM cards WHERE card_name IN ({placeholders})",
        list(card_set),
    ).fetchall()
    return {r[0]: decode_colors(r[1] or 0) for r in rows}


# ---------------------------------------------------------------------------
# UMAP layout
# ---------------------------------------------------------------------------

def compute_umap_layout(
    card_list: list[str],
    pairs: list[tuple[str, str, float]],
) -> dict[str, tuple[float, float]]:
    """
    Build a sparse Jaccard similarity matrix and project to 2D with UMAP.
    Cards with similar co-occurrence partners end up near each other.
    Returns {card_name: (x, y)}.
    """
    n = len(card_list)
    idx = {name: i for i, name in enumerate(card_list)}

    row_idx, col_idx, vals = [], [], []
    for a, b, jac in pairs:
        i, j = idx[a], idx[b]
        row_idx.extend([i, j])
        col_idx.extend([j, i])
        vals.extend([jac, jac])

    X = csr_matrix((vals, (row_idx, col_idx)), shape=(n, n))

    n_neighbors = min(15, n - 1)
    reducer = umap.UMAP(
        n_components=2,
        metric="cosine",
        n_neighbors=n_neighbors,
        min_dist=0.05,
        random_state=42,
        low_memory=True,
        verbose=False,
    )
    embedding = reducer.fit_transform(X)

    return {card_list[i]: (float(embedding[i, 0]), float(embedding[i, 1])) for i in range(n)}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(
    conn: sqlite3.Connection,
    formats: list[str],
    min_decks: int,
    min_cooccur: int,
) -> None:
    init_layout_table(conn)

    for fmt in formats:
        print(f"\n[{fmt}]")

        cards = load_cards(conn, fmt, min_decks)
        if not cards:
            print("  No cards meet the threshold — run compute_stats.py first.")
            continue
        legal_col = f"legal_{fmt}"
        has_legal = legal_col in {r[1] for r in conn.execute("PRAGMA table_info(cards)")}
        legality_note = f", legal_{fmt} = 'legal'" if has_legal else " (no legality column, unfiltered)"
        print(f"  {len(cards)} cards (deck_count >= {min_decks}{legality_note})")

        pairs = load_pairs(conn, fmt, set(cards), min_cooccur)
        print(f"  {len(pairs)} pairs (cooccurrence >= {min_cooccur})")

        if len(cards) < 2:
            print("  Too few cards for layout, skipping.")
            continue

        print(f"  Running UMAP (n_neighbors={min(15, len(cards)-1)}, min_dist=0.05)...",
              end=" ", flush=True)
        card_list = sorted(cards)
        pos = compute_umap_layout(card_list, pairs)
        print("done")

        color_ids = get_color_identities(conn, set(cards))

        conn.execute("DELETE FROM card_layout WHERE format = ?", (fmt,))
        conn.executemany(
            "INSERT INTO card_layout (card_name, format, x, y, color_identity) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (card, fmt, pos[card][0], pos[card][1], color_ids.get(card, ""))
                for card in card_list
                if card in pos
            ],
        )
        conn.commit()
        print(f"  Stored layout for {len(pos)} cards.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Precompute UMAP 2D layout for the card visualization."
    )
    parser.add_argument("--format", "-f", dest="format", default=None,
                        help="Limit to one format (default: all)")
    parser.add_argument("--min-decks", dest="min_decks", type=int,
                        default=DEFAULT_MIN_DECKS,
                        help=f"Minimum deck count to include a card (default: {DEFAULT_MIN_DECKS})")
    parser.add_argument("--min-cooccur", dest="min_cooccur", type=int,
                        default=DEFAULT_MIN_COOCCUR,
                        help=f"Minimum co-occurrence count for layout edges (default: {DEFAULT_MIN_COOCCUR})")
    parser.add_argument("--db", dest="db_path", default=str(DB_PATH))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    formats = get_formats(conn, args.format)
    if not formats:
        print("No formats found — run compute_stats.py first.")
        conn.close()
        return

    print(f"Formats: {', '.join(formats)}")
    print(f"Min decks: {args.min_decks}  |  Min cooccur: {args.min_cooccur}")

    try:
        run(conn, formats, args.min_decks, args.min_cooccur)
    finally:
        conn.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
