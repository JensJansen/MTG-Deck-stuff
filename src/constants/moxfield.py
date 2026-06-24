"""
Moxfield API constants and deck-parsing utilities.

MTG domain constants (formats, colors, boards) live in constants.mtg.
"""

from datetime import datetime, timezone

from constants.mtg import encode_colors

API_SEARCH = "https://api2.moxfield.com/v2/decks/search"
API_DECK   = "https://api2.moxfield.com/v2/decks/all/{public_id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; deck-gen-scraper/1.0)",
    "Accept":     "application/json",
}


def moxfield_search_name(card_name: str) -> str:
    """For DFCs stored as 'Front // Back', Moxfield expects only the front face."""
    return card_name.split(" // ")[0] if " // " in card_name else card_name


def parse_deck(raw: dict, fmt: str | None) -> dict:
    """Parse a single deck entry from a Moxfield search result into a DB-ready dict."""
    colors    = raw.get("colorIdentity") or raw.get("colors") or []
    public_id = raw.get("publicId") or raw.get("id") or raw.get("slug")
    return {
        "public_id":      public_id,
        "name":           raw.get("name"),
        "format":         raw.get("format") or fmt,
        "author":         (raw.get("createdByUser") or {}).get("userName") or raw.get("authorUserName"),
        "color_mask":     encode_colors(colors),
        "created_at_utc": raw.get("createdAtUtc"),
        "updated_at_utc": raw.get("lastUpdatedAtUtc"),
        "scraped_at":     datetime.now(timezone.utc).isoformat(),
    }
