-- V13: singleton_decks table for commander and highlanderCanadian formats
--
-- Mirrors the shape of decks but stores deck contents inline as JSONB,
-- eliminating the need for a linking table. Each element in cards has the shape:
-- {"card_name": "Sol Ring", "board": "mainboard", "quantity": 1}

CREATE TABLE IF NOT EXISTS singleton_decks (
    public_id        TEXT         PRIMARY KEY,
    name             TEXT,
    format           TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TEXT,
    updated_at_utc   TEXT,
    scraped_at       TEXT,
    cards_fetched_at TEXT,
    cards            JSONB,

    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

-- Work queue indexes (mirrors decks)
CREATE INDEX IF NOT EXISTS idx_singleton_decks_status         ON singleton_decks (status);
CREATE INDEX IF NOT EXISTS idx_singleton_decks_format         ON singleton_decks (format);
CREATE INDEX IF NOT EXISTS idx_singleton_decks_status_scraped ON singleton_decks (status, scraped_at);

-- JSONB containment — enables fast "which decks run card X" queries
CREATE INDEX IF NOT EXISTS idx_singleton_decks_cards_gin      ON singleton_decks USING GIN (cards);
