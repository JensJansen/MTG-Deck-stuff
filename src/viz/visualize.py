"""
visualize.py - Export card co-occurrence data as JSON for the React visualization app.

Writes one JSON file per format to src/viz/public/data/<format>.json, plus a
manifest.json listing available formats. The React app (src/viz/) reads these
files via fetch('/data/<format>.json').

JSON structure:
    {
      "format": "pauper",
      "nodes": [{name, x, y, color_cat, deck_count, total_decks, inclusion_pct, avg_qty}, ...],
      "ego":   {card_name: [{n, c, l, j}, ...], ...},
      "edges": [[a_idx, b_idx, dist_int], ...]
    }

dist_int = round((1 - jaccard) * 100); divided by 100 in JS to get float distance.

Usage:
    python src/viz/visualize.py --format pauper
    python src/viz/visualize.py

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

from constants.moxfield import encode_colors
from constants.env import load_env

OUTPUT_DIR = Path(__file__).parent / "public" / "data"

EGO_TOP_N         = 50
EGO_MIN_COOCCUR   = 5
GRAPH_MIN_COOCCUR = 20
FOCUS_MIN_COOCCUR = 5


def categorize_color(color_identity: str) -> str:
    colors = [c.strip() for c in (color_identity or "").split(",") if c.strip()]
    if not colors:
        return "Colorless"
    return colors[0] if len(colors) == 1 else "Multi"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def get_layout_formats(conn, fmt_filter: str | None) -> list[str]:
    if fmt_filter:
        return [fmt_filter]
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT format FROM card_layout ORDER BY format")
        return [r[0] for r in cur.fetchall()]


def load_nodes(conn, fmt: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                cl.card_name, cl.x, cl.y, cl.color_identity,
                cs.deck_count, cs.total_decks, cs.inclusion_rate, cs.avg_quantity,
                c.image_uri
            FROM card_layout cl
            JOIN card_stats cs ON cl.card_name = cs.card_name AND cl.format = cs.format
            LEFT JOIN cards c ON cl.card_name = c.card_name
            WHERE cl.format = %s
        """, (fmt,))
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
    conn, fmt: str, card_names: set[str], min_cooccur: int
) -> list[tuple]:
    card_list = list(card_names)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT card_a, card_b, cooccurrence_count, lift, jaccard
            FROM card_pair_stats
            WHERE format = %s AND cooccurrence_count >= %s
              AND card_a = ANY(%s) AND card_b = ANY(%s)
            ORDER BY cooccurrence_count DESC
        """, (fmt, min_cooccur, card_list, card_list))
        return cur.fetchall()


def load_ego(conn, fmt: str, card_names: set[str]) -> dict[str, list[dict]]:
    rows = _load_pair_rows(conn, fmt, card_names, EGO_MIN_COOCCUR)
    ego: dict[str, list] = defaultdict(list)
    for card_a, card_b, cooccur, lift, jaccard in rows:
        if len(ego[card_a]) < EGO_TOP_N:
            ego[card_a].append({"n": card_b, "c": cooccur, "l": round(lift, 2), "j": round(jaccard, 3)})
        if len(ego[card_b]) < EGO_TOP_N:
            ego[card_b].append({"n": card_a, "c": cooccur, "l": round(lift, 2), "j": round(jaccard, 3)})
    return dict(ego)


def load_edges(conn, fmt: str, name_to_idx: dict[str, int]) -> list[list[int]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT card_a, card_b, jaccard
            FROM card_pair_stats
            WHERE format = %s AND cooccurrence_count >= %s
        """, (fmt, GRAPH_MIN_COOCCUR))
        rows = cur.fetchall()

    edges = []
    for card_a, card_b, jaccard in rows:
        a = name_to_idx.get(card_a)
        b = name_to_idx.get(card_b)
        if a is not None and b is not None:
            edges.append([a, b, round((1.0 - jaccard) * 100)])
    return edges


def load_focus(conn, fmt: str, card_names: set[str]) -> dict[str, list]:
    """All co-occurring pairs (>=FOCUS_MIN_COOCCUR) as compact [name, count, lift] tuples."""
    rows = _load_pair_rows(conn, fmt, card_names, FOCUS_MIN_COOCCUR)
    focus: dict[str, list] = defaultdict(list)
    for card_a, card_b, cooccur, lift, _jaccard in rows:
        focus[card_a].append([card_b, cooccur, round(lift, 2)])
        focus[card_b].append([card_a, cooccur, round(lift, 2)])
    return dict(focus)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_format(conn, fmt: str, output_dir: Path) -> bool:
    nodes = load_nodes(conn, fmt)
    if not nodes:
        print(f"  No layout data for '{fmt}' — run precompute_layout.py first.")
        return False

    card_names  = {n["name"] for n in nodes}
    name_to_idx = {n["name"]: i for i, n in enumerate(nodes)}

    ego   = load_ego(conn, fmt, card_names)
    edges = load_edges(conn, fmt, name_to_idx)

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

    focus      = load_focus(conn, fmt, card_names)
    focus_path = output_dir / f"{fmt}.focus.json"
    focus_path.write_text(json.dumps(focus, separators=(",", ":")), encoding="utf-8")
    focus_kb = focus_path.stat().st_size // 1024
    print(f"  {focus_path.name}  ({len(focus):,} cards with partners, {focus_kb} KB)")

    return True


def write_manifest(output_dir: Path, formats: list[str]) -> None:
    manifest = {"formats": formats}
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export card co-occurrence data as JSON for the React visualization app."
    )
    parser.add_argument("--format", "-f", dest="format", default=None,
                        help="Limit to one format (default: all with layout data)")
    args = parser.parse_args()

    load_env()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL not set. Fill in src/distributed scraper/.env and retry.")
        return

    conn = psycopg2.connect(pg_url)

    try:
        formats = get_layout_formats(conn, args.format)
        if not formats:
            print("No layout data found — run precompute_layout.py first.")
            return

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Output: {OUTPUT_DIR}")

        exported = []
        for fmt in formats:
            print(f"\n[{fmt}]")
            if export_format(conn, fmt, OUTPUT_DIR):
                exported.append(fmt)
    finally:
        conn.close()

    if exported:
        manifest_path = OUTPUT_DIR / "manifest.json"
        existing: list[str] = []
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text())["formats"]
            except (json.JSONDecodeError, KeyError) as exc:
                print(f"  [warn] manifest.json is malformed ({exc}), starting fresh")
        all_formats = sorted(set(existing) | set(exported))
        write_manifest(OUTPUT_DIR, all_formats)
        print(f"\nManifest updated: {all_formats}")

    print("\nDone.")


if __name__ == "__main__":
    main()
