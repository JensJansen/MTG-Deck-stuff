"""
Runtime configuration for the distributed scraper.

MTG domain constants live in src/constants/mtg.py.
Moxfield API constants live in src/constants/moxfield.py.
This module re-exports both alongside env-var-backed runtime settings so that
all existing `from config import X` statements remain valid.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from constants.env import load_env
from constants.mtg import (
    COLOR_BITS,
    ALL_FORMATS,
    SINGLETON_FORMATS,
    REGULAR_FORMATS,
    LEGAL_FORMATS,
    BOARDS,
    DEFAULT_BOARDS,
    encode_colors,
    format_to_table_prefix,
)
from constants.moxfield import (
    API_SEARCH,
    API_DECK,
    HEADERS,
    moxfield_search_name,
    parse_deck,
)

load_env()

# ── Scraper operational config ──────────────────────────────────────────────────
RATE_LIMIT_SECONDS     = 1.0
DEFAULT_BATCH_SIZE     = 50
CLAIM_TIMEOUT_MINUTES  = 30
REFRESH_INTERVAL_HOURS = 24

SCRAPER_API_URL = os.environ.get("SCRAPER_API_URL", "http://localhost:8000")
SCRAPER_API_KEY = os.environ.get("API_KEY", "")
