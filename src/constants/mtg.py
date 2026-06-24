"""
MTG domain constants and utilities shared across all packages.

Covers: format lists, color encoding, board names, and the format→table-prefix mapping.
"""

COLOR_BITS: dict[str, int] = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}

SINGLETON_FORMATS: list[str] = ["commander", "highlanderCanadian"]
REGULAR_FORMATS:   list[str] = ["pauper", "modern", "vintage", "legacy"]
ALL_FORMATS:       list[str] = SINGLETON_FORMATS + REGULAR_FORMATS

LEGAL_FORMATS = ALL_FORMATS  # backward-compat alias

_FORMAT_TABLE_PREFIX: dict[str, str] = {"highlanderCanadian": "canadian_highlander"}


def format_to_table_prefix(fmt: str) -> str:
    return _FORMAT_TABLE_PREFIX.get(fmt, fmt)


BOARDS: list[str] = [
    "commanders", "companions", "signatureSpells",
    "mainboard", "sideboard", "maybeboard", "attractions", "stickers",
]

DEFAULT_BOARDS: frozenset[str] = frozenset({"mainboard", "commanders", "companions", "signatureSpells"})


def encode_colors(colors: list | str | None) -> int:
    if isinstance(colors, str):
        colors = [c.strip() for c in colors.split(",") if c.strip()]
    mask = 0
    for c in (colors or []):
        mask |= COLOR_BITS.get(str(c).strip().upper(), 0)
    return mask
