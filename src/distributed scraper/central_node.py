"""
central_node.py - Discover Moxfield decks and record them in the shared database.

This node only discovers decks (stores public_id + metadata) and never fetches
card contents. Scraper nodes handle card fetching separately.

The card list used to drive searches is read from the shared Postgres cards
table (seeded by seed_cards.py). Run seed_cards.py at least once before
starting the central node.

Usage:
    python "src/distributed scraper/central_node.py"
    python "src/distributed scraper/central_node.py" --format pauper
    python "src/distributed scraper/central_node.py" --card "Lightning Bolt"
    python "src/distributed scraper/central_node.py" --format commander --early-stop

Environment:
    DATABASE_URL  PostgreSQL connection string (required)
"""

import argparse
import time
from datetime import datetime, timezone

import requests

from config import (
    API_SEARCH,
    HEADERS,
    LEGAL_FORMATS,
    RATE_LIMIT_SECONDS,
    encode_colors,
)
from db import apply_schema, get_connection


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_cards_for_mode(conn, card: str | None, fmt: str | None) -> list[str]:
    with conn.cursor() as cur:
        if card:
            return [card]
        if fmt:
            cur.execute(
                f"SELECT card_name FROM cards WHERE legal_{fmt} = 'legal' ORDER BY card_name"
            )
        else:
            cur.execute("SELECT card_name FROM cards ORDER BY card_name")
        return [row[0] for row in cur.fetchall()]


def upsert_deck(conn, deck: dict) -> None:
    """
    Insert or update a deck row.  The status column is intentionally excluded
    from ON CONFLICT updates so that a deck already claimed or done does not
    get reset to 'discovered'.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO decks (
                public_id, name, format, author, color_mask,
                created_at_utc, updated_at_utc, scraped_at, status
            ) VALUES (
                %(public_id)s, %(name)s, %(format)s, %(author)s, %(color_mask)s,
                %(created_at_utc)s, %(updated_at_utc)s, %(scraped_at)s, 'discovered'
            )
            ON CONFLICT (public_id) DO UPDATE SET
                name           = EXCLUDED.name,
                format         = EXCLUDED.format,
                author         = EXCLUDED.author,
                color_mask     = EXCLUDED.color_mask,
                updated_at_utc = EXCLUDED.updated_at_utc,
                scraped_at     = EXCLUDED.scraped_at
        """, deck)


# ---------------------------------------------------------------------------
# Moxfield API
# ---------------------------------------------------------------------------

def fetch_page(fmt: str | None, page: int, page_size: int, card_name: str | None) -> dict:
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


def parse_deck(raw: dict, fmt: str | None) -> dict:
    colors    = raw.get("colorIdentity") or raw.get("colors") or []
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


def moxfield_search_name(card_name: str) -> str:
    return card_name.split(" // ")[0] if " // " in card_name else card_name


# ---------------------------------------------------------------------------
# Core sweep logic
# ---------------------------------------------------------------------------

def _sweep_one_card(
    conn,
    card_name: str,
    fmt: str | None,
    page_size: int,
    early_stop: bool,
) -> tuple[int, int]:
    """
    Paginate through Moxfield decks containing card_name.
    Returns (new_decks_inserted, pages_fetched).
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
            page_new += 1

        conn.commit()
        print(f"{len(raw_decks)} decks upserted, {page_new} processed")
        new_decks += page_new

        if page >= total_pages or len(raw_decks) < page_size:
            break
        if early_stop and page_new == 0:
            break

        page += 1
        time.sleep(RATE_LIMIT_SECONDS)

    return new_decks, page


def sweep(conn, card_names: list[str], fmt: str | None, page_size: int, early_stop: bool) -> None:
    n = len(card_names)
    fmt_label = fmt or "all formats"
    print(f"Central node: sweeping {n} card(s) [{fmt_label}]  early_stop={'on' if early_stop else 'off'}")

    total = 0
    w = len(str(n))

    for idx, card_name in enumerate(card_names, 1):
        print(f"\n  [{idx:>{w}}/{n}] {card_name}", flush=True)
        new_decks, pages = _sweep_one_card(conn, card_name, fmt, page_size, early_stop)
        total += new_decks
        if new_decks:
            print(f"    -> +{new_decks} deck(s) across {pages} page(s)")
        time.sleep(RATE_LIMIT_SECONDS)

    print(f"\nDone. {total} deck(s) upserted as 'discovered'.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Central node: discover Moxfield decks and store them in Postgres.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes (mutually exclusive):
  --card "Name"    Search decks containing a specific card.
  --format <fmt>   Search decks for all cards legal in that format.
  (neither)        Search decks for every card in the cards table.

Examples:
  python "src/distributed scraper/central_node.py" --format pauper
  python "src/distributed scraper/central_node.py" --card "Lightning Bolt"
  python "src/distributed scraper/central_node.py" --format commander --early-stop
""",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--card",   "-c", dest="card",   default=None)
    mode.add_argument("--format", "-f", dest="format", default=None, choices=LEGAL_FORMATS)

    parser.add_argument("--page-size",  dest="page_size",  type=int, default=100,
                        choices=[10, 25, 50, 64, 100])
    parser.add_argument("--early-stop", dest="early_stop", action="store_true",
                        help="Stop paginating a card once a full page is all already-seen decks")

    args = parser.parse_args()

    conn = get_connection()
    apply_schema(conn)

    card_names = get_cards_for_mode(conn, args.card, args.format)
    if not card_names:
        print("No cards found in the database. Run seed_cards.py first.")
        conn.close()
        return

    try:
        sweep(conn, card_names, args.format, args.page_size, args.early_stop)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
