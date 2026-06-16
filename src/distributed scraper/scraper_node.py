"""
scraper_node.py - Claim batches of discovered decks and fetch their card lists.

Multiple instances can run on different machines simultaneously.  Each node
atomically claims a batch of 'discovered' decks using SELECT FOR UPDATE SKIP
LOCKED so two nodes never process the same deck.

Decks that have been claimed but not finished within CLAIM_TIMEOUT_MINUTES are
eligible for reclaiming (handles node crashes / network failures).

Usage:
    python "src/distributed scraper/scraper_node.py"
    python "src/distributed scraper/scraper_node.py" --batch-size 25
    python "src/distributed scraper/scraper_node.py" --once
    python "src/distributed scraper/scraper_node.py" --worker-id my-node-1

Environment:
    DATABASE_URL  PostgreSQL connection string (required)
"""

import argparse
import socket
import os
import time
from datetime import datetime, timezone

import requests

from config import (
    API_DECK,
    BOARDS,
    CLAIM_TIMEOUT_MINUTES,
    DEFAULT_BATCH_SIZE,
    HEADERS,
    RATE_LIMIT_SECONDS,
)
from db import apply_schema, get_connection


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------

def claim_batch(conn, batch_size: int, worker_id: str) -> list[str]:
    """
    Atomically claim up to batch_size 'discovered' decks (or stale 'claimed'
    decks whose claim has timed out).  Returns the list of claimed public_ids.
    """
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE decks
            SET    status     = 'claimed',
                   claimed_at = NOW(),
                   claimed_by = %s
            WHERE  public_id IN (
                SELECT public_id
                FROM   decks
                WHERE  status = 'discovered'
                   OR  (
                           status     = 'claimed'
                       AND claimed_at < NOW() - (%s * INTERVAL '1 minute')
                   )
                ORDER  BY scraped_at
                LIMIT  %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING public_id
        """, (worker_id, CLAIM_TIMEOUT_MINUTES, batch_size))
        conn.commit()
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Card resolution
# ---------------------------------------------------------------------------

def resolve_card_names(conn, names: list[str]) -> dict[str, int]:
    """Return {card_name: id} for all names that exist in the cards table."""
    if not names:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT card_name, id FROM cards WHERE card_name = ANY(%s)",
            (names,),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Database writes
# ---------------------------------------------------------------------------

def replace_deck_cards(conn, deck_id: str, deck_card_rows: list[dict]) -> int:
    """
    Delete existing card rows for the deck and insert the new ones.
    Returns the number of rows written.
    """
    if not deck_card_rows:
        return 0

    names = list({r["card_name"] for r in deck_card_rows})
    name_to_id = resolve_card_names(conn, names)

    for name in names:
        if name not in name_to_id:
            print(f"    [warn] card not in DB, skipping: {name!r}")

    rows = [
        (deck_id, name_to_id[r["card_name"]], r["board"], r["quantity"])
        for r in deck_card_rows
        if r["card_name"] in name_to_id
    ]

    with conn.cursor() as cur:
        cur.execute("DELETE FROM deck_cards WHERE deck_id = %s", (deck_id,))
        if rows:
            cur.executemany(
                "INSERT INTO deck_cards (deck_id, card_id, board, quantity) VALUES (%s, %s, %s, %s)",
                rows,
            )
        cur.execute(
            "UPDATE decks SET status = 'done', cards_fetched_at = %s WHERE public_id = %s",
            (datetime.now(timezone.utc).isoformat(), deck_id),
        )

    conn.commit()
    return len(rows)


def mark_error(conn, deck_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE decks SET status = 'error' WHERE public_id = %s",
            (deck_id,),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Moxfield API
# ---------------------------------------------------------------------------

def fetch_deck_detail(public_id: str) -> dict:
    url  = API_DECK.format(public_id=public_id)
    resp = requests.get(url, headers=HEADERS, timeout=15)
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

def process_batch(conn, deck_ids: list[str]) -> tuple[int, int]:
    """Process a batch of claimed decks.  Returns (done, errors)."""
    done   = 0
    errors = 0

    for i, deck_id in enumerate(deck_ids):
        if i > 0:
            time.sleep(RATE_LIMIT_SECONDS)

        print(f"  [{i + 1}/{len(deck_ids)}] {deck_id}", end=" ... ", flush=True)
        try:
            detail         = fetch_deck_detail(deck_id)
            deck_card_rows = parse_deck_detail(detail)
            n              = replace_deck_cards(conn, deck_id, deck_card_rows)
            print(f"done ({n} card-rows)")
            done += 1
        except requests.HTTPError as exc:
            code = exc.response.status_code
            print(f"HTTP {code} — marking error")
            mark_error(conn, deck_id)
            errors += 1
        except requests.RequestException as exc:
            print(f"request failed ({exc}) — marking error")
            mark_error(conn, deck_id)
            errors += 1

    return done, errors


def run_loop(conn, batch_size: int, worker_id: str, once: bool, delay: float) -> None:
    print(f"Scraper node '{worker_id}' starting  (batch_size={batch_size})")

    total_done   = 0
    total_errors = 0

    while True:
        deck_ids = claim_batch(conn, batch_size, worker_id)

        if not deck_ids:
            if once:
                print("No discoverable decks — exiting.")
                break
            print(f"No discoverable decks — sleeping {delay:.0f}s ...", flush=True)
            time.sleep(delay)
            continue

        print(f"\nClaimed {len(deck_ids)} deck(s):")
        done, errors = process_batch(conn, deck_ids)
        total_done   += done
        total_errors += errors
        print(f"  Batch done: {done} ok, {errors} errors  "
              f"(total: {total_done} ok, {total_errors} errors)")

        if once:
            break

    print(f"\nFinished. {total_done} decks processed, {total_errors} errors.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper node: claim and process discovered decks from Postgres.",
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
        help=f"Number of decks to claim per batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--worker-id", dest="worker_id", default=None,
        help="Identifier for this node stored in the DB (default: hostname:pid)",
    )
    parser.add_argument(
        "--once", dest="once", action="store_true",
        help="Process one batch then exit (default: loop until no work remains)",
    )
    parser.add_argument(
        "--delay", dest="delay", type=float, default=30.0,
        help="Seconds to sleep between polls when no work is available (default: 30)",
    )
    args = parser.parse_args()

    worker_id = args.worker_id or default_worker_id()

    conn = get_connection()
    apply_schema(conn)

    try:
        run_loop(conn, args.batch_size, worker_id, args.once, args.delay)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
