"""
central_node.py - Discover Moxfield decks and record them via the scraper API.

This node leases cards from the API (POST /cards/claim), then paginates through
Moxfield search results for each leased card, posting discovered decks back to
the API. It never writes to the database directly and never chooses which cards
to work on — the API hands out distinct cards so any number of nodes can run in
parallel without overlapping.

Sweep mode is decided by the API per card: cards never fully swept run in
full-sweep mode (all pages); previously-swept cards run in incremental mode
(stops once previously-seen decks appear).

Exactly one format is processed per invocation (--format is required).

Usage:
    python "src/distributed scraper/central_node.py" --format pauper
    python "src/distributed scraper/central_node.py" --format commander
    python "src/distributed scraper/central_node.py" --format commander --batch-size 10

Environment:
    SCRAPER_API_URL   Base URL of the scraper API (default: http://localhost:8000)
    API_KEY           Shared API key for authentication (required)
"""

import argparse
import os
import socket
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

def _api_claim_cards(fmt: str, worker_id: str, batch_size: int) -> list[tuple[str, bool]]:
    """Lease a batch of cards from the API. Returns [(card_name, fully_swept), ...]."""
    resp = requests.post(
        f"{SCRAPER_API_URL}/cards/claim",
        headers=_API_HEADERS,
        json={"format": fmt, "worker_id": worker_id, "batch_size": batch_size},
        timeout=30,
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


def run(fmt: str, page_size: int, worker_id: str, batch_size: int, delay: float) -> None:
    """
    Claim cards from the API and sweep them until no claimable cards remain.

    A transient API failure does not kill the node: the claim is retried after
    `delay` seconds (matching the scraper node), so a brief API outage self-heals
    instead of sidelining the node until the wrapper's next restart.
    """
    print(f"Central node [{fmt}]  worker={worker_id}  batch={batch_size}")

    total_decks = 0
    cards_done = 0

    while True:
        try:
            cards = _api_claim_cards(fmt, worker_id, batch_size)
        except requests.RequestException as exc:
            print(f"  [warn] claim request to {SCRAPER_API_URL} failed: {exc} "
                  f"- retrying in {delay:.0f}s")
            time.sleep(delay)
            continue

        if not cards:
            print(f"\nNo claimable cards for {fmt}. "
                  f"Processed {cards_done} card(s), discovered {total_decks} deck(s).")
            return

        for card_name, fully_swept in cards:
            full_sweep = not fully_swept
            mode = "incremental" if fully_swept else "full"
            print(f"\n  {card_name}  [{mode}]", flush=True)
            new_decks, pages, reached_end = _sweep_one_card(card_name, fmt, page_size, full_sweep)
            total_decks += new_decks
            cards_done += 1
            if new_decks:
                print(f"    -> +{new_decks} deck(s) across {pages} page(s)")
            # Flip swept FALSE->TRUE only when a first full sweep completes.
            # Incremental refreshes are already swept; their claim timestamp
            # (stamped at claim time) advances the refresh queue on its own.
            if reached_end and not fully_swept:
                try:
                    _api_mark_swept(card_name, fmt)
                except requests.RequestException as exc:
                    print(f"    [warn] Could not mark {card_name!r} swept: {exc}")
            time.sleep(RATE_LIMIT_SECONDS)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Central node: discover Moxfield decks via API-leased cards.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python "src/distributed scraper/central_node.py" --format pauper
  python "src/distributed scraper/central_node.py" --format commander --batch-size 10
""",
    )

    parser.add_argument("--format", "-f", dest="format", required=True,
                        choices=ALL_FORMATS, metavar="FORMAT",
                        help="Format to process (exactly one; required).")
    parser.add_argument("--page-size", dest="page_size", type=int, default=100,
                        choices=[10, 25, 50, 64, 100])
    parser.add_argument("--batch-size", dest="batch_size", type=int, default=5,
                        help="Cards to lease from the API per claim request (default 5).")
    parser.add_argument("--worker-id", dest="worker_id", default=None, metavar="ID",
                        help="Identifier stored with leased cards (default hostname:pid).")
    parser.add_argument("--delay", dest="delay", type=float, default=30.0,
                        help="Seconds to wait before retrying after an API failure (default 30).")

    args = parser.parse_args()
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}"

    fmt = args.format
    print(f"\n{'='*60}")
    print(f"Format: {fmt}")
    print(f"{'='*60}")
    run(fmt, args.page_size, worker_id, args.batch_size, args.delay)


if __name__ == "__main__":
    main()
