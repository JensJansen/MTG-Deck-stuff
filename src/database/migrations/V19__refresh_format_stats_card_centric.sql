-- V19: refresh_format_stats — card-centric pair accumulation
--
-- Replaces the deck-centric batching approach from V15 with a card-centric
-- loop that uses the idx_deck_cards_card_id index for each anchor card.
-- This avoids the large self-join intermediate result set produced by the
-- prior approach and eliminates the need for ON CONFLICT accumulation, since
-- each (card_id_a, card_id_b) pair is produced exactly once.
--
-- p_batch_size is retained for interface compatibility but is no longer used.

CREATE OR REPLACE PROCEDURE refresh_format_stats(
    p_format      TEXT,
    p_boards      TEXT[],
    p_min_cooccur INT DEFAULT 5,
    p_batch_size  INT DEFAULT 10000
)
LANGUAGE plpgsql AS $$
DECLARE
    v_card_id INT;
BEGIN
    -- ── 0. Clean up any tables left by a prior killed run ────────────────────
    DROP TABLE IF EXISTS _format_deck_ids;
    DROP TABLE IF EXISTS _card_pair_staging;

    -- ── 1. Materialize format deck_ids once, indexed for fast joining ────────
    CREATE TABLE _format_deck_ids (
        deck_id TEXT NOT NULL PRIMARY KEY
    );

    INSERT INTO _format_deck_ids (deck_id)
    SELECT public_id
    FROM   decks
    WHERE  format = p_format;

    -- ── 2. Card stats ────────────────────────────────────────────────────────
    DELETE FROM card_stats WHERE format = p_format;

    INSERT INTO card_stats
        (card_name, format, deck_count, total_decks, inclusion_rate, avg_quantity)
    WITH
    fmt_dc AS (
        SELECT   dc.card_id,
                 dc.deck_id,
                 SUM(dc.quantity) AS quantity
        FROM     deck_cards dc
        JOIN     _format_deck_ids fd ON dc.deck_id = fd.deck_id
        WHERE    dc.board = ANY(p_boards)
        GROUP BY dc.card_id, dc.deck_id
    ),
    total AS (
        SELECT COUNT(DISTINCT deck_id) AS n FROM fmt_dc
    ),
    per_card AS (
        SELECT card_id,
               COUNT(*)                        AS deck_count,
               SUM(quantity)::float / COUNT(*) AS avg_qty
        FROM   fmt_dc
        GROUP  BY card_id
    )
    SELECT c.card_name,
           p_format,
           pc.deck_count,
           t.n,
           pc.deck_count::float / NULLIF(t.n, 0),
           pc.avg_qty
    FROM   per_card pc
    JOIN   cards    c  ON pc.card_id = c.id
    CROSS  JOIN total  t;

    -- ── 3. Pair accumulation — card-centric, one anchor card per iteration ───
    DELETE FROM card_pair_stats WHERE format = p_format;

    CREATE TABLE _card_pair_staging (
        card_id_a INT NOT NULL,
        card_id_b INT NOT NULL,
        cooccur   INT NOT NULL
    );

    FOR v_card_id IN (
        SELECT DISTINCT dc.card_id
        FROM   deck_cards dc
        JOIN   _format_deck_ids fd ON dc.deck_id = fd.deck_id
        WHERE  dc.board = ANY(p_boards)
        ORDER  BY dc.card_id
    ) LOOP
        -- Index seek on card_id for dc1; card_id_b > anchor guarantees each
        -- pair is produced exactly once across the full loop, so no ON CONFLICT.
        INSERT INTO _card_pair_staging (card_id_a, card_id_b, cooccur)
        SELECT v_card_id,
               dc2.card_id,
               COUNT(*)
        FROM   deck_cards dc1
        JOIN   _format_deck_ids fd ON dc1.deck_id = fd.deck_id
        JOIN   deck_cards dc2      ON dc2.deck_id  = dc1.deck_id
                                  AND dc2.card_id  > v_card_id
                                  AND dc2.board    = ANY(p_boards)
        WHERE  dc1.card_id = v_card_id
          AND  dc1.board   = ANY(p_boards)
        GROUP  BY dc2.card_id;
    END LOOP;

    -- ── 4. Compute final metrics from accumulated counts ─────────────────────
    INSERT INTO card_pair_stats
        (card_a, card_b, format, cooccurrence_count,
         lift, pmi, jaccard, confidence_a_to_b, confidence_b_to_a)
    WITH
    total AS (
        SELECT total_decks AS n
        FROM   card_stats
        WHERE  format = p_format
        LIMIT  1
    ),
    cc AS (
        SELECT card_name, deck_count
        FROM   card_stats
        WHERE  format = p_format
    )
    SELECT ca.card_name,
           cb.card_name,
           p_format,
           s.cooccur,
           (s.cooccur::float * n.n) / (cc_a.deck_count * cc_b.deck_count)         AS lift,
           LN((s.cooccur::float * n.n) / (cc_a.deck_count * cc_b.deck_count))     AS pmi,
           s.cooccur::float / (cc_a.deck_count + cc_b.deck_count - s.cooccur)     AS jaccard,
           s.cooccur::float / cc_a.deck_count                                     AS conf_a_to_b,
           s.cooccur::float / cc_b.deck_count                                     AS conf_b_to_a
    FROM   _card_pair_staging s
    JOIN   cards ca   ON s.card_id_a = ca.id
    JOIN   cards cb   ON s.card_id_b = cb.id
    JOIN   cc    cc_a ON ca.card_name = cc_a.card_name
    JOIN   cc    cc_b ON cb.card_name = cc_b.card_name
    CROSS  JOIN  total n
    WHERE  s.cooccur >= p_min_cooccur;

    -- ── 5. Cleanup ───────────────────────────────────────────────────────────
    DROP TABLE IF EXISTS _card_pair_staging;
    DROP TABLE IF EXISTS _format_deck_ids;
END;
$$;
