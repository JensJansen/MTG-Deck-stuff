-- V38: Per-format claim columns for API-managed card distribution.
--
-- Discovery (central) nodes no longer slice the card list by start-card.
-- Instead the API leases cards to nodes via POST /cards/claim using these
-- columns, so concurrent nodes never sweep the same card. This mirrors the
-- existing per-format deck-claiming model (status/claimed_at/claimed_by).
--
--   claimed_at_{fmt}  when the card was last leased (NULL = never).
--                     Doubles as the "last processed" marker that orders the
--                     incremental-refresh queue.
--   claimed_by_{fmt}  worker id that last leased it (informational).
--
-- No backfill is required: V12 pre-populated one card_sweep_status row per
-- card and a trigger keeps it in sync, so every legal card already has a row.
-- 'standard' is intentionally omitted (dropped in V33).

ALTER TABLE card_sweep_status
    ADD COLUMN IF NOT EXISTS claimed_at_commander          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS claimed_by_commander          TEXT,
    ADD COLUMN IF NOT EXISTS claimed_at_highlanderCanadian TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS claimed_by_highlanderCanadian TEXT,
    ADD COLUMN IF NOT EXISTS claimed_at_pauper             TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS claimed_by_pauper             TEXT,
    ADD COLUMN IF NOT EXISTS claimed_at_modern             TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS claimed_by_modern             TEXT,
    ADD COLUMN IF NOT EXISTS claimed_at_vintage            TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS claimed_by_vintage            TEXT,
    ADD COLUMN IF NOT EXISTS claimed_at_legacy             TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS claimed_by_legacy             TEXT;

-- Commander is the distributed format; index its claim ordering/filtering.
-- Other formats run single-node locally and don't need the index.
CREATE INDEX IF NOT EXISTS idx_css_claim_commander
    ON card_sweep_status (swept_commander, claimed_at_commander);
