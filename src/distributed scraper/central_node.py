"""
central_node.py - Discover Moxfield decks and record them via the scraper API.

This node paginates through Moxfield search results for each card in the
shared card catalogue, posting discovered decks to the API server. It never
writes to the database directly.

Sweep mode is determined automatically per card from card_sweep_status:
cards that have never been fully swept run in full-sweep mode (all pages);
cards previously swept run in incremental mode (stops when previously-seen
decks are detected).

Usage:
    python "src/distributed scraper/central_node.py"
    python "src/distributed scraper/central_node.py" --format pauper
    python "src/distributed scraper/central_node.py" --format pauper --format modern --format legacy
    python "src/distributed scraper/central_node.py" --format commander --start-card "Lightning Bolt"
    python "src/distributed scraper/central_node.py" --format commander --reversed --start-card "Wizard's Retort"

Environment:
    SCRAPER_API_URL   Base URL of the scraper API (default: http://localhost:8000)
    API_KEY           Shared API key for authentication (required)
"""

import argparse
import time
from urllib.parse import quote

import requests

from config import (
    API_SEARCH,
    HEADERS as MOXFIELD_HEADERS,
    ALL_FORMATS,
    RATE_LIMIT_SECONDS,
    SCRAPER_API_KEY,
    SCRAPER_API_URL,
    moxfield_search_name,
    parse_deck,
)

_API_HEADERS = {"X-Api-Key": SCRAPER_API_KEY}


# ---------------------------------------------------------------------------
# Scraper API helpers
# ---------------------------------------------------------------------------

def _api_get_cards(fmt: str) -> list[tuple[str, bool]]:
    """Returns [(card_name, fully_swept), ...] for cards legal in fmt."""
    resp = requests.get(
        f"{SCRAPER_API_URL}/cards",
        headers=_API_HEADERS,
        params={"format": fmt},
        timeout=15,
    )
    resp.raise_for_status()
    return [(c["name"], c["fully_swept"]) for c in resp.json()]


def _api_mark_swept(card_name: str, fmt: str) -> None:
    resp = requests.post(
        f"{SCRAPER_API_URL}/cards/{quote(card_name, safe='')}/swept",
        headers=_API_HEADERS,
        json={"format": fmt},
        timeout=15,
    )
    resp.raise_for_status()


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

def fetch_page(fmt: str, page: int, page_size: int, card_name: str) -> dict:
    params = {
        "pageNumber":    page,
        "pageSize":      page_size,
        "sortType":      "created",
        "sortDirection": "descending",
        "fmt":           fmt,
        "cardName":      card_name,
    }
    resp = requests.get(API_SEARCH, headers=MOXFIELD_HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Core sweep logic
# ---------------------------------------------------------------------------

def _sweep_one_card(
    card_name: str,
    fmt: str,
    page_size: int,
    full_sweep: bool,
) -> tuple[int, int, bool]:
    """
    Paginate through Moxfield decks containing card_name.
    Returns (new_decks_discovered, pages_fetched, reached_end).
    reached_end is True only when all available pages were exhausted naturally.
    """
    search_name = moxfield_search_name(card_name)
    new_decks = 0
    page = 1
    reached_end = False

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
            reached_end = True
            break

        total_pages = data.get("totalPages", 1)
        print(f"    page {page}/{total_pages} ...", end=" ", flush=True)

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
            reached_end = True
            break
        if not full_sweep and page_new < len(parsed_page):
            break  # incremental early exit — reached_end stays False

        page += 1
        time.sleep(RATE_LIMIT_SECONDS)

    return new_decks, page, reached_end


def sweep(card_infos: list[tuple[str, bool]], fmt: str, page_size: int) -> None:
    n = len(card_infos)
    print(f"Central node: sweeping {n} card(s) [{fmt}]")

    total = 0
    w = len(str(n))

    for idx, (card_name, fully_swept) in enumerate(card_infos, 1):
        full_sweep = not fully_swept
        mode = "incremental" if fully_swept else "full"
        print(f"\n  [{idx:>{w}}/{n}] {card_name}  [{mode}]", flush=True)
        new_decks, pages, reached_end = _sweep_one_card(card_name, fmt, page_size, full_sweep)
        total += new_decks
        if new_decks:
            print(f"    -> +{new_decks} deck(s) across {pages} page(s)")
        if reached_end and not fully_swept:
            try:
                _api_mark_swept(card_name, fmt)
            except requests.RequestException as exc:
                print(f"    [warn] Could not mark {card_name!r} as swept: {exc}")
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
Examples:
  python "src/distributed scraper/central_node.py"
  python "src/distributed scraper/central_node.py" --format pauper
  python "src/distributed scraper/central_node.py" --format pauper --format modern --format legacy
  python "src/distributed scraper/central_node.py" --format commander --reversed --start-card "Wizard's Retort"
""",
    )

    parser.add_argument("--format", "-f", dest="formats", action="append",
                        default=[], choices=ALL_FORMATS, metavar="FORMAT",
                        help="Format to sweep (repeatable). Defaults to all formats.")
    parser.add_argument("--page-size",  dest="page_size",  type=int, default=100,
                        choices=[10, 25, 50, 64, 100])
    parser.add_argument("--reversed", dest="reversed", action="store_true",
                        help="Process cards in reverse order within each format")
    parser.add_argument("--start-card", dest="start_card", default=None, metavar="CARD",
                        help="Skip all cards before this one in the (possibly reversed) list")

    args = parser.parse_args()

    def _apply_start(
        infos: list[tuple[str, bool]], start: str | None, context: str
    ) -> list[tuple[str, bool]] | None:
        if not start:
            return infos
        names = [name for name, _ in infos]
        try:
            idx = names.index(start)
        except ValueError:
            print(f"ERROR: --start-card {start!r} not found in card list for {context}")
            return None
        if idx:
            print(f"  Skipping {idx} card(s) before {start!r}")
        return infos[idx:]

    formats = args.formats if args.formats else list(ALL_FORMATS)

    # Sweep each format independently so per-format Moxfield result pages aren't
    # shared across formats (each gets its own 10,000-deck cap).
    for fmt in formats:
        print(f"\n{'='*60}")
        print(f"Format: {fmt}")
        print(f"{'='*60}")
        try:
            card_infos = _api_get_cards(fmt)
        except requests.RequestException as exc:
            print(f"ERROR: Could not reach API at {SCRAPER_API_URL}: {exc}")
            return
        if not card_infos:
            print(f"No cards found for {fmt}, skipping.")
            continue
        if args.reversed:
            card_infos = list(reversed(card_infos))
        card_infos = _apply_start(card_infos, args.start_card, fmt)
        if card_infos is None:
            return
        sweep(card_infos, fmt, args.page_size)


if __name__ == "__main__":
    main()
