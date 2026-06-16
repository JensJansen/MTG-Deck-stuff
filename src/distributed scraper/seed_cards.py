"""
seed_cards.py - One-time import of card data from a local cards.db into Postgres.

Run this once (or whenever cards.db is refreshed from Scryfall) before starting
the central_node.  The central_node uses the cards table to decide which cards
to search for on Moxfield.

Usage:
    python "src/distributed scraper/seed_cards.py"
    python "src/distributed scraper/seed_cards.py" --cards-db path/to/cards.db
    python "src/distributed scraper/seed_cards.py" --dry-run

Environment:
    DATABASE_URL  PostgreSQL connection string (required)
"""

import argparse
import sqlite3
from pathlib import Path

import psycopg2.extras

from config import LEGAL_FORMATS
from db import apply_schema, get_connection

DEFAULT_CARDS_DB = Path(__file__).parents[2] / "src" / "data" / "cards.db"
BATCH_SIZE = 500

# Columns copied from cards.db (SQLite 'name' column → Postgres 'card_name').
# All are nullable in Postgres so missing columns in older cards.db files are safe.
COLUMNS: list[str] = [
    "scryfall_id",
    "oracle_id",
    "layout",
    "mana_cost",
    "cmc",
    "type_line",
    "oracle_text",
    "power",
    "toughness",
    "loyalty",
    "defense",
    "color_mask",
    "ci_mask",
    "rarity",
    "reserved",
    "textless",
    "game_changer",
    "edhrec_rank",
    "image_uri",
    "keywords_json",
] + [f"legal_{fmt}" for fmt in LEGAL_FORMATS]


def _available_columns(sqlite_conn: sqlite3.Connection) -> list[str]:
    """Return the subset of COLUMNS that actually exist in cards.db."""
    existing = {row[1] for row in sqlite_conn.execute("PRAGMA table_info(cards)")}
    return [c for c in COLUMNS if c in existing]


def _build_upsert_sql(avail_cols: list[str]) -> str:
    all_cols   = ["card_name"] + avail_cols
    col_list   = ", ".join(all_cols)
    val_list   = ", ".join(["%s"] * len(all_cols))
    update_set = ",\n        ".join(
        f"{c} = EXCLUDED.{c}" for c in avail_cols
    )
    return f"""
        INSERT INTO cards ({col_list})
        VALUES ({val_list})
        ON CONFLICT (card_name) DO UPDATE SET
        {update_set}
    """


def seed(sqlite_conn: sqlite3.Connection, pg_conn, dry_run: bool) -> tuple[int, int]:
    avail_cols  = _available_columns(sqlite_conn)
    upsert_sql  = _build_upsert_sql(avail_cols)
    col_select  = ", ".join(["name"] + avail_cols)

    print(f"Columns available in cards.db: {len(avail_cols) + 1} (name + {len(avail_cols)} others)")

    rows  = sqlite_conn.execute(f"SELECT {col_select} FROM cards").fetchall()
    total = len(rows)
    print(f"Cards to seed: {total:,}")

    if dry_run:
        print("DRY RUN — no changes written.")
        return total, 0

    upserted = 0
    for batch_start in range(0, total, BATCH_SIZE):
        batch = rows[batch_start : batch_start + BATCH_SIZE]
        with pg_conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, upsert_sql, batch, page_size=BATCH_SIZE)
        pg_conn.commit()
        upserted += len(batch)
        pct = min(batch_start + BATCH_SIZE, total) / total * 100
        print(f"  {upserted:>6}/{total}  ({pct:4.0f}%)", end="\r", flush=True)

    print()
    return total, upserted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the Postgres cards table from a local Scryfall cards.db.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python "src/distributed scraper/seed_cards.py"
  python "src/distributed scraper/seed_cards.py" --cards-db ~/data/cards.db
  python "src/distributed scraper/seed_cards.py" --dry-run
""",
    )
    parser.add_argument(
        "--cards-db", dest="cards_db", default=str(DEFAULT_CARDS_DB),
        help=f"Path to local Scryfall cards.db (default: {DEFAULT_CARDS_DB})",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Report what would be seeded without writing anything",
    )
    args = parser.parse_args()

    cards_db = Path(args.cards_db)
    if not cards_db.exists():
        print(f"ERROR: cards.db not found at {cards_db}")
        print("Run src/scraping/scryfall_bulk_cards_importer.py first to build it.")
        return

    sqlite_conn = sqlite3.connect(f"file:{cards_db}?mode=ro", uri=True)
    pg_conn     = get_connection()
    apply_schema(pg_conn)

    try:
        total, upserted = seed(sqlite_conn, pg_conn, args.dry_run)
        if not args.dry_run:
            print(f"Done: {upserted:,}/{total:,} cards upserted into Postgres.")
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
