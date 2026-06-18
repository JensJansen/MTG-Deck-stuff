"""
api.py - FastAPI coordination layer for the distributed scraper.

All database writes from satellite nodes go through this service.
Runs alongside the PostgreSQL database on the central host.

Endpoints:
    GET  /health                   Liveness probe (no auth required)
    GET  /cards                    Card names for discovery nodes to iterate
    POST /decks                    Bulk-upsert discovered decks (routes by format)
    POST /decks/batch              Atomically claim a batch (singleton-first)
    POST /decks/cards/batch        Submit card rows for a batch of decks
    POST /decks/{id}/cards         Submit card rows for a single deck
    POST /decks/{id}/error         Mark a deck as errored

Environment variables:
    DATABASE_URL   PostgreSQL connection string (required)
    API_KEY        Shared secret; must appear in X-Api-Key request header (required)

Usage:
    uvicorn api:app --host 0.0.0.0 --port 8000
"""

import json
import os
from typing import Annotated

import psycopg2.extras
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from config import ALL_FORMATS, CLAIM_TIMEOUT_MINUTES, SINGLETON_FORMATS
from constants.env import load_env
from db import get_connection

load_env()

app = FastAPI(title="Deck Scraper API", version="1.0")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _get_api_key() -> str:
    key = os.environ.get("API_KEY", "")
    if not key:
        raise RuntimeError("API_KEY environment variable is not set.")
    return key


def _require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != _get_api_key():
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


Auth = Annotated[None, Depends(_require_key)]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class CardInfo(BaseModel):
    name: str
    fully_swept: bool


class SweptRequest(BaseModel):
    format: str


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
    format: str
    cards: list[CardIn]


class CardsResult(BaseModel):
    rows_written: int
    collision: bool = False


class DeckCardsSubmission(BaseModel):
    deck_id: str
    worker_id: str
    format: str
    cards: list[CardIn]


class DeckCardsResult(BaseModel):
    deck_id: str
    rows_written: int
    collision: bool


class ErrorReport(BaseModel):
    worker_id: str
    detail: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_col(prefix: str, fmt: str) -> str:
    """Build a validated column name like 'legal_pauper' or 'swept_commander'."""
    if fmt not in ALL_FORMATS:
        raise ValueError(f"Unknown format: {fmt!r}")
    return f"{prefix}_{fmt}"

def _upsert_decks(cur, rows: list[tuple], table: str) -> list[tuple]:
    """Bulk-upsert deck rows into the given table. Returns (is_new,) tuples."""
    return psycopg2.extras.execute_values(
        cur,
        f"""
        INSERT INTO {table} (
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


def _claim_from(cur, table: str, timeout_minutes: int, batch_size: int, worker_id: str) -> list[tuple]:
    """Attempt to claim up to batch_size decks from the given table."""
    cur.execute(
        f"""
        WITH to_claim AS (
            SELECT public_id
            FROM   {table}
            WHERE  status = 'discovered'
               OR  (status = 'claimed'
                    AND claimed_at < NOW() - (%s * INTERVAL '1 minute'))
            ORDER  BY scraped_at
            LIMIT  %s
            FOR UPDATE SKIP LOCKED
        )
        UPDATE {table}
        SET    status     = 'claimed',
               claimed_at = NOW(),
               claimed_by = %s
        WHERE  public_id IN (SELECT public_id FROM to_claim)
        RETURNING public_id, name, format, author
        """,
        (timeout_minutes, batch_size, worker_id),
    )
    return cur.fetchall()


def _call_submit_proc(cur, proc: str, deck_id: str, worker_id: str, cards_json: str) -> tuple[int, bool]:
    cur.execute(f"CALL {proc}(%s, %s, %s::jsonb, 0, FALSE)", (deck_id, worker_id, cards_json))
    row = cur.fetchone()
    return (row[0], row[1]) if row else (0, False)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"ok": True}


@app.get("/cards", response_model=list[CardInfo])
def get_cards(_: Auth, format: str | None = None) -> list[CardInfo]:
    """Return cards with their per-format sweep status, filtered to legal cards when format is given."""
    if format and format not in ALL_FORMATS:
        raise HTTPException(status_code=400, detail=f"Unknown format: {format!r}")

    with get_connection() as conn:
        with conn.cursor() as cur:
            if format:
                swept_col = _format_col("swept", format)
                legal_col = _format_col("legal", format)
                cur.execute(
                    f"""
                    SELECT c.card_name, COALESCE(ss.{swept_col}, FALSE)
                    FROM   cards c
                    LEFT   JOIN card_sweep_status ss ON c.card_name = ss.card_name
                    WHERE  c.{legal_col} = 'legal'
                    ORDER  BY c.card_name
                    """
                )
            else:
                cur.execute("SELECT card_name, FALSE FROM cards ORDER BY card_name")
            return [CardInfo(name=row[0], fully_swept=row[1]) for row in cur.fetchall()]


@app.post("/cards/{card_name}/swept", status_code=200)
def mark_card_swept(_: Auth, card_name: str, req: SweptRequest) -> dict:
    """Mark a card as fully swept for a given format."""
    if req.format not in ALL_FORMATS:
        raise HTTPException(status_code=400, detail=f"Unknown format: {req.format!r}")
    swept_col = _format_col("swept", req.format)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE card_sweep_status SET {swept_col} = TRUE WHERE card_name = %s",
                (card_name,),
            )
        conn.commit()
    return {"ok": True}


@app.post("/decks", response_model=UpsertResult)
def post_decks(_: Auth, decks: list[DeckIn]) -> UpsertResult:
    """
    Bulk-upsert newly discovered decks, routing to singleton_decks or decks by format.

    On conflict, metadata fields are refreshed but status/claimed_at/claimed_by
    are never overwritten, so claimed or done decks remain untouched.
    """
    if not decks:
        return UpsertResult(upserted=0, new=0, existing=0)

    def _rows(subset: list[DeckIn]) -> list[tuple]:
        return [
            (d.public_id, d.name, d.format, d.author, d.color_mask,
             d.created_at_utc, d.updated_at_utc, d.scraped_at, "discovered")
            for d in subset
        ]

    singleton = [d for d in decks if d.format in SINGLETON_FORMATS]
    regular   = [d for d in decks if d.format not in SINGLETON_FORMATS]
    returned: list[tuple] = []

    with get_connection() as conn:
        with conn.cursor() as cur:
            if singleton:
                returned += _upsert_decks(cur, _rows(singleton), "singleton_decks")
            if regular:
                returned += _upsert_decks(cur, _rows(regular), "decks")
        conn.commit()

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

    Tries singleton_decks first; falls back to decks when no singleton work
    is available. Never mixes rows from both tables in one response.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            rows = _claim_from(cur, "singleton_decks", CLAIM_TIMEOUT_MINUTES, req.batch_size, req.worker_id)
            if not rows:
                rows = _claim_from(cur, "decks", CLAIM_TIMEOUT_MINUTES, req.batch_size, req.worker_id)
        conn.commit()

    return [DeckOut(public_id=r[0], name=r[1], format=r[2], author=r[3]) for r in rows]


@app.post("/decks/cards/batch", response_model=list[DeckCardsResult])
def submit_cards_batch(_: Auth, submissions: list[DeckCardsSubmission]) -> list[DeckCardsResult]:
    """
    Submit card rows for a batch of decks in a single request.

    Routes each deck to submit_singleton_deck or submit_deck_cards based on format.
    All decks share one transaction.
    """
    if not submissions:
        return []

    results: list[DeckCardsResult] = []

    with get_connection() as conn:
        with conn.cursor() as cur:
            for sub in submissions:
                proc = "submit_singleton_deck" if sub.format in SINGLETON_FORMATS else "submit_deck_cards"
                cards_json = json.dumps([
                    {"card_name": c.card_name, "board": c.board, "quantity": c.quantity}
                    for c in sub.cards
                ])
                rows_written, collision = _call_submit_proc(cur, proc, sub.deck_id, sub.worker_id, cards_json)
                results.append(DeckCardsResult(
                    deck_id=sub.deck_id,
                    rows_written=rows_written,
                    collision=collision,
                ))
        conn.commit()

    return results


@app.post("/decks/{public_id}/cards", response_model=CardsResult)
def submit_cards(_: Auth, public_id: str, submission: CardsSubmission) -> CardsResult:
    """
    Write card rows for a single processed deck and mark it done.

    Routes to submit_singleton_deck or submit_deck_cards based on format.
    Returns 409 if the deck was already done.
    """
    proc = "submit_singleton_deck" if submission.format in SINGLETON_FORMATS else "submit_deck_cards"
    cards_json = json.dumps([
        {"card_name": c.card_name, "board": c.board, "quantity": c.quantity}
        for c in submission.cards
    ])

    with get_connection() as conn:
        with conn.cursor() as cur:
            rows_written, collision = _call_submit_proc(cur, proc, public_id, submission.worker_id, cards_json)
        conn.commit()

    if collision:
        raise HTTPException(status_code=409, detail=f"Deck {public_id} is already done.")

    return CardsResult(rows_written=rows_written)


@app.post("/decks/{public_id}/error", status_code=200)
def report_error(_: Auth, public_id: str, report: ErrorReport) -> dict:
    """Mark a deck as errored after a failed fetch attempt."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE decks SET status = 'error' WHERE public_id = %s",
                (public_id,),
            )
        conn.commit()
    return {"ok": True}
