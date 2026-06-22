-- V27: Update get_layout_cards and get_layout_pairs to use per-format tables
--
-- V9 defined these functions against the global card_stats / card_pair_stats
-- tables, which V21 stopped writing to. They now target the per-format tables
-- introduced in V20 ({format}_card_stats, {format}_card_pair_stats).
-- The format filter clauses are removed since the table is already format-scoped.

CREATE OR REPLACE FUNCTION get_layout_cards(
    p_format    TEXT,
    p_min_decks INT
) RETURNS TABLE (card_name TEXT, deck_count INT, color_identity TEXT)
LANGUAGE plpgsql AS $$
DECLARE
    v_stats_table TEXT := p_format || '_card_stats';
BEGIN
    RETURN QUERY EXECUTE format(
        'SELECT cs.card_name,
                cs.deck_count::int,
                decode_color_mask(c.ci_mask)
         FROM   %I cs
         JOIN   cards c ON cs.card_name = c.card_name
         WHERE  cs.deck_count >= %s
           AND  c.legal_%I     = %L',
        v_stats_table,
        p_min_decks,
        p_format,
        'legal'
    );
END;
$$;


CREATE OR REPLACE FUNCTION get_layout_pairs(
    p_format      TEXT,
    p_min_decks   INT,
    p_min_cooccur INT
) RETURNS TABLE (card_a TEXT, card_b TEXT, jaccard REAL)
LANGUAGE plpgsql AS $$
DECLARE
    v_stats_table TEXT := p_format || '_card_stats';
    v_pair_table  TEXT := p_format || '_card_pair_stats';
BEGIN
    RETURN QUERY EXECUTE format(
        'SELECT ps.card_a,
                ps.card_b,
                ps.jaccard::real
         FROM   %I ps
         JOIN   %I cs_a ON ps.card_a = cs_a.card_name
         JOIN   %I cs_b ON ps.card_b = cs_b.card_name
         JOIN   cards c_a ON ps.card_a = c_a.card_name
         JOIN   cards c_b ON ps.card_b = c_b.card_name
         WHERE  ps.cooccurrence_count   >= %s
           AND  cs_a.deck_count         >= %s
           AND  cs_b.deck_count         >= %s
           AND  c_a.legal_%I            = %L
           AND  c_b.legal_%I            = %L',
        v_pair_table,
        v_stats_table,
        v_stats_table,
        p_min_cooccur,
        p_min_decks,
        p_min_decks,
        p_format, 'legal',
        p_format, 'legal'
    );
END;
$$;
