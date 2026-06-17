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
import os
from pathlib import Path

import psycopg2
import psycopg2.extras
import umap
from scipy.sparse import csr_matrix

_COLOR_BITS = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}

DEFAULT_MIN_DECKS   = 5
DEFAULT_MIN_COOCCUR = 20


def decode_colors(mask: int) -> str:
    if not mask:
        return ""
    return ",".join(c for c, bit in _COLOR_BITS.items() if mask & bit)


# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

_ENV_FILE = Path(__file__).parents[1] / "distributed scraper" / ".env"

_ENV_TEMPLATE = """\
DATABASE_URL=postgresql://postgres:yourpassword@localhost/deckgen
API_KEY=your-api-key
SCRAPER_API_URL=http://127.0.0.1:8000
"""


def _load_env() -> None:
    if not _ENV_FILE.exists():
        _ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ENV_FILE.write_text(_ENV_TEMPLATE)
        print(f"Created {_ENV_FILE} with placeholder values — please fill in real credentials.")
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def get_formats(conn, fmt_filter: str | None) -> list[str]:
    if fmt_filter:
        return [fmt_filter]
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT format FROM card_stats ORDER BY format")
        return [r[0] for r in cur.fetchall()]


def load_cards(conn, fmt: str, min_decks: int) -> dict[str, int]:
    """
    Returns {card_name: deck_count} for cards above the deck threshold
    that are legal in the given format.
    """
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT cs.card_name, cs.deck_count
            FROM card_stats cs
            JOIN cards c ON cs.card_name = c.card_name
            WHERE cs.format = %s
              AND cs.deck_count >= %s
              AND c.legal_{fmt} = 'legal'
        """, (fmt, min_decks))
        return {r[0]: r[1] for r in cur.fetchall()}


def load_pairs(
    conn,
    fmt: str,
    card_set: set[str],
    min_cooccur: int,
) -> list[tuple[str, str, float]]:
    """Returns (card_a, card_b, jaccard) for pairs within card_set."""
    card_list = list(card_set)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT card_a, card_b, jaccard
            FROM card_pair_stats
            WHERE format = %s
              AND cooccurrence_count >= %s
              AND card_a = ANY(%s)
              AND card_b = ANY(%s)
        """, (fmt, min_cooccur, card_list, card_list))
        return cur.fetchall()


def get_color_identities(conn, card_set: set[str]) -> dict[str, str]:
    """Return the decoded color identity string for each card."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT card_name, ci_mask FROM cards WHERE card_name = ANY(%s)",
            (list(card_set),)
        )
        return {r[0]: decode_colors(r[1] or 0) for r in cur.fetchall()}


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

        cards = load_cards(conn, fmt, min_decks)
        if not cards:
            print("  No cards meet the threshold — run compute_stats.py first.")
            continue
        print(f"  {len(cards)} cards (deck_count >= {min_decks}, legal_{fmt} = 'legal')")

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

        with conn.cursor() as cur:
            cur.execute("DELETE FROM card_layout WHERE format = %s", (fmt,))
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO card_layout (card_name, format, x, y, color_identity) VALUES %s",
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
    args = parser.parse_args()

    _load_env()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL not set. Fill in src/distributed scraper/.env and retry.")
        return

    conn = psycopg2.connect(pg_url)

    try:
        formats = get_formats(conn, args.format)
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
