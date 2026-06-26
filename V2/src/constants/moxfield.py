"""
Moxfield API constants and deck-parsing utilities for the V2 stack.

MTG domain constants (formats, colors, boards) live in constants.mtg.
"""

from datetime import datetime, timezone

from constants.mtg import BOARDS, encode_colors

API_SEARCH = "https://api2.moxfield.com/v2/decks/search"
API_DECK   = "https://api2.moxfield.com/v2/decks/all/{public_id}"

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    # Intentionally omit 'br': stock `requests` can't decode brotli without the
    # optional brotli package, and Moxfield will serve brotli if offered, which
    # silently yields undecodable bodies. gzip/deflate are always supported.
    "Accept-Encoding": "gzip, deflate",
    "Referer":         "https://www.moxfield.com/",
    "Origin":          "https://www.moxfield.com",
}


def moxfield_search_name(card_name: str) -> str:
    """For DFCs stored as 'Front // Back', Moxfield expects only the front face."""
    return card_name.split(" // ")[0] if " // " in card_name else card_name


def parse_search_deck(raw: dict, fmt: str | None) -> dict:
    """Parse one deck entry from a Moxfield search result into a discovery stub.

    Returns the shape POSTed to /decks. Card contents are fetched separately by
    scraper nodes; this only carries the metadata the search endpoint provides.
    """
    colors      = raw.get("colorIdentity") or raw.get("colors") or []
    moxfield_id = raw.get("publicId") or raw.get("id") or raw.get("slug")
    return {
        "moxfield_id":    moxfield_id,
        "name":           raw.get("name"),
        "format":         raw.get("format") or fmt,
        "author":         (raw.get("createdByUser") or {}).get("userName") or raw.get("authorUserName"),
        "color_mask":     encode_colors(colors),
        "created_at_utc": raw.get("createdAtUtc"),
        "updated_at_utc": raw.get("lastUpdatedAtUtc"),
        "scraped_at":     datetime.now(timezone.utc).isoformat(),
    }


def parse_deck_cards(detail: dict) -> list[dict]:
    """Flatten a Moxfield deck-detail response into card rows.

    Returns a list of {card_name, board, quantity} across every board Moxfield
    returned. The API buckets these per format (deck_cards rows for regular
    formats; card_ids / commander_ids arrays for singletons).
    """
    rows: list[dict] = []
    for board_name in BOARDS:
        board_data = detail.get(board_name) or {}
        for entry in board_data.values():
            card_name = (entry.get("card") or {}).get("name")
            if not card_name:
                continue
            rows.append({
                "card_name": card_name,
                "board":     board_name,
                "quantity":  entry.get("quantity", 1),
            })
    return rows
