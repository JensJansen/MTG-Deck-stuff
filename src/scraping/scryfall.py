"""
Scryfall API wrapper.

Bulk download (recommended for full card lists):
    from scryfall import ScryfallClient
    client = ScryfallClient()
    cards = client.download_oracle_cards()          # ~27k unique printings
    cards = client.download_all_cards()             # ~100k+ all printings

Single-card lookups:
    card = client.get_card_by_name("Lightning Bolt")
    card = client.get_card_by_id("e3285e6b-3e79-4d7c-bf96-d920f973b122")
    cards = list(client.search("type:creature cmc=1 color:r"))

CLI test:
    python src/scraping/scryfall.py
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
    "User-Agent": "deck-gen/1.0 (contact: deck-gen-project)",
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
          "rulings"        - rulings only, not card data

        cache_path: if given, save the raw JSON there and skip the download
                    if the file already exists.
        """
        if cache_path and Path(cache_path).exists():
            with open(cache_path, encoding="utf-8") as fh:
                return json.load(fh)

        url = self._bulk_download_url(bulk_type)
        print(f"Downloading {bulk_type} from {url} ...")
        resp = self._session.get(url, stream=True, timeout=300)
        resp.raise_for_status()

        chunks = []
        for chunk in resp.iter_content(chunk_size=1 << 20):  # 1 MB chunks
            chunks.append(chunk)

        raw = b"".join(chunks)
        cards = json.loads(raw)

        if cache_path:
            Path(cache_path).write_bytes(raw)
            print(f"Saved to {cache_path}")

        return cards

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

    def get_card_by_set(self, set_code: str, collector_number: str) -> dict:
        """Fetch a specific printing by set + collector number."""
        return self._get(f"{CARDS_URL}/{set_code}/{collector_number}").json()

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

        Example queries:
            "type:creature cmc=1 color:r"
            "format:pauper rarity:common"
            "set:mh3"
        """
        params = {"q": query, "order": order, "unique": unique}
        url = f"{CARDS_URL}/search?" + urlencode(params)

        while url:
            data = self._get(url).json()
            for card in data.get("data", []):
                yield card
            url = data.get("next_page")


# ---------------------------------------------------------------------------
# CLI -- quick field inspection
# ---------------------------------------------------------------------------

def _safe(text: str) -> str:
    return text.encode("ascii", errors="replace").decode("ascii")


def _inspect_fields(card: dict, indent: int = 2) -> None:
    pad = " " * indent
    for key, value in sorted(card.items()):
        if isinstance(value, dict):
            print(_safe(f"{pad}{key}: {{...}}"))
        elif isinstance(value, list):
            print(_safe(f"{pad}{key}: [{len(value)} items]"))
        else:
            short = str(value)
            if len(short) > 80:
                short = short[:77] + "..."
            print(_safe(f"{pad}{key}: {short!r}"))


def main() -> None:
    client = ScryfallClient()

    print("=== Available bulk-data files ===")
    for entry in client.list_bulk_data():
        size_mb = entry.get("size", 0) / 1_048_576
        print(_safe(f"  {entry['type']:<24} {size_mb:>7.1f} MB  --  {entry['name']}"))

    print("\n=== Fields on a single card (Lightning Bolt) ===")
    bolt = client.get_card_by_name("Lightning Bolt")
    _inspect_fields(bolt)

    print("\n=== Fields on a DFC (Delver of Secrets) ===")
    delver = client.get_card_by_name("Delver of Secrets", fuzzy=True)
    _inspect_fields(delver)
    if "card_faces" in delver:
        print("  card_faces[0] sub-fields:")
        _inspect_fields(delver["card_faces"][0], indent=4)

    print("\n=== Search sample: first 5 pauper common CMC-1 red cards ===")
    results = client.search("format:pauper color:r cmc=1 rarity:common", order="edhrec")
    for i, card in enumerate(results):
        if i >= 5:
            break
        print(_safe(f"  {card['name']:<30}  {card.get('mana_cost',''):<8}  {card.get('type_line','')}"))


if __name__ == "__main__":
    main()
