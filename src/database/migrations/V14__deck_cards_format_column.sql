-- V13: Add format column to deck_cards for direct format filtering
--
-- Eliminates the JOIN to decks required to filter by format in
-- refresh_format_stats. Backfilling existing rows is handled manually
-- outside of this migration to avoid a long-running lock on the full table.

ALTER TABLE deck_cards ADD COLUMN IF NOT EXISTS format TEXT;

CREATE INDEX IF NOT EXISTS idx_deck_cards_format_board_card
    ON deck_cards (format, board, card_id);
