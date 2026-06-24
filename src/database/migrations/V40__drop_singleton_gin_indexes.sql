-- V40: Drop GIN indexes on singleton format deck card columns.
--
-- These indexes were created to support containment queries (cards @> '...'),
-- but no such queries exist in the codebase. All reads use CROSS JOIN LATERAL
-- jsonb_array_elements() which is a sequential scan the GIN cannot accelerate.
-- At ~32 GB the index is larger than the data it indexes (17 GB) and causes
-- significant write amplification: ~300 GIN entries are inserted and deleted
-- on every deck submission.

DROP INDEX IF EXISTS idx_commander_decks_cards_gin;
DROP INDEX IF EXISTS idx_canadian_highlander_decks_cards_gin;
