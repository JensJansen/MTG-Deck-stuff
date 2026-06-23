-- V35: Layout support for singleton formats (commander, canadian_highlander)
--
-- 1. Creates _card_layout tables for both singleton formats.
-- 2. Updates get_layout_cards and get_layout_pairs to skip the legal_{format}
--    filter when that column does not exist on the cards table (e.g.
--    canadian_highlander has no Scryfall legality column — absence implies legal).

CREATE TABLE IF NOT EXISTS commander_card_layout (
    card_name      TEXT NOT NULL PRIMARY KEY,
    x              REAL NOT NULL,
    y              REAL NOT NULL,
    color_identity TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS canadian_highlander_card_layout (
    card_name      TEXT NOT NULL PRIMARY KEY,
    x              REAL NOT NULL,
    y              REAL NOT NULL,
    color_identity TEXT NOT NULL DEFAULT ''
);


CREATE OR REPLACE FUNCTION get_layout_cards(
    p_format    TEXT,
    p_min_decks INT
) RETURNS TABLE (card_name TEXT, deck_count INT, color_identity TEXT)
LANGUAGE plpgsql AS $$
DECLARE
    v_stats_table   TEXT    := p_format || '_card_stats';
    v_has_legal_col BOOLEAN;
    v_sql           TEXT;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_name  = 'cards'
        AND    column_name = 'legal_' || p_format
    ) INTO v_has_legal_col;

    v_sql := format(
        'SELECT cs.card_name,
                cs.deck_count::int,
                decode_color_mask(c.ci_mask)
         FROM   %I cs
         JOIN   cards c ON cs.card_name = c.card_name
         WHERE  cs.deck_count >= %s',
        v_stats_table,
        p_min_decks
    );

    IF v_has_legal_col THEN
        v_sql := v_sql || format(' AND c.legal_%I = %L', p_format, 'legal');
    END IF;

    RETURN QUERY EXECUTE v_sql;
END;
$$;


CREATE OR REPLACE FUNCTION get_layout_pairs(
    p_format      TEXT,
    p_min_decks   INT,
    p_min_cooccur INT
) RETURNS TABLE (card_a TEXT, card_b TEXT, jaccard REAL)
LANGUAGE plpgsql AS $$
DECLARE
    v_stats_table   TEXT    := p_format || '_card_stats';
    v_pair_table    TEXT    := p_format || '_card_pair_stats';
    v_has_legal_col BOOLEAN;
    v_sql           TEXT;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_name  = 'cards'
        AND    column_name = 'legal_' || p_format
    ) INTO v_has_legal_col;

    v_sql := format(
        'SELECT ps.card_a,
                ps.card_b,
                ps.jaccard::real
         FROM   %I ps
         JOIN   %I cs_a ON ps.card_a = cs_a.card_name
         JOIN   %I cs_b ON ps.card_b = cs_b.card_name
         WHERE  ps.cooccurrence_count >= %s
           AND  cs_a.deck_count       >= %s
           AND  cs_b.deck_count       >= %s',
        v_pair_table,
        v_stats_table,
        v_stats_table,
        p_min_cooccur,
        p_min_decks,
        p_min_decks
    );

    IF v_has_legal_col THEN
        v_sql := v_sql || format(
            ' AND EXISTS (SELECT 1 FROM cards c WHERE c.card_name = ps.card_a AND c.legal_%I = %L)'
            ' AND EXISTS (SELECT 1 FROM cards c WHERE c.card_name = ps.card_b AND c.legal_%I = %L)',
            p_format, 'legal',
            p_format, 'legal'
        );
    END IF;

    RETURN QUERY EXECUTE v_sql;
END;
$$;
