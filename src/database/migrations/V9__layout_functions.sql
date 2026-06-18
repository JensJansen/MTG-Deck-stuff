-- V9: Stored functions for precompute_layout.py data loading and write-back
--
-- Replaces three Python round-trips (load_cards, load_pairs, get_color_identities)
-- with two independent SELECT calls, and the DELETE+INSERT write-back with a
-- single CALL. The dynamic legal_{format} column is handled server-side via
-- EXECUTE format(...) rather than Python f-string interpolation.


-- ---------------------------------------------------------------------------
-- Helper: integer bitmask → comma-separated WUBRG color string
-- Mirrors the Python decode_colors() function in precompute_layout.py.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION decode_color_mask(p_mask INT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT COALESCE(
        string_agg(color, ',' ORDER BY bit_val),
        ''
    )
    FROM (VALUES ('W', 1), ('U', 2), ('B', 4), ('R', 8), ('G', 16)) AS t(color, bit_val)
    WHERE (p_mask & bit_val) > 0
$$;


-- ---------------------------------------------------------------------------
-- get_layout_cards: eligible cards for UMAP layout with color identities
--
-- Replaces load_cards() + get_color_identities() — one round-trip instead of
-- two, and the dynamic legal_{format} column stays server-side.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_layout_cards(
    p_format    TEXT,
    p_min_decks INT
) RETURNS TABLE (card_name TEXT, deck_count INT, color_identity TEXT)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY EXECUTE format(
        'SELECT cs.card_name,
                cs.deck_count::int,
                decode_color_mask(c.ci_mask)
         FROM   card_stats cs
         JOIN   cards c ON cs.card_name = c.card_name
         WHERE  cs.format      = %L
           AND  cs.deck_count >= %s
           AND  c.legal_%I     = %L',
        p_format,
        p_min_decks,
        p_format,
        'legal'
    );
END;
$$;


-- ---------------------------------------------------------------------------
-- get_layout_pairs: qualifying card pairs for UMAP edge weights
--
-- Replaces load_pairs() — the eligible card filter is expressed as JOINs so
-- Python never needs to pass the card list back to Postgres.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_layout_pairs(
    p_format      TEXT,
    p_min_decks   INT,
    p_min_cooccur INT
) RETURNS TABLE (card_a TEXT, card_b TEXT, jaccard REAL)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY EXECUTE format(
        'SELECT ps.card_a,
                ps.card_b,
                ps.jaccard::real
         FROM   card_pair_stats ps
         JOIN   card_stats cs_a ON ps.card_a = cs_a.card_name AND cs_a.format = ps.format
         JOIN   card_stats cs_b ON ps.card_b = cs_b.card_name AND cs_b.format = ps.format
         JOIN   cards c_a ON ps.card_a = c_a.card_name
         JOIN   cards c_b ON ps.card_b = c_b.card_name
         WHERE  ps.format                = %L
           AND  ps.cooccurrence_count   >= %s
           AND  cs_a.deck_count         >= %s
           AND  cs_b.deck_count         >= %s
           AND  c_a.legal_%I            = %L
           AND  c_b.legal_%I            = %L',
        p_format,
        p_min_cooccur,
        p_min_decks,
        p_min_decks,
        p_format, 'legal',
        p_format, 'legal'
    );
END;
$$;


-- ---------------------------------------------------------------------------
-- store_card_layout: atomic write-back of UMAP results
--
-- Replaces the DELETE + execute_values INSERT pair in run().
-- Accepts the full layout as a JSONB array of {card_name, x, y, color_identity}.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE store_card_layout(
    p_format TEXT,
    p_layout JSONB   -- [{card_name: str, x: float, y: float, color_identity: str}, ...]
)
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM card_layout WHERE format = p_format;

    INSERT INTO card_layout (card_name, format, x, y, color_identity)
    SELECT elem->>'card_name',
           p_format,
           (elem->>'x')::real,
           (elem->>'y')::real,
           COALESCE(elem->>'color_identity', '')
    FROM   jsonb_array_elements(p_layout) AS elem;
END;
$$;
