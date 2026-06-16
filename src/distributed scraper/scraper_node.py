"""
scraper_node.py - Claim batches of discovered decks and fetch their card lists.

Multiple instances can run on different machines simultaneously. Each node
requests a batch of unclaimed decks from the API, fetches their card contents
from Moxfield, and submits the results back through the API. No direct database
access is required.

Decks that have been claimed but not finished within CLAIM_TIMEOUT_MINUTES are
automatically eligible for reclaiming by another node (handles crashes /
network failures).

Usage:
    python "src/distributed scraper/scraper_node.py"
    python "src/distributed scraper/scraper_node.py" --batch-size 25
    python "src/distributed scraper/scraper_node.py" --once
    python "src/distributed scraper/scraper_node.py" --worker-id my-node-1

Environment:
    SCRAPER_API_URL   Base URL of the scraper API (default: http://localhost:8000)
    API_KEY           Shared API key for authentication (required)
"""

import argparse
import os
import socket
import time

import requests

from config import (
    API_DECK,
    BOARDS,
    DEFAULT_BATCH_SIZE,
    HEADERS as MOXFIELD_HEADERS,
    RATE_LIMIT_SECONDS,
    SCRAPER_API_KEY,
    SCRAPER_API_URL,
)

_API_HEADERS = {"X-Api-Key": SCRAPER_API_KEY}


# ---------------------------------------------------------------------------
# Scraper API helpers
# ---------------------------------------------------------------------------

def api_claim_batch(batch_size: int, worker_id: str) -> list[dict]:
    """Request a batch of unclaimed decks from the API. Returns list of deck objects."""
    resp = requests.post(
        f"{SCRAPER_API_URL}/decks/batch",
        headers=_API_HEADERS,
        json={"batch_size": batch_size, "worker_id": worker_id},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def api_submit_cards(public_id: str, worker_id: str, cards: list[dict]) -> int:
    """
    Submit card rows for a processed deck.
    Returns the number of rows written, or -1 if the deck was already done (collision).
    """
    resp = requests.post(
        f"{SCRAPER_API_URL}/decks/{public_id}/cards",
        headers=_API_HEADERS,
        json={"worker_id": worker_id, "cards": cards},
        timeout=30,
    )
    if resp.status_code == 409:
        return -1
    resp.raise_for_status()
    return resp.json()["rows_written"]


def api_report_error(public_id: str, worker_id: str, detail: str) -> None:
    """Mark a deck as errored. Best-effort; ignores failures."""
    try:
        requests.post(
            f"{SCRAPER_API_URL}/decks/{public_id}/error",
            headers=_API_HEADERS,
            json={"worker_id": worker_id, "detail": detail},
            timeout=10,
        )
    except requests.RequestException:
        pass


# ---------------------------------------------------------------------------
# Moxfield API
# ---------------------------------------------------------------------------

def fetch_deck_detail(public_id: str) -> dict:
    url  = API_DECK.format(public_id=public_id)
    resp = requests.get(url, headers=MOXFIELD_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_deck_detail(detail: dict) -> list[dict]:
    rows: list[dict] = []
    for board_name in BOARDS:
        board_data = detail.get(board_name) or {}
        for entry in board_data.values():
            card_name = (entry.get("card") or {}).get("name")
            if not card_name:
                continue
            rows.append({
                "card_name": card_name,
                "board":     board_name,
                "quantity":  entry.get("quantity", 1),
            })
    return rows


# ---------------------------------------------------------------------------
# Processing loop
# ---------------------------------------------------------------------------

def process_batch(decks: list[dict], worker_id: str) -> tuple[int, int, int]:
    """Process a claimed batch. Returns (done, collisions, errors)."""
    done       = 0
    collisions = 0
    errors     = 0

    for i, deck in enumerate(decks):
        public_id = deck["public_id"]
        if i > 0:
            time.sleep(RATE_LIMIT_SECONDS)

        print(f"  [{i + 1}/{len(decks)}] {public_id}", end=" ... ", flush=True)
        try:
            detail         = fetch_deck_detail(public_id)
            deck_card_rows = parse_deck_detail(detail)
            n              = api_submit_cards(public_id, worker_id, deck_card_rows)
            if n == -1:
                print("collision — deck already processed")
                collisions += 1
            else:
                print(f"done ({n} card-rows)")
                done += 1
        except requests.HTTPError as exc:
            code = exc.response.status_code
            print(f"HTTP {code} — marking error")
            api_report_error(public_id, worker_id, f"HTTP {code}")
            errors += 1
        except requests.RequestException as exc:
            print(f"request failed ({exc}) — marking error")
            api_report_error(public_id, worker_id, str(exc))
            errors += 1

    return done, collisions, errors


def run_loop(batch_size: int, worker_id: str, once: bool, delay: float) -> None:
    print(f"Scraper node '{worker_id}' starting  (batch_size={batch_size})")

    total_done       = 0
    total_collisions = 0
    total_errors     = 0

    while True:
        try:
            decks = api_claim_batch(batch_size, worker_id)
        except requests.RequestException as exc:
            print(f"[error] Failed to claim batch: {exc}. Sleeping {delay:.0f}s ...")
            time.sleep(delay)
            continue

        if not decks:
            print("No claimable decks — exiting.")
            break

        print(f"\nClaimed {len(decks)} deck(s):")
        done, collisions, errors = process_batch(decks, worker_id)
        total_done       += done
        total_collisions += collisions
        total_errors     += errors
        print(
            f"  Batch done: {done} ok, {collisions} collision(s), {errors} error(s)  "
            f"(total: {total_done} ok, {total_collisions} collision(s), {total_errors} error(s))"
        )

        if once:
            break

    print(f"\nFinished. {total_done} processed, {total_collisions} collision(s), {total_errors} error(s).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper node: claim and process discovered decks via the API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python "src/distributed scraper/scraper_node.py"
  python "src/distributed scraper/scraper_node.py" --batch-size 25
  python "src/distributed scraper/scraper_node.py" --once
  python "src/distributed scraper/scraper_node.py" --worker-id server-2
""",
    )
    parser.add_argument(
        "--batch-size", dest="batch_size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"Decks to claim per batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--worker-id", dest="worker_id", default=None,
        help="Identifier stored with claimed decks (default: hostname:pid)",
    )
    parser.add_argument(
        "--once", dest="once", action="store_true",
        help="Process one batch then exit",
    )
    parser.add_argument(
        "--delay", dest="delay", type=float, default=30.0,
        help="Seconds to sleep between polls when no work is available (default: 30)",
    )
    args = parser.parse_args()

    run_loop(args.batch_size, args.worker_id or _default_worker_id(), args.once, args.delay)


if __name__ == "__main__":
    main()
