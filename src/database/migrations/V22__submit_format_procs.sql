-- V22: Per-format card submission stored procedures
--
-- Replaces the global submit_deck_cards and submit_singleton_deck procs with
-- table-parameterised equivalents that write to per-format tables introduced
-- in V20. Table names are passed at call time; EXECUTE handles dynamic routing.
--
-- submit_format_deck_cards   — regular formats; writes rows to {format}_deck_cards
-- submit_format_singleton_deck — singleton formats; writes JSONB to {format}_decks.cards


CREATE OR REPLACE PROCEDURE submit_format_deck_cards(
    p_deck_table   TEXT,
    p_cards_table  TEXT,
    p_deck_id      TEXT,
    p_worker_id    TEXT,
    p_cards        JSONB,
    INOUT p_rows_written INT  DEFAULT 0,
    INOUT p_collision    BOOL DEFAULT FALSE
)
LANGUAGE plpgsql AS $$
DECLARE
    v_status TEXT;
BEGIN
    EXECUTE format('SELECT status FROM %I WHERE public_id = $1', p_deck_table)
    INTO v_status
    USING p_deck_id;

    IF v_status = 'done' THEN
        p_collision := TRUE;
        RETURN;
    END IF;

    EXECUTE format('DELETE FROM %I WHERE deck_id = $1', p_cards_table)
    USING p_deck_id;

    EXECUTE format($sql$
        INSERT INTO %I (deck_id, card_id, board, quantity)
        SELECT $1,
               c.id,
               elem->>'board',
               (elem->>'quantity')::integer
        FROM   jsonb_array_elements($2) AS elem
        JOIN   cards c ON c.card_name = elem->>'card_name'
    $sql$, p_cards_table)
    USING p_deck_id, p_cards;

    GET DIAGNOSTICS p_rows_written = ROW_COUNT;

    EXECUTE format($sql$
        UPDATE %I
        SET    status           = 'done',
               cards_fetched_at = NOW()::text
        WHERE  public_id = $1
    $sql$, p_deck_table)
    USING p_deck_id;

    p_collision := FALSE;
END;
$$;


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
    v_status TEXT;
BEGIN
    EXECUTE format('SELECT status FROM %I WHERE public_id = $1', p_deck_table)
    INTO v_status
    USING p_deck_id;

    IF v_status = 'done' THEN
        p_collision := TRUE;
        RETURN;
    END IF;

    EXECUTE format($sql$
        UPDATE %I
        SET    cards            = $1,
               status           = 'done',
               cards_fetched_at = NOW()::text
        WHERE  public_id = $2
    $sql$, p_deck_table)
    USING p_cards, p_deck_id;

    p_rows_written := jsonb_array_length(p_cards);
    p_collision    := FALSE;
END;
$$;
