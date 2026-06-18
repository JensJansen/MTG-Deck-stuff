"""
precompute_layout.py - Compute UMAP 2D layout for card visualization.

Builds a sparse co-occurrence matrix (cards × cards, weighted by Jaccard),
runs UMAP to project to 2D, and stores (x, y, color_identity) in card_layout.

Usage:
    python src/analysis/precompute_layout.py --format pauper
    python src/analysis/precompute_layout.py
    python src/analysis/precompute_layout.py --min-decks 10 --min-cooccur 10

Reads DATABASE_URL from src/distributed scraper/.env automatically.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import psycopg2
import umap
from scipy.sparse import csr_matrix

from constants.env import load_env

DEFAULT_MIN_DECKS   = 5
DEFAULT_MIN_COOCCUR = 20


def get_stats_formats(conn, fmt_filter: str | None) -> list[str]:
    if fmt_filter:
        return [fmt_filter]
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT format FROM card_stats ORDER BY format")
        return [r[0] for r in cur.fetchall()]


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

def run(conn, formats: list[str], min_decks: int, min_cooccur: int) -> None:
    for fmt in formats:
        print(f"\n[{fmt}]")

        with conn.cursor() as cur:
            cur.execute("SELECT * FROM get_layout_cards(%s, %s)", (fmt, min_decks))
            card_rows = cur.fetchall()

        if not card_rows:
            print("  No cards meet the threshold — run compute_stats.py first.")
            continue
        print(f"  {len(card_rows)} cards (deck_count >= {min_decks}, legal_{fmt} = 'legal')")

        color_ids = {r[0]: r[2] for r in card_rows}

        with conn.cursor() as cur:
            cur.execute("SELECT * FROM get_layout_pairs(%s, %s, %s)", (fmt, min_decks, min_cooccur))
            pairs = cur.fetchall()
        print(f"  {len(pairs)} pairs (cooccurrence >= {min_cooccur})")

        if len(card_rows) < 2:
            print("  Too few cards for layout, skipping.")
            continue

        card_list = sorted(color_ids)
        print(f"  Running UMAP (n_neighbors={min(15, len(card_list)-1)}, min_dist=0.05)...",
              end=" ", flush=True)
        pos = compute_umap_layout(card_list, pairs)
        print("done")

        layout = [
            {"card_name": card, "x": pos[card][0], "y": pos[card][1], "color_identity": color_ids.get(card, "")}
            for card in card_list
            if card in pos
        ]
        with conn.cursor() as cur:
            cur.execute("CALL store_card_layout(%s, %s)", (fmt, json.dumps(layout)))
        conn.commit()
        print(f"  Stored layout for {len(layout)} cards.")


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
    args = parser.parse_args()

    load_env()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL not set. Fill in src/distributed scraper/.env and retry.")
        return

    conn = psycopg2.connect(pg_url)

    try:
        formats = get_stats_formats(conn, args.format)
        if not formats:
            print("No formats found — run compute_stats.py first.")
            return

        print(f"Formats: {', '.join(formats)}")
        print(f"Min decks: {args.min_decks}  |  Min cooccur: {args.min_cooccur}")

        run(conn, formats, args.min_decks, args.min_cooccur)
    finally:
        conn.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
