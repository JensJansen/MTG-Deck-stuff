-- V7: Composite index on deck_cards + stored procedure for stats refresh
--
-- The composite index covers the format-filtered deck_cards scan used by
-- refresh_format_stats, avoiding per-row heap lookups for board/card_id/quantity.
--
-- refresh_format_stats replaces the Python-orchestrated multi-round-trip flow
-- (fetch card stats → Python → write back, fetch pair stats → Python → write back)
-- with a single Postgres call that keeps all intermediate data server-side.

-- ---------------------------------------------------------------------------
-- Better index for the board-filtered deck lookup used in refresh_format_stats
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_deck_cards_deck_board_card
    ON deck_cards (deck_id, board, card_id)
    INCLUDE (quantity);


-- ---------------------------------------------------------------------------
-- Stored procedure: recompute card_stats + card_pair_stats for one format
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE refresh_format_stats(
    p_format      TEXT,
    p_boards      TEXT[],
    p_min_cooccur INT DEFAULT 5
)
LANGUAGE plpgsql AS $$
BEGIN
    -- ── 1. Materialise this format's deck_cards into a temp table ─────────────
    --
    -- GROUP BY (deck_id, card_id) collapses any cross-board duplicates and
    -- guarantees a unique (deck_id, card_id) key, so later COUNT(*) in the
    -- self-join is equivalent to COUNT(DISTINCT deck_id).
    CREATE TEMP TABLE _fmt_dc ON COMMIT DROP AS
    SELECT   dc.deck_id,
             dc.card_id,
             SUM(dc.quantity) AS quantity
    FROM     deck_cards dc
    JOIN     decks d ON dc.deck_id = d.public_id
    WHERE    d.format  = p_format
      AND    dc.board  = ANY(p_boards)
    GROUP BY dc.deck_id, dc.card_id;

    -- Composite index mirrors the access pattern of the pair self-join.
    CREATE INDEX ON _fmt_dc (deck_id, card_id);
    CREATE INDEX ON _fmt_dc (card_id);
    ANALYZE  _fmt_dc;

    -- ── 2. Clear existing stats for this format ────────────────────────────────
    DELETE FROM card_stats      WHERE format = p_format;
    DELETE FROM card_pair_stats WHERE format = p_format;

    -- ── 3. Card stats — no round-trip; INSERT … SELECT entirely within Postgres ─
    INSERT INTO card_stats
        (card_name, format, deck_count, total_decks, inclusion_rate, avg_quantity)
    WITH
    total AS (
        SELECT COUNT(DISTINCT deck_id) AS n FROM _fmt_dc
    ),
    per_card AS (
        -- (deck_id, card_id) is unique in _fmt_dc, so COUNT(*) = COUNT(DISTINCT deck_id)
        SELECT card_id,
               COUNT(*)                       AS deck_count,
               SUM(quantity)::float / COUNT(*) AS avg_qty
        FROM   _fmt_dc
        GROUP  BY card_id
    )
    SELECT c.card_name,
           p_format,
           pc.deck_count,
           t.n,
           pc.deck_count::float / NULLIF(t.n, 0),
           pc.avg_qty
    FROM   per_card pc
    JOIN   cards c ON pc.card_id = c.id
    CROSS  JOIN total t;

    -- ── 4. Pair stats — self-join on the indexed temp table only ──────────────
    INSERT INTO card_pair_stats
        (card_a, card_b, format, cooccurrence_count,
         lift, pmi, jaccard, confidence_a_to_b, confidence_b_to_a)
    WITH
    total AS (
        SELECT COUNT(DISTINCT deck_id) AS n FROM _fmt_dc
    ),
    card_counts AS (
        SELECT card_id, COUNT(*) AS cnt FROM _fmt_dc GROUP BY card_id
    ),
    pairs AS (
        -- Unique (deck_id, card_id) in _fmt_dc → each matching row = one deck
        SELECT   a.card_id AS id_a,
                 b.card_id AS id_b,
                 COUNT(*)  AS cooccur
        FROM     _fmt_dc a
        JOIN     _fmt_dc b ON a.deck_id = b.deck_id AND a.card_id < b.card_id
        GROUP BY a.card_id, b.card_id
        HAVING   COUNT(*) >= p_min_cooccur
    )
    SELECT ca.card_name,
           cb.card_name,
           p_format,
           p.cooccur,
           (p.cooccur::float * n.n) / (cc_a.cnt * cc_b.cnt)          AS lift,
           LN((p.cooccur::float * n.n) / (cc_a.cnt * cc_b.cnt))      AS pmi,
           p.cooccur::float / (cc_a.cnt + cc_b.cnt - p.cooccur)      AS jaccard,
           p.cooccur::float / cc_a.cnt                                AS conf_a_to_b,
           p.cooccur::float / cc_b.cnt                                AS conf_b_to_a
    FROM   pairs p
    JOIN   cards       ca    ON p.id_a = ca.id
    JOIN   cards       cb    ON p.id_b = cb.id
    JOIN   card_counts cc_a  ON p.id_a = cc_a.card_id
    JOIN   card_counts cc_b  ON p.id_b = cc_b.card_id
    CROSS  JOIN total n;

    DROP TABLE _fmt_dc;
END;
$$;
