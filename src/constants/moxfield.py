"""
Shared Moxfield API constants, MTG format/color constants, and parsing utilities.

Used by both the single-machine scraper (src/scraping/) and the
distributed scraper (src/distributed scraper/).
"""

from datetime import datetime, timezone

API_SEARCH = "https://api2.moxfield.com/v2/decks/search"
API_DECK   = "https://api2.moxfield.com/v2/decks/all/{public_id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; deck-gen-scraper/1.0)",
    "Accept":     "application/json",
}

RATE_LIMIT_SECONDS    = 1.0
DEFAULT_BATCH_SIZE    = 50
CLAIM_TIMEOUT_MINUTES = 30

COLOR_BITS: dict[str, int] = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}

SINGLETON_FORMATS: list[str] = ["commander", "highlanderCanadian"]
REGULAR_FORMATS: list[str] = ["pauper", "standard", "modern", "vintage", "legacy"]
ALL_FORMATS: list[str] = SINGLETON_FORMATS + REGULAR_FORMATS

LEGAL_FORMATS = ALL_FORMATS  # backward-compat alias

BOARDS: list[str] = [
    "commanders", "companions", "signatureSpells",
    "mainboard", "sideboard", "maybeboard", "attractions", "stickers",
]

# Boards counted as part of the constructed deck for analysis purposes.
# Excludes sideboard, maybeboard, attractions, and stickers.
DEFAULT_BOARDS: frozenset[str] = frozenset({"mainboard", "commanders", "companions", "signatureSpells"})


def encode_colors(colors: list | str | None) -> int:
    if isinstance(colors, str):
        colors = [c.strip() for c in colors.split(",") if c.strip()]
    mask = 0
    for c in (colors or []):
        mask |= COLOR_BITS.get(str(c).strip().upper(), 0)
    return mask


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
