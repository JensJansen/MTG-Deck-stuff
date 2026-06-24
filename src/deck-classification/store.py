"""
Persist archetype records and deck assignments to Postgres.

The full result of a pipeline run is passed as two structures:

  archetypes_to_write : list of archetype dicts (see _write_archetypes)
  assignments         : list of (deck_id, archetype_db_id, level, confidence)

Existing rows for the same format and run_id are replaced so the pipeline
can be re-run safely without accumulating stale data.
"""
import io
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


def clear_format(conn, fmt: str, color_mask: int | None = None) -> None:
    """
    Remove archetype records before writing fresh ones.
    When color_mask is provided only that partition is cleared, leaving other
    color-identity partitions untouched.  Cascades to deck_archetypes via FK.
    Does NOT commit — callers own the transaction.
    """
    with conn.cursor() as cur:
        if color_mask is not None:
            cur.execute(
                "DELETE FROM archetypes WHERE format = %s AND color_mask = %s",
                (fmt, color_mask),
            )
            print(f"  Cleared archetypes for format={fmt!r} color_mask={color_mask}")
        else:
            cur.execute("DELETE FROM archetypes WHERE format = %s", (fmt,))
            print(f"  Cleared archetypes for format={fmt!r}")


def write_archetypes(
    conn,
    fmt: str,
    run_id: str,
    records: list[dict],
    color_mask: int | None = None,
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
                color_mask,
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
            db_ids = [
                row[0]
                for row in psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO archetypes
                        (format, color_mask, level, parent_id, centroid, keystone_cards,
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
    chunk_size: int = 200_000,
) -> None:
    """
    Bulk-load deck → archetype assignments via COPY.

    clear_format() cascades a DELETE to deck_archetypes before this is called,
    so the table is empty for this format and COPY is safe (no conflicts).

    assignments: [(deck_id, archetype_db_id, level, confidence), ...]
    """
    total = 0
    with conn.cursor() as cur:
        buf = io.StringIO()
        for deck_id, archetype_db_id, level, confidence in assignments:
            conf = f"{round(float(confidence), 6)}" if confidence is not None else r"\N"
            buf.write(f"{deck_id}\t{archetype_db_id}\t{level}\t{conf}\n")
            total += 1
            if total % chunk_size == 0:
                buf.seek(0)
                cur.copy_expert(
                    "COPY deck_archetypes (deck_id, archetype_id, level, confidence)"
                    " FROM STDIN",
                    buf,
                )
                buf = io.StringIO()
        if buf.tell() > 0:
            buf.seek(0)
            cur.copy_expert(
                "COPY deck_archetypes (deck_id, archetype_id, level, confidence)"
                " FROM STDIN",
                buf,
            )
    print(f"  Wrote {total:,} deck assignments")
