"""
Typed client for the V2 scraper coordination API.

Thin wrapper over the endpoints a node uses. Workers speak only in terms of
moxfield_id and card_name; the API owns all id/format/schema detail.
"""

import requests


class ApiClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 45) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"X-Api-Key": api_key})

    # -- discovery -----------------------------------------------------------

    def claim_sweep(self, worker_id: str) -> dict | None:
        """Lease one (card, format) sweep unit, or None when nothing is claimable."""
        r = self._session.post(f"{self._base}/sweeps/claim",
                               json={"worker_id": worker_id}, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def post_decks(self, decks: list[dict]) -> dict:
        """Upsert discovered deck stubs. Returns {upserted, new, existing}."""
        r = self._session.post(f"{self._base}/decks", json=decks, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def complete_sweep(self, card_name: str, fmt: str) -> None:
        r = self._session.post(f"{self._base}/sweeps/complete",
                               json={"card_name": card_name, "format": fmt}, timeout=self._timeout)
        r.raise_for_status()

    # -- fetch ---------------------------------------------------------------

    def claim_decks(self, batch_size: int, worker_id: str) -> list[dict]:
        """Lease a batch of decks from one format. Returns [{moxfield_id, format}, ...]."""
        r = self._session.post(f"{self._base}/decks/claim",
                               json={"batch_size": batch_size, "worker_id": worker_id},
                               timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def submit_decks(self, submissions: list[dict]) -> list[dict]:
        """Submit fetched card lists and/or error outcomes for a batch of decks."""
        r = self._session.post(f"{self._base}/decks/submit",
                               json=submissions, timeout=max(self._timeout, 60))
        r.raise_for_status()
        return r.json()
