"""
compute_stats.py - Precomputes per-card and per-pair co-occurrence statistics.

All heavy computation runs in Postgres — card inclusion stats via GROUP BY and
pair co-occurrence via a self-join on deck_cards. Python only orchestrates and
writes results back.

Usage:
    python src/analysis/compute_stats.py
    python src/analysis/compute_stats.py --min-cooccur 10
    python src/analysis/compute_stats.py --format commander

Reads DATABASE_URL from src/distributed scraper/.env automatically.
"""

import argparse
import heapq
import os
from pathlib import Path

import psycopg2
import psycopg2.extras

# Boards treated as part of the constructed deck (excludes maybeboard / sideboard)
DEFAULT_BOARDS      = frozenset({"mainboard", "commanders", "companions", "signatureSpells"})
DEFAULT_MIN_COOCCUR = 5


# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

_ENV_FILE = Path(__file__).parents[1] / "distributed scraper" / ".env"

_ENV_TEMPLATE = """\
DATABASE_URL=postgresql://postgres:yourpassword@localhost/deckgen
API_KEY=your-api-key
SCRAPER_API_URL=http://127.0.0.1:8000
"""


def _load_env() -> None:
    if not _ENV_FILE.exists():
        _ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ENV_FILE.write_text(_ENV_TEMPLATE)
        print(f"Created {_ENV_FILE} with placeholder values — please fill in real credentials.")
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

def init_stats_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS card_stats (
                card_name      TEXT    NOT NULL,
                format         TEXT    NOT NULL,
                deck_count     INTEGER NOT NULL,
                total_decks    INTEGER NOT NULL,
                inclusion_rate REAL    NOT NULL,
                avg_quantity   REAL    NOT NULL,
                PRIMARY KEY (card_name, format)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS card_pair_stats (
                card_a             TEXT    NOT NULL,
                card_b             TEXT    NOT NULL,
                format             TEXT    NOT NULL,
                cooccurrence_count INTEGER NOT NULL,
                lift               REAL    NOT NULL,
                pmi                REAL    NOT NULL,
                jaccard            REAL    NOT NULL,
                confidence_a_to_b  REAL    NOT NULL,
                confidence_b_to_a  REAL    NOT NULL,
                PRIMARY KEY (card_a, card_b, format)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pair_card_a  ON card_pair_stats (card_a, format)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pair_card_b  ON card_pair_stats (card_b, format)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pair_lift     ON card_pair_stats (lift    DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pair_jaccard  ON card_pair_stats (jaccard DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pair_pmi      ON card_pair_stats (pmi     DESC)")
    conn.commit()


# ---------------------------------------------------------------------------
# Stat computation — pushed to Postgres
# ---------------------------------------------------------------------------

def load_card_stats(conn, fmt: str, boards: frozenset[str]) -> list[tuple]:
    """
    Compute per-card inclusion stats for a format entirely in Postgres.
    Returns tuples of (card_name, format, deck_count, total_decks, inclusion_rate, avg_quantity).
    """
    with conn.cursor() as cur:
        cur.execute("""
            WITH total AS (
                SELECT COUNT(DISTINCT dc.deck_id) AS n
                FROM deck_cards dc
                JOIN decks d ON dc.deck_id = d.public_id
                WHERE d.format = %s AND dc.board = ANY(%s)
            )
            SELECT
                c.card_name,
                %s AS format,
                COUNT(DISTINCT dc.deck_id)                               AS deck_count,
                t.n                                                       AS total_decks,
                COUNT(DISTINCT dc.deck_id)::float / NULLIF(t.n, 0)       AS inclusion_rate,
                SUM(dc.quantity)::float / COUNT(DISTINCT dc.deck_id)      AS avg_quantity
            FROM deck_cards dc
            JOIN decks d  ON dc.deck_id = d.public_id
            JOIN cards c  ON dc.card_id = c.id
            CROSS JOIN total t
            WHERE d.format = %s
              AND dc.board = ANY(%s)
            GROUP BY c.card_name, t.n
        """, (fmt, list(boards), fmt, fmt, list(boards)))
        return cur.fetchall()


def load_pair_stats(conn, fmt: str, boards: frozenset[str], min_cooccur: int) -> list[tuple]:
    """
    Compute pair co-occurrence metrics via a self-join on deck_cards in Postgres.
    Returns tuples of (card_a, card_b, format, cooccurrence_count, lift, pmi, jaccard,
                       confidence_a_to_b, confidence_b_to_a).
    """
    with conn.cursor() as cur:
        cur.execute("""
            WITH
            total AS (
                SELECT COUNT(DISTINCT dc.deck_id) AS n
                FROM deck_cards dc
                JOIN decks d ON dc.deck_id = d.public_id
                WHERE d.format = %s AND dc.board = ANY(%s)
            ),
            card_counts AS (
                SELECT dc.card_id, COUNT(DISTINCT dc.deck_id) AS cnt
                FROM deck_cards dc
                JOIN decks d ON dc.deck_id = d.public_id
                WHERE d.format = %s AND dc.board = ANY(%s)
                GROUP BY dc.card_id
            ),
            pairs AS (
                SELECT
                    dc1.card_id                      AS id_a,
                    dc2.card_id                      AS id_b,
                    COUNT(DISTINCT dc1.deck_id)      AS cooccur
                FROM deck_cards dc1
                JOIN deck_cards dc2
                  ON  dc1.deck_id  = dc2.deck_id
                  AND dc1.card_id  < dc2.card_id
                JOIN decks d ON dc1.deck_id = d.public_id
                WHERE d.format    = %s
                  AND dc1.board   = ANY(%s)
                  AND dc2.board   = ANY(%s)
                GROUP BY dc1.card_id, dc2.card_id
                HAVING COUNT(DISTINCT dc1.deck_id) >= %s
            )
            SELECT
                ca.card_name,
                cb.card_name,
                %s,
                p.cooccur,
                (p.cooccur::float * n.n) / (cc_a.cnt * cc_b.cnt)              AS lift,
                LN((p.cooccur::float * n.n) / (cc_a.cnt * cc_b.cnt))          AS pmi,
                p.cooccur::float / (cc_a.cnt + cc_b.cnt - p.cooccur)          AS jaccard,
                p.cooccur::float / cc_a.cnt                                    AS conf_a_to_b,
                p.cooccur::float / cc_b.cnt                                    AS conf_b_to_a
            FROM pairs p
            JOIN cards      ca    ON p.id_a      = ca.id
            JOIN cards      cb    ON p.id_b      = cb.id
            JOIN card_counts cc_a ON p.id_a      = cc_a.card_id
            JOIN card_counts cc_b ON p.id_b      = cc_b.card_id
            CROSS JOIN total n
        """, (
            fmt, list(boards),           # total CTE
            fmt, list(boards),           # card_counts CTE
            fmt, list(boards), list(boards), min_cooccur,  # pairs CTE
            fmt,                         # format column in SELECT
        ))
        return cur.fetchall()


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

def get_formats(conn, fmt_filter: str | None) -> list[str]:
    if fmt_filter:
        return [fmt_filter]
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT format FROM decks ORDER BY format")
        return [r[0] for r in cur.fetchall()]


def clear_format_stats(conn, fmt: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM card_stats      WHERE format = %s", (fmt,))
        cur.execute("DELETE FROM card_pair_stats WHERE format = %s", (fmt,))


def write_card_stats(conn, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO card_stats
                (card_name, format, deck_count, total_decks, inclusion_rate, avg_quantity)
            VALUES %s
            ON CONFLICT (card_name, format) DO UPDATE SET
                deck_count     = EXCLUDED.deck_count,
                total_decks    = EXCLUDED.total_decks,
                inclusion_rate = EXCLUDED.inclusion_rate,
                avg_quantity   = EXCLUDED.avg_quantity
        """, rows)


def write_pair_stats(conn, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO card_pair_stats
                (card_a, card_b, format,
                 cooccurrence_count, lift, pmi, jaccard,
                 confidence_a_to_b, confidence_b_to_a)
            VALUES %s
            ON CONFLICT (card_a, card_b, format) DO UPDATE SET
                cooccurrence_count = EXCLUDED.cooccurrence_count,
                lift               = EXCLUDED.lift,
                pmi                = EXCLUDED.pmi,
                jaccard            = EXCLUDED.jaccard,
                confidence_a_to_b  = EXCLUDED.confidence_a_to_b,
                confidence_b_to_a  = EXCLUDED.confidence_b_to_a
        """, rows)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _print_sample(pair_rows: list[tuple]) -> None:
    top = heapq.nlargest(5, pair_rows, key=lambda r: r[4])  # index 4 = lift
    print("  top pairs by lift:")
    for r in top:
        print(
            f"    {r[0]!r:35s} + {r[1]!r:35s}"
            f"  cooccur={r[3]:3d}"
            f"  lift={r[4]:.2f}"
            f"  jaccard={r[6]:.3f}"
        )


def run(conn, formats: list[str], boards: frozenset[str], min_cooccur: int) -> None:
    init_stats_tables(conn)

    for fmt in formats:
        print(f"\n[{fmt}]")

        print("  computing card stats ...", end=" ", flush=True)
        card_rows = load_card_stats(conn, fmt, boards)
        print(f"{len(card_rows)} cards")

        print("  computing pair stats  ...", end=" ", flush=True)
        pair_rows = load_pair_stats(conn, fmt, boards, min_cooccur)
        print(f"{len(pair_rows)} pairs (cooccur >= {min_cooccur})")

        if not card_rows:
            print("  No decks found for this format — skipping.")
            continue

        clear_format_stats(conn, fmt)
        write_card_stats(conn, card_rows)
        write_pair_stats(conn, pair_rows)
        conn.commit()

        if pair_rows:
            _print_sample(pair_rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute card co-occurrence statistics from scraped deck data."
    )
    parser.add_argument("--format", "-f", dest="format", default=None,
                        help="Limit computation to one format (default: all formats in DB)")
    parser.add_argument("--min-cooccur", dest="min_cooccur", type=int,
                        default=DEFAULT_MIN_COOCCUR,
                        help=f"Minimum co-occurrence count to store a pair (default: {DEFAULT_MIN_COOCCUR})")
    parser.add_argument("--include-sideboard", dest="include_sideboard", action="store_true",
                        help="Also count sideboard cards when computing statistics")
    args = parser.parse_args()

    _load_env()

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("ERROR: DATABASE_URL not set. Fill in src/distributed scraper/.env and retry.")
        return

    boards = DEFAULT_BOARDS | ({"sideboard"} if args.include_sideboard else set())
    conn   = psycopg2.connect(pg_url)

    try:
        formats = get_formats(conn, args.format)
        if not formats:
            print("No matching formats found in the database.")
            return

        print(f"Computing stats for: {', '.join(formats)}")
        print(f"Boards: {', '.join(sorted(boards))}  |  min co-occurrence: {args.min_cooccur}")

        run(conn, formats, boards, args.min_cooccur)
    finally:
        conn.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
