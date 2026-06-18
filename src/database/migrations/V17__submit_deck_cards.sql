-- V17: Stored procedures for deck card submission
--
-- submit_deck_cards   — for regular (non-singleton) formats; writes to deck_cards
-- submit_singleton_deck — for singleton formats; writes JSONB to singleton_decks.cards
--
-- Both return rows_written and collision flag via INOUT parameters.

CREATE OR REPLACE PROCEDURE submit_deck_cards(
    p_deck_id      TEXT,
    p_worker_id    TEXT,
    p_cards        JSONB,
    INOUT p_rows_written INT  DEFAULT 0,
    INOUT p_collision    BOOL DEFAULT FALSE
)
LANGUAGE plpgsql AS $$
DECLARE
    v_status TEXT;
    v_format TEXT;
BEGIN
    SELECT status, format INTO v_status, v_format
    FROM   decks
    WHERE  public_id = p_deck_id;

    IF v_status = 'done' THEN
        p_collision := TRUE;
        RETURN;
    END IF;

    DELETE FROM deck_cards WHERE deck_id = p_deck_id;

    INSERT INTO deck_cards (deck_id, card_id, board, quantity, format)
    SELECT p_deck_id,
           c.id,
           elem->>'board',
           (elem->>'quantity')::integer,
           v_format
    FROM   jsonb_array_elements(p_cards) AS elem
    JOIN   cards c ON c.card_name = elem->>'card_name';

    GET DIAGNOSTICS p_rows_written = ROW_COUNT;

    UPDATE decks
    SET    status           = 'done',
           cards_fetched_at = NOW()::text
    WHERE  public_id = p_deck_id;

    p_collision := FALSE;
END;
$$;


CREATE OR REPLACE PROCEDURE submit_singleton_deck(
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
    SELECT status INTO v_status
    FROM   singleton_decks
    WHERE  public_id = p_deck_id;

    IF v_status = 'done' THEN
        p_collision := TRUE;
        RETURN;
    END IF;

    UPDATE singleton_decks
    SET    cards            = p_cards,
           status           = 'done',
           cards_fetched_at = NOW()::text
    WHERE  public_id = p_deck_id;

    p_rows_written := jsonb_array_length(p_cards);
    p_collision    := FALSE;
END;
$$;
