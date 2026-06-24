-- V42: Update submit_format_singleton_deck to write card_ids instead of cards JSONB.
--
-- Card names from the incoming JSONB are resolved to integer IDs via a JOIN against
-- the cards table at write time. Unresolved names are silently dropped; p_rows_written
-- reflects the actual resolved count so callers can detect partial resolution in logs.
--
-- commander_decks additionally populates commander_card_ids (cards on the 'commanders'
-- board), supporting partner commanders. canadian_highlander_decks has no such column.

CREATE OR REPLACE PROCEDURE submit_format_singleton_deck(
    p_deck_table  TEXT,
    p_deck_id     TEXT,
    p_worker_id   TEXT,
    p_cards       JSONB,
    INOUT p_rows_written INT  DEFAULT 0,
    INOUT p_collision    BOOL DEFAULT FALSE
)
LANGUAGE plpgsql AS $$
DECLARE
    v_status             TEXT;
    v_card_ids           BIGINT[];
    v_commander_card_ids BIGINT[];
BEGIN
    EXECUTE format('SELECT status FROM %I WHERE public_id = $1', p_deck_table)
    INTO v_status
    USING p_deck_id;

    IF v_status = 'done' THEN
        p_collision := TRUE;
        RETURN;
    END IF;

    SELECT ARRAY(
        SELECT c.id
        FROM   jsonb_array_elements(p_cards) AS elem
        JOIN   cards c ON c.card_name = elem->>'card_name'
    ) INTO v_card_ids;

    IF p_deck_table = 'commander_decks' THEN
        SELECT ARRAY(
            SELECT c.id
            FROM   jsonb_array_elements(p_cards) AS elem
            JOIN   cards c ON c.card_name = elem->>'card_name'
            WHERE  elem->>'board' = 'commanders'
        ) INTO v_commander_card_ids;

        EXECUTE format($sql$
            UPDATE %I
            SET    card_ids           = $1,
                   commander_card_ids = $2,
                   status             = 'done',
                   cards_fetched_at   = NOW()::text
            WHERE  public_id = $3
        $sql$, p_deck_table)
        USING v_card_ids, v_commander_card_ids, p_deck_id;
    ELSE
        EXECUTE format($sql$
            UPDATE %I
            SET    card_ids         = $1,
                   status           = 'done',
                   cards_fetched_at = NOW()::text
            WHERE  public_id = $2
        $sql$, p_deck_table)
        USING v_card_ids, p_deck_id;
    END IF;

    p_rows_written := cardinality(v_card_ids);
    p_collision    := FALSE;
END;
$$;
