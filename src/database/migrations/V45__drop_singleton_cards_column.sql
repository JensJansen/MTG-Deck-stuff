-- V45: Drop the JSONB cards column from singleton format deck tables.
--
-- card_ids (BIGINT[]) and commander_card_ids (BIGINT[]) were added in V41 and
-- populated by the submit_format_singleton_deck procedure (V42). Stats
-- computation now reads exclusively from card_ids via refresh_stats.py (V43).
--
-- The GIN indexes on cards were already removed in V40.
-- Run this only after confirming all decks have been fully re-processed
-- (i.e. card_ids IS NOT NULL for every row that previously had cards IS NOT NULL).

ALTER TABLE commander_decks
    DROP COLUMN IF EXISTS cards;

ALTER TABLE canadian_highlander_decks
    DROP COLUMN IF EXISTS cards;
