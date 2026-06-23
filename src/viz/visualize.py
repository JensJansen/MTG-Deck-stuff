"""
visualize.py - Compute UMAP layout and export JSON for the React visualization app.

Reads co-occurrence stats from the database, projects cards to 2D with UMAP,
stores coordinates in {format}_card_layout, then exports JSON files to
src/viz/public/data/ for the React frontend.

Step 1 (per format): reads {format}_card_pair_stats → UMAP → {format}_card_layout
Step 2 (per format): reads {format}_card_layout + stats → JSON export

Supports all formats (pauper, modern, vintage, legacy, commander, highlanderCanadian).
Run compute_stats.py first to populate the stats tables.

Usage:
    python src/viz/visualize.py
    python src/viz/visualize.py --format pauper
    python src/viz/visualize.py --format commander
    python src/viz/visualize.py --format modern --min-decks 10 --min-cooccur 10

Reads DATABASE_URL from src/distributed scraper/.env automatically.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import psycopg2
import umap
from scipy.sparse import csr_matrix

from constants.env import load_env
from constants.moxfield import encode_colors, ALL_FORMATS, REGULAR_FORMATS

_FORMAT_TABLE: dict[str, str] = {"highlanderCanadian": "canadian_highlander"}


def _table_prefix(fmt: str) -> str:
    return _FORMAT_TABLE.get(fmt, fmt)

OUTPUT_DIR = Path(__file__).parent / "public" / "data"

DEFAULT_MIN_DECKS   = 5
DEFAULT_MIN_COOCCUR = 20

EGO_TOP_N         = 50
EGO_MIN_COOCCUR   = 5
GRAPH_MIN_COOCCUR = 20
FOCUS_MIN_COOCCUR = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def categorize_color(color_identity: str) -> str:
    colors = [c.strip() for c in (color_identity or "").split(",") if c.strip()]
    if not colors:
        return "Colorless"
    return colors[0] if len(colors) == 1 else "Multi"


def get_formats(conn, fmt_filter: str | None) -> list[str]:
    fmts = [fmt_filter] if fmt_filter else list(ALL_FORMATS)
    result = []
    for fmt in fmts:
        prefix = _table_prefix(fmt)
        with conn.cursor() as cur:
            cur.execute(f"SELECT EXISTS(SELECT 1 FROM {prefix}_card_stats LIMIT 1)")
            if cur.fetchone()[0]:
                result.append(fmt)
    return result


# ---------------------------------------------------------------------------
# Layout (UMAP)
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


def build_layout(conn, prefix: str, min_decks: int, min_cooccur: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM get_layout_cards(%s, %s)", (prefix, min_decks))
        card_rows = cur.fetchall()

    if not card_rows:
        print("  No cards meet the threshold — run compute_stats.py first.")
        return False

    if len(card_rows) < 2:
        print("  Too few cards for layout, skipping.")
        return False

    print(f"  {len(card_rows)} cards (deck_count >= {min_decks})")

    color_ids = {r[0]: r[2] for r in card_rows}

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM get_layout_pairs(%s, %s, %s)", (prefix, min_decks, min_cooccur))
        pairs = cur.fetchall()
    print(f"  {len(pairs)} pairs (cooccurrence >= {min_cooccur})")

    card_list = sorted(color_ids)
    print(f"  Running UMAP (n_neighbors={min(15, len(card_list)-1)}, min_dist=0.05)...",
          end=" ", flush=True)
    pos = compute_umap_layout(card_list, pairs)
    print("done")

    layout = [
        {"card_name": card, "x": pos[card][0], "y": pos[card][1], "color_identity": color_ids.get(card, "")}
        for card in card_list
    ]
    with conn.cursor() as cur:
        cur.execute("CALL store_card_layout(%s, %s)", (prefix, json.dumps(layout)))
    conn.commit()
    print(f"  Stored layout for {len(layout)} cards.")
    return True


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

def load_nodes(conn, prefix: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                cl.card_name, cl.x, cl.y, cl.color_identity,
                cs.deck_count, cs.total_decks, cs.inclusion_rate, cs.avg_quantity,
                c.image_uri
            FROM {prefix}_card_layout cl
            JOIN {prefix}_card_stats cs ON cl.card_name = cs.card_name
            LEFT JOIN cards c ON cl.card_name = c.card_name
        """)
        rows = cur.fetchall()

    return [
        {
            "name":          r[0],
            "x":             r[1],
            "y":             r[2],
            "color_cat":     categorize_color(r[3]),
            "color_mask":    encode_colors(r[3]),
            "deck_count":    r[4],
            "total_decks":   r[5],
            "inclusion_pct": round(r[6] * 100, 1),
            "avg_qty":       round(r[7], 2),
            "image_uri":     r[8],
        }
        for r in rows
    ]


def _load_pair_rows(
    conn, prefix: str, card_names: set[str], min_cooccur: int
) -> list[tuple]:
    card_list = list(card_names)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT card_a, card_b, cooccurrence_count, lift, jaccard
            FROM {prefix}_card_pair_stats
            WHERE cooccurrence_count >= %s
              AND card_a = ANY(%s) AND card_b = ANY(%s)
            ORDER BY cooccurrence_count DESC
        """, (min_cooccur, card_list, card_list))
        return cur.fetchall()


def load_ego(pair_rows: list[tuple]) -> dict[str, list[dict]]:
    ego: dict[str, list] = defaultdict(list)
    for card_a, card_b, cooccur, lift, jaccard in pair_rows:
        if len(ego[card_a]) < EGO_TOP_N:
            ego[card_a].append({"n": card_b, "c": cooccur, "l": round(lift, 2), "j": round(jaccard, 3)})
        if len(ego[card_b]) < EGO_TOP_N:
            ego[card_b].append({"n": card_a, "c": cooccur, "l": round(lift, 2), "j": round(jaccard, 3)})
    return dict(ego)


def load_edges(conn, prefix: str, name_to_idx: dict[str, int]) -> list[list[int]]:
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT card_a, card_b, jaccard
            FROM {prefix}_card_pair_stats
            WHERE cooccurrence_count >= %s
        """, (GRAPH_MIN_COOCCUR,))
        rows = cur.fetchall()

    edges = []
    for card_a, card_b, jaccard in rows:
        a = name_to_idx.get(card_a)
        b = name_to_idx.get(card_b)
        if a is not None and b is not None:
            edges.append([a, b, round((1.0 - jaccard) * 100)])
    return edges


def load_focus(pair_rows: list[tuple]) -> dict[str, list]:
    """All co-occurring pairs as compact [name, count, lift] tuples."""
    focus: dict[str, list] = defaultdict(list)
    for card_a, card_b, cooccur, lift, _jaccard in pair_rows:
        focus[card_a].append([card_b, cooccur, round(lift, 2)])
        focus[card_b].append([card_a, cooccur, round(lift, 2)])
    return dict(focus)


def export_format(conn, fmt: str, prefix: str, output_dir: Path) -> None:
    nodes = load_nodes(conn, prefix)
    card_names  = {n["name"] for n in nodes}
    name_to_idx = {n["name"]: i for i, n in enumerate(nodes)}

    pair_rows = _load_pair_rows(conn, prefix, card_names, EGO_MIN_COOCCUR)
    ego   = load_ego(pair_rows)
    edges = load_edges(conn, prefix, name_to_idx)

    payload = {
        "format": fmt,
        "nodes":  nodes,
        "ego":    ego,
        "edges":  edges,
    }

    out_path = output_dir / f"{fmt}.json"
    out_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    size_kb = out_path.stat().st_size // 1024
    print(f"  {out_path.name}  ({len(nodes):,} cards, {len(edges):,} edges, {size_kb} KB)")

    focus      = load_focus(pair_rows)
    focus_path = output_dir / f"{fmt}.focus.json"
    focus_path.write_text(json.dumps(focus, separators=(",", ":")), encoding="utf-8")
    focus_kb = focus_path.stat().st_size // 1024
    print(f"  {focus_path.name}  ({len(focus):,} cards with partners, {focus_kb} KB)")


def write_manifest(output_dir: Path, formats: list[str]) -> None:
    manifest = {"formats": formats}
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(conn, formats: list[str], min_decks: int, min_cooccur: int) -> list[str]:
    exported = []
    for fmt in formats:
        prefix = _table_prefix(fmt)
        print(f"\n[{fmt}]")
        if build_layout(conn, prefix, min_decks, min_cooccur):
            export_format(conn, fmt, prefix, OUTPUT_DIR)
            exported.append(fmt)
    return exported


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute UMAP layout and export JSON for the React visualization app."
    )
    parser.add_argument("--format", "-f", dest="format", default=None,
                        help=f"Limit to one format (default: all). Choices: {', '.join(ALL_FORMATS)}")
    parser.add_argument("--min-decks", dest="min_decks", type=int, default=DEFAULT_MIN_DECKS,
                        help=f"Minimum deck count to include a card (default: {DEFAULT_MIN_DECKS})")
    parser.add_argument("--min-cooccur", dest="min_cooccur", type=int, default=DEFAULT_MIN_COOCCUR,
                        help=f"Minimum co-occurrence count for layout edges (default: {DEFAULT_MIN_COOCCUR})")
    args = parser.parse_args()

    if args.format and args.format not in ALL_FORMATS:
        print(f"ERROR: '{args.format}' is not a supported format.")
        print(f"  Supported: {', '.join(ALL_FORMATS)}")
        sys.exit(1)

    load_env()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL not set. Fill in src/distributed scraper/.env and retry.")
        sys.exit(1)

    conn = psycopg2.connect(pg_url)

    try:
        formats = get_formats(conn, args.format)
        if not formats:
            print("No stats data found — run compute_stats.py first.")
            return

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Formats:     {', '.join(formats)}")
        print(f"Min decks:   {args.min_decks}")
        print(f"Min cooccur: {args.min_cooccur}")
        print(f"Output:      {OUTPUT_DIR}")

        exported = run(conn, formats, args.min_decks, args.min_cooccur)
    finally:
        conn.close()

    if exported:
        write_manifest(OUTPUT_DIR, sorted(exported))
        print(f"\nManifest updated: {sorted(exported)}")

    print("\nDone.")


if __name__ == "__main__":
    main()
