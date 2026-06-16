"""
Shared constants for the distributed scraper.
"""

import os

API_SEARCH = "https://api2.moxfield.com/v2/decks/search"
API_DECK   = "https://api2.moxfield.com/v2/decks/all/{public_id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; deck-gen-scraper/1.0)",
    "Accept":     "application/json",
}

RATE_LIMIT_SECONDS   = 1.0
DEFAULT_BATCH_SIZE   = 50
CLAIM_TIMEOUT_MINUTES = 30

COLOR_BITS = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}

LEGAL_FORMATS = [
    "standard", "future", "historic", "timeless", "gladiator",
    "pioneer", "explorer", "modern", "legacy", "pauper", "vintage",
    "penny", "commander", "oathbreaker", "standardbrawl", "brawl",
    "alchemy", "paupercommander", "duel", "oldschool", "premodern",
    "predh", "historicbrawl",
]

BOARDS = [
    "commanders", "companions", "signatureSpells",
    "mainboard", "sideboard", "maybeboard", "attractions", "stickers",
]


# Scraper API — nodes reach the API server via these; set in each node's environment.
SCRAPER_API_URL = os.environ.get("SCRAPER_API_URL", "http://localhost:8000")
SCRAPER_API_KEY = os.environ.get("API_KEY", "")


def encode_colors(colors) -> int:
    if isinstance(colors, str):
        colors = [c.strip() for c in colors.split(",")]
    mask = 0
    for c in (colors or []):
        mask |= COLOR_BITS.get(c.strip().upper(), 0)
    return mask
