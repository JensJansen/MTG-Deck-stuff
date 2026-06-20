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
import traceback

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


def _log(phase: str, msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{phase}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Scraper API helpers
# ---------------------------------------------------------------------------

def api_claim_batch(batch_size: int, worker_id: str) -> list[dict]:
    t0 = time.monotonic()
    _log("CLAIM", f"requesting batch (size={batch_size}) from {SCRAPER_API_URL}/decks/batch")
    resp = requests.post(
        f"{SCRAPER_API_URL}/decks/batch",
        headers=_API_HEADERS,
        json={"batch_size": batch_size, "worker_id": worker_id},
        timeout=45,
    )
    elapsed = time.monotonic() - t0
    _log("CLAIM", f"response {resp.status_code} in {elapsed:.2f}s")
    resp.raise_for_status()
    decks = resp.json()
    _log("CLAIM", f"claimed {len(decks)} deck(s)")
    return decks


def api_submit_batch(submissions: list[dict]) -> list[dict]:
    t0 = time.monotonic()
    _log("PERSIST", f"submitting {len(submissions)} deck(s) to {SCRAPER_API_URL}/decks/cards/batch")
    resp = requests.post(
        f"{SCRAPER_API_URL}/decks/cards/batch",
        headers=_API_HEADERS,
        json=submissions,
        timeout=60,
    )
    elapsed = time.monotonic() - t0
    _log("PERSIST", f"response {resp.status_code} in {elapsed:.2f}s")
    if not resp.ok:
        body_preview = resp.text[:300].replace("\n", " ")
        _log("PERSIST", f"error body: {body_preview}")
    resp.raise_for_status()
    return resp.json()


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

def process_batch(decks: list[dict], worker_id: str) -> tuple[int, int]:
    """
    Process a claimed batch. Returns (done, errors).

    Fetches all decks from Moxfield first (with rate limiting), then submits
    all successful results to the API in a single batch request. Failed decks
    are skipped and will be automatically reclaimed after CLAIM_TIMEOUT_MINUTES.
    """
    done   = 0
    errors = 0

    # ---- Phase 2: fetch card details from Moxfield -------------------------
    _log("MOXFIELD", f"fetching {len(decks)} deck(s) from Moxfield")
    t_phase = time.monotonic()
    submissions: list[dict] = []

    for i, deck in enumerate(decks):
        public_id = deck["public_id"]
        fmt       = deck.get("format", "unknown")
        if i > 0:
            time.sleep(RATE_LIMIT_SECONDS)

        print(f"  [{i + 1}/{len(decks)}] {public_id} ({fmt})", end=" ... ", flush=True)
        t0 = time.monotonic()
        try:
            detail         = fetch_deck_detail(public_id)
            deck_card_rows = parse_deck_detail(detail)
            elapsed = time.monotonic() - t0
            submissions.append({
                "deck_id":   public_id,
                "worker_id": worker_id,
                "format":    fmt,
                "cards":     deck_card_rows,
            })
            print(f"ok ({len(deck_card_rows)} cards, {elapsed:.2f}s)", flush=True)
        except requests.HTTPError as exc:
            elapsed = time.monotonic() - t0
            body_preview = exc.response.text[:120].replace("\n", " ")
            print(f"HTTP {exc.response.status_code} in {elapsed:.2f}s — skipping  [{body_preview}]", flush=True)
            errors += 1
        except requests.RequestException as exc:
            elapsed = time.monotonic() - t0
            print(f"request error in {elapsed:.2f}s — skipping  [{type(exc).__name__}: {exc}]", flush=True)
            errors += 1

    mox_elapsed = time.monotonic() - t_phase
    _log("MOXFIELD", f"done: {len(submissions)} fetched, {errors} failed in {mox_elapsed:.1f}s")

    if not submissions:
        _log("PERSIST", "nothing to submit (all Moxfield fetches failed)")
        return done, errors

    # ---- Phase 3: submit to API --------------------------------------------
    try:
        t0 = time.monotonic()
        results = api_submit_batch(submissions)
        elapsed = time.monotonic() - t0

        for result in results:
            rows = result.get("rows_written", 0)
            _log("PERSIST", f"  {result['deck_id']}: {rows} card-row(s) written")
            done += 1

        _log("PERSIST", f"batch committed: {done} deck(s) in {elapsed:.1f}s")

    except requests.HTTPError as exc:
        _log("PERSIST", f"API error {exc.response.status_code} — batch NOT persisted, decks will be reclaimed")
        _log("PERSIST", f"response body: {exc.response.text[:500]}")
        errors += len(submissions)
    except requests.Timeout:
        _log("PERSIST", f"API timed out after 60s — batch NOT persisted, decks will be reclaimed")
        errors += len(submissions)
    except requests.RequestException as exc:
        _log("PERSIST", f"API connection error — batch NOT persisted, decks will be reclaimed")
        _log("PERSIST", f"{type(exc).__name__}: {exc}")
        errors += len(submissions)
    except Exception as exc:
        _log("PERSIST", f"unexpected error during submission — batch NOT persisted")
        _log("PERSIST", traceback.format_exc())
        errors += len(submissions)

    return done, errors


def run_loop(batch_size: int, worker_id: str, once: bool, delay: float) -> None:
    _log("INIT", f"scraper node '{worker_id}' starting  (batch_size={batch_size}, api={SCRAPER_API_URL})")
    _log("INIT", f"api_key set: {'yes' if SCRAPER_API_KEY else 'NO — requests will fail with 401'}")

    total_done   = 0
    total_errors = 0

    while True:
        # ---- Phase 1: claim -------------------------------------------------
        try:
            decks = api_claim_batch(batch_size, worker_id)
        except requests.HTTPError as exc:
            _log("CLAIM", f"HTTP {exc.response.status_code} — sleeping {delay:.0f}s  [{exc.response.text[:200]}]")
            time.sleep(delay)
            continue
        except requests.RequestException as exc:
            _log("CLAIM", f"{type(exc).__name__}: {exc} — sleeping {delay:.0f}s")
            time.sleep(delay)
            continue

        if not decks:
            if once:
                _log("CLAIM", "no claimable decks")
                break
            _log("CLAIM", f"no claimable decks — sleeping {delay:.0f}s")
            time.sleep(delay)
            continue

        _log("CLAIM", f"processing {len(decks)} deck(s)")
        t_batch = time.monotonic()
        done, errors = process_batch(decks, worker_id)
        batch_elapsed = time.monotonic() - t_batch

        total_done   += done
        total_errors += errors
        _log("BATCH", (
            f"done={done} errors={errors} "
            f"total=({total_done} ok, {total_errors} err) "
            f"elapsed={batch_elapsed:.1f}s"
        ))

        if once:
            break

    _log("INIT", f"finished — {total_done} processed, {total_errors} skipped")


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
