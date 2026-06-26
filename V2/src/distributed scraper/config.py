"""
Runtime configuration for the V2 distributed scraper.

Re-exports MTG / Moxfield constants alongside env-var-backed runtime settings,
and provides the append-only logger for card names that could not be resolved
to an id during deck submission.
"""

import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from constants.env import load_env
from constants.mtg import (
    ALL_FORMAT_TOKENS,
    BOARDS,
    COMMANDER_BOARD,
    DECK_BOARDS,
    FORMATS,
    FORMATS_BY_TOKEN,
    Format,
    encode_colors,
    get_format,
)
from constants.moxfield import (
    API_DECK,
    API_SEARCH,
    HEADERS,
    moxfield_search_name,
    parse_search_deck,
)

load_env()

# ── Scraper operational config ──────────────────────────────────────────────
RATE_LIMIT_SECONDS     = 1.0 / 20  # 20 RPS per node (shared across all workers in a process)
DEFAULT_BATCH_SIZE     = 50
CLAIM_TIMEOUT_MINUTES  = 30        # lease expiry: stale claims become reclaimable
MAX_DECK_FETCH_FAILS   = 3         # consecutive non-429 fetch failures before a deck is marked 'error'

# Resweep is driven by a global cycle, not a per-card timer: a (card, format) is
# claimable only while swept = FALSE, and every swept flag resets together once
# the deck queue fully drains (see claim_sweep). There is no refresh interval.

SCRAPER_API_URL = os.environ.get("SCRAPER_API_URL", "http://localhost:8000")
SCRAPER_API_KEY = os.environ.get("API_KEY", "")

# ── Unknown-card log ────────────────────────────────────────────────────────
# Card names a scraped deck referenced that are not present in v2.cards. They
# are skipped from the write and appended here for later review / reseeding.
_LOG_DIR = Path(__file__).parent / "logs"
UNKNOWN_CARDS_LOG = _LOG_DIR / "unknown_cards.log"
_unknown_lock = threading.Lock()


def log_unknown_cards(fmt: str, moxfield_id: str, card_names: list[str]) -> None:
    """Append one tab-separated line per unresolved card name.

    Format:  <iso8601>\t<format>\t<moxfield_id>\t<card_name>
    """
    if not card_names:
        return
    ts = datetime.now(timezone.utc).isoformat()
    lines = "".join(f"{ts}\t{fmt}\t{moxfield_id}\t{name}\n" for name in card_names)
    with _unknown_lock:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with UNKNOWN_CARDS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(lines)
