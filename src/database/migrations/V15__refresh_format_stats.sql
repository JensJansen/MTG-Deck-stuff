-- V15: refresh_format_stats — batched pair accumulation, joins decks for format
--
-- deck_cards has no format column; format is derived by joining decks.
-- Card stats JOIN decks once in the fmt_dc CTE.
-- Pair accumulation paginates through decks for the format, then filters
-- deck_cards by deck_id only (already constrained to the right format).

CREATE OR REPLACE PROCEDURE refresh_format_stats(
    p_format      TEXT,
    p_boards      TEXT[],
    p_min_cooccur INT DEFAULT 5,
    p_batch_size  INT DEFAULT 10000
)
LANGUAGE plpgsql AS $$
DECLARE
    v_offset    INT := 0;
    v_batch_ids TEXT[];
BEGIN
    -- ── 0. Clean up any staging table left by a prior killed run ─────────────
    DROP TABLE IF EXISTS _card_pair_staging;
    CREATE TABLE _card_pair_staging (
        card_id_a INT NOT NULL,
        card_id_b INT NOT NULL,
        cooccur   INT NOT NULL DEFAULT 1,
        PRIMARY KEY (card_id_a, card_id_b)
    );

    -- ── 1. Card stats — join decks for format filter ─────────────────────────
    DELETE FROM card_stats WHERE format = p_format;

    INSERT INTO card_stats
        (card_name, format, deck_count, total_decks, inclusion_rate, avg_quantity)
    WITH
    fmt_dc AS (
        SELECT   dc.deck_id, dc.card_id, SUM(dc.quantity) AS quantity
        FROM     deck_cards dc
        JOIN     decks d ON dc.deck_id = d.public_id
        WHERE    d.format  = p_format
          AND    dc.board  = ANY(p_boards)
        GROUP BY dc.deck_id, dc.card_id
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
    JOIN   cards c ON pc.card_id = c.id
    CROSS  JOIN total t;

    -- ── 2. Pair accumulation — batched; deck_ids already scoped to format ────
    DELETE FROM card_pair_stats WHERE format = p_format;

    LOOP
        SELECT array_agg(public_id) INTO v_batch_ids
        FROM (
            SELECT public_id
            FROM   decks
            WHERE  format = p_format
            ORDER  BY public_id
            LIMIT  p_batch_size OFFSET v_offset
        ) sub;

        EXIT WHEN v_batch_ids IS NULL;

        INSERT INTO _card_pair_staging (card_id_a, card_id_b, cooccur)
        WITH batch_dc AS (
            SELECT DISTINCT deck_id, card_id
            FROM   deck_cards
            WHERE  deck_id = ANY(v_batch_ids)
              AND  board   = ANY(p_boards)
        )
        SELECT a.card_id,
               b.card_id,
               COUNT(*)
        FROM   batch_dc a
        JOIN   batch_dc b ON a.deck_id = b.deck_id AND a.card_id < b.card_id
        GROUP  BY a.card_id, b.card_id
        ON CONFLICT (card_id_a, card_id_b) DO UPDATE
            SET cooccur = _card_pair_staging.cooccur + EXCLUDED.cooccur;

        v_offset := v_offset + p_batch_size;
    END LOOP;

    -- ── 3. Compute final metrics from accumulated counts ──────────────────────
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
    JOIN   cards ca    ON s.card_id_a = ca.id
    JOIN   cards cb    ON s.card_id_b = cb.id
    JOIN   cc    cc_a  ON ca.card_name = cc_a.card_name
    JOIN   cc    cc_b  ON cb.card_name = cc_b.card_name
    CROSS  JOIN  total n
    WHERE  s.cooccur >= p_min_cooccur;

    DROP TABLE IF EXISTS _card_pair_staging;
END;
$$;
