-- V24: Replace composite primary key on per-format deck_cards tables
--
-- Drops the (deck_id, card_id, board) primary key from all five regular-format
-- deck_cards tables and replaces it with a non-unique index on (deck_id, card_id).
-- Queries against deck_id alone or (deck_id, card_id) together will use this index.
-- Removing the PK also speeds up bulk inserts by eliminating unique index maintenance.

ALTER TABLE pauper_deck_cards   DROP CONSTRAINT pauper_deck_cards_pkey;
ALTER TABLE standard_deck_cards DROP CONSTRAINT standard_deck_cards_pkey;
ALTER TABLE modern_deck_cards   DROP CONSTRAINT modern_deck_cards_pkey;
ALTER TABLE vintage_deck_cards  DROP CONSTRAINT vintage_deck_cards_pkey;
ALTER TABLE legacy_deck_cards   DROP CONSTRAINT legacy_deck_cards_pkey;

CREATE INDEX IF NOT EXISTS idx_pauper_deck_cards_deck_card   ON pauper_deck_cards   (deck_id, card_id);
CREATE INDEX IF NOT EXISTS idx_standard_deck_cards_deck_card ON standard_deck_cards (deck_id, card_id);
CREATE INDEX IF NOT EXISTS idx_modern_deck_cards_deck_card   ON modern_deck_cards   (deck_id, card_id);
CREATE INDEX IF NOT EXISTS idx_vintage_deck_cards_deck_card  ON vintage_deck_cards  (deck_id, card_id);
CREATE INDEX IF NOT EXISTS idx_legacy_deck_cards_deck_card   ON legacy_deck_cards   (deck_id, card_id);

DROP INDEX IF EXISTS idx_pauper_deck_cards_card_id;
DROP INDEX IF EXISTS idx_standard_deck_cards_card_id;
DROP INDEX IF EXISTS idx_modern_deck_cards_card_id;
DROP INDEX IF EXISTS idx_vintage_deck_cards_card_id;
DROP INDEX IF EXISTS idx_legacy_deck_cards_card_id;
