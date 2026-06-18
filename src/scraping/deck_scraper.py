"""
deck_scraper.py - Scrape Moxfield decks targeted by a local card list.

Rather than broad page scraping, this script queries the Postgres card database
to decide which cards to search for, then hits Moxfield's per-card search
endpoint for each one (up to the 100-page API limit). Decks already stored
and unchanged are skipped.

By default runs in incremental mode: pages are fetched newest-first and
pagination stops for a card as soon as any deck on a page already has cards
stored in the DB (meaning we've caught up to previously scraped data).
Pass --full-sweep to disable this and fetch all pages regardless.

Note: --skip-cards disables card fetching, so incremental mode cannot detect
already-scraped decks. Combining --skip-cards with incremental mode behaves
identically to --full-sweep.

Usage:
    python src/scraping/deck_scraper.py --card "Lightning Bolt"
    python src/scraping/deck_scraper.py --format pauper
    python src/scraping/deck_scraper.py
    python src/scraping/deck_scraper.py --full-sweep
    python src/scraping/deck_scraper.py --format commander --skip-cards

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
import requests

from constants.moxfield import (
    API_DECK, API_SEARCH, BOARDS, HEADERS, LEGAL_FORMATS, RATE_LIMIT_SECONDS,
    moxfield_search_name, parse_deck,
)
from constants.env import load_env


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def upsert_deck(conn, deck: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO decks (
                public_id, name, format, author, color_mask,
                created_at_utc, updated_at_utc, scraped_at
            ) VALUES (
                %(public_id)s, %(name)s, %(format)s, %(author)s, %(color_mask)s,
                %(created_at_utc)s, %(updated_at_utc)s, %(scraped_at)s
            )
            ON CONFLICT (public_id) DO UPDATE SET
                name           = EXCLUDED.name,
                format         = EXCLUDED.format,
                author         = EXCLUDED.author,
                color_mask     = EXCLUDED.color_mask,
                updated_at_utc = EXCLUDED.updated_at_utc,
                scraped_at     = EXCLUDED.scraped_at
        """, deck)


def deck_needs_card_fetch(conn, public_id: str, updated_at_utc: str | None) -> bool:
    """Return True if the deck's cards have never been fetched, or the deck was updated since."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cards_fetched_at, updated_at_utc FROM decks WHERE public_id = %s",
            (public_id,)
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        return True
    return updated_at_utc is not None and updated_at_utc > row[1]


def _decks_with_cards(conn, public_ids: list[str]) -> set[str]:
    """Return the subset of public_ids that already have card rows in deck_cards."""
    if not public_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT deck_id FROM deck_cards WHERE deck_id = ANY(%s)",
            (public_ids,)
        )
        return {row[0] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Card selection
# ---------------------------------------------------------------------------

def get_cards_for_mode(conn, card: str | None, fmt: str | None) -> list[str]:
    """
    Return the ordered list of card names to drive deck searches.
      card set  → [card]
      fmt set   → all cards where legal_{fmt} = 'legal'
      neither   → all cards in the database
    """
    if card:
        return [card]
    with conn.cursor() as cur:
        if fmt:
            cur.execute(
                f"SELECT card_name FROM cards WHERE legal_{fmt} = 'legal' ORDER BY card_name"
            )
        else:
            cur.execute("SELECT card_name FROM cards ORDER BY card_name")
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def fetch_page(fmt: str | None, page: int, page_size: int, card_name: str | None = None) -> dict:
    params: dict = {
        "pageNumber":    page,
        "pageSize":      page_size,
        "sortType":      "created",
        "sortDirection": "descending",
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

def _fetch_cards_for_page(raw_decks: list[dict], conn) -> int:
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
        if not deck_card_rows:
            continue  # empty deck — leave status unchanged for retry

        with conn.cursor() as cur:
            cur.execute(
                "CALL replace_deck_cards(%s, %s)",
                (public_id, json.dumps(deck_card_rows)),
            )
        rows_written += len(deck_card_rows)

    return rows_written


def _sweep_one_card(
    conn,
    card_name: str,
    fmt: str | None,
    page_size: int,
    fetch_cards: bool,
    full_sweep: bool,
) -> tuple[int, int]:
    effective_full_sweep = full_sweep or not fetch_cards
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

        parsed_page = [d for raw in raw_decks if (d := parse_deck(raw, fmt))["public_id"]]

        has_cards = (
            set() if effective_full_sweep
            else _decks_with_cards(conn, [d["public_id"] for d in parsed_page])
        )

        page_new = sum(1 for d in parsed_page if d["public_id"] not in has_cards)

        for deck in parsed_page:
            upsert_deck(conn, deck)
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
        if not effective_full_sweep and page_new < len(parsed_page):
            break

        page += 1
        time.sleep(RATE_LIMIT_SECONDS)

    return new_decks, page


def sweep(conn, card_names: list[str], fmt: str | None, page_size: int, fetch_cards: bool, full_sweep: bool) -> None:
    n = len(card_names)
    fmt_label = fmt or "all formats"
    mode = "full" if (full_sweep or not fetch_cards) else "incremental"
    print(f"Sweeping {n} card(s) [{fmt_label}]  mode={mode}")

    total_new_decks = 0
    w = len(str(n))

    for idx, card_name in enumerate(card_names, 1):
        print(f"\n  [{idx:>{w}}/{n}] {card_name}", flush=True)
        new_decks, pages = _sweep_one_card(conn, card_name, fmt, page_size, fetch_cards, full_sweep)
        total_new_decks += new_decks
        if new_decks:
            print(f"    -> +{new_decks} new decks ({pages} page(s))")
        time.sleep(RATE_LIMIT_SECONDS)

    print(f"\nDone. +{total_new_decks} new decks.")


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
  python src/scraping/deck_scraper.py --format commander --full-sweep
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
    parser.add_argument("--full-sweep", dest="full_sweep", action="store_true",
                        help="Fetch all pages for every card, ignoring already-scraped decks")

    args = parser.parse_args()

    load_env()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL not set. Fill in src/distributed scraper/.env and retry.")
        return

    conn = psycopg2.connect(pg_url)

    try:
        card_names = get_cards_for_mode(conn, args.card, args.format)
        if not card_names:
            print("No cards found matching the given criteria.")
            return

        sweep(
            conn=conn,
            card_names=card_names,
            fmt=args.format,
            page_size=args.page_size,
            fetch_cards=not args.skip_cards,
            full_sweep=args.full_sweep,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
