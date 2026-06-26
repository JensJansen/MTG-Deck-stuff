"""
Moxfield HTTP client shared by all workers in a node.

Owns one persistent session (so Cloudflare cookies carry across requests) and
routes every request through the shared RateLimiter. 429s are retried internally
with escalating backoff and never surface to callers; any other HTTP/network
failure raises MoxfieldError so the worker can count it toward a deck's retry
budget.
"""

import time

import requests

from config import HEADERS, API_SEARCH, API_DECK, moxfield_search_name


class MoxfieldError(Exception):
    """A non-429 Moxfield failure (or 429 retries exhausted)."""


class MoxfieldClient:
    _BACKOFF = (5, 10, 15)  # seconds, per consecutive 429

    def __init__(self, rate_limiter) -> None:
        self._rl = rate_limiter
        self._session = requests.Session()
        self._session.headers.update(HEADERS)

    def _get_json(self, url: str, params: dict | None = None, timeout: int = 15) -> dict:
        attempt_429 = 0
        while True:
            self._rl.acquire()
            try:
                resp = self._session.get(url, params=params, timeout=timeout)
            except requests.RequestException as exc:
                raise MoxfieldError(f"network error: {type(exc).__name__}: {exc}") from exc

            if resp.status_code == 429:
                if attempt_429 >= len(self._BACKOFF):
                    raise MoxfieldError("429 retries exhausted")
                time.sleep(self._BACKOFF[attempt_429])
                attempt_429 += 1
                continue

            if not resp.ok:
                raise MoxfieldError(f"HTTP {resp.status_code}: {resp.text[:120]}")

            try:
                return resp.json()
            except ValueError as exc:
                raise MoxfieldError(f"invalid JSON: {exc}") from exc

    def search_page(self, fmt: str, card_name: str, page: int, page_size: int) -> dict:
        """One page of the Moxfield deck search for a card in a format (created desc)."""
        params = {
            "pageNumber":    page,
            "pageSize":      page_size,
            "sortType":      "created",
            "sortDirection": "descending",
            "fmt":           fmt,
            "cardName":      moxfield_search_name(card_name),
        }
        return self._get_json(API_SEARCH, params=params)

    def fetch_deck(self, moxfield_id: str) -> dict:
        """Full deck detail (all boards) for a public deck id."""
        return self._get_json(API_DECK.format(public_id=moxfield_id))
