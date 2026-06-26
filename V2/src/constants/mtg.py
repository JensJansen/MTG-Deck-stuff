"""
MTG domain constants for the V2 stack.

The single source of truth for format metadata is FORMATS below. Everything the
API needs — deck-claim order, table routing, singleton detection, the commander
zone, and which v2.cards legality column gates a card into a format — derives
from these descriptors. There are no format->table-name mappings anywhere: a
format's table prefix is simply its Moxfield token lowercased.
"""

from dataclasses import dataclass

# ── Colors ──────────────────────────────────────────────────────────────────

COLOR_BITS: dict[str, int] = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}


def encode_colors(colors: list | str | None) -> int:
    """Encode a list (or comma string) of color letters into a 5-bit WUBRG mask."""
    if isinstance(colors, str):
        colors = [c.strip() for c in colors.split(",") if c.strip()]
    mask = 0
    for c in (colors or []):
        mask |= COLOR_BITS.get(str(c).strip().upper(), 0)
    return mask


# ── Boards ──────────────────────────────────────────────────────────────────

# Every board Moxfield may return for a deck. Regular formats persist all of
# these (board column preserved); singleton formats keep only DECK_BOARDS.
BOARDS: list[str] = [
    "commanders", "companions", "signatureSpells",
    "mainboard", "sideboard", "maybeboard", "attractions", "stickers",
]

# Boards that constitute the actual singleton deck list (excludes sideboard /
# maybeboard, which are not part of a singleton deck). Used to build card_ids.
DECK_BOARDS: frozenset[str] = frozenset({"mainboard", "commanders", "companions", "signatureSpells"})

# The board holding a commander deck's commander(s) — stored in commander_ids.
COMMANDER_BOARD: str = "commanders"


# ── Formats ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Format:
    """Canonical descriptor for one scraped format.

    token            Moxfield's exact format identifier, canonical everywhere.
    is_singleton     True -> cards stored as INTEGER[] arrays on the deck row;
                     False -> rows in {prefix}_deck_cards.
    legality_column  Column on v2.cards that gates legality, or None when every
                     card is legal in the format (highlanderCanadian).
    has_commander_zone  True only for commander; populates commander_ids.
    """
    token: str
    is_singleton: bool
    legality_column: str | None
    has_commander_zone: bool = False

    @property
    def table_prefix(self) -> str:
        return self.token.lower()

    @property
    def deck_table(self) -> str:
        return f"v2.{self.table_prefix}_decks"

    @property
    def deck_cards_table(self) -> str:
        return f"v2.{self.table_prefix}_deck_cards"


# Ordered by deck-claim priority (commander first, mirroring V1).
FORMATS: list[Format] = [
    Format("commander",          is_singleton=True,  legality_column="legal_commander", has_commander_zone=True),
    Format("highlanderCanadian", is_singleton=True,  legality_column=None),
    Format("pauper",             is_singleton=False, legality_column="legal_pauper"),
    Format("modern",             is_singleton=False, legality_column="legal_modern"),
    Format("vintage",            is_singleton=False, legality_column="legal_vintage"),
    Format("legacy",             is_singleton=False, legality_column="legal_legacy"),
]

FORMATS_BY_TOKEN: dict[str, Format] = {f.token: f for f in FORMATS}
ALL_FORMAT_TOKENS: list[str] = [f.token for f in FORMATS]


def get_format(token: str) -> Format:
    """Look up a Format by Moxfield token, raising KeyError if unknown."""
    return FORMATS_BY_TOKEN[token]
