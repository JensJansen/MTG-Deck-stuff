"""
api.py - FastAPI coordination layer for the distributed scraper.

All database writes from satellite nodes go through this service.
Runs alongside the PostgreSQL database on the central host.

Endpoints:
    GET  /health                   Liveness probe (no auth required)
    GET  /cards                    Card names for discovery nodes to iterate
    POST /decks                    Bulk-upsert discovered decks
    POST /decks/batch              Atomically claim a batch for processing
    POST /decks/{id}/cards         Submit card rows; logs collision if already done
    POST /decks/{id}/error         Mark a deck as errored

Environment variables:
    DATABASE_URL   PostgreSQL connection string (required)
    API_KEY        Shared secret; must appear in X-Api-Key request header (required)

Usage:
    uvicorn api:app --host 0.0.0.0 --port 8000
"""

import os
from typing import Annotated

import psycopg2.extras
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from config import CLAIM_TIMEOUT_MINUTES, LEGAL_FORMATS
from constants.env import load_env
from db import get_connection

load_env()

app = FastAPI(title="Deck Scraper API", version="1.0")

_API_KEY = os.environ.get("API_KEY", "")
if not _API_KEY:
    raise RuntimeError("API_KEY environment variable is not set.")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


Auth = Annotated[None, Depends(_require_key)]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class DeckIn(BaseModel):
    public_id: str
    name: str | None = None
    format: str | None = None
    author: str | None = None
    color_mask: int = 0
    created_at_utc: str | None = None
    updated_at_utc: str | None = None
    scraped_at: str


class UpsertResult(BaseModel):
    upserted: int
    new: int
    existing: int


class BatchRequest(BaseModel):
    batch_size: int
    worker_id: str


class DeckOut(BaseModel):
    public_id: str
    name: str | None = None
    format: str | None = None
    author: str | None = None


class CardIn(BaseModel):
    card_name: str
    board: str
    quantity: int = 1


class CardsSubmission(BaseModel):
    worker_id: str
    cards: list[CardIn]


class CardsResult(BaseModel):
    rows_written: int


class ErrorReport(BaseModel):
    worker_id: str
    detail: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"ok": True}


@app.get("/cards", response_model=list[str])
def get_cards(_: Auth, format: str | None = None) -> list[str]:
    """Return card names, optionally filtered to cards legal in a given format."""
    if format and format not in LEGAL_FORMATS:
        raise HTTPException(status_code=400, detail=f"Unknown format: {format!r}")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if format:
                cur.execute(
                    f"SELECT card_name FROM cards WHERE legal_{format} = 'legal' ORDER BY card_name"
                )
            else:
                cur.execute("SELECT card_name FROM cards ORDER BY card_name")
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


@app.post("/decks", response_model=UpsertResult)
def post_decks(_: Auth, decks: list[DeckIn]) -> UpsertResult:
    """
    Bulk-upsert newly discovered decks.

    On conflict, metadata fields are refreshed but status/claimed_at/claimed_by
    are never overwritten, so claimed or done decks remain untouched.
    Returns counts of newly inserted vs already-known decks (used by discovery
    nodes to detect when incremental pagination has caught up to prior runs).
    """
    if not decks:
        return UpsertResult(upserted=0, new=0, existing=0)

    rows = [
        (
            d.public_id, d.name, d.format, d.author, d.color_mask,
            d.created_at_utc, d.updated_at_utc, d.scraped_at, "discovered",
        )
        for d in decks
    ]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            returned = psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO decks (
                    public_id, name, format, author, color_mask,
                    created_at_utc, updated_at_utc, scraped_at, status
                ) VALUES %s
                ON CONFLICT (public_id) DO UPDATE SET
                    name           = EXCLUDED.name,
                    format         = EXCLUDED.format,
                    author         = EXCLUDED.author,
                    color_mask     = EXCLUDED.color_mask,
                    updated_at_utc = EXCLUDED.updated_at_utc,
                    scraped_at     = EXCLUDED.scraped_at
                RETURNING (xmax = 0) AS is_new
                """,
                rows,
                fetch=True,
            )
        conn.commit()
    finally:
        conn.close()

    new_count = sum(1 for (is_new,) in returned if is_new)
    return UpsertResult(
        upserted=len(returned),
        new=new_count,
        existing=len(returned) - new_count,
    )


@app.post("/decks/batch", response_model=list[DeckOut])
def claim_batch(_: Auth, req: BatchRequest) -> list[DeckOut]:
    """
    Atomically claim up to batch_size unclaimed decks.

    Also reclaims decks that have been in 'claimed' state longer than
    CLAIM_TIMEOUT_MINUTES, which recovers from node crashes.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH to_claim AS (
                    SELECT public_id
                    FROM   decks
                    WHERE  status = 'discovered'
                       OR  (status = 'claimed'
                            AND claimed_at < NOW() - (%s * INTERVAL '1 minute'))
                    ORDER  BY scraped_at
                    LIMIT  %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE decks
                SET    status     = 'claimed',
                       claimed_at = NOW(),
                       claimed_by = %s
                WHERE  public_id IN (SELECT public_id FROM to_claim)
                RETURNING public_id, name, format, author
                """,
                (CLAIM_TIMEOUT_MINUTES, req.batch_size, req.worker_id),
            )
            rows = cur.fetchall()
        conn.commit()
    finally:
        conn.close()

    return [DeckOut(public_id=r[0], name=r[1], format=r[2], author=r[3]) for r in rows]


@app.post("/decks/{public_id}/cards", response_model=CardsResult)
def submit_cards(_: Auth, public_id: str, submission: CardsSubmission) -> CardsResult:
    """
    Write card rows for a processed deck and mark it done.

    If the deck is already marked 'done', logs the attempt to collision_log
    and returns 409. This handles the race where a timed-out node finishes
    after another node already completed the same deck.
    """
    card_rows: list[tuple] = []

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM decks WHERE public_id = %s", (public_id,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Deck {public_id!r} not found")

            if row[0] == "done":
                cur.execute(
                    "INSERT INTO collision_log (deck_id, worker_id, detail) VALUES (%s, %s, %s)",
                    (public_id, submission.worker_id, "Submitted cards for deck already marked done"),
                )
                conn.commit()
                raise HTTPException(
                    status_code=409,
                    detail=f"Deck {public_id} is already done; collision logged.",
                )

            names = list({c.card_name for c in submission.cards})
            cur.execute(
                "SELECT card_name, id FROM cards WHERE card_name = ANY(%s)",
                (names,),
            )
            name_to_id = {r[0]: r[1] for r in cur.fetchall()}

            for name in names:
                if name not in name_to_id:
                    print(f"[warn] deck {public_id}: card not in DB, skipping: {name!r}")

            card_rows = [
                (public_id, name_to_id[c.card_name], c.board, c.quantity)
                for c in submission.cards
                if c.card_name in name_to_id
            ]

            cur.execute("DELETE FROM deck_cards WHERE deck_id = %s", (public_id,))
            if card_rows:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO deck_cards (deck_id, card_id, board, quantity) VALUES %s",
                    card_rows,
                )
            cur.execute(
                "UPDATE decks SET status = 'done', cards_fetched_at = NOW() WHERE public_id = %s",
                (public_id,),
            )

        conn.commit()
    finally:
        conn.close()

    return CardsResult(rows_written=len(card_rows))


@app.post("/decks/{public_id}/error", status_code=200)
def report_error(_: Auth, public_id: str, report: ErrorReport) -> dict:
    """Mark a deck as errored after a failed fetch attempt."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE decks SET status = 'error' WHERE public_id = %s",
                (public_id,),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}
