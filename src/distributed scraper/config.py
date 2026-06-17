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

from constants.moxfield import (  # noqa: E402
    API_SEARCH,
    API_DECK,
    HEADERS,
    RATE_LIMIT_SECONDS,
    DEFAULT_BATCH_SIZE,
    CLAIM_TIMEOUT_MINUTES,
    COLOR_BITS,
    LEGAL_FORMATS,
    BOARDS,
    encode_colors,
)

SCRAPER_API_URL = os.environ.get("SCRAPER_API_URL", "http://localhost:8000")
SCRAPER_API_KEY = os.environ.get("API_KEY", "")
