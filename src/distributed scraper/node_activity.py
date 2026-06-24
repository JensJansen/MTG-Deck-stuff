"""
node_activity.py - Show which distributed nodes are actively claiming work.

Reads claim timestamps straight from the database (the source of truth):
  - Central/discovery nodes stamp card_sweep_status.claimed_by_{fmt} / claimed_at_{fmt}
  - Scraper/fetch nodes stamp <format>_decks.claimed_by / claimed_at (status='claimed')

A node counts as "active" if it has claimed something within --window minutes.

Usage:
    python "src/distributed scraper/node_activity.py"
    python "src/distributed scraper/node_activity.py" --window 5
"""

import argparse
import os

import psycopg2

from config import ALL_FORMATS, format_to_table_prefix
from constants.env import load_env


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", type=int, default=10, help="Active window in minutes (default 10).")
    args = ap.parse_args()
    w = args.window

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()

    print(f"\nActivity in the last {w} minute(s)\n" + "=" * 50)

    # ---- Central / discovery nodes (card claims) ----------------------------
    print("\nCENTRAL (discovery) nodes - by format:")
    central_workers: set[str] = set()
    for fmt in ALL_FORMATS:
        at = f"claimed_at_{fmt}"
        by = f"claimed_by_{fmt}"
        cur.execute(
            f"""
            SELECT {by}, COUNT(*), MAX({at})
            FROM   card_sweep_status
            WHERE  {at} > NOW() - (%s * INTERVAL '1 minute') AND {by} IS NOT NULL
            GROUP  BY {by}
            ORDER  BY MAX({at}) DESC
            """,
            (w,),
        )
        rows = cur.fetchall()
        if not rows:
            continue
        print(f"  [{fmt}]")
        for worker, n, last in rows:
            central_workers.add(worker)
            print(f"    {worker:<32} {n:>5} cards   last {last:%H:%M:%S}")
    print(f"  -> {len(central_workers)} active central worker(s)")

    # ---- Scraper / fetch nodes (deck claims) --------------------------------
    print("\nSCRAPER (fetch) nodes - currently-claimed decks:")
    scraper_workers: set[str] = set()
    for fmt in ALL_FORMATS:
        table = format_to_table_prefix(fmt) + "_decks"
        cur.execute(
            f"""
            SELECT claimed_by, COUNT(*), MAX(claimed_at)
            FROM   {table}
            WHERE  status = 'claimed' AND claimed_at > NOW() - (%s * INTERVAL '1 minute')
                   AND claimed_by IS NOT NULL
            GROUP  BY claimed_by
            ORDER  BY MAX(claimed_at) DESC
            """,
            (w,),
        )
        rows = cur.fetchall()
        if not rows:
            continue
        print(f"  [{fmt}]")
        for worker, n, last in rows:
            scraper_workers.add(worker)
            print(f"    {worker:<32} {n:>5} decks   last {last:%H:%M:%S}")
    print(f"  -> {len(scraper_workers)} active scraper worker(s)")

    print("\n" + "=" * 50)
    print(f"TOTAL: {len(central_workers)} central + {len(scraper_workers)} scraper active\n")
    conn.close()


if __name__ == "__main__":
    main()
