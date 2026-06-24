-- V41: Add card_ids and commander_card_ids columns to singleton deck tables.
--
-- card_ids           : resolved integer IDs for all cards in the deck (all boards).
--                      Replaces the JSONB cards column as the primary storage format.
--                      cardinality(card_ids) gives total card count.
--
-- commander_card_ids : IDs of cards on the 'commanders' board only (commander_decks
--                      only; supports partner commanders). Not added to
--                      canadian_highlander_decks which has no commander concept.
--
-- Both columns are nullable: NULL means the deck has not yet been processed.
-- Populated by submit_format_singleton_deck (see V42).

ALTER TABLE commander_decks
    ADD COLUMN card_ids           BIGINT[],
    ADD COLUMN commander_card_ids BIGINT[];

ALTER TABLE canadian_highlander_decks
    ADD COLUMN card_ids BIGINT[];
