-- V28: Per-format card_layout tables
--
-- Replaces the global card_layout table (format column as PK component) with
-- five format-scoped tables, consistent with _card_stats and _card_pair_stats.
-- store_card_layout is updated to route dynamically via EXECUTE.

CREATE TABLE IF NOT EXISTS pauper_card_layout (
    card_name      TEXT NOT NULL PRIMARY KEY,
    x              REAL NOT NULL,
    y              REAL NOT NULL,
    color_identity TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS standard_card_layout (
    card_name      TEXT NOT NULL PRIMARY KEY,
    x              REAL NOT NULL,
    y              REAL NOT NULL,
    color_identity TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS modern_card_layout (
    card_name      TEXT NOT NULL PRIMARY KEY,
    x              REAL NOT NULL,
    y              REAL NOT NULL,
    color_identity TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS vintage_card_layout (
    card_name      TEXT NOT NULL PRIMARY KEY,
    x              REAL NOT NULL,
    y              REAL NOT NULL,
    color_identity TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS legacy_card_layout (
    card_name      TEXT NOT NULL PRIMARY KEY,
    x              REAL NOT NULL,
    y              REAL NOT NULL,
    color_identity TEXT NOT NULL DEFAULT ''
);

-- Migrate any existing layout data
INSERT INTO pauper_card_layout   SELECT card_name, x, y, color_identity FROM card_layout WHERE format = 'pauper'   ON CONFLICT DO NOTHING;
INSERT INTO standard_card_layout SELECT card_name, x, y, color_identity FROM card_layout WHERE format = 'standard' ON CONFLICT DO NOTHING;
INSERT INTO modern_card_layout   SELECT card_name, x, y, color_identity FROM card_layout WHERE format = 'modern'   ON CONFLICT DO NOTHING;
INSERT INTO vintage_card_layout  SELECT card_name, x, y, color_identity FROM card_layout WHERE format = 'vintage'  ON CONFLICT DO NOTHING;
INSERT INTO legacy_card_layout   SELECT card_name, x, y, color_identity FROM card_layout WHERE format = 'legacy'   ON CONFLICT DO NOTHING;

-- Update store_card_layout to route to per-format tables
CREATE OR REPLACE PROCEDURE store_card_layout(
    p_format TEXT,
    p_layout JSONB
)
LANGUAGE plpgsql AS $$
DECLARE
    v_layout_table TEXT := p_format || '_card_layout';
BEGIN
    EXECUTE format('DELETE FROM %I', v_layout_table);

    EXECUTE format($sql$
        INSERT INTO %I (card_name, x, y, color_identity)
        SELECT elem->>'card_name',
               (elem->>'x')::real,
               (elem->>'y')::real,
               COALESCE(elem->>'color_identity', '')
        FROM   jsonb_array_elements($1) AS elem
    $sql$, v_layout_table)
    USING p_layout;
END;
$$;

DROP TABLE IF EXISTS card_layout;
