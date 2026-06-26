"""
Seed v2.cards from a Scryfall bulk-data export.

v2.cards has NO unique constraint or index on card_name (only the integer PK),
so ON CONFLICT (card_name) is unavailable and the integer id must stay stable
because decks reference it (deck_cards.card_id, singleton card_ids[]). This
seeder therefore:

  1. Parses the bulk export and DEDUPES by card_name in Python — guaranteeing
     one row per name, which is the invariant the in-memory API resolver relies
     on.
  2. Bulk-loads the deduped rows into a TEMP table.
  3. UPDATEs mutable fields of cards that already exist (matched by card_name).
  4. INSERTs only cards whose name is not already present (anti-join), so
     existing ids never change. The AFTER INSERT trigger on v2.cards then
     populates v2.card_format_status for each new card.

Steps 3 and 4 are single set-based statements (hash joins), so no index on
card_name is needed.

Only the five actively scraped formats have legality columns; Canadian
Highlander has none (every card is eligible there, handled by the trigger).

Usage:
    python "V2/src/scraping/seed_cards.py"                  # oracle_cards (~27k)
    python "V2/src/scraping/seed_cards.py" --bulk all_cards # every printing
    python "V2/src/scraping/seed_cards.py" --cache          # cache raw JSON
    python "V2/src/scraping/seed_cards.py" --info           # DB stats only

Reads DATABASE_URL from V2/src/distributed scraper/.env automatically.
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

from constants.env import load_env
from constants.mtg import encode_colors
from scryfall import ScryfallClient

CACHE_DIR  = Path(__file__).parents[1] / "data" / "cache"
PAGE_SIZE  = 1000

# v2.cards columns this seeder writes, in order. 'id' is omitted — it is an
# IDENTITY column assigned by Postgres. Legality is limited to the 5 formats
# with a column on v2.cards (highlanderCanadian has none).
LEGAL_FORMATS = ["commander", "pauper", "modern", "vintage", "legacy"]

BASE_COLUMNS = [
    "card_name", "scryfall_id", "oracle_id", "layout",
    "mana_cost", "cmc", "type_line", "oracle_text",
    "power", "toughness", "loyalty", "defense",
    "color_mask", "ci_mask", "rarity",
    "reserved", "textless", "game_changer",
    "edhrec_rank", "image_uri", "keywords_json",
]
LEGAL_COLUMNS = [f"legal_{fmt}" for fmt in LEGAL_FORMATS]
COLUMNS = BASE_COLUMNS + LEGAL_COLUMNS

# Mutable columns refreshed for already-existing cards (everything but the name,
# which is the match key).
_UPDATE_COLUMNS = [c for c in COLUMNS if c != "card_name"]


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


def parse_card(raw: dict) -> dict:
    """Parse a raw Scryfall card dict into a column->value dict."""
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
        row[f"legal_{fmt}"] = legalities.get(fmt)
    return row


def dedupe_by_name(cards: list[dict]) -> tuple[list[dict], int]:
    """Parse and collapse to one row per card_name (first occurrence wins).

    Returns (rows, collisions) where rows is ordered and unique by card_name.
    """
    seen: dict[str, dict] = {}
    collisions = 0
    for raw in cards:
        try:
            row = parse_card(raw)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            print(f"  [warn] parse error for {raw.get('name', '?')!r}: {exc}")
            continue
        name = row["card_name"]
        if not name:
            continue
        if name in seen:
            collisions += 1
            continue
        seen[name] = row
    return list(seen.values()), collisions


# ---------------------------------------------------------------------------
# Upsert via temp table
# ---------------------------------------------------------------------------

def _create_temp_table(cur) -> None:
    col_list = ", ".join(COLUMNS)
    cur.execute(
        f"CREATE TEMP TABLE _seed AS SELECT {col_list} FROM v2.cards WITH NO DATA"
    )


def _load_temp(cur, rows: list[dict]) -> None:
    tuples = [tuple(r[c] for c in COLUMNS) for r in rows]
    psycopg2.extras.execute_values(
        cur,
        f"INSERT INTO _seed ({', '.join(COLUMNS)}) VALUES %s",
        tuples,
        page_size=PAGE_SIZE,
    )


def _apply_updates(cur) -> int:
    set_clause = ", ".join(f"{c} = s.{c}" for c in _UPDATE_COLUMNS)
    cur.execute(
        f"""
        UPDATE v2.cards c
        SET    {set_clause}
        FROM   _seed s
        WHERE  c.card_name = s.card_name
        """
    )
    return cur.rowcount


def _apply_inserts(cur) -> int:
    col_list = ", ".join(COLUMNS)
    src_list = ", ".join(f"s.{c}" for c in COLUMNS)
    cur.execute(
        f"""
        INSERT INTO v2.cards ({col_list})
        SELECT {src_list}
        FROM   _seed s
        WHERE  NOT EXISTS (
            SELECT 1 FROM v2.cards c WHERE c.card_name = s.card_name
        )
        """
    )
    return cur.rowcount


def seed(rows: list[dict], conn) -> tuple[int, int]:
    """Upsert deduped rows into v2.cards. Returns (updated, inserted)."""
    with conn.cursor() as cur:
        _create_temp_table(cur)
        _load_temp(cur, rows)
        updated  = _apply_updates(cur)
        inserted = _apply_inserts(cur)
        cur.execute("DROP TABLE _seed")
    conn.commit()
    return updated, inserted


# ---------------------------------------------------------------------------
# Info / stats
# ---------------------------------------------------------------------------

def print_db_stats(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM v2.cards")
        total = cur.fetchone()[0]
        if total == 0:
            print("v2.cards is empty.")
            return
        print(f"Total cards: {total:,}")

        cur.execute(
            "SELECT format, COUNT(*) AS n FROM v2.card_format_status GROUP BY format ORDER BY n DESC"
        )
        print("\ncard_format_status rows by format:")
        for fmt, n in cur.fetchall():
            print(f"  {fmt:<22} {n:>7,}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

BULK_TYPES = ["oracle_cards", "unique_artwork", "default_cards", "all_cards"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed v2.cards from a Scryfall bulk-data export.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--bulk", dest="bulk_type", default="oracle_cards", choices=BULK_TYPES)
    parser.add_argument("--cache", dest="cache", action="store_true",
                        help="Save downloaded JSON to data/cache/ and reuse it on subsequent runs")
    parser.add_argument("--info", dest="info", action="store_true",
                        help="Print database statistics and exit (no download)")
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
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = CACHE_DIR / f"{args.bulk_type}.json"

    client = ScryfallClient()

    t0 = time.perf_counter()
    print(f"Fetching bulk data: {args.bulk_type} ...")
    cards = client.download_bulk(args.bulk_type, cache_path=cache_path)
    print(f"Downloaded {len(cards):,} cards in {time.perf_counter() - t0:.1f}s")

    rows, collisions = dedupe_by_name(cards)
    print(f"Deduped to {len(rows):,} unique card names "
          f"({collisions:,} duplicate name(s) dropped)")

    print("Seeding v2.cards ...")
    t1 = time.perf_counter()
    updated, inserted = seed(rows, conn)
    print(f"Done: {inserted:,} inserted, {updated:,} updated in {time.perf_counter() - t1:.1f}s")

    print()
    print_db_stats(conn)
    conn.close()


if __name__ == "__main__":
    main()
