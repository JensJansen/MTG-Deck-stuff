"""Shared constants for the scraping package."""

LEGAL_FORMATS: list[str] = [
    "standard", "future", "historic", "timeless", "gladiator",
    "pioneer", "explorer", "modern", "legacy", "pauper", "vintage",
    "penny", "commander", "oathbreaker", "standardbrawl", "brawl",
    "alchemy", "paupercommander", "duel", "oldschool", "premodern",
    "predh", "historicbrawl",
]

COLOR_BITS: dict[str, int] = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}


def encode_colors(colors: list | str | None) -> int:
    if isinstance(colors, str):
        colors = [c.strip() for c in colors.split(",") if c.strip()]
    mask = 0
    for c in (colors or []):
        mask |= COLOR_BITS.get(str(c).strip().upper(), 0)
    return mask
