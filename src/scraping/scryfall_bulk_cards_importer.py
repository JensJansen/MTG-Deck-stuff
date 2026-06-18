"""
Download all MTG cards from Scryfall's bulk-data endpoint and upsert them
directly into the Postgres cards table.

Usage:
    python src/scraping/scryfall_bulk_cards_importer.py                        # oracle_cards (~27k unique cards)
    python src/scraping/scryfall_bulk_cards_importer.py --bulk all_cards       # every printing (~110k+)
    python src/scraping/scryfall_bulk_cards_importer.py --cache                # save raw JSON to disk; reuse on next run
    python src/scraping/scryfall_bulk_cards_importer.py --info                 # show DB stats only, no download

Reads DATABASE_URL from src/distributed scraper/.env automatically.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import psycopg2
import psycopg2.extras

from constants.moxfield import LEGAL_FORMATS, encode_colors
from constants.env import load_env
from scryfall import ScryfallClient

CACHE_DIR  = Path(__file__).parents[1] / "data" / "cache"
BATCH_SIZE = 500

# Columns written to Postgres in insertion order.
# 'card_name' maps from Scryfall's 'name' field.
COLUMNS = [
    "card_name", "scryfall_id", "oracle_id", "layout",
    "mana_cost", "cmc", "type_line", "oracle_text",
    "power", "toughness", "loyalty", "defense",
    "color_mask", "ci_mask", "rarity",
    "reserved", "textless", "game_changer",
    "edhrec_rank", "image_uri", "keywords_json",
] + [f"legal_{fmt}" for fmt in LEGAL_FORMATS]


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

def _build_upsert_sql() -> str:
    col_list   = ", ".join(COLUMNS)
    update_set = ",\n        ".join(
        f"{c} = EXCLUDED.{c}"
        for c in COLUMNS if c != "card_name"
    )
    return f"""
        INSERT INTO cards ({col_list})
        VALUES %s
        ON CONFLICT (card_name) DO UPDATE SET
        {update_set}
    """


UPSERT_SQL = _build_upsert_sql()


# ---------------------------------------------------------------------------
# Card parsing
# ---------------------------------------------------------------------------

def _bool(v) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        return 1 if v.lower() == "true" else 0
    return int(bool(v))


def _image_uri(raw: dict) -> str | None:
    uris = raw.get("image_uris")
    if uris:
        return uris.get("normal") or uris.get("large") or next(iter(uris.values()), None)
    faces = raw.get("card_faces") or []
    if faces:
        face_uris = faces[0].get("image_uris") or {}
        return face_uris.get("normal") or face_uris.get("large") or next(iter(face_uris.values()), None)
    return None


def parse_card(raw: dict) -> tuple:
    """Parse a raw Scryfall card dict into a tuple ordered by COLUMNS."""
    legalities = raw.get("legalities") or {}
    row = {
        "card_name":     raw.get("name", ""),
        "scryfall_id":   raw.get("id"),
        "oracle_id":     raw.get("oracle_id", ""),
        "layout":        raw.get("layout"),
        "mana_cost":     raw.get("mana_cost"),
        "cmc":           raw.get("cmc") or 0.0,
        "type_line":     raw.get("type_line"),
        "oracle_text":   raw.get("oracle_text"),
        "power":         raw.get("power"),
        "toughness":     raw.get("toughness"),
        "loyalty":       raw.get("loyalty"),
        "defense":       raw.get("defense"),
        "color_mask":    encode_colors(raw.get("colors", [])),
        "ci_mask":       encode_colors(raw.get("color_identity", [])),
        "rarity":        raw.get("rarity"),
        "reserved":      _bool(raw.get("reserved", False)),
        "textless":      _bool(raw.get("textless", False)),
        "game_changer":  _bool(raw.get("game_changer", False)),
        "edhrec_rank":   raw.get("edhrec_rank"),
        "image_uri":     _image_uri(raw),
        "keywords_json": json.dumps(raw.get("keywords") or [], ensure_ascii=False),
    }
    for fmt in LEGAL_FORMATS:
        row[f"legal_{fmt}"] = "legal" if fmt == "highlanderCanadian" else legalities.get(fmt)

    return tuple(row[c] for c in COLUMNS)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_cards(cards: list[dict], conn) -> tuple[int, int]:
    total  = len(cards)
    ok     = 0
    errors = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch = cards[batch_start : batch_start + BATCH_SIZE]
        rows  = []
        for raw in batch:
            try:
                rows.append(parse_card(raw))
            except Exception as exc:
                print(f"  [warn] parse error for {raw.get('name', '?')!r}: {exc}")
                errors += 1

        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, UPSERT_SQL, rows)
        conn.commit()
        ok += len(rows)

        pct = min(batch_start + BATCH_SIZE, total) / total * 100
        print(f"  {min(ok, total):>6}/{total}  ({pct:4.0f}%)", end="\r", flush=True)

    print()
    return ok, errors


# ---------------------------------------------------------------------------
# Info / stats
# ---------------------------------------------------------------------------

def print_db_stats(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM cards")
        total = cur.fetchone()[0]

    if total == 0:
        print("cards table is empty.")
        return

    print(f"Total cards: {total:,}")

    with conn.cursor() as cur:
        cur.execute("SELECT rarity, COUNT(*) AS n FROM cards GROUP BY rarity ORDER BY n DESC")
        print("\nBy rarity:")
        for rarity, n in cur.fetchall():
            print(f"  {rarity or '(none)':<12} {n:>7,}")

        cur.execute(
            "SELECT layout, COUNT(*) AS n FROM cards GROUP BY layout ORDER BY n DESC LIMIT 10"
        )
        print("\nBy layout (top 10):")
        for layout, n in cur.fetchall():
            print(f"  {layout or '(none)':<20} {n:>7,}")

        cur.execute(
            "SELECT legal_pauper, COUNT(*) AS n FROM cards GROUP BY legal_pauper ORDER BY n DESC"
        )
        print("\nLegality sample (pauper):")
        for status, n in cur.fetchall():
            print(f"  {status or '(null)':<15} {n:>7,}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

BULK_TYPES = ["oracle_cards", "unique_artwork", "default_cards", "all_cards"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download MTG cards from Scryfall and upsert into Postgres.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/scraping/scryfall_bulk_cards_importer.py
  python src/scraping/scryfall_bulk_cards_importer.py --bulk all_cards
  python src/scraping/scryfall_bulk_cards_importer.py --cache
  python src/scraping/scryfall_bulk_cards_importer.py --info
""",
    )
    parser.add_argument(
        "--bulk", dest="bulk_type", default="oracle_cards", choices=BULK_TYPES,
    )
    parser.add_argument(
        "--cache", dest="cache", action="store_true",
        help="Save downloaded JSON to cache/ and reuse it on subsequent runs",
    )
    parser.add_argument(
        "--info", dest="info", action="store_true",
        help="Print database statistics and exit (no download)",
    )
    args = parser.parse_args()

    load_env()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL environment variable is not set.")
        return

    conn = psycopg2.connect(pg_url)

    if args.info:
        print_db_stats(conn)
        conn.close()
        return

    cache_path: Path | None = None
    if args.cache:
        CACHE_DIR.mkdir(exist_ok=True)
        cache_path = CACHE_DIR / f"{args.bulk_type}.json"

    client = ScryfallClient()

    t0 = time.perf_counter()
    print(f"Fetching bulk data: {args.bulk_type} ...")
    cards = client.download_bulk(args.bulk_type, cache_path=cache_path)
    t_download = time.perf_counter() - t0
    print(f"Downloaded {len(cards):,} cards in {t_download:.1f}s")

    print("Importing into Postgres ...")
    t1 = time.perf_counter()
    ok, errors = import_cards(cards, conn)
    t_import = time.perf_counter() - t1
    print(f"Done: {ok:,} upserted, {errors} errors in {t_import:.1f}s")

    print()
    print_db_stats(conn)
    conn.close()


if __name__ == "__main__":
    main()
