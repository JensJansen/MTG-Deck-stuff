-- Remove format columns no longer tracked
ALTER TABLE cards
    DROP COLUMN IF EXISTS legal_historic,
    DROP COLUMN IF EXISTS legal_timeless,
    DROP COLUMN IF EXISTS legal_gladiator,
    DROP COLUMN IF EXISTS legal_pioneer,
    DROP COLUMN IF EXISTS legal_explorer,
    DROP COLUMN IF EXISTS legal_penny,
    DROP COLUMN IF EXISTS legal_oathbreaker,
    DROP COLUMN IF EXISTS legal_standardbrawl,
    DROP COLUMN IF EXISTS legal_brawl,
    DROP COLUMN IF EXISTS legal_alchemy,
    DROP COLUMN IF EXISTS legal_paupercommander,
    DROP COLUMN IF EXISTS legal_duel,
    DROP COLUMN IF EXISTS legal_oldschool,
    DROP COLUMN IF EXISTS legal_premodern,
    DROP COLUMN IF EXISTS legal_predh,
    DROP COLUMN IF EXISTS legal_historicbrawl;

-- Add Canadian Highlander — all cards legal, not tracked by Scryfall
ALTER TABLE cards
    ADD COLUMN IF NOT EXISTS legal_highlanderCanadian TEXT DEFAULT 'legal';

CREATE INDEX IF NOT EXISTS idx_cards_legal_highlanderCanadian ON cards (legal_highlanderCanadian);
