"""
Runtime configuration for the distributed scraper.

Static constants (API URLs, format list, etc.) live in src/constants/moxfield.py.
This module re-exports them alongside the env-var-backed runtime settings so that
all existing `from config import X` statements remain valid.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from constants.env import load_env  # noqa: E402
from constants.moxfield import (  # noqa: E402
    API_SEARCH,
    API_DECK,
    HEADERS,
    RATE_LIMIT_SECONDS,
    DEFAULT_BATCH_SIZE,
    CLAIM_TIMEOUT_MINUTES,
    COLOR_BITS,
    ALL_FORMATS,
    SINGLETON_FORMATS,
    REGULAR_FORMATS,
    LEGAL_FORMATS,
    BOARDS,
    encode_colors,
    moxfield_search_name,
    parse_deck,
)

load_env()

SCRAPER_API_URL = os.environ.get("SCRAPER_API_URL", "http://localhost:8000")
SCRAPER_API_KEY = os.environ.get("API_KEY", "")
