ALTER TABLE cards DROP COLUMN IF EXISTS legal_future;
DROP INDEX IF EXISTS idx_cards_legal_future;
