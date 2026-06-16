"""
merge_cards.py - Merges Scryfall card metadata into the Moxfield deck database.

DATA SOURCES
------------
- decks.db  : Built by the Moxfield scraper (deck_scraper.py). Contains real deck
              usage data. The cards table is sparse — only fields Moxfield
              exposes are populated. This is the operational database; the
              deck_cards table has a hard FK on cards.id.

- cards.db  : Built by the Scryfall bulk importer (scryfall_bulk_cards_importer.py). Contains rich
              card metadata for every MTG card. Read-only throughout this script.
              After a successful run, cards.db is no longer needed for runtime
              queries — decks.db becomes the single source of truth.

WHAT THIS SCRIPT DOES
---------------------
1. Migration  — Adds new Scryfall-sourced columns to decks.db cards table.
                Idempotent: skips columns that already exist.

2. Backfill   — For each existing decks.db card, finds the matching Scryfall
                card by name (case-insensitive) and fills in any NULL columns.
                Existing non-NULL values from Moxfield are never overwritten.
                Double-faced cards (DFCs) stored as "Front // Back" in Scryfall
                are matched against either half, since Moxfield stores only the
                front face name. MTG card names are globally unique so this
                cannot produce false positives.

3. Insert     — Cards present in Scryfall but absent from decks.db are inserted
                as new rows. set_code and collector_number are left NULL (those
                are Moxfield-specific fields). id is auto-generated.

COLUMNS INTENTIONALLY EXCLUDED
-------------------------------
Collectible / print-specific fields that carry no gameplay or analysis value
are not brought over: released_at, border_color, frame, artist, reprint,
digital, foil, nonfoil, booster, lang, flavor_text, full_art, oversized, promo.

SAFE TO RE-RUN
--------------
Migration checks existing columns before altering. Backfill uses NULL-guarded
UPDATEs. Insert checks for existing card names before inserting.

Usage:
    python src/scraping/merge_cards.py
    python src/scraping/merge_cards.py --decks-db path/to/decks.db --cards-db path/to/cards.db
    python src/scraping/merge_cards.py --dry-run
"""

import argparse
import sqlite3
from pathlib import Path

from constants import LEGAL_FORMATS

DECKS_DB = Path(__file__).parents[1] / "data" / "decks.db"
CARDS_DB = Path(__file__).parents[1] / "data" / "cards.db"

# ---------------------------------------------------------------------------
# Schema — new columns added to decks.db cards table
# ---------------------------------------------------------------------------

# Tuples of (column_name, sqlite_type). All nullable — existing rows that
# have no Scryfall match will simply have NULL in these columns.
NEW_COLUMNS: list[tuple[str, str]] = [
    ("oracle_id",     "TEXT"),
    ("layout",        "TEXT"),
    ("power",         "TEXT"),
    ("toughness",     "TEXT"),
    ("loyalty",       "TEXT"),
    ("defense",       "TEXT"),
    ("reserved",      "INTEGER"),
    ("textless",      "INTEGER"),
    ("game_changer",  "INTEGER"),
    ("edhrec_rank",   "INTEGER"),
    ("image_uri",     "TEXT"),
    ("keywords_json", "TEXT"),
] + [(f"legal_{fmt}", "TEXT") for fmt in LEGAL_FORMATS]

# Columns that exist in both DBs and may be backfilled into decks.db if NULL.
# These are never overwritten when already populated.
SHARED_COLUMNS: list[str] = [
    "scryfall_id",
    "cmc",
    "type_line",
    "mana_cost",
    "color_mask",
    "ci_mask",
    "rarity",
    "oracle_text",
]

# All Scryfall columns written during backfill / insert (new + shared).
ALL_SCRYFALL_COLUMNS: list[str] = (
    SHARED_COLUMNS
    + [col for col, _ in NEW_COLUMNS]
)


# ---------------------------------------------------------------------------
# Step 1 — Migration
# ---------------------------------------------------------------------------

def migrate(conn: sqlite3.Connection, dry_run: bool) -> int:
    """Add missing Scryfall columns to the cards table. Returns count added."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
    to_add = [(col, typ) for col, typ in NEW_COLUMNS if col not in existing]

    if not to_add:
        print("  Migration: no new columns needed.")
        return 0

    for col, typ in to_add:
        print(f"  + {col} {typ}")
        if not dry_run:
            conn.execute(f"ALTER TABLE cards ADD COLUMN {col} {typ}")

    if not dry_run:
        conn.commit()

    print(f"  Migration: {'would add' if dry_run else 'added'} {len(to_add)} column(s).")
    return len(to_add)


# ---------------------------------------------------------------------------
# Step 2 — Load Scryfall data
# ---------------------------------------------------------------------------

def _select_columns(scryfall_conn: sqlite3.Connection) -> list[str]:
    """Return the subset of ALL_SCRYFALL_COLUMNS that exist in cards.db."""
    existing = {row[1] for row in scryfall_conn.execute("PRAGMA table_info(cards)")}
    return [col for col in ALL_SCRYFALL_COLUMNS if col in existing]


def load_scryfall(scryfall_conn: sqlite3.Connection) -> dict[str, dict]:
    """
    Return a dict mapping lowercase card name -> Scryfall row dict.

    DFCs ("Front // Back") are indexed under three keys:
      - the full name
      - the front half
      - the back half
    All three point to the same dict so a Moxfield front-face name resolves
    correctly. MTG names are globally unique so no collisions are possible.
    """
    cols = _select_columns(scryfall_conn)
    col_list = ", ".join(cols)

    rows = scryfall_conn.execute(f"SELECT name, {col_list} FROM cards").fetchall()

    by_name: dict[str, dict] = {}
    for row in rows:
        name = row[0]
        data = dict(zip(cols, row[1:]))

        by_name[name.lower()] = data

        if " // " in name:
            front, back = name.split(" // ", 1)
            by_name[front.lower()] = data
            by_name[back.lower()]  = data

    return by_name


# ---------------------------------------------------------------------------
# Step 3 — Backfill existing rows
# ---------------------------------------------------------------------------

def _build_update_sql(cols: list[str]) -> str:
    """
    Build an UPDATE that only writes a column when the existing value IS NULL.
    This ensures Moxfield data is never overwritten.
    """
    set_clauses = ",\n        ".join(
        f"{col} = CASE WHEN {col} IS NULL THEN ? ELSE {col} END"
        for col in cols
    )
    return f"""
        UPDATE cards
        SET
        {set_clauses}
        WHERE LOWER(card_name) = ?
    """


def backfill(
    decks_conn: sqlite3.Connection,
    scryfall_by_name: dict[str, dict],
    available_cols: list[str],
    dry_run: bool,
    verbose: bool = False,
) -> tuple[int, int, int]:
    """
    Fill NULL columns on existing decks.db cards from Scryfall data.
    Returns (matched, backfilled, unmatched).
    """
    rows = decks_conn.execute("SELECT card_name FROM cards").fetchall()

    update_sql = _build_update_sql(available_cols)

    matched   = 0
    backfilled = 0
    unmatched  = 0

    updates: list[tuple] = []
    for (card_name,) in rows:
        scryfall = scryfall_by_name.get(card_name.lower())
        if scryfall is None:
            unmatched += 1
            if verbose:
                print(f"    [unmatched] {card_name!r}")
            continue

        matched += 1
        values = [scryfall.get(col) for col in available_cols]
        updates.append((*values, card_name.lower()))

    if not dry_run and updates:
        decks_conn.executemany(update_sql, updates)
        decks_conn.commit()
        backfilled = len(updates)
    else:
        backfilled = len(updates)

    return matched, backfilled, unmatched


# ---------------------------------------------------------------------------
# Step 4 — Insert Scryfall-only cards
# ---------------------------------------------------------------------------

def _build_insert_sql(available_cols: list[str]) -> str:
    col_list = ", ".join(["card_name"] + available_cols)
    val_list = ", ".join(["?"] * (1 + len(available_cols)))
    return f"INSERT OR IGNORE INTO cards ({col_list}) VALUES ({val_list})"


def insert_new(
    decks_conn: sqlite3.Connection,
    scryfall_conn: sqlite3.Connection,
    scryfall_by_name: dict[str, dict],
    available_cols: list[str],
    dry_run: bool,
) -> int:
    """
    Insert Scryfall cards not already present in decks.db.
    Only inserts from full Scryfall names (not split-half aliases) to avoid
    inserting the same DFC twice.
    Returns count of rows inserted.
    """
    existing_names = {
        row[0].lower()
        for row in decks_conn.execute("SELECT card_name FROM cards")
    }

    # Fetch full names from cards.db to distinguish canonical entries from
    # the split-half aliases we added in load_scryfall().
    full_names = {
        row[0]
        for row in scryfall_conn.execute("SELECT name FROM cards")
    }

    insert_sql = _build_insert_sql(available_cols)
    to_insert: list[tuple] = []

    for full_name in full_names:
        if full_name.lower() in existing_names:
            continue
        scryfall = scryfall_by_name.get(full_name.lower())
        if scryfall is None:
            continue
        values = [scryfall.get(col) for col in available_cols]
        to_insert.append((full_name, *values))

    if not dry_run and to_insert:
        decks_conn.executemany(insert_sql, to_insert)
        decks_conn.commit()

    return len(to_insert)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(decks_db: Path, cards_db: Path, dry_run: bool, verbose: bool = False) -> None:
    if not cards_db.exists():
        print(f"ERROR: cards.db not found at {cards_db}")
        print("Run src/scraping/scryfall_bulk_cards_importer.py first to build the Scryfall card catalogue.")
        return

    print(f"Decks DB : {decks_db}")
    print(f"Cards DB : {cards_db}")
    if dry_run:
        print("DRY RUN — no changes will be written.\n")

    # Open cards.db read-only; all writes go to decks.db.
    scryfall_conn = sqlite3.connect(f"file:{cards_db}?mode=ro", uri=True)
    decks_conn    = sqlite3.connect(decks_db)
    decks_conn.execute("PRAGMA foreign_keys = ON")
    decks_conn.execute("PRAGMA journal_mode = WAL")

    try:
        # 1. Migrate
        print("\n[1/4] Migrating decks.db schema ...")
        migrate(decks_conn, dry_run)

        # 2. Load Scryfall data
        print("\n[2/4] Loading Scryfall card data ...")
        scryfall_by_name = load_scryfall(scryfall_conn)
        available_cols   = _select_columns(scryfall_conn)
        print(f"  {len(scryfall_by_name):,} name entries loaded "
              f"({len(available_cols)} columns available)")

        # 3. Backfill
        print("\n[3/4] Backfilling existing cards ...")
        matched, backfilled, unmatched = backfill(
            decks_conn, scryfall_by_name, available_cols, dry_run, verbose
        )
        print(f"  Matched  : {matched:,}")
        print(f"  Backfilled: {backfilled:,}")
        print(f"  Unmatched : {unmatched:,}  (no Scryfall entry found by name)")

        # 4. Insert new cards
        print("\n[4/4] Inserting Scryfall-only cards ...")
        inserted = insert_new(
            decks_conn, scryfall_conn, scryfall_by_name, available_cols, dry_run
        )
        print(f"  {'Would insert' if dry_run else 'Inserted'}: {inserted:,} new card(s)")

    finally:
        scryfall_conn.close()
        decks_conn.close()

    print("\nDone.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge Scryfall card metadata into the Moxfield deck database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/scraping/merge_cards.py
  python src/scraping/merge_cards.py --dry-run
  python src/scraping/merge_cards.py --decks-db path/to/decks.db --cards-db path/to/cards.db
""",
    )
    parser.add_argument(
        "--decks-db", dest="decks_db", default=str(DECKS_DB),
        help=f"Path to Moxfield deck database (default: {DECKS_DB})",
    )
    parser.add_argument(
        "--cards-db", dest="cards_db", default=str(CARDS_DB),
        help=f"Path to Scryfall card database (default: {CARDS_DB})",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Report what would be done without writing anything",
    )
    parser.add_argument(
        "--verbose", dest="verbose", action="store_true",
        help="Print names of cards that could not be matched to Scryfall",
    )
    args = parser.parse_args()

    run(Path(args.decks_db), Path(args.cards_db), args.dry_run, args.verbose)


if __name__ == "__main__":
    main()
