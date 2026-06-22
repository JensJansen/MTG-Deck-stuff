-- V26: Drop legacy stored procedures
--
-- replace_deck_cards    — wrote to dropped deck_cards/decks tables
-- submit_deck_cards     — replaced by submit_format_deck_cards (V22)
-- submit_singleton_deck — replaced by submit_format_singleton_deck (V22)
-- run_data_migration    — one-time migration procedure, now complete

DROP PROCEDURE IF EXISTS replace_deck_cards(TEXT, JSONB);
DROP PROCEDURE IF EXISTS submit_deck_cards(TEXT, TEXT, JSONB, INT, BOOL);
DROP PROCEDURE IF EXISTS submit_singleton_deck(TEXT, TEXT, JSONB, INT, BOOL);
DROP PROCEDURE IF EXISTS run_data_migration(INT);
