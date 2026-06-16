"""
Download all MTG cards from Scryfall's bulk-data endpoint and store them
in a local SQLite database (cards.db).  Completely separate from decks.db.

Usage:
    python src/scraping/cards_db.py                        # oracle_cards (~27k unique cards)
    python src/scraping/cards_db.py --bulk all_cards       # every printing (~110k+)
    python src/scraping/cards_db.py --cache                # save raw JSON to disk; reuse on next run
    python src/scraping/cards_db.py --db path/to/custom.db
    python src/scraping/cards_db.py --info                 # show DB stats only, no download
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path

from scryfall import ScryfallClient

DB_PATH   = Path(__file__).parents[1] / "data" / "cards.db"
CACHE_DIR = Path(__file__).parents[1] / "data" / "cache"

COLOR_BITS = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}

# All formats Scryfall includes in the legalities dict.
LEGAL_FORMATS = [
    "standard", "future", "historic", "timeless", "gladiator",
    "pioneer", "explorer", "modern", "legacy", "pauper", "vintage",
    "penny", "commander", "oathbreaker", "standardbrawl", "brawl",
    "alchemy", "paupercommander", "duel", "oldschool", "premodern",
    "predh", "historicbrawl",
]


def encode_colors(colors) -> int:
    if isinstance(colors, str):
        colors = [c.strip() for c in colors.split(",") if c.strip()]
    mask = 0
    for c in (colors or []):
        mask |= COLOR_BITS.get(str(c).strip().upper(), 0)
    return mask


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _legality_columns_ddl() -> str:
    return "\n".join(
        f"    legal_{fmt:<20} TEXT,"
        for fmt in LEGAL_FORMATS
    )


DDL = f"""
CREATE TABLE IF NOT EXISTS cards (
    -- Scryfall identity
    scryfall_id      TEXT PRIMARY KEY,
    oracle_id        TEXT NOT NULL,
    name             TEXT NOT NULL,
    lang             TEXT NOT NULL DEFAULT 'en',
    layout           TEXT,

    -- Gameplay (NULL on DFCs without top-level fields)
    mana_cost        TEXT,
    cmc              REAL NOT NULL DEFAULT 0,
    type_line        TEXT,
    oracle_text      TEXT,
    flavor_text      TEXT,
    power            TEXT,
    toughness        TEXT,
    loyalty          TEXT,
    defense          TEXT,

    -- Colors (bitmask: W=1 U=2 B=4 R=8 G=16)
    color_mask       INTEGER NOT NULL DEFAULT 0,
    ci_mask          INTEGER NOT NULL DEFAULT 0,

    -- Print / edition info
    released_at      TEXT,
    rarity           TEXT,
    artist           TEXT,
    border_color     TEXT,
    frame            TEXT,

    -- Boolean flags (0/1)
    reserved         INTEGER NOT NULL DEFAULT 0,
    reprint          INTEGER NOT NULL DEFAULT 0,
    digital          INTEGER NOT NULL DEFAULT 0,
    foil             INTEGER NOT NULL DEFAULT 0,
    nonfoil          INTEGER NOT NULL DEFAULT 0,
    full_art         INTEGER NOT NULL DEFAULT 0,
    textless         INTEGER NOT NULL DEFAULT 0,
    oversized        INTEGER NOT NULL DEFAULT 0,
    game_changer     INTEGER NOT NULL DEFAULT 0,
    booster          INTEGER NOT NULL DEFAULT 0,
    promo            INTEGER NOT NULL DEFAULT 0,

    -- Rankings
    edhrec_rank      INTEGER,

    -- Single representative image URL (normal size; front face for DFCs)
    image_uri        TEXT,

    -- Keywords as a JSON array (e.g. ["Flying","Trample"])
    keywords_json    TEXT,

    -- Format legalities -- values: 'legal', 'not_legal', 'banned', 'restricted'
{_legality_columns_ddl()}

    -- Absorb trailing comma from legality columns
    _reserved_col    INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cards_oracle_id   ON cards(oracle_id);
CREATE INDEX IF NOT EXISTS idx_cards_name        ON cards(name);
CREATE INDEX IF NOT EXISTS idx_cards_rarity      ON cards(rarity);
CREATE INDEX IF NOT EXISTS idx_cards_ci_mask     ON cards(ci_mask);
CREATE INDEX IF NOT EXISTS idx_cards_released_at ON cards(released_at);
CREATE INDEX IF NOT EXISTS idx_cards_layout      ON cards(layout);
"""

LEGALITY_INDEX_DDL = "\n".join(
    f"CREATE INDEX IF NOT EXISTS idx_cards_legal_{fmt} ON cards(legal_{fmt});"
    for fmt in LEGAL_FORMATS
)


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.executescript(LEGALITY_INDEX_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Card parsing
# ---------------------------------------------------------------------------

def _bool(v) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        return 1 if v.lower() == "true" else 0
    return int(bool(v))


def _image_uri(raw: dict) -> str | None:
    """Pick one representative image URL."""
    uris = raw.get("image_uris")
    if uris:
        return uris.get("normal") or uris.get("large") or next(iter(uris.values()), None)
    faces = raw.get("card_faces") or []
    if faces:
        face_uris = faces[0].get("image_uris") or {}
        return face_uris.get("normal") or face_uris.get("large") or next(iter(face_uris.values()), None)
    return None


def parse_card(raw: dict) -> dict:
    legalities = raw.get("legalities") or {}
    row: dict = {
        "scryfall_id":  raw.get("id"),
        "oracle_id":    raw.get("oracle_id", ""),
        "name":         raw.get("name", ""),
        "lang":         raw.get("lang", "en"),
        "layout":       raw.get("layout"),

        "mana_cost":    raw.get("mana_cost"),
        "cmc":          raw.get("cmc") or 0.0,
        "type_line":    raw.get("type_line"),
        "oracle_text":  raw.get("oracle_text"),
        "flavor_text":  raw.get("flavor_text"),
        "power":        raw.get("power"),
        "toughness":    raw.get("toughness"),
        "loyalty":      raw.get("loyalty"),
        "defense":      raw.get("defense"),

        "color_mask":   encode_colors(raw.get("colors", [])),
        "ci_mask":      encode_colors(raw.get("color_identity", [])),

        "released_at":  raw.get("released_at"),
        "rarity":       raw.get("rarity"),
        "artist":       raw.get("artist"),
        "border_color": raw.get("border_color"),
        "frame":        raw.get("frame"),

        "reserved":     _bool(raw.get("reserved", False)),
        "reprint":      _bool(raw.get("reprint", False)),
        "digital":      _bool(raw.get("digital", False)),
        "foil":         _bool(raw.get("foil", False)),
        "nonfoil":      _bool(raw.get("nonfoil", False)),
        "full_art":     _bool(raw.get("full_art", False)),
        "textless":     _bool(raw.get("textless", False)),
        "oversized":    _bool(raw.get("oversized", False)),
        "game_changer": _bool(raw.get("game_changer", False)),
        "booster":      _bool(raw.get("booster", False)),
        "promo":        _bool(raw.get("promo", False)),

        "edhrec_rank":  raw.get("edhrec_rank"),
        "image_uri":    _image_uri(raw),
        "keywords_json": json.dumps(raw.get("keywords") or [], ensure_ascii=False),
    }
    for fmt in LEGAL_FORMATS:
        row[f"legal_{fmt}"] = legalities.get(fmt)
    return row


def _build_upsert_sql() -> str:
    legal_cols = [f"legal_{f}" for f in LEGAL_FORMATS]
    all_cols = [
        "scryfall_id", "oracle_id", "name", "lang", "layout",
        "mana_cost", "cmc", "type_line", "oracle_text", "flavor_text",
        "power", "toughness", "loyalty", "defense",
        "color_mask", "ci_mask",
        "released_at", "rarity", "artist", "border_color", "frame",
        "reserved", "reprint", "digital", "foil", "nonfoil",
        "full_art", "textless", "oversized", "game_changer", "booster", "promo",
        "edhrec_rank", "image_uri", "keywords_json",
    ] + legal_cols

    cols_sql    = ", ".join(all_cols)
    vals_sql    = ", ".join(f":{c}" for c in all_cols)
    updates_sql = ",\n    ".join(
        f"{c} = excluded.{c}"
        for c in all_cols if c != "scryfall_id"
    )
    return f"""
INSERT INTO cards ({cols_sql})
VALUES ({vals_sql})
ON CONFLICT(scryfall_id) DO UPDATE SET
    {updates_sql}
"""


UPSERT_SQL = _build_upsert_sql()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

BATCH_SIZE = 500


def import_cards(cards: list[dict], conn: sqlite3.Connection) -> tuple[int, int]:
    ok = 0
    errors = 0
    total = len(cards)

    for batch_start in range(0, total, BATCH_SIZE):
        batch = cards[batch_start : batch_start + BATCH_SIZE]
        rows = []
        for raw in batch:
            try:
                rows.append(parse_card(raw))
            except Exception as exc:
                print(f"  [warn] parse error for {raw.get('name', '?')!r}: {exc}")
                errors += 1

        conn.executemany(UPSERT_SQL, rows)
        conn.commit()
        ok += len(rows)

        pct = min(batch_start + BATCH_SIZE, total) / total * 100
        print(f"  {min(batch_start + BATCH_SIZE, total):>6}/{total}  ({pct:4.0f}%)", end="\r", flush=True)

    print()
    return ok, errors


# ---------------------------------------------------------------------------
# Info / stats
# ---------------------------------------------------------------------------

def print_db_stats(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    if total == 0:
        print("Database is empty.")
        return

    print(f"Total cards: {total:,}")

    print("\nBy rarity:")
    for row in conn.execute(
        "SELECT rarity, COUNT(*) AS n FROM cards GROUP BY rarity ORDER BY n DESC"
    ).fetchall():
        print(f"  {row[0] or '(none)':<12} {row[1]:>7,}")

    print("\nBy layout (top 10):")
    for row in conn.execute(
        "SELECT layout, COUNT(*) AS n FROM cards GROUP BY layout ORDER BY n DESC LIMIT 10"
    ).fetchall():
        print(f"  {row[0] or '(none)':<20} {row[1]:>7,}")

    print("\nLegality sample (pauper):")
    for row in conn.execute(
        "SELECT legal_pauper, COUNT(*) AS n FROM cards GROUP BY legal_pauper ORDER BY n DESC"
    ).fetchall():
        print(f"  {row[0] or '(null)':<15} {row[1]:>7,}")

    print("\nExample query -- pauper-legal commons:")
    rows = conn.execute(
        "SELECT name, type_line FROM cards "
        "WHERE legal_pauper = 'legal' AND rarity = 'common' "
        "ORDER BY name LIMIT 8"
    ).fetchall()
    for r in rows:
        print(f"  {r[0]:<30}  {r[1]}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

BULK_TYPES = ["oracle_cards", "unique_artwork", "default_cards", "all_cards"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download all MTG cards from Scryfall and store in cards.db.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/scraping/cards_db.py                         # oracle_cards (~27k unique cards)
  python src/scraping/cards_db.py --bulk all_cards        # every printing (~110k+)
  python src/scraping/cards_db.py --cache                 # cache JSON; reuse on subsequent runs
  python src/scraping/cards_db.py --info                  # show DB stats without downloading
""",
    )
    parser.add_argument(
        "--bulk", dest="bulk_type", default="oracle_cards", choices=BULK_TYPES,
    )
    parser.add_argument(
        "--cache", dest="cache", action="store_true",
        help="Save downloaded JSON to cache/ and reuse it on subsequent runs",
    )
    parser.add_argument(
        "--db", dest="db_path", default=str(DB_PATH),
    )
    parser.add_argument(
        "--info", dest="info", action="store_true",
        help="Print database statistics and exit (no download)",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    init_db(conn)

    if args.info:
        print_db_stats(conn)
        conn.close()
        return

    cache_path: Path | None = None
    if args.cache:
        CACHE_DIR.mkdir(exist_ok=True)
        cache_path = CACHE_DIR / f"{args.bulk_type}.json"

    client = ScryfallClient()

    t0 = time.perf_counter()
    print(f"Fetching bulk data: {args.bulk_type} ...")
    cards = client.download_bulk(args.bulk_type, cache_path=cache_path)
    t_download = time.perf_counter() - t0
    print(f"Downloaded {len(cards):,} cards in {t_download:.1f}s")

    print(f"Importing into {db_path} ...")
    t1 = time.perf_counter()
    ok, errors = import_cards(cards, conn)
    t_import = time.perf_counter() - t1
    conn.close()

    print(f"Done: {ok:,} upserted, {errors} errors in {t_import:.1f}s  ->  {db_path}")
    print()
    conn2 = sqlite3.connect(db_path)
    print_db_stats(conn2)
    conn2.close()


if __name__ == "__main__":
    main()
