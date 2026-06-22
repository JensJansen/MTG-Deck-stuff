-- V23: Data migration procedure — legacy tables → per-format tables
--
-- Defines run_data_migration(p_batch_size) which moves rows from the old
-- global tables into the per-format tables introduced in V20.
--
-- Call repeatedly until all NOTICE lines report 0 rows:
--     CALL run_data_migration(10000);
--
-- Migration order per call:
--   1. singleton_decks        → commander_decks          (all rows are commander)
--   2. decks                  → {format}_decks           (per format, batched)
--   3. deck_cards             → {format}_deck_cards      (only for decks already
--                                                          migrated to target table,
--                                                          preserving FK integrity)
--
-- All inserts use ON CONFLICT DO NOTHING so the procedure is safe to re-run.
-- deck_cards rows are only moved after their parent deck exists in the target
-- table, so FK constraints are never violated.

CREATE OR REPLACE PROCEDURE run_data_migration(
    p_batch_size INT DEFAULT 10000
)
LANGUAGE plpgsql AS $$
DECLARE
    v_moved     INT := 0;
    v_total     INT := 0;
    v_fmt       TEXT;
    v_decks_tbl TEXT;
    v_dc_tbl    TEXT;
BEGIN
    -- ── 1. singleton_decks → commander_decks ─────────────────────────────────
    INSERT INTO commander_decks (
        public_id, name, author, color_mask,
        created_at_utc, updated_at_utc, scraped_at, cards_fetched_at,
        cards, status, claimed_at, claimed_by
    )
    SELECT s.public_id, s.name, s.author, s.color_mask,
           s.created_at_utc, s.updated_at_utc, s.scraped_at, s.cards_fetched_at,
           s.cards, s.status, s.claimed_at, s.claimed_by
    FROM   singleton_decks s
    LEFT   JOIN commander_decks c ON s.public_id = c.public_id
    WHERE  c.public_id IS NULL
    LIMIT  p_batch_size
    ON CONFLICT DO NOTHING;

    GET DIAGNOSTICS v_moved = ROW_COUNT;
    v_total := v_total + v_moved;
    RAISE NOTICE 'singleton_decks → commander_decks: % rows', v_moved;

    -- ── 2. decks → per-format deck tables ────────────────────────────────────
    FOREACH v_fmt IN ARRAY ARRAY['pauper', 'standard', 'modern', 'vintage', 'legacy'] LOOP
        v_decks_tbl := v_fmt || '_decks';

        EXECUTE format($sql$
            INSERT INTO %I (
                public_id, name, author, color_mask,
                created_at_utc, updated_at_utc, scraped_at, cards_fetched_at,
                status, claimed_at, claimed_by
            )
            SELECT d.public_id, d.name, d.author, d.color_mask,
                   d.created_at_utc, d.updated_at_utc, d.scraped_at, d.cards_fetched_at,
                   d.status, d.claimed_at, d.claimed_by
            FROM   decks d
            LEFT   JOIN %I t ON d.public_id = t.public_id
            WHERE  d.format    = $1
              AND  t.public_id IS NULL
            LIMIT  $2
            ON CONFLICT DO NOTHING
        $sql$, v_decks_tbl, v_decks_tbl)
        USING v_fmt, p_batch_size;

        GET DIAGNOSTICS v_moved = ROW_COUNT;
        v_total := v_total + v_moved;
        RAISE NOTICE 'decks[%] → %: % rows', v_fmt, v_decks_tbl, v_moved;
    END LOOP;

    -- ── 3. deck_cards → per-format deck_cards tables ─────────────────────────
    -- Joins against the target deck table (not the source) so deck_cards are
    -- only migrated after their parent deck is already in the target, keeping
    -- FK constraints satisfied throughout.
    FOREACH v_fmt IN ARRAY ARRAY['pauper', 'standard', 'modern', 'vintage', 'legacy'] LOOP
        v_decks_tbl := v_fmt || '_decks';
        v_dc_tbl    := v_fmt || '_deck_cards';

        EXECUTE format($sql$
            INSERT INTO %I (deck_id, card_id, board, quantity)
            SELECT dc.deck_id, dc.card_id, dc.board, dc.quantity
            FROM   deck_cards  dc
            JOIN   %I          fd ON dc.deck_id = fd.public_id
            LEFT   JOIN %I     t  ON dc.deck_id = t.deck_id
                               AND  dc.card_id  = t.card_id
                               AND  dc.board    = t.board
            WHERE  t.deck_id IS NULL
            LIMIT  $1
            ON CONFLICT DO NOTHING
        $sql$, v_dc_tbl, v_decks_tbl, v_dc_tbl)
        USING p_batch_size;

        GET DIAGNOSTICS v_moved = ROW_COUNT;
        v_total := v_total + v_moved;
        RAISE NOTICE 'deck_cards[%] → %: % rows', v_fmt, v_dc_tbl, v_moved;
    END LOOP;

    RAISE NOTICE 'Total rows moved this call: %', v_total;
END;
$$;
