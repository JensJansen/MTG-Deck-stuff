"""Quick breakdown of commander card sweep status by category."""
import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent.parent))
from constants.env import load_env
load_env()

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("""
SELECT
  CASE
    WHEN ss.swept_commander = FALSE AND ss.claimed_at_commander IS NULL
      THEN 'A. Unswept - never claimed'
    WHEN ss.swept_commander = FALSE
         AND ss.claimed_at_commander < NOW() - (30 * INTERVAL '1 minute')
      THEN 'B. Unswept - lease expired (reclaimable)'
    WHEN ss.swept_commander = FALSE
         AND ss.claimed_at_commander >= NOW() - (30 * INTERVAL '1 minute')
      THEN 'C. Unswept - lease active (in progress)'
    WHEN ss.swept_commander = TRUE
         AND (ss.claimed_at_commander IS NULL
              OR ss.claimed_at_commander < NOW() - (24 * INTERVAL '1 hour'))
      THEN 'D. Swept - due for refresh'
    WHEN ss.swept_commander = TRUE
         AND ss.claimed_at_commander >= NOW() - (24 * INTERVAL '1 hour')
      THEN 'E. Swept - refresh not yet due'
    ELSE 'F. Unknown'
  END AS category,
  COUNT(*) AS card_count
FROM card_sweep_status ss
JOIN cards c ON c.card_name = ss.card_name
WHERE c.legal_commander = 'legal'
GROUP BY 1
ORDER BY 1;
""")
rows = cur.fetchall()
total = sum(r[1] for r in rows)
print("%-45s %8s" % ("Category", "Count"))
print("-" * 55)
for cat, count in rows:
    print("%-45s %8d" % (cat, count))
print("-" * 55)
print("%-45s %8d" % ("TOTAL", total))
conn.close()
