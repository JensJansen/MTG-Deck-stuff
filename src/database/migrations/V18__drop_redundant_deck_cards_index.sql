-- V18: Drop redundant index on deck_cards(deck_id)
-- deck_id is the leading column of the composite PK (deck_id, card_id, board),
-- so this index duplicates coverage already provided by the PK index.
DROP INDEX IF EXISTS idx_deck_cards_deck_id;
