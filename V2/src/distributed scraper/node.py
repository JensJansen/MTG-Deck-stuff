"""
node.py - The single deployable scraper node.

One process runs a pool of identical workers that share one Moxfield session,
one rate limiter, and one API client. There are NO startup role flags: each
worker self-selects its job every iteration based on what the API hands out.

    1. claim a sweep   -> discover decks for that (card, format)
    2. else claim decks -> fetch their card lists
    3. else idle briefly

This yields the two phases automatically: while unswept cards remain everyone
discovers; once the API reports no claimable sweep, everyone drains the deck
queue. When the queue is empty the API starts a fresh sweep cycle on its own
(see /sweeps/claim), so the node runs forever with no external coordination.

Discovery mode comes from the API:
    full        - paginate every page (first-ever sweep of the card)
    incremental - stop early once a page yields no new decks (later cycles)

A deck that fails to fetch MAX_DECK_FETCH_FAILS times in a row (non-429) is
submitted as 'error' so it never blocks the cycle reset.

Usage:
    python "V2/src/distributed scraper/node.py"
    python "V2/src/distributed scraper/node.py" --workers 16

Environment (from .env):
    SCRAPER_API_URL   Base URL of the coordination API
    API_KEY           Shared API key
"""

import argparse
import os
import signal
import socket
import threading
import time

import requests

from api_client import ApiClient
from config import (
    DEFAULT_BATCH_SIZE,
    MAX_DECK_FETCH_FAILS,
    RATE_LIMIT_SECONDS,
    SCRAPER_API_KEY,
    SCRAPER_API_URL,
)
from constants.moxfield import parse_deck_cards, parse_search_deck
from moxfield_client import MoxfieldClient, MoxfieldError
from ratelimiter import RateLimiter

SEARCH_PAGE_SIZE = 100
IDLE_DELAY       = 15.0   # seconds to wait when neither sweeps nor decks are claimable
API_ERROR_DELAY  = 30.0   # seconds to wait after an API failure


def _log(worker: str, phase: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [{worker}] [{phase}] {msg}", flush=True)


class Worker:
    """One self-selecting worker: prefers discovery, falls back to fetching."""

    def __init__(self, worker_id: str, mox: MoxfieldClient, api: ApiClient,
                 batch_size: int, stop: threading.Event) -> None:
        self.id = worker_id
        self.mox = mox
        self.api = api
        self.batch_size = batch_size
        self.stop = stop

    # -- main loop -----------------------------------------------------------

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                sweep = self.api.claim_sweep(self.id)
            except requests.RequestException as exc:
                _log(self.id, "CLAIM", f"sweep claim failed: {exc} — waiting {API_ERROR_DELAY:.0f}s")
                self.stop.wait(API_ERROR_DELAY)
                continue

            if sweep:
                self._discover(sweep["card_name"], sweep["format"], sweep["mode"])
                continue

            try:
                batch = self.api.claim_decks(self.batch_size, self.id)
            except requests.RequestException as exc:
                _log(self.id, "CLAIM", f"deck claim failed: {exc} — waiting {API_ERROR_DELAY:.0f}s")
                self.stop.wait(API_ERROR_DELAY)
                continue

            if batch:
                self._fetch(batch)
                continue

            # Nothing claimable right now (queue drained mid-cycle, or contention).
            self.stop.wait(IDLE_DELAY)

    # -- discovery -----------------------------------------------------------

    def _discover(self, card_name: str, fmt: str, mode: str) -> None:
        incremental = mode == "incremental"
        _log(self.id, "SWEEP", f"{card_name!r} [{fmt}] {mode}")
        total_new = 0
        page = 1
        while not self.stop.is_set():
            try:
                data = self.mox.search_page(fmt, card_name, page, SEARCH_PAGE_SIZE)
            except MoxfieldError as exc:
                # Abort the sweep: don't complete it, so it stays unswept and is
                # re-leased after the lease expires.
                _log(self.id, "SWEEP", f"abort {card_name!r} [{fmt}] p{page}: {exc}")
                return

            raw = data.get("data", [])
            if not raw:
                break  # reached the end

            parsed = [p for r in raw if (p := parse_search_deck(r, fmt))["moxfield_id"]]
            try:
                result = self.api.post_decks(parsed)
            except requests.RequestException as exc:
                _log(self.id, "SWEEP", f"post_decks failed p{page}: {exc} — aborting sweep")
                return
            total_new += result["new"]

            total_pages = data.get("totalPages", 1)
            if page >= total_pages or len(raw) < SEARCH_PAGE_SIZE:
                break  # last page
            if incremental and result["new"] == 0:
                break  # caught up to known decks
            page += 1

        try:
            self.api.complete_sweep(card_name, fmt)
        except requests.RequestException as exc:
            _log(self.id, "SWEEP", f"complete failed {card_name!r} [{fmt}]: {exc}")
            return
        _log(self.id, "SWEEP", f"done {card_name!r} [{fmt}]: +{total_new} new over {page} page(s)")

    # -- fetch ---------------------------------------------------------------

    def _fetch(self, batch: list[dict]) -> None:
        fmt = batch[0]["format"]
        _log(self.id, "FETCH", f"{len(batch)} {fmt} deck(s)")
        submissions: list[dict] = []

        for deck in batch:
            if self.stop.is_set():
                break
            mid = deck["moxfield_id"]
            cards = self._fetch_one(mid)
            if cards is None:
                submissions.append({"moxfield_id": mid, "format": fmt, "error": True})
            else:
                submissions.append({"moxfield_id": mid, "format": fmt, "cards": cards})

        if not submissions:
            return
        try:
            results = self.api.submit_decks(submissions)
        except requests.RequestException as exc:
            _log(self.id, "FETCH", f"submit failed: {exc} — decks will be reclaimed after lease expiry")
            return

        done = sum(1 for r in results if not r.get("errored") and not r.get("collision") and r.get("written", -1) >= 0)
        errored = sum(1 for r in results if r.get("errored"))
        _log(self.id, "FETCH", f"submitted {len(results)}: {done} ok, {errored} error")

    def _fetch_one(self, moxfield_id: str) -> list[dict] | None:
        """Fetch one deck's cards. Returns rows, or None after MAX_DECK_FETCH_FAILS
        consecutive non-429 failures (caller marks it 'error')."""
        fails = 0
        while fails < MAX_DECK_FETCH_FAILS and not self.stop.is_set():
            try:
                detail = self.mox.fetch_deck(moxfield_id)
                return parse_deck_cards(detail)
            except MoxfieldError as exc:
                fails += 1
                _log(self.id, "FETCH", f"{moxfield_id} fail {fails}/{MAX_DECK_FETCH_FAILS}: {exc}")
        return None


class Node:
    """Owns the shared clients and runs a pool of workers."""

    def __init__(self, workers: int, batch_size: int) -> None:
        self.n = workers
        self.batch_size = batch_size
        self.stop = threading.Event()

        # One rate limiter and one Moxfield session shared by every worker, so the
        # whole process honors the single per-IP request budget.
        self.rl = RateLimiter(rate_per_sec=1.0 / RATE_LIMIT_SECONDS)
        self.mox = MoxfieldClient(self.rl)
        self.api = ApiClient(SCRAPER_API_URL, SCRAPER_API_KEY)
        self._base_id = f"{socket.gethostname()}:{os.getpid()}"

    def _install_signal_handlers(self) -> None:
        """Stop gracefully on SIGINT (Ctrl-C) and SIGTERM (Fly redeploy / docker stop)."""
        def handler(signum, _frame):
            print(f"\nsignal {signum} received — draining workers ...", flush=True)
            self.stop.set()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass  # not the main thread, or signal unsupported on this platform

    def run(self) -> None:
        print(f"Node {self._base_id}  workers={self.n}  api={SCRAPER_API_URL}  "
              f"budget={1.0 / RATE_LIMIT_SECONDS:.0f} RPS", flush=True)
        self._install_signal_handlers()

        threads: list[threading.Thread] = []
        for i in range(self.n):
            w = Worker(f"{self._base_id}#{i}", self.mox, self.api, self.batch_size, self.stop)
            t = threading.Thread(target=w.run, name=f"worker-{i}", daemon=True)
            t.start()
            threads.append(t)

        while not self.stop.is_set() and any(t.is_alive() for t in threads):
            time.sleep(0.5)

        if self.stop.is_set():
            print("waiting for workers to finish current request ...", flush=True)
            for t in threads:
                t.join(timeout=25)
            print("stopped.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 scraper node (self-selecting discovery/fetch).")
    # Enough workers that Moxfield's 20 RPS rate limit is the binding constraint,
    # not worker availability — ~20x loop time, with headroom for the API-write /
    # parse portion of each iteration that doesn't consume a Moxfield token.
    # Tunable per deployment via NODE_WORKERS (e.g. in fly.node.toml [env]).
    parser.add_argument("--workers", type=int, default=int(os.environ.get("NODE_WORKERS", "24")),
                        help="Concurrent workers sharing the rate budget (default 24, or $NODE_WORKERS).")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Decks claimed per fetch batch (default {DEFAULT_BATCH_SIZE}).")
    args = parser.parse_args()

    if not SCRAPER_API_KEY:
        print("WARNING: API_KEY is not set — API calls will 401.", flush=True)

    Node(workers=args.workers, batch_size=args.batch_size).run()


if __name__ == "__main__":
    main()
