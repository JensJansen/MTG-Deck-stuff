"""
api.py - FastAPI coordination layer for the V2 distributed scraper.

Every database read and write from satellite nodes goes through this service;
nodes hold no DB credentials and never learn the schema layout. The API speaks
to nodes purely in terms of `moxfield_id` and `card_name` — integer primary keys
are an internal detail.

Two changes anchor the V2 design:
  - An in-memory card resolver (cards.resolver) handles all card_name <-> id
    translation, because v2.cards has no index on card_name. It is loaded at
    startup and refreshed via POST /admin/reload-cards.
  - All write logic lives here in versioned Python. There are no stored
    procedures; migrations are pure DDL.

Work is leased at (card, format) granularity from the normalized
v2.card_format_status table, and at (deck batch, format) granularity from the
per-format deck tables. Leasing uses FOR UPDATE SKIP LOCKED plus time-based
lease expiry so a crashed node's work is automatically reclaimed.

Endpoints:
    GET  /health                 Liveness probe (no auth)
    GET  /ready                  Readiness: DB reachable AND card map loaded
    POST /sweeps/claim           Lease one (card, format) to a discovery node
    POST /sweeps/complete        Mark a (card, format) fully swept
    POST /decks                  Bulk-upsert discovered deck stubs
    POST /decks/claim            Lease a batch of decks from one format
    POST /decks/submit           Submit fetched card lists; mark decks done
    POST /admin/reload-cards     Reload the in-memory card map after seeding

Environment variables:
    DATABASE_URL   PostgreSQL connection string (required)
    API_KEY        Shared secret; must appear in X-Api-Key request header (required)

Usage:
    uvicorn api:app --host 0.0.0.0 --port 8000
"""

import logging
import os
import time
import traceback
from contextlib import asynccontextmanager
from typing import Annotated

import psycopg2.extras
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from cards import resolver
from config import (
    CLAIM_TIMEOUT_MINUTES,
    COMMANDER_BOARD,
    DECK_BOARDS,
    FORMATS,
    FORMATS_BY_TOKEN,
    get_format,
    log_unknown_cards,
)
from constants.env import load_env
from db import get_connection, _get_pool

load_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("api")


# ---------------------------------------------------------------------------
# Lifespan: load the card map before serving traffic
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with get_connection() as conn:
            n = resolver.load(conn)
        log.info("card resolver loaded: %d cards", n)
    except Exception:
        log.error("failed to load card resolver at startup:\n%s", traceback.format_exc())
    yield


app = FastAPI(title="Deck Scraper API (v2)", version="2.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

class _LogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        t0 = time.monotonic()
        log.info("→ %s %s", request.method, request.url.path)
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

class WorkerRequest(BaseModel):
    worker_id: str


class SweepClaim(BaseModel):
    card_name: str
    format: str
    mode: str  # "full" (never fully swept) | "incremental" (re-sweep with early-exit)


class SweepComplete(BaseModel):
    card_name: str
    format: str


class DeckIn(BaseModel):
    moxfield_id: str
    name: str | None = None
    format: str
    author: str | None = None
    color_mask: int = 0
    created_at_utc: str | None = None
    updated_at_utc: str | None = None
    scraped_at: str


class UpsertResult(BaseModel):
    upserted: int
    new: int
    existing: int


class DeckBatchRequest(BaseModel):
    batch_size: int
    worker_id: str


class DeckOut(BaseModel):
    moxfield_id: str
    format: str


class CardIn(BaseModel):
    card_name: str
    board: str
    quantity: int = 1


class DeckSubmission(BaseModel):
    moxfield_id: str
    format: str
    cards: list[CardIn] = []
    error: bool = False  # True when the node gave up fetching this deck (mark it 'error')


class SubmitResult(BaseModel):
    moxfield_id: str
    written: int
    unresolved: list[str] = []
    collision: bool = False
    errored: bool = False


class ReloadResult(BaseModel):
    cards: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_format(token: str) -> None:
    if token not in FORMATS_BY_TOKEN:
        raise HTTPException(status_code=400, detail=f"Unknown format: {token!r}")


def _resolve_cards(cards: list[CardIn]) -> tuple[list[tuple[int, str, int]], list[str]]:
    """Split a submitted card list into (resolved, unresolved).

    resolved   list of (card_id, board, quantity)
    unresolved list of card names absent from v2.cards
    """
    resolved: list[tuple[int, str, int]] = []
    unresolved: list[str] = []
    for c in cards:
        cid = resolver.id_for(c.card_name)
        if cid is None:
            unresolved.append(c.card_name)
        else:
            resolved.append((cid, c.board, c.quantity))
    return resolved, unresolved


# ---------------------------------------------------------------------------
# Health / readiness
# ---------------------------------------------------------------------------

@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"ok": True}


@app.get("/ready", include_in_schema=False)
def ready() -> Response:
    """Ready only when the DB is reachable and the card map has loaded."""
    db_ok = True
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception:
        db_ok = False

    ok = db_ok and resolver.loaded
    payload = {"db": db_ok, "cards_loaded": resolver.loaded, "card_count": resolver.count}
    if not ok:
        return Response(content=str(payload), status_code=503, media_type="text/plain")
    return Response(content=str(payload), status_code=200, media_type="text/plain")


# ---------------------------------------------------------------------------
# Sweep (card x format) leasing
# ---------------------------------------------------------------------------

# Eligibility is simply "not swept this cycle" (plus the lease check). The mode
# returned to the node is decided by ever_swept: a card that has never completed
# a full sweep is always swept in full, so an interrupted first sweep can never
# leave a permanent coverage hole.
_SWEEP_CLAIM_SQL = """
    WITH ranked AS (
        SELECT card_id, format
        FROM   v2.card_format_status
        WHERE  NOT swept
          AND  (claimed_at IS NULL OR claimed_at < NOW() - (%(lease)s * INTERVAL '1 minute'))
        ORDER BY
            ever_swept ASC,                  -- never-fully-swept cards first
            claimed_at ASC NULLS FIRST,      -- then never-/least-recently-claimed
            card_id
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    UPDATE v2.card_format_status s
    SET    claimed_at = NOW(),
           claimed_by = %(worker)s
    FROM   ranked r
    WHERE  s.card_id = r.card_id AND s.format = r.format
    RETURNING s.card_id, s.format, s.ever_swept
"""


def _try_claim_sweep(conn, worker_id: str) -> tuple | None:
    with conn.cursor() as cur:
        cur.execute(_SWEEP_CLAIM_SQL, {"lease": CLAIM_TIMEOUT_MINUTES, "worker": worker_id})
        row = cur.fetchone()
    conn.commit()
    return row


def _deck_queue_drained(conn) -> bool:
    """True when no deck in any format is still pending (discovered or claimed).

    'done' and 'error' are both terminal, so a permanently-failing deck (marked
    'error' after MAX_DECK_FETCH_FAILS) can never block the cycle reset.
    """
    with conn.cursor() as cur:
        for fmt in FORMATS:
            cur.execute(
                f"SELECT 1 FROM {fmt.deck_table} WHERE status IN ('discovered', 'claimed') LIMIT 1"
            )
            if cur.fetchone():
                return False
    return True


def _reset_sweep_cycle(conn) -> int:
    """Re-arm every swept unit for a new cycle. ever_swept is left untouched."""
    with conn.cursor() as cur:
        cur.execute("UPDATE v2.card_format_status SET swept = FALSE WHERE swept = TRUE")
        n = cur.rowcount
    conn.commit()
    return n


@app.post("/sweeps/claim", response_model=SweepClaim | None)
def claim_sweep(_: Auth, req: WorkerRequest) -> SweepClaim | None:
    """Atomically lease one (card, format) sweep unit to a worker.

    Returns only units not yet swept this cycle. When none remain, the deck
    queue is checked: if it is fully drained, a new sweep cycle is started
    (all swept flags reset) and a unit is leased from the fresh cycle; otherwise
    null is returned so the worker falls back to fetching decks.

    `mode` is "full" for a card that has never completed a sweep, else
    "incremental".
    """
    with get_connection() as conn:
        row = _try_claim_sweep(conn, req.worker_id)
        if row is None and _deck_queue_drained(conn):
            n = _reset_sweep_cycle(conn)
            log.info("claim_sweep: deck queue drained — new sweep cycle (%d units re-armed)", n)
            row = _try_claim_sweep(conn, req.worker_id)

    if not row:
        return None

    card_id, fmt, ever_swept = row
    card_name = resolver.name_for(card_id)
    if card_name is None:
        # Map is stale relative to the DB; treat as nothing to do rather than
        # hand back an unusable lease.
        log.warning("claim_sweep: card_id %d not in resolver (stale map?)", card_id)
        return None

    mode = "incremental" if ever_swept else "full"
    log.info("claim_sweep: leased (%r, %s) [%s] to %s", card_name, fmt, mode, req.worker_id)
    return SweepClaim(card_name=card_name, format=fmt, mode=mode)


@app.post("/sweeps/complete", status_code=200)
def complete_sweep(_: Auth, req: SweepComplete) -> dict:
    """Mark a (card, format) swept for this cycle and fully-swept for all time.

    Sets swept = TRUE (so it is not re-claimed until the next cycle) and
    ever_swept = TRUE (so future cycles sweep it incrementally rather than full).
    """
    _validate_format(req.format)
    card_id = resolver.id_for(req.card_name)
    if card_id is None:
        raise HTTPException(status_code=400, detail=f"Unknown card: {req.card_name!r}")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE v2.card_format_status SET swept = TRUE, ever_swept = TRUE "
                "WHERE card_id = %s AND format = %s",
                (card_id, req.format),
            )
            updated = cur.rowcount
        conn.commit()

    if not updated:
        raise HTTPException(
            status_code=404,
            detail=f"No status row for card {req.card_name!r} in format {req.format!r}",
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Deck discovery (stub upsert)
# ---------------------------------------------------------------------------

# On conflict: refresh metadata always. If the incoming updated_at_utc is newer
# than what we stored, the deck changed on Moxfield since we last saw it, so
# re-queue it for fetching (status -> 'discovered', lease cleared) even if it was
# already 'done' or 'error'. Otherwise status / claim are left untouched.
_NEWER = (
    "EXCLUDED.updated_at_utc IS NOT NULL "
    "AND (d.updated_at_utc IS NULL OR EXCLUDED.updated_at_utc > d.updated_at_utc)"
)

_DECK_UPSERT_TEMPLATE = f"""
    INSERT INTO {{table}} AS d (
        moxfield_id, name, author, color_mask,
        created_at_utc, updated_at_utc, scraped_at, status
    ) VALUES %s
    ON CONFLICT (moxfield_id) DO UPDATE SET
        name           = EXCLUDED.name,
        author         = EXCLUDED.author,
        color_mask     = EXCLUDED.color_mask,
        updated_at_utc = EXCLUDED.updated_at_utc,
        scraped_at     = EXCLUDED.scraped_at,
        status     = CASE WHEN {_NEWER} THEN 'discovered' ELSE d.status     END,
        claimed_at = CASE WHEN {_NEWER} THEN NULL          ELSE d.claimed_at END,
        claimed_by = CASE WHEN {_NEWER} THEN NULL          ELSE d.claimed_by END
    RETURNING (xmax = 0) AS is_new
"""


@app.post("/decks", response_model=UpsertResult)
def post_decks(_: Auth, decks: list[DeckIn]) -> UpsertResult:
    """Bulk-upsert discovered deck stubs into their per-format deck tables.

    New decks are inserted as 'discovered'. On conflict, metadata is refreshed;
    a deck whose updated_at_utc advanced is re-queued for fetching, otherwise its
    status / claim are preserved.
    """
    if not decks:
        return UpsertResult(upserted=0, new=0, existing=0)

    invalid = {d.format for d in decks if d.format not in FORMATS_BY_TOKEN}
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown format(s): {invalid}")

    by_format: dict[str, list[DeckIn]] = {}
    for d in decks:
        by_format.setdefault(d.format, []).append(d)

    returned: list[tuple] = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            for fmt_token, group in by_format.items():
                table = get_format(fmt_token).deck_table
                rows = [
                    (d.moxfield_id, d.name, d.author, d.color_mask,
                     d.created_at_utc, d.updated_at_utc, d.scraped_at, "discovered")
                    for d in group
                ]
                returned += psycopg2.extras.execute_values(
                    cur,
                    _DECK_UPSERT_TEMPLATE.format(table=table),
                    rows,
                    fetch=True,
                )
        conn.commit()

    new_count = sum(1 for (is_new,) in returned if is_new)
    return UpsertResult(upserted=len(returned), new=new_count, existing=len(returned) - new_count)


# ---------------------------------------------------------------------------
# Deck claiming (batch, single format)
# ---------------------------------------------------------------------------

def _claim_decks(cur, table: str, batch_size: int, worker_id: str) -> list[str]:
    cur.execute(
        f"""
        WITH to_claim AS (
            SELECT id
            FROM   {table}
            WHERE  status = 'discovered'
               OR  (status = 'claimed'
                    AND claimed_at < NOW() - (%s * INTERVAL '1 minute'))
            ORDER  BY scraped_at
            LIMIT  %s
            FOR UPDATE SKIP LOCKED
        )
        UPDATE {table} t
        SET    status     = 'claimed',
               claimed_at = NOW(),
               claimed_by = %s
        FROM   to_claim c
        WHERE  t.id = c.id
        RETURNING t.moxfield_id
        """,
        (CLAIM_TIMEOUT_MINUTES, batch_size, worker_id),
    )
    return [r[0] for r in cur.fetchall()]


@app.post("/decks/claim", response_model=list[DeckOut])
def claim_decks(_: Auth, req: DeckBatchRequest) -> list[DeckOut]:
    """Atomically claim up to batch_size decks from a single format.

    Formats are tried in priority order; the first with claimable work returns.
    A batch is always from one format — never a mix.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            for fmt in FORMATS:
                ids = _claim_decks(cur, fmt.deck_table, req.batch_size, req.worker_id)
                if ids:
                    conn.commit()
                    log.info("claim_decks: %d %s deck(s) -> %s", len(ids), fmt.token, req.worker_id)
                    return [DeckOut(moxfield_id=mid, format=fmt.token) for mid in ids]
        conn.commit()
    return []


# ---------------------------------------------------------------------------
# Deck submission (write fetched card lists, mark done)
# ---------------------------------------------------------------------------

def _submit_regular(cur, fmt, sub: DeckSubmission,
                    resolved: list[tuple[int, str, int]]) -> SubmitResult | None:
    """Write rows into {fmt}_deck_cards and mark the deck done.

    Returns a SubmitResult, or None if the deck row does not exist.
    All boards are preserved (board column kept).
    """
    cur.execute(
        f"SELECT id, status FROM {fmt.deck_table} WHERE moxfield_id = %s",
        (sub.moxfield_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    deck_id, status = row
    if status == "done":
        return SubmitResult(moxfield_id=sub.moxfield_id, written=0, collision=True)

    cur.execute(f"DELETE FROM {fmt.deck_cards_table} WHERE deck_id = %s", (deck_id,))

    written = 0
    if resolved:
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO {fmt.deck_cards_table} (deck_id, card_id, board, quantity)
            VALUES %s
            ON CONFLICT (deck_id, card_id, board) DO UPDATE SET
                quantity = EXCLUDED.quantity
            """,
            [(deck_id, cid, board, qty) for cid, board, qty in resolved],
        )
        written = cur.rowcount

    cur.execute(
        f"UPDATE {fmt.deck_table} SET status = 'done', cards_fetched_at = NOW() WHERE id = %s",
        (deck_id,),
    )
    return SubmitResult(moxfield_id=sub.moxfield_id, written=written)


def _submit_singleton(cur, fmt, sub: DeckSubmission,
                      resolved: list[tuple[int, str, int]]) -> SubmitResult | None:
    """Write card_ids (and commander_ids for commander) as INTEGER[] arrays.

    card_ids is the full deck multiset (quantity-expanded) over DECK_BOARDS;
    sideboard / maybeboard are excluded. commander_ids is the commander zone.
    Returns None if the deck row does not exist.
    """
    cur.execute(
        f"SELECT id, status FROM {fmt.deck_table} WHERE moxfield_id = %s",
        (sub.moxfield_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    deck_id, status = row
    if status == "done":
        return SubmitResult(moxfield_id=sub.moxfield_id, written=0, collision=True)

    card_ids: list[int] = []
    commander_ids: list[int] = []
    for cid, board, qty in resolved:
        if board in DECK_BOARDS:
            card_ids.extend([cid] * max(qty, 1))
        if fmt.has_commander_zone and board == COMMANDER_BOARD:
            commander_ids.extend([cid] * max(qty, 1))

    if fmt.has_commander_zone:
        cur.execute(
            f"""
            UPDATE {fmt.deck_table}
            SET    card_ids = %s, commander_ids = %s,
                   status = 'done', cards_fetched_at = NOW()
            WHERE  id = %s
            """,
            (card_ids, commander_ids, deck_id),
        )
    else:
        cur.execute(
            f"""
            UPDATE {fmt.deck_table}
            SET    card_ids = %s, status = 'done', cards_fetched_at = NOW()
            WHERE  id = %s
            """,
            (card_ids, deck_id),
        )
    return SubmitResult(moxfield_id=sub.moxfield_id, written=len(card_ids))


def _mark_error(cur, fmt, moxfield_id: str) -> SubmitResult | None:
    """Mark a deck 'error' (terminal) after the node exhausted its fetch retries.

    Leaves a 'done' deck alone (it succeeded elsewhere). Returns None if the deck
    row does not exist.
    """
    cur.execute(
        f"UPDATE {fmt.deck_table} SET status = 'error' "
        f"WHERE moxfield_id = %s AND status <> 'done'",
        (moxfield_id,),
    )
    if cur.rowcount == 0:
        # Either no such deck, or it was already 'done' — check which.
        cur.execute(f"SELECT 1 FROM {fmt.deck_table} WHERE moxfield_id = %s", (moxfield_id,))
        if cur.fetchone() is None:
            return None
    return SubmitResult(moxfield_id=moxfield_id, written=0, errored=True)


@app.post("/decks/submit", response_model=list[SubmitResult])
def submit_decks(_: Auth, submissions: list[DeckSubmission]) -> list[SubmitResult]:
    """Submit outcomes for a batch of fetched decks.

    A submission either carries card rows (success -> deck marked 'done') or
    error=True (the node gave up after MAX_DECK_FETCH_FAILS -> deck marked
    'error', terminal). Each deck is processed under its own savepoint so one
    failure does not abort the batch. Card names absent from v2.cards are
    skipped, logged to unknown_cards.log, and reported in `unresolved`.
    """
    if not submissions:
        return []

    invalid = {s.format for s in submissions if s.format not in FORMATS_BY_TOKEN}
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown format(s): {invalid}")

    results: list[SubmitResult] = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            for sub in submissions:
                fmt = get_format(sub.format)
                if sub.error:
                    resolved, unresolved = [], []
                else:
                    resolved, unresolved = _resolve_cards(sub.cards)
                    if unresolved:
                        log_unknown_cards(sub.format, sub.moxfield_id, unresolved)

                cur.execute("SAVEPOINT deck_submit")
                try:
                    if sub.error:
                        res = _mark_error(cur, fmt, sub.moxfield_id)
                    elif fmt.is_singleton:
                        res = _submit_singleton(cur, fmt, sub, resolved)
                    else:
                        res = _submit_regular(cur, fmt, sub, resolved)
                    cur.execute("RELEASE SAVEPOINT deck_submit")
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT deck_submit")
                    cur.execute("RELEASE SAVEPOINT deck_submit")
                    log.error("submit %s (%s) failed:\n%s",
                              sub.moxfield_id, sub.format, traceback.format_exc())
                    results.append(SubmitResult(moxfield_id=sub.moxfield_id, written=-1,
                                                unresolved=unresolved))
                    continue

                if res is None:
                    log.warning("submit %s (%s): no such deck row", sub.moxfield_id, sub.format)
                    results.append(SubmitResult(moxfield_id=sub.moxfield_id, written=-1,
                                                unresolved=unresolved))
                else:
                    res.unresolved = unresolved
                    results.append(res)
        conn.commit()

    return results


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.post("/admin/reload-cards", response_model=ReloadResult)
def reload_cards(_: Auth) -> ReloadResult:
    """Reload the in-memory card map after new cards have been seeded."""
    with get_connection() as conn:
        n = resolver.load(conn)
    log.info("reload_cards: %d cards", n)
    return ReloadResult(cards=n)
