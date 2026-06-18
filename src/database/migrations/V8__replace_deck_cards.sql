-- V8: Stored procedure for atomic deck card replacement
--
-- Replaces the four-round-trip Python flow (name→id lookup, DELETE, INSERT,
-- UPDATE status) with a single CALL. Card name→id resolution happens via a
-- server-side JOIN on the JSONB input — no intermediate data leaves Postgres.
--
-- Empty deck_card_rows are guarded in Python before the CALL; this procedure
-- assumes p_cards is non-empty and always marks the deck done.

CREATE OR REPLACE PROCEDURE replace_deck_cards(
    p_deck_id TEXT,
    p_cards   JSONB    -- [{card_name: str, board: str, quantity: int}, ...]
)
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM deck_cards
    WHERE  deck_id = p_deck_id;

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
