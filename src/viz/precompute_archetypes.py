"""
Export archetype visualization data for a format to JSON.

    python src/viz/precompute_archetypes.py --format pauper
    python src/viz/precompute_archetypes.py          # all formats with archetype data

Reads from the archetypes table (populated by pipeline.py) and writes
{format}.archetypes.json to src/viz/public/data/.

Also updates manifest.json to add an archetype_formats list so the frontend
knows which formats have archetype data available.

Run pipeline.py first to populate the archetypes table.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parents[1]))
from constants.env import load_env

OUTPUT_DIR = Path(__file__).parent / "public" / "data"
MANIFEST   = OUTPUT_DIR / "manifest.json"


# ── Manifest helpers ───────────────────────────────────────────────────────────

def _load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"formats": [], "archetype_formats": []}


def _save_manifest(m: dict) -> None:
    MANIFEST.write_text(json.dumps(m, separators=(",", ":")), encoding="utf-8")


# ── Export ─────────────────────────────────────────────────────────────────────

def export_format(conn, fmt: str) -> bool:
    """
    Read archetypes for `fmt` and write {fmt}.archetypes.json.
    Returns True if data was found and written.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, level, parent_id, name, member_count,
                   keystone_cards, top_cards, color_profile, cmc_curve
            FROM archetypes
            WHERE format = %s
            ORDER BY level, member_count DESC
        """, (fmt,))
        rows = cur.fetchall()

    if not rows:
        print(f"  No archetype data found for format={fmt!r}  (run pipeline.py first)")
        return False

    total_l1 = sum(r[4] for r in rows if r[1] == 1)

    archetypes = []
    for row in rows:
        id_, level, parent_id, name, member_count, keystone_cards, top_cards, color_profile, cmc_curve = row
        archetypes.append({
            "id":            id_,
            "level":         level,
            "parent_id":     parent_id,
            "name":          name,
            "member_count":  member_count,
            "meta_share":    round(member_count / total_l1, 4) if (total_l1 > 0 and level == 1) else None,
            "keystone_cards": keystone_cards,
            "top_cards":     top_cards,
            "color_profile": color_profile,
            "cmc_curve":     cmc_curve,
        })

    payload = {
        "format":           fmt,
        "total_classified": total_l1,
        "archetypes":       archetypes,
    }

    out = OUTPUT_DIR / f"{fmt}.archetypes.json"
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    n_l1 = sum(1 for a in archetypes if a["level"] == 1)
    n_l2 = sum(1 for a in archetypes if a["level"] == 2)
    print(f"  {fmt}: {n_l1} archetypes  {n_l2} sub-archetypes  {total_l1:,} classified decks  → {out.name}")
    return True


def get_formats_with_data(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT format FROM archetypes ORDER BY format")
        return [row[0] for row in cur.fetchall()]


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export archetype data to JSON for the visualization frontend."
    )
    parser.add_argument("--format", "-f",
                        help="Format to export (default: all formats with archetype data)")
    args = parser.parse_args()

    load_env()
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL not set.  Source your .env file first.")
        sys.exit(1)

    conn = psycopg2.connect(db_url)
    try:
        if args.format:
            fmt_list = [args.format]
        else:
            fmt_list = get_formats_with_data(conn)
            if not fmt_list:
                print("No archetype data found.  Run pipeline.py first.")
                return

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        exported: list[str] = []

        for fmt in fmt_list:
            if export_format(conn, fmt):
                exported.append(fmt)

        if not exported:
            return

        # Update manifest — keep existing card-layout formats, add archetype_formats
        manifest = _load_manifest()
        existing = set(manifest.get("archetype_formats", []))
        existing.update(exported)
        manifest["archetype_formats"] = sorted(existing)
        _save_manifest(manifest)
        print(f"\nManifest updated: archetype_formats={manifest['archetype_formats']}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
