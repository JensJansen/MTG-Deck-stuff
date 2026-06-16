"""
deck_scraper.py - Scrape Moxfield decks targeted by a local card list.

Rather than broad page scraping, this script queries the local card database
to decide which cards to search for, then hits Moxfield's per-card search
endpoint for each one (up to the 100-page API limit). Decks already stored
and unchanged are skipped.

Usage:
    python src/scraping/deck_scraper.py --card "Lightning Bolt"
    python src/scraping/deck_scraper.py --format pauper
    python src/scraping/deck_scraper.py
    python src/scraping/deck_scraper.py --early-stop
    python src/scraping/deck_scraper.py --format commander --skip-cards
"""

import argparse
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_SEARCH = "https://api2.moxfield.com/v2/decks/search"
API_DECK   = "https://api2.moxfield.com/v2/decks/all/{public_id}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; deck-gen-scraper/1.0)",
    "Accept": "application/json",
}
RATE_LIMIT_SECONDS = 1.0

# Scryfall format names — these correspond to legal_<fmt> columns in cards table.
LEGAL_FORMATS = [
    "standard", "future", "historic", "timeless", "gladiator",
    "pioneer", "explorer", "modern", "legacy", "pauper", "vintage",
    "penny", "commander", "oathbreaker", "standardbrawl", "brawl",
    "alchemy", "paupercommander", "duel", "oldschool", "premodern",
    "predh", "historicbrawl",
]

BOARDS = [
    "commanders", "companions", "signatureSpells",
    "mainboard", "sideboard", "maybeboard", "attractions", "stickers",
]

DB_PATH = Path(__file__).parents[1] / "data" / "decks.db"

# ---------------------------------------------------------------------------
# Color bitmask
# ---------------------------------------------------------------------------

COLOR_BITS = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}


def encode_colors(colors) -> int:
    if isinstance(colors, str):
        colors = [c.strip() for c in colors.split(",")]
    mask = 0
    for c in (colors or []):
        mask |= COLOR_BITS.get(c.strip().upper(), 0)
    return mask


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS decks (
            public_id        TEXT PRIMARY KEY,
            name             TEXT,
            format           TEXT,
            author           TEXT,
            color_mask       INTEGER NOT NULL DEFAULT 0,
            created_at_utc   TEXT,
            updated_at_utc   TEXT,
            scraped_at       TEXT,
            cards_fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS deck_cards (
            deck_id  TEXT    NOT NULL REFERENCES decks(public_id) ON DELETE CASCADE,
            card_id  INTEGER NOT NULL REFERENCES cards(id),
            board    TEXT    NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (deck_id, card_id, board)
        );

        CREATE INDEX IF NOT EXISTS idx_deck_cards_card_id ON deck_cards(card_id);
        CREATE INDEX IF NOT EXISTS idx_deck_cards_deck_id ON deck_cards(deck_id);
        CREATE INDEX IF NOT EXISTS idx_decks_format       ON decks(format);
    """)
    conn.commit()


def upsert_deck(conn: sqlite3.Connection, deck: dict) -> None:
    conn.execute("""
        INSERT INTO decks (
            public_id, name, format, author, color_mask,
            created_at_utc, updated_at_utc, scraped_at
        ) VALUES (
            :public_id, :name, :format, :author, :color_mask,
            :created_at_utc, :updated_at_utc, :scraped_at
        )
        ON CONFLICT(public_id) DO UPDATE SET
            name           = excluded.name,
            format         = excluded.format,
            author         = excluded.author,
            color_mask     = excluded.color_mask,
            updated_at_utc = excluded.updated_at_utc,
            scraped_at     = excluded.scraped_at
    """, deck)


def deck_needs_card_fetch(conn: sqlite3.Connection, public_id: str, updated_at_utc: str | None) -> bool:
    """Return True if the deck's cards have never been fetched, or the deck was updated since."""
    row = conn.execute(
        "SELECT cards_fetched_at, updated_at_utc FROM decks WHERE public_id = ?",
        (public_id,)
    ).fetchone()
    if not row or row[0] is None:
        return True
    return updated_at_utc is not None and updated_at_utc > row[1]


def replace_deck_cards(
    conn: sqlite3.Connection,
    deck_id: str,
    deck_card_rows: list[dict],
) -> None:
    if not deck_card_rows:
        return

    names = list({r["card_name"] for r in deck_card_rows})
    placeholders = ",".join("?" * len(names))
    name_to_id: dict[str, int] = {
        row[0]: row[1]
        for row in conn.execute(
            f"SELECT card_name, id FROM cards WHERE card_name IN ({placeholders})",
            names,
        )
    }

    for name in names:
        if name not in name_to_id:
            print(f"    [warn] card not in DB, skipping: {name!r}")

    conn.execute("DELETE FROM deck_cards WHERE deck_id = ?", (deck_id,))
    conn.executemany(
        "INSERT INTO deck_cards (deck_id, card_id, board, quantity) VALUES (?, ?, ?, ?)",
        [
            (deck_id, name_to_id[r["card_name"]], r["board"], r["quantity"])
            for r in deck_card_rows
            if r["card_name"] in name_to_id
        ],
    )
    conn.execute(
        "UPDATE decks SET cards_fetched_at = ? WHERE public_id = ?",
        (datetime.now(timezone.utc).isoformat(), deck_id),
    )


# ---------------------------------------------------------------------------
# Card selection
# ---------------------------------------------------------------------------

def get_cards_for_mode(
    conn: sqlite3.Connection,
    card: str | None,
    fmt: str | None,
) -> list[str]:
    """
    Return the ordered list of card names to drive deck searches.
      card set  → [card]
      fmt set   → all cards where legal_{fmt} = 'legal'
      neither   → all cards in the database
    """
    if card:
        return [card]
    if fmt:
        return [r[0] for r in conn.execute(
            f"SELECT card_name FROM cards WHERE legal_{fmt} = 'legal' ORDER BY card_name"
        )]
    return [r[0] for r in conn.execute(
        "SELECT card_name FROM cards ORDER BY card_name"
    )]


def moxfield_search_name(card_name: str) -> str:
    """For DFCs stored as 'Front // Back', Moxfield expects only the front face."""
    return card_name.split(" // ")[0] if " // " in card_name else card_name


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def fetch_page(
    fmt: str | None,
    page: int,
    page_size: int,
    card_name: str | None = None,
) -> dict:
    params: dict = {
        "pageNumber":    page,
        "pageSize":      page_size,
        "sortType":      "updated",
        "sortDirection": "Descending",
    }
    if fmt:
        params["fmt"] = fmt
    if card_name:
        params["cardName"] = card_name
    resp = requests.get(API_SEARCH, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_deck_detail(public_id: str) -> dict:
    resp = requests.get(API_DECK.format(public_id=public_id), headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_deck(raw: dict, fmt: str | None) -> dict:
    colors = raw.get("colorIdentity") or raw.get("colors") or []
    public_id = raw.get("publicId") or raw.get("id") or raw.get("slug")
    return {
        "public_id":      public_id,
        "name":           raw.get("name"),
        "format":         raw.get("format") or fmt,
        "author":         (raw.get("createdByUser") or {}).get("userName") or raw.get("authorUserName"),
        "color_mask":     encode_colors(colors),
        "created_at_utc": raw.get("createdAtUtc"),
        "updated_at_utc": raw.get("lastUpdatedAtUtc"),
        "scraped_at":     datetime.now(timezone.utc).isoformat(),
    }


def parse_deck_detail(detail: dict) -> list[dict]:
    """Returns deck_card_rows: [{card_name, board, quantity}]."""
    deck_card_rows: list[dict] = []

    for board_name in BOARDS:
        board_data = detail.get(board_name) or {}
        for entry in board_data.values():
            card_name = (entry.get("card") or {}).get("name")
            if not card_name:
                continue
            deck_card_rows.append({
                "card_name": card_name,
                "board":     board_name,
                "quantity":  entry.get("quantity", 1),
            })

    return deck_card_rows


# ---------------------------------------------------------------------------
# Core sweep logic
# ---------------------------------------------------------------------------

def _fetch_cards_for_page(
    raw_decks: list[dict],
    conn: sqlite3.Connection,
) -> int:
    """Fetch and store card details for new or updated decks. Returns deck-card rows written."""
    rows_written = 0
    for raw in raw_decks:
        public_id = raw.get("publicId") or raw.get("id")
        if not public_id:
            continue
        if not deck_needs_card_fetch(conn, public_id, raw.get("lastUpdatedAtUtc")):
            continue

        time.sleep(RATE_LIMIT_SECONDS)
        try:
            detail = fetch_deck_detail(public_id)
        except requests.HTTPError as exc:
            print(f"    [warn] {public_id}: HTTP {exc.response.status_code}, skipping")
            continue
        except requests.RequestException as exc:
            print(f"    [warn] {public_id}: {exc}, skipping")
            continue

        deck_card_rows = parse_deck_detail(detail)
        replace_deck_cards(conn, public_id, deck_card_rows)
        rows_written += len(deck_card_rows)

    return rows_written


def _sweep_one_card(
    conn: sqlite3.Connection,
    card_name: str,
    fmt: str | None,
    page_size: int,
    fetch_cards: bool,
    early_stop: bool,
) -> tuple[int, int]:
    """
    Paginate through all Moxfield decks containing card_name (filtered by fmt if given).
    Returns (new_decks_saved, pages_fetched).
    """
    search_name = moxfield_search_name(card_name)
    new_decks = 0
    page = 1

    while True:
        try:
            data = fetch_page(fmt, page, page_size, card_name=search_name)
        except requests.HTTPError as exc:
            print(f"\n    [warn] {search_name!r}: HTTP {exc.response.status_code}, skipping")
            break
        except requests.RequestException as exc:
            print(f"\n    [warn] {search_name!r}: {exc}, skipping")
            break

        raw_decks = data.get("data", [])
        if not raw_decks:
            break

        total_pages = data.get("totalPages", 1)
        print(f"    page {page}/{total_pages} ...", end=" ", flush=True)

        page_new = 0
        for raw in raw_decks:
            deck = parse_deck(raw, fmt)
            if not deck["public_id"]:
                continue
            upsert_deck(conn, deck)
            if not conn.execute(
                "SELECT 1 FROM deck_cards WHERE deck_id = ?", (deck["public_id"],)
            ).fetchone():
                page_new += 1

        conn.commit()

        if fetch_cards:
            rows = _fetch_cards_for_page(raw_decks, conn)
            conn.commit()
            print(f"{len(raw_decks)} decks, {page_new} new, {rows} card-rows")
        else:
            print(f"{len(raw_decks)} decks, {page_new} new")

        new_decks += page_new

        if page >= total_pages or len(raw_decks) < page_size:
            break

        if early_stop and page_new == 0:
            break

        page += 1
        time.sleep(RATE_LIMIT_SECONDS)

    return new_decks, page


def sweep(
    conn: sqlite3.Connection,
    db_path: Path,
    card_names: list[str],
    fmt: str | None,
    page_size: int,
    fetch_cards: bool,
    early_stop: bool,
) -> None:
    n = len(card_names)
    fmt_label = fmt or "all formats"
    print(f"Sweeping {n} card(s) [{fmt_label}]  early_stop={'on' if early_stop else 'off'}")

    total_new_decks = 0
    w = len(str(n))

    for idx, card_name in enumerate(card_names, 1):
        print(f"\n  [{idx:>{w}}/{n}] {card_name}", flush=True)
        new_decks, pages = _sweep_one_card(
            conn, card_name, fmt, page_size, fetch_cards, early_stop
        )
        total_new_decks += new_decks
        if new_decks:
            print(f"    -> +{new_decks} new decks ({pages} page(s))")

        time.sleep(RATE_LIMIT_SECONDS)

    print(f"\nDone. +{total_new_decks} new decks -> {db_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape Moxfield decks targeted by card name, format legality, or all cards.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes (mutually exclusive):
  --card "Name"    Search for decks containing a specific card.
  --format <fmt>   Search for decks containing any card legal in that format.
  (neither)        Search for decks containing any card in the database.

Examples:
  python src/scraping/deck_scraper.py --card "Lightning Bolt"
  python src/scraping/deck_scraper.py --format pauper
  python src/scraping/deck_scraper.py --format commander --early-stop
  python src/scraping/deck_scraper.py --skip-cards
""",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--card", "-c", dest="card", default=None,
                      help="Scrape decks containing a specific card name")
    mode.add_argument("--format", "-f", dest="format", default=None,
                      choices=LEGAL_FORMATS,
                      help="Scrape decks for all cards legal in this format")
    parser.add_argument("--page-size", dest="page_size", type=int, default=100,
                        choices=[10, 25, 50, 64, 100],
                        help="Decks per API page (default: 100)")
    parser.add_argument("--skip-cards", dest="skip_cards", action="store_true",
                        help="Store deck metadata only; skip fetching card contents")
    parser.add_argument("--early-stop", dest="early_stop", action="store_true",
                        help="Stop paginating a card once a full page is all already-seen decks")
    parser.add_argument("--db", dest="db_path", default=str(DB_PATH),
                        help=f"SQLite database path (default: {DB_PATH})")

    args = parser.parse_args()

    db_path = Path(args.db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)

    card_names = get_cards_for_mode(conn, args.card, args.format)
    if not card_names:
        print("No cards found matching the given criteria.")
        conn.close()
        return

    try:
        sweep(
            conn=conn,
            db_path=db_path,
            card_names=card_names,
            fmt=args.format,
            page_size=args.page_size,
            fetch_cards=not args.skip_cards,
            early_stop=args.early_stop,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
