"""
api.py - FastAPI coordination layer for the distributed scraper.

All database writes from satellite nodes go through this service.
Runs alongside the PostgreSQL database on the central host.

Endpoints:
    GET  /health                   Liveness probe (no auth required)
    GET  /cards                    Card names for discovery nodes to iterate
    POST /decks                    Bulk-upsert discovered decks (routes by format)
    POST /decks/batch              Atomically claim a batch from a single format table
    POST /decks/cards/batch        Submit card rows for a batch of decks
    POST /decks/{id}/cards         Submit card rows for a single deck

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
# Format → table name helpers
# ---------------------------------------------------------------------------

# Formats whose canonical name differs from their table prefix.
_FORMAT_TABLE_NAME: dict[str, str] = {
    "highlanderCanadian": "canadian_highlander",
}
_TABLE_FORMAT_NAME: dict[str, str] = {v: k for k, v in _FORMAT_TABLE_NAME.items()}


def _format_table(fmt: str) -> str:
    """Return the table prefix for a format, e.g. 'highlanderCanadian' → 'canadian_highlander'."""
    return _FORMAT_TABLE_NAME.get(fmt, fmt)


def _table_format(prefix: str) -> str:
    """Reverse of _format_table: table prefix → canonical format string."""
    return _TABLE_FORMAT_NAME.get(prefix, prefix)


# Ordered list of (canonical_format, deck_table) used by claim_batch.
# A node always claims from exactly one table per request.
_CLAIM_ORDER: list[tuple[str, str]] = [
    ("commander",          "commander_decks"),
    ("highlanderCanadian", "canadian_highlander_decks"),
    ("pauper",             "pauper_decks"),
    ("standard",           "standard_decks"),
    ("modern",             "modern_decks"),
    ("vintage",            "vintage_decks"),
    ("legacy",             "legacy_decks"),
]


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
    """Bulk-upsert deck rows into a per-format deck table. Returns (is_new,) tuples."""
    return psycopg2.extras.execute_values(
        cur,
        f"""
        INSERT INTO {table} (
            public_id, name, author, color_mask,
            created_at_utc, updated_at_utc, scraped_at, status
        ) VALUES %s
        ON CONFLICT (public_id) DO UPDATE SET
            name           = EXCLUDED.name,
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
    """Attempt to claim up to batch_size decks from the given per-format deck table."""
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
        RETURNING public_id, name, author
        """,
        (timeout_minutes, batch_size, worker_id),
    )
    rows = cur.fetchall()
    log.info("claim from %s: %d row(s) in %.2fs", table, len(rows), time.monotonic() - t0)
    return rows


def _call_submit_proc(cur, proc: str, args: tuple) -> tuple[int, bool]:
    """
    Call a submission stored procedure and return (rows_written, collision).
    All positional args are passed via `args`; cards JSON must be the final element
    and is cast to JSONB automatically. Uses a savepoint so a failure on this deck
    does not abort the outer transaction.
    Returns (-1, False) if the call itself failed.
    """
    t0 = time.monotonic()
    try:
        cur.execute("SAVEPOINT deck_submit")
        n_pre = len(args) - 1
        placeholders = ", ".join(["%s"] * n_pre)
        cur.execute(
            f"CALL {proc}({placeholders}, %s::jsonb, 0, FALSE)",
            args,
        )
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT deck_submit")
        rows_written = row[0] if row else 0
        collision    = bool(row[1]) if row else False
        elapsed = time.monotonic() - t0
        log.info(
            "  %s  proc=%s  rows=%d  collision=%s  %.2fs",
            args[2] if len(args) > 2 else args[1], proc, rows_written, collision, elapsed,
        )
        return rows_written, collision
    except Exception as exc:
        elapsed = time.monotonic() - t0
        log.error(
            "  proc=%s  FAILED in %.2fs: %s\n%s",
            proc, elapsed, exc, traceback.format_exc(),
        )
        try:
            cur.execute("ROLLBACK TO SAVEPOINT deck_submit")
            cur.execute("RELEASE SAVEPOINT deck_submit")
        except Exception as rb_exc:
            log.error("  savepoint rollback also failed: %s", rb_exc)
        return -1, False


def _submission_proc_and_args(fmt: str, deck_id: str, worker_id: str, cards_json: str) -> tuple[str, tuple]:
    """Return (proc_name, full_args_tuple) for submitting cards for a deck of the given format."""
    table_prefix = _format_table(fmt)
    if fmt in SINGLETON_FORMATS:
        return (
            "submit_format_singleton_deck",
            (f"{table_prefix}_decks", deck_id, worker_id, cards_json),
        )
    return (
        "submit_format_deck_cards",
        (f"{table_prefix}_decks", f"{table_prefix}_deck_cards", deck_id, worker_id, cards_json),
    )


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
    Bulk-upsert newly discovered decks into per-format deck tables.

    Decks are grouped by format and each group is upserted into its own table.
    On conflict, metadata fields are refreshed but status/claimed_at/claimed_by
    are never overwritten, so claimed or done decks remain untouched.
    """
    if not decks:
        return UpsertResult(upserted=0, new=0, existing=0)

    invalid = {d.format for d in decks if d.format not in ALL_FORMATS}
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown format(s): {invalid}")

    by_format: dict[str, list[DeckIn]] = {}
    for d in decks:
        by_format.setdefault(d.format, []).append(d)

    def _rows(subset: list[DeckIn]) -> list[tuple]:
        return [
            (d.public_id, d.name, d.author, d.color_mask,
             d.created_at_utc, d.updated_at_utc, d.scraped_at, "discovered")
            for d in subset
        ]

    returned: list[tuple] = []

    with get_connection() as conn:
        with conn.cursor() as cur:
            for fmt, group in by_format.items():
                table = f"{_format_table(fmt)}_decks"
                returned += _upsert_decks(cur, _rows(group), table)
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
    Atomically claim up to batch_size unclaimed decks from a single format table.

    Iterates per-format tables in priority order and returns from the first table
    that has claimable work. A batch is always from one format — nodes never
    receive a mix of formats in a single response.
    """
    log.info("claim_batch: worker=%s size=%d", req.worker_id, req.batch_size)
    with get_connection() as conn:
        with conn.cursor() as cur:
            for fmt, table in _CLAIM_ORDER:
                rows = _claim_from(cur, table, CLAIM_TIMEOUT_MINUTES, req.batch_size, req.worker_id)
                if rows:
                    conn.commit()
                    _log_pool_state("post-claim")
                    return [DeckOut(public_id=r[0], name=r[1], format=fmt, author=r[2]) for r in rows]
        conn.commit()

    _log_pool_state("post-claim")
    return []


@app.post("/decks/cards/batch", response_model=list[DeckCardsResult])
def submit_cards_batch(_: Auth, submissions: list[DeckCardsSubmission]) -> list[DeckCardsResult]:
    """
    Submit card rows for a batch of decks in a single request.

    Routes each deck to the correct per-format stored procedure based on format.
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
                cards_json = json.dumps([
                    {"card_name": c.card_name, "board": c.board, "quantity": c.quantity}
                    for c in sub.cards
                ])
                proc, args = _submission_proc_and_args(sub.format, sub.deck_id, sub.worker_id, cards_json)
                log.info("  submitting %s  proc=%s  cards=%d", sub.deck_id, proc, len(sub.cards))
                rows_written, collision = _call_submit_proc(cur, proc, args)

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

    Routes to the correct per-format stored procedure based on format.
    Returns 409 if the deck was already done.
    """
    cards_json = json.dumps([
        {"card_name": c.card_name, "board": c.board, "quantity": c.quantity}
        for c in submission.cards
    ])
    proc, args = _submission_proc_and_args(submission.format, public_id, submission.worker_id, cards_json)

    with get_connection() as conn:
        with conn.cursor() as cur:
            rows_written, collision = _call_submit_proc(cur, proc, args)
        conn.commit()

    if collision:
        raise HTTPException(status_code=409, detail="Deck already processed")

    return CardsResult(rows_written=rows_written if rows_written != -1 else 0)
