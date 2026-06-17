"""
Shared Moxfield API constants and MTG format/color constants.

Used by both the single-machine scraper (src/scraping/) and the
distributed scraper (src/distributed scraper/).
"""

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

LEGAL_FORMATS: list[str] = [
    "standard", "historic", "timeless", "gladiator",
    "pioneer", "explorer", "modern", "legacy", "pauper", "vintage",
    "penny", "commander", "oathbreaker", "standardbrawl", "brawl",
    "alchemy", "paupercommander", "duel", "oldschool", "premodern",
    "predh", "historicbrawl",
]

BOARDS: list[str] = [
    "commanders", "companions", "signatureSpells",
    "mainboard", "sideboard", "maybeboard", "attractions", "stickers",
]


def encode_colors(colors: list | str | None) -> int:
    if isinstance(colors, str):
        colors = [c.strip() for c in colors.split(",") if c.strip()]
    mask = 0
    for c in (colors or []):
        mask |= COLOR_BITS.get(str(c).strip().upper(), 0)
    return mask
