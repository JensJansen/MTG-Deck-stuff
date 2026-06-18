-- V14: Write format into deck_cards via replace_deck_cards
--
-- Derives format server-side from the decks table using p_deck_id so no
-- callers need to change their signatures.

CREATE OR REPLACE PROCEDURE replace_deck_cards(
    p_deck_id TEXT,
    p_cards   JSONB
)
LANGUAGE plpgsql AS $$
DECLARE
    v_format TEXT;
BEGIN
    SELECT format INTO v_format FROM decks WHERE public_id = p_deck_id;

    DELETE FROM deck_cards WHERE deck_id = p_deck_id;

    INSERT INTO deck_cards (deck_id, card_id, board, quantity, format)
    SELECT p_deck_id,
           c.id,
           elem->>'board',
           (elem->>'quantity')::integer,
           v_format
    FROM   jsonb_array_elements(p_cards) AS elem
    JOIN   cards c ON c.card_name = elem->>'card_name';

    UPDATE decks
    SET    cards_fetched_at = NOW()::text,
           status           = 'done'
    WHERE  public_id = p_deck_id;
END;
$$;
