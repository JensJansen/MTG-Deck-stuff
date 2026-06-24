import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent.parent))
from constants.env import load_env
from constants.mtg import COLOR_BITS

load_env()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur  = conn.cursor()

cur.execute("""
    SELECT color_mask, COUNT(*) AS n
    FROM commander_decks
    WHERE status = 'done' AND color_mask IS NOT NULL
    GROUP BY color_mask
    ORDER BY n DESC
""")
rows = cur.fetchall()

def decode(mask):
    return "".join(c for c, b in COLOR_BITS.items() if mask & b) or "C"

total = sum(r[1] for r in rows)
print("%-10s %10s %8s" % ("Identity", "Decks", "Share"))
print("-" * 32)
for mask, n in rows:
    print("%-10s %10d %7.1f%%" % (decode(mask), n, 100 * n / total))
print("-" * 32)
print("%-10s %10d" % ("TOTAL", total))

cur.execute("SELECT COUNT(DISTINCT color_mask) FROM commander_decks WHERE status = 'done'")
print("Distinct identities:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM commander_decks WHERE status = 'done' AND color_mask IS NULL")
print("NULL color_mask:", cur.fetchone()[0])
conn.close()
