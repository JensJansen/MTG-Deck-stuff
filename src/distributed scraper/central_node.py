"""
central_node.py - Discover Moxfield decks and record them via the scraper API.

This node paginates through Moxfield search results for each card in the
shared card catalogue, posting discovered decks to the API server. It never
writes to the database directly.

By default runs in incremental mode: pages are fetched newest-first and
pagination stops for a card as soon as any deck on a page is already known
(meaning we've caught up to previously discovered data). Pass --full-sweep to
disable this and fetch all pages regardless.

Usage:
    python "src/distributed scraper/central_node.py"
    python "src/distributed scraper/central_node.py" --format pauper
    python "src/distributed scraper/central_node.py" --format pauper --format modern --format legacy
    python "src/distributed scraper/central_node.py" --card "Lightning Bolt"
    python "src/distributed scraper/central_node.py" --format commander --full-sweep
    python "src/distributed scraper/central_node.py" --exclude-format pauper --exclude-format modern
    python "src/distributed scraper/central_node.py" --exclude-format vintage --reversed

Environment:
    SCRAPER_API_URL   Base URL of the scraper API (default: http://localhost:8000)
    API_KEY           Shared API key for authentication (required)
"""

import argparse
import time
from datetime import datetime, timezone

import requests

from config import (
    API_SEARCH,
    HEADERS as MOXFIELD_HEADERS,
    LEGAL_FORMATS,
    RATE_LIMIT_SECONDS,
    SCRAPER_API_KEY,
    SCRAPER_API_URL,
    encode_colors,
)

_API_HEADERS = {"X-Api-Key": SCRAPER_API_KEY}


# ---------------------------------------------------------------------------
# Scraper API helpers
# ---------------------------------------------------------------------------

def _api_get_cards(fmt: str | None) -> list[str]:
    params = {"format": fmt} if fmt else {}
    resp = requests.get(
        f"{SCRAPER_API_URL}/cards",
        headers=_API_HEADERS,
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _api_post_decks(decks: list[dict]) -> dict:
    """POST a parsed page to the API. Returns {upserted, new, existing}."""
    resp = requests.post(
        f"{SCRAPER_API_URL}/decks",
        headers=_API_HEADERS,
        json=decks,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Moxfield API
# ---------------------------------------------------------------------------

def fetch_page(fmt: str | None, page: int, page_size: int, card_name: str | None) -> dict:
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
    resp = requests.get(API_SEARCH, headers=MOXFIELD_HEADERS, params=params, timeout=15)
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
    """Strip the back-face name from double-faced cards for Moxfield search."""
    return card_name.split(" // ")[0] if " // " in card_name else card_name


# ---------------------------------------------------------------------------
# Core sweep logic
# ---------------------------------------------------------------------------

def _sweep_one_card(
    card_name: str,
    fmt: str | None,
    page_size: int,
    full_sweep: bool,
) -> tuple[int, int]:
    """
    Paginate through Moxfield decks containing card_name.
    Returns (new_decks_discovered, pages_fetched).
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

        # Parse all decks once; filter entries with no public_id.
        parsed_page = [d for raw in raw_decks if (d := parse_deck(raw, fmt))["public_id"]]

        try:
            result = _api_post_decks(parsed_page)
        except requests.RequestException as exc:
            print(f"\n    [warn] API error posting decks: {exc}, skipping page")
            break

        page_new = result["new"]
        print(f"{len(raw_decks)} decks, {page_new} new")
        new_decks += page_new

        if page >= total_pages or len(raw_decks) < page_size:
            break
        if not full_sweep and page_new < len(parsed_page):
            break

        page += 1
        time.sleep(RATE_LIMIT_SECONDS)

    return new_decks, page


def sweep(card_names: list[str], fmt: str | None, page_size: int, full_sweep: bool) -> None:
    n = len(card_names)
    fmt_label = fmt or "all formats"
    print(f"Central node: sweeping {n} card(s) [{fmt_label}]  mode={'full' if full_sweep else 'incremental'}")

    total = 0
    w = len(str(n))

    for idx, card_name in enumerate(card_names, 1):
        print(f"\n  [{idx:>{w}}/{n}] {card_name}", flush=True)
        new_decks, pages = _sweep_one_card(card_name, fmt, page_size, full_sweep)
        total += new_decks
        if new_decks:
            print(f"    -> +{new_decks} deck(s) across {pages} page(s)")
        time.sleep(RATE_LIMIT_SECONDS)

    print(f"\nDone. {total} deck(s) discovered.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Central node: discover Moxfield decks via the scraper API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Format selection (pick one approach):
  --format <fmt>          Sweep one or more specific formats (repeatable).
  --exclude-format <fmt>  Sweep all formats except these (repeatable).
  (neither)               Sweep every format.

--format and --exclude-format are mutually exclusive.
--card bypasses format-based card lookup entirely.

Examples:
  python "src/distributed scraper/central_node.py" --format pauper
  python "src/distributed scraper/central_node.py" --format pauper --format modern --format legacy
  python "src/distributed scraper/central_node.py" --exclude-format vintage --exclude-format oldschool
  python "src/distributed scraper/central_node.py" --card "Lightning Bolt"
  python "src/distributed scraper/central_node.py" --format commander --full-sweep
""",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--card", "-c", dest="card", default=None)

    parser.add_argument("--format", "-f", dest="formats", action="append",
                        default=[], choices=LEGAL_FORMATS, metavar="FORMAT",
                        help="Format to sweep (repeatable). Cannot be combined with --exclude-format.")
    parser.add_argument("--exclude-format", "-x", dest="exclude_formats", action="append",
                        default=[], choices=LEGAL_FORMATS, metavar="FORMAT",
                        help="Exclude a format from the full sweep (repeatable). Cannot be combined with --format.")
    parser.add_argument("--page-size",  dest="page_size",  type=int, default=100,
                        choices=[10, 25, 50, 64, 100])
    parser.add_argument("--full-sweep", dest="full_sweep", action="store_true",
                        help="Fetch all pages for every card, ignoring already-discovered decks")
    parser.add_argument("--reversed", dest="reversed", action="store_true",
                        help="Process cards in reverse order within each format")

    args = parser.parse_args()

    if args.formats and args.exclude_formats:
        parser.error("--format and --exclude-format are mutually exclusive")

    if args.card:
        card_names = [args.card]
        if args.reversed:
            card_names = list(reversed(card_names))
        fmt = args.formats[0] if args.formats else None
        sweep(card_names, fmt, args.page_size, args.full_sweep)
        return

    # Determine which formats to sweep.
    if args.formats:
        formats = args.formats
    else:
        formats = [f for f in LEGAL_FORMATS if f not in args.exclude_formats]
        if args.exclude_formats:
            print(f"Excluding formats: {', '.join(args.exclude_formats)}")

    # Sweep each format independently so per-format Moxfield result pages aren't
    # shared across formats (each gets its own 10,000-deck cap).
    for fmt in formats:
        print(f"\n{'='*60}")
        print(f"Format: {fmt}")
        print(f"{'='*60}")
        try:
            card_names = _api_get_cards(fmt)
        except requests.RequestException as exc:
            print(f"ERROR: Could not reach API at {SCRAPER_API_URL}: {exc}")
            return
        if not card_names:
            print(f"No cards found for {fmt}, skipping.")
            continue
        if args.reversed:
            card_names = list(reversed(card_names))
        sweep(card_names, fmt, args.page_size, args.full_sweep)


if __name__ == "__main__":
    main()
