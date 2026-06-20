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
import logging
import os
import time
import traceback
from typing import Annotated

import psycopg2
import psycopg2.extras
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from config import ALL_FORMATS, CLAIM_TIMEOUT_MINUTES, SINGLETON_FORMATS
from constants.env import load_env
from db import get_connection, _get_pool

load_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("api")

app = FastAPI(title="Deck Scraper API", version="1.0")


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

class _LogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        t0 = time.monotonic()
        worker = request.headers.get("x-api-key", "")[:8] or "anon"
        log.info("→ %s %s  (key=...%s)", request.method, request.url.path, worker[-4:])
        _log_pool_state("pre-request")
        try:
            response: Response = await call_next(request)
        except Exception:
            elapsed = time.monotonic() - t0
            log.error("← %s %s  UNHANDLED EXCEPTION  %.2fs\n%s",
                      request.method, request.url.path, elapsed, traceback.format_exc())
            raise
        elapsed = time.monotonic() - t0
        log.info("← %s %s  %s  %.2fs", request.method, request.url.path, response.status_code, elapsed)
        return response

app.add_middleware(_LogMiddleware)


def _log_pool_state(label: str = "") -> None:
    try:
        pool = _get_pool()
        used = len(pool._used)
        avail = len(pool._pool)
        log.info("pool[%s]: %d in-use, %d available, %d max", label, used, avail, pool.maxconn)
    except Exception:
        pass


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


class DeckCardsSubmission(BaseModel):
    deck_id: str
    worker_id: str
    format: str
    cards: list[CardIn]


class DeckCardsResult(BaseModel):
    deck_id: str
    rows_written: int


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
    t0 = time.monotonic()
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
    rows = cur.fetchall()
    log.info("claim from %s: %d row(s) in %.2fs", table, len(rows), time.monotonic() - t0)
    return rows


def _call_submit_proc(cur, proc: str, deck_id: str, worker_id: str, cards_json: str) -> tuple[int, bool]:
    """
    Call a submission stored procedure and return (rows_written, collision).
    Uses a savepoint so a failure on this deck doesn't abort the outer transaction.
    Returns (-1, False) if the call itself failed — caller should log and skip.
    """
    t0 = time.monotonic()
    try:
        cur.execute("SAVEPOINT deck_submit")
        cur.execute(
            f"CALL {proc}(%s, %s, %s::jsonb, 0, FALSE)",
            (deck_id, worker_id, cards_json),
        )
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT deck_submit")
        rows_written = row[0] if row else 0
        collision    = bool(row[1]) if row else False
        elapsed = time.monotonic() - t0
        log.info(
            "  %s  proc=%s  rows=%d  collision=%s  %.2fs",
            deck_id, proc, rows_written, collision, elapsed,
        )
        return rows_written, collision
    except Exception as exc:
        elapsed = time.monotonic() - t0
        log.error(
            "  %s  proc=%s  FAILED in %.2fs: %s\n%s",
            deck_id, proc, elapsed, exc, traceback.format_exc(),
        )
        try:
            cur.execute("ROLLBACK TO SAVEPOINT deck_submit")
            cur.execute("RELEASE SAVEPOINT deck_submit")
        except Exception as rb_exc:
            log.error("  savepoint rollback also failed: %s", rb_exc)
        return -1, False


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


@app.post("/cards/{card_name:path}/swept", status_code=200)
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
    log.info("claim_batch: worker=%s size=%d", req.worker_id, req.batch_size)
    with get_connection() as conn:
        with conn.cursor() as cur:
            rows = _claim_from(cur, "singleton_decks", CLAIM_TIMEOUT_MINUTES, req.batch_size, req.worker_id)
            if not rows:
                rows = _claim_from(cur, "decks", CLAIM_TIMEOUT_MINUTES, req.batch_size, req.worker_id)
        conn.commit()

    _log_pool_state("post-claim")
    return [DeckOut(public_id=r[0], name=r[1], format=r[2], author=r[3]) for r in rows]


@app.post("/decks/cards/batch", response_model=list[DeckCardsResult])
def submit_cards_batch(_: Auth, submissions: list[DeckCardsSubmission]) -> list[DeckCardsResult]:
    """
    Submit card rows for a batch of decks in a single request.

    Routes each deck to submit_singleton_deck or submit_deck_cards based on format.
    Each deck uses a savepoint so one failure does not abort the whole batch.
    """
    if not submissions:
        return []

    log.info("submit_cards_batch: %d deck(s)", len(submissions))
    results: list[DeckCardsResult] = []
    failed  = 0

    t0 = time.monotonic()
    with get_connection() as conn:
        with conn.cursor() as cur:
            for sub in submissions:
                proc = "submit_singleton_deck" if sub.format in SINGLETON_FORMATS else "submit_deck_cards"
                cards_json = json.dumps([
                    {"card_name": c.card_name, "board": c.board, "quantity": c.quantity}
                    for c in sub.cards
                ])
                log.info("  submitting %s  proc=%s  cards=%d", sub.deck_id, proc, len(sub.cards))
                rows_written, collision = _call_submit_proc(cur, proc, sub.deck_id, sub.worker_id, cards_json)

                if rows_written == -1:
                    failed += 1
                elif collision:
                    log.warning("  %s already done (collision) — skipping", sub.deck_id)
                    results.append(DeckCardsResult(deck_id=sub.deck_id, rows_written=0))
                else:
                    results.append(DeckCardsResult(deck_id=sub.deck_id, rows_written=rows_written))

        conn.commit()

    elapsed = time.monotonic() - t0
    _log_pool_state("post-submit")
    log.info(
        "submit_cards_batch done: %d ok, %d failed, %d collision in %.2fs",
        len(results), failed, sum(1 for r in results if r.rows_written == 0), elapsed,
    )
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
        raise HTTPException(status_code=409, detail="Deck already processed")

    return CardsResult(rows_written=rows_written if rows_written != -1 else 0)
