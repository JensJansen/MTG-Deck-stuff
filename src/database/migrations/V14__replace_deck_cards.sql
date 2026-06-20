-- V14: replace_deck_cards without format column
--
-- Derives status update server-side from p_deck_id.
-- format is not stored on deck_cards; callers do not need to supply it.

CREATE OR REPLACE PROCEDURE replace_deck_cards(
    p_deck_id TEXT,
    p_cards   JSONB
)
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM deck_cards WHERE deck_id = p_deck_id;

    INSERT INTO deck_cards (deck_id, card_id, board, quantity)
    SELECT p_deck_id,
           c.id,
           elem->>'board',
           (elem->>'quantity')::integer
    FROM   jsonb_array_elements(p_cards) AS elem
    JOIN   cards c ON c.card_name = elem->>'card_name';

    UPDATE decks
    SET    cards_fetched_at = NOW()::text,
           status           = 'done'
    WHERE  public_id = p_deck_id;
END;
$$;
