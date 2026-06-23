"""
Persist archetype records and deck assignments to Postgres.

The full result of a pipeline run is passed as two structures:

  archetypes_to_write : list of archetype dicts (see _write_archetypes)
  assignments         : list of (deck_id, archetype_db_id, level, confidence)

Existing rows for the same format and run_id are replaced so the pipeline
can be re-run safely without accumulating stale data.
"""
import sys
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).parent))


def _centroid_to_bytes(centroid: np.ndarray | None) -> bytes | None:
    if centroid is None:
        return None
    return centroid.astype(np.float32).tobytes()


def clear_format(conn, fmt: str) -> None:
    """
    Remove all archetype records for a format before writing fresh ones.
    Cascades to deck_archetypes via the FK on archetype_id.
    Does NOT commit — callers own the transaction.
    """
    with conn.cursor() as cur:
        cur.execute("DELETE FROM archetypes WHERE format = %s", (fmt,))
    print(f"  Cleared existing archetypes for format={fmt!r}")


def write_archetypes(
    conn,
    fmt: str,
    run_id: str,
    records: list[dict],
) -> dict[tuple[int, int], int]:
    """
    Insert archetype rows and return a mapping of
    (level, local_cluster_id) → db_id so that deck assignments can reference them.

    Each record in `records`:
    {
        "level":          1 or 2,
        "local_id":       int,          # L1 cluster_id or (L1_id, sub_id) tuple
        "parent_local":   int | None,   # L1 cluster_id for Level 2 rows
        "centroid":       np.ndarray | None,
        "keystone_cards": list[dict] | None,
        "member_count":   int,
    }
    """
    # Two-pass: write Level 1 first so Level 2 rows can reference their parent db_id.
    local_to_db: dict = {}

    for level in (1, 2):
        level_records = [r for r in records if r["level"] == level]
        if not level_records:
            continue

        rows = []
        for r in level_records:
            parent_db = None
            if r.get("parent_local") is not None:
                parent_db = local_to_db.get((1, r["parent_local"]))
            rows.append((
                fmt,
                level,
                parent_db,
                _centroid_to_bytes(r.get("centroid")),
                psycopg2.extras.Json(r["keystone_cards"]) if r.get("keystone_cards") else None,
                psycopg2.extras.Json(r["top_cards"])      if r.get("top_cards")      else None,
                psycopg2.extras.Json(r["color_profile"])  if r.get("color_profile")  else None,
                psycopg2.extras.Json(r["cmc_curve"])      if r.get("cmc_curve")      else None,
                r["member_count"],
                run_id,
            ))

        with conn.cursor() as cur:
            # execute_values(fetch=True) returns accumulated RETURNING results
            # across all pages internally — do NOT call cur.fetchall() after it.
            db_ids = [
                row[0]
                for row in psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO archetypes
                        (format, level, parent_id, centroid, keystone_cards,
                         top_cards, color_profile, cmc_curve, member_count, run_id)
                    VALUES %s
                    RETURNING id
                    """,
                    rows,
                    fetch=True,
                )
            ]

        for r, db_id in zip(level_records, db_ids):
            local_to_db[(level, r["local_id"])] = db_id

    print(f"  Wrote {len(records)} archetype records")
    return local_to_db


def write_assignments(
    conn,
    assignments: list[tuple[str, int, int, float]],
    batch_size: int = 5_000,
) -> None:
    """
    Upsert deck → archetype assignments.

    assignments: [(deck_id, archetype_db_id, level, confidence), ...]
    """
    with conn.cursor() as cur:
        for i in range(0, len(assignments), batch_size):
            batch = assignments[i : i + batch_size]
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO deck_archetypes (deck_id, archetype_id, level, confidence)
                VALUES %s
                ON CONFLICT (deck_id, level) DO UPDATE
                    SET archetype_id  = EXCLUDED.archetype_id,
                        confidence    = EXCLUDED.confidence,
                        classified_at = NOW()
                """,
                batch,
            )
    print(f"  Wrote {len(assignments):,} deck assignments")
