-- V25: Drop legacy global deck tables and unused archetype tables
--
-- All data has been migrated to per-format tables (V20).
-- deck_archetypes is dropped first as it holds a FK reference to decks.
-- archetypes is dropped next as deck_archetypes references it.
-- deck_cards is dropped before decks to satisfy its FK constraint.
-- singleton_decks has no dependents and is dropped independently.

DROP TABLE IF EXISTS deck_archetypes;
DROP TABLE IF EXISTS archetypes;
DROP TABLE IF EXISTS deck_cards;
DROP TABLE IF EXISTS decks;
DROP TABLE IF EXISTS singleton_decks;
