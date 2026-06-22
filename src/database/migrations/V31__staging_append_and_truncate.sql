-- V31: Append-only staging table and TRUNCATE for stats tables
--
-- Two changes to refresh_format_stats:
--
-- 1. Staging table loses its PRIMARY KEY. Each batch blindly appends its pairs
--    rather than upsert-merging into an indexed table. This eliminates the
--    per-row index lookup and dead-tuple overhead that ON CONFLICT DO UPDATE
--    incurs under MVCC. A single GROUP BY SUM at the end of the loop replaces
--    the running-aggregate approach.
--
-- 2. TRUNCATE replaces DELETE for clearing card_stats and card_pair_stats.
--    DELETE marks every row dead individually; TRUNCATE drops the storage in
--    one operation, which is orders of magnitude faster on large tables.

CREATE OR REPLACE PROCEDURE refresh_format_stats(
    p_format      TEXT,
    p_min_cooccur INT DEFAULT 5,
    p_batch_size  INT DEFAULT 1000
)
LANGUAGE plpgsql AS $$
DECLARE
    v_decks_table TEXT := p_format || '_decks';
    v_dc_table    TEXT := p_format || '_deck_cards';
    v_stats_table TEXT := p_format || '_card_stats';
    v_pair_table  TEXT := p_format || '_card_pair_stats';
    v_last_id     TEXT := '';
    v_batch_ids   TEXT[];
BEGIN
    -- ── 0. Clean up any tables left by a prior killed run ────────────────────
    DROP TABLE IF EXISTS _card_pair_staging;

    CREATE UNLOGGED TABLE _card_pair_staging (
        card_id_a BIGINT NOT NULL,
        card_id_b BIGINT NOT NULL,
        cooccur   INT    NOT NULL
    );

    -- ── 1. Card stats ────────────────────────────────────────────────────────
    EXECUTE format('TRUNCATE %I', v_stats_table);

    EXECUTE format($sql$
        INSERT INTO %I (card_name, deck_count, total_decks, inclusion_rate, avg_quantity)
        WITH
        dc AS (
            SELECT   card_id,
                     deck_id,
                     SUM(quantity) AS quantity
            FROM     %I
            GROUP BY card_id, deck_id
        ),
        total AS (
            SELECT COUNT(DISTINCT deck_id) AS n FROM dc
        ),
        per_card AS (
            SELECT card_id,
                   COUNT(*)                        AS deck_count,
                   SUM(quantity)::float / COUNT(*) AS avg_qty
            FROM   dc
            GROUP  BY card_id
        )
        SELECT c.card_name,
               pc.deck_count,
               t.n,
               pc.deck_count::float / NULLIF(t.n, 0),
               pc.avg_qty
        FROM   per_card pc
        JOIN   cards    c ON pc.card_id = c.id
        CROSS  JOIN total t
    $sql$, v_stats_table, v_dc_table);

    -- ── 2. Pair accumulation — batched by deck, append-only ─────────────────
    EXECUTE format('TRUNCATE %I', v_pair_table);

    LOOP
        EXECUTE format($sql$
            SELECT array_agg(public_id)
            FROM (
                SELECT public_id
                FROM   %I
                WHERE  public_id > $1
                ORDER  BY public_id
                LIMIT  $2
            ) sub
        $sql$, v_decks_table)
        INTO v_batch_ids
        USING v_last_id, p_batch_size;

        EXIT WHEN v_batch_ids IS NULL;

        EXECUTE format($sql$
            INSERT INTO _card_pair_staging (card_id_a, card_id_b, cooccur)
            WITH batch_dc AS (
                SELECT DISTINCT deck_id, card_id
                FROM   %I
                WHERE  deck_id = ANY($1)
            )
            SELECT a.card_id,
                   b.card_id,
                   COUNT(*)
            FROM   batch_dc a
            JOIN   batch_dc b ON a.deck_id = b.deck_id
                             AND a.card_id  < b.card_id
            GROUP  BY a.card_id, b.card_id
        $sql$, v_dc_table)
        USING v_batch_ids;

        v_last_id := v_batch_ids[array_length(v_batch_ids, 1)];
    END LOOP;

    -- ── 3. Aggregate staging rows, then compute final metrics ────────────────
    EXECUTE format($sql$
        INSERT INTO %I
            (card_a, card_b, cooccurrence_count,
             lift, pmi, jaccard, confidence_a_to_b, confidence_b_to_a)
        WITH
        aggregated AS (
            SELECT card_id_a, card_id_b, SUM(cooccur) AS cooccur
            FROM   _card_pair_staging
            GROUP  BY card_id_a, card_id_b
            HAVING SUM(cooccur) >= $1
        ),
        total AS (
            SELECT total_decks AS n FROM %I LIMIT 1
        ),
        cc AS (
            SELECT card_name, deck_count FROM %I
        )
        SELECT ca.card_name,
               cb.card_name,
               s.cooccur,
               (s.cooccur::float * n.n) / (cc_a.deck_count * cc_b.deck_count)         AS lift,
               LN((s.cooccur::float * n.n) / (cc_a.deck_count * cc_b.deck_count))     AS pmi,
               s.cooccur::float / (cc_a.deck_count + cc_b.deck_count - s.cooccur)     AS jaccard,
               s.cooccur::float / cc_a.deck_count                                     AS conf_a_to_b,
               s.cooccur::float / cc_b.deck_count                                     AS conf_b_to_a
        FROM   aggregated s
        JOIN   cards ca   ON s.card_id_a = ca.id
        JOIN   cards cb   ON s.card_id_b = cb.id
        JOIN   cc    cc_a ON ca.card_name = cc_a.card_name
        JOIN   cc    cc_b ON cb.card_name = cc_b.card_name
        CROSS  JOIN  total n
    $sql$, v_pair_table, v_stats_table, v_stats_table)
    USING p_min_cooccur;

    -- ── 4. Cleanup ───────────────────────────────────────────────────────────
    DROP TABLE IF EXISTS _card_pair_staging;
END;
$$;
