"""
Scryfall API wrapper for the V2 stack.

Bulk download (recommended for full card lists):
    from scryfall import ScryfallClient
    client = ScryfallClient()
    cards = client.download_oracle_cards()          # ~27k unique printings
    cards = client.download_all_cards()             # ~100k+ all printings

Single-card lookups:
    card = client.get_card_by_name("Lightning Bolt")
    card = client.get_card_by_id("e3285e6b-3e79-4d7c-bf96-d920f973b122")
    cards = list(client.search("type:creature cmc=1 color:r"))
"""

import json
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urlencode

import requests

# Scryfall asks for <=10 req/s; 100 ms between requests is the safe floor.
_RATE_LIMIT = 0.1

BULK_DATA_URL = "https://api.scryfall.com/bulk-data"
CARDS_URL = "https://api.scryfall.com/cards"

HEADERS = {
    "User-Agent": "deck-gen/2.0 (contact: deck-gen-project)",
    "Accept": "application/json;q=0.9,*/*;q=0.8",
}


class ScryfallError(Exception):
    pass


class ScryfallClient:
    def __init__(self, rate_limit: float = _RATE_LIMIT) -> None:
        self._rate_limit = rate_limit
        self._last_request: float = 0.0
        self._session = requests.Session()
        self._session.headers.update(HEADERS)

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _wait(self) -> None:
        elapsed = time.perf_counter() - self._last_request
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)

    def _get(self, url: str, **kwargs) -> requests.Response:
        self._wait()
        resp = self._session.get(url, timeout=30, **kwargs)
        self._last_request = time.perf_counter()
        if not resp.ok:
            try:
                detail = resp.json().get("details", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            raise ScryfallError(f"HTTP {resp.status_code}: {detail}")
        return resp

    # ------------------------------------------------------------------
    # Bulk data
    # ------------------------------------------------------------------

    def list_bulk_data(self) -> list[dict]:
        """Return Scryfall's available bulk-data files with their metadata."""
        return self._get(BULK_DATA_URL).json()["data"]

    def _bulk_download_url(self, bulk_type: str) -> str:
        entries = self.list_bulk_data()
        for entry in entries:
            if entry["type"] == bulk_type:
                return entry["download_uri"]
        types = [e["type"] for e in entries]
        raise ScryfallError(f"Bulk type {bulk_type!r} not found. Available: {types}")

    def download_bulk(
        self,
        bulk_type: str = "oracle_cards",
        cache_path: Path | None = None,
    ) -> list[dict]:
        """
        Download a Scryfall bulk file and return it as a list of card dicts.

        bulk_type options:
          "oracle_cards"   - one entry per unique Oracle card (~27k)
          "unique_artwork" - one entry per unique artwork
          "default_cards"  - most recent printing of each card (~27k)
          "all_cards"      - every single printing (~110k+)

        cache_path: if given, save the raw JSON there and skip the download
                    if the file already exists.
        """
        if cache_path and Path(cache_path).exists():
            with open(cache_path, encoding="utf-8") as fh:
                return json.load(fh)

        url = self._bulk_download_url(bulk_type)
        print(f"Downloading {bulk_type} from {url} ...")
        resp = self._session.get(url, timeout=300)
        resp.raise_for_status()

        if cache_path:
            Path(cache_path).write_bytes(resp.content)
            print(f"Saved to {cache_path}")
            del resp
            with open(cache_path, encoding="utf-8") as fh:
                return json.load(fh)

        return resp.json()

    def download_oracle_cards(self, cache_path: Path | None = None) -> list[dict]:
        """One card object per unique Oracle identity (~27k cards)."""
        return self.download_bulk("oracle_cards", cache_path)

    def download_all_cards(self, cache_path: Path | None = None) -> list[dict]:
        """Every printing of every card (~110k+ objects)."""
        return self.download_bulk("all_cards", cache_path)

    # ------------------------------------------------------------------
    # Single-card lookups
    # ------------------------------------------------------------------

    def get_card_by_id(self, scryfall_id: str) -> dict:
        """Fetch a single card by its Scryfall UUID."""
        return self._get(f"{CARDS_URL}/{scryfall_id}").json()

    def get_card_by_name(self, name: str, *, fuzzy: bool = False) -> dict:
        """
        Fetch a card by name.
        fuzzy=False -> exact match (raises on ambiguity)
        fuzzy=True  -> Scryfall's fuzzy search (picks closest match)
        """
        param = "fuzzy" if fuzzy else "exact"
        return self._get(f"{CARDS_URL}/named", params={param: name}).json()

    # ------------------------------------------------------------------
    # Search (paginated)
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        order: str = "name",
        unique: str = "cards",
    ) -> Iterator[dict]:
        """
        Yield every card matching a Scryfall search query.
        Handles pagination automatically.
        """
        params = {"q": query, "order": order, "unique": unique}
        url = f"{CARDS_URL}/search?" + urlencode(params)

        while url:
            data = self._get(url).json()
            for card in data.get("data", []):
                yield card
            url = data.get("next_page")
