-- V33: Remove standard format entirely
--
-- Standard rotates too frequently to be useful for long-term analysis.
-- Drops all standard-specific tables and removes legal_standard from cards.

DROP TABLE IF EXISTS standard_card_layout;
DROP TABLE IF EXISTS standard_card_pair_stats;
DROP TABLE IF EXISTS standard_card_stats;
DROP TABLE IF EXISTS standard_deck_cards;
DROP TABLE IF EXISTS standard_decks;

ALTER TABLE cards DROP COLUMN IF EXISTS legal_standard;
DROP INDEX IF EXISTS idx_cards_legal_standard;
