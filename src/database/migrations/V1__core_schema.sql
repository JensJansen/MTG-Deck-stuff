-- V1: Core schema — cards, decks, deck_cards, collision_log
--
-- This migration is the baseline for existing databases.
-- All statements use IF NOT EXISTS so the migration is safe to inspect
-- even though Flyway will not execute it against a database that has been
-- baselined (see flyway.toml: baselineOnMigrate = true).

-- ---------------------------------------------------------------------------
-- cards
-- Seeded once by seed_cards.py from a Scryfall bulk data export.
-- Never written to by scraper nodes.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cards (
    id               BIGSERIAL    PRIMARY KEY,
    card_name        TEXT         NOT NULL,
    scryfall_id      TEXT,
    oracle_id        TEXT,
    layout           TEXT,
    mana_cost        TEXT,
    cmc              REAL         NOT NULL DEFAULT 0,
    type_line        TEXT,
    oracle_text      TEXT,
    power            TEXT,
    toughness        TEXT,
    loyalty          TEXT,
    defense          TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    ci_mask          INTEGER      NOT NULL DEFAULT 0,
    rarity           TEXT,
    reserved         SMALLINT     NOT NULL DEFAULT 0,
    textless         SMALLINT     NOT NULL DEFAULT 0,
    game_changer     SMALLINT     NOT NULL DEFAULT 0,
    edhrec_rank      INTEGER,
    image_uri        TEXT,
    keywords_json    TEXT,

    -- Format legalities: 'legal' | 'not_legal' | 'banned' | 'restricted' | NULL
    legal_standard        TEXT,
    legal_historic        TEXT,
    legal_timeless        TEXT,
    legal_gladiator       TEXT,
    legal_pioneer         TEXT,
    legal_explorer        TEXT,
    legal_modern          TEXT,
    legal_legacy          TEXT,
    legal_pauper          TEXT,
    legal_vintage         TEXT,
    legal_penny           TEXT,
    legal_commander       TEXT,
    legal_oathbreaker     TEXT,
    legal_standardbrawl   TEXT,
    legal_brawl           TEXT,
    legal_alchemy         TEXT,
    legal_paupercommander TEXT,
    legal_duel            TEXT,
    legal_oldschool       TEXT,
    legal_premodern       TEXT,
    legal_predh           TEXT,
    legal_historicbrawl   TEXT,

    CONSTRAINT cards_card_name_unique UNIQUE (card_name)
);

CREATE INDEX IF NOT EXISTS idx_cards_scryfall_id ON cards (scryfall_id);
CREATE INDEX IF NOT EXISTS idx_cards_rarity      ON cards (rarity);
CREATE INDEX IF NOT EXISTS idx_cards_ci_mask     ON cards (ci_mask);
CREATE INDEX IF NOT EXISTS idx_cards_layout      ON cards (layout);

CREATE INDEX IF NOT EXISTS idx_cards_legal_standard        ON cards (legal_standard);
CREATE INDEX IF NOT EXISTS idx_cards_legal_historic        ON cards (legal_historic);
CREATE INDEX IF NOT EXISTS idx_cards_legal_timeless        ON cards (legal_timeless);
CREATE INDEX IF NOT EXISTS idx_cards_legal_gladiator       ON cards (legal_gladiator);
CREATE INDEX IF NOT EXISTS idx_cards_legal_pioneer         ON cards (legal_pioneer);
CREATE INDEX IF NOT EXISTS idx_cards_legal_explorer        ON cards (legal_explorer);
CREATE INDEX IF NOT EXISTS idx_cards_legal_modern          ON cards (legal_modern);
CREATE INDEX IF NOT EXISTS idx_cards_legal_legacy          ON cards (legal_legacy);
CREATE INDEX IF NOT EXISTS idx_cards_legal_pauper          ON cards (legal_pauper);
CREATE INDEX IF NOT EXISTS idx_cards_legal_vintage         ON cards (legal_vintage);
CREATE INDEX IF NOT EXISTS idx_cards_legal_penny           ON cards (legal_penny);
CREATE INDEX IF NOT EXISTS idx_cards_legal_commander       ON cards (legal_commander);
CREATE INDEX IF NOT EXISTS idx_cards_legal_oathbreaker     ON cards (legal_oathbreaker);
CREATE INDEX IF NOT EXISTS idx_cards_legal_standardbrawl   ON cards (legal_standardbrawl);
CREATE INDEX IF NOT EXISTS idx_cards_legal_brawl           ON cards (legal_brawl);
CREATE INDEX IF NOT EXISTS idx_cards_legal_alchemy         ON cards (legal_alchemy);
CREATE INDEX IF NOT EXISTS idx_cards_legal_paupercommander ON cards (legal_paupercommander);
CREATE INDEX IF NOT EXISTS idx_cards_legal_duel            ON cards (legal_duel);
CREATE INDEX IF NOT EXISTS idx_cards_legal_oldschool       ON cards (legal_oldschool);
CREATE INDEX IF NOT EXISTS idx_cards_legal_premodern       ON cards (legal_premodern);
CREATE INDEX IF NOT EXISTS idx_cards_legal_predh           ON cards (legal_predh);
CREATE INDEX IF NOT EXISTS idx_cards_legal_historicbrawl   ON cards (legal_historicbrawl);


-- ---------------------------------------------------------------------------
-- decks
-- Discovered by central_node; processed by scraper_node.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS decks (
    public_id        TEXT         PRIMARY KEY,
    name             TEXT,
    format           TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TEXT,
    updated_at_utc   TEXT,
    scraped_at       TEXT,
    cards_fetched_at TEXT,

    -- 'discovered' → ready for a scraper node to claim
    -- 'claimed'    → a scraper node is currently processing it
    -- 'done'       → cards have been fetched and stored
    -- 'error'      → scraper node encountered a fatal error
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX IF NOT EXISTS idx_decks_status        ON decks (status);
CREATE INDEX IF NOT EXISTS idx_decks_format        ON decks (format);
CREATE INDEX IF NOT EXISTS idx_decks_status_scraped ON decks (status, scraped_at);


-- ---------------------------------------------------------------------------
-- deck_cards
-- Written by scraper_node after fetching deck details from Moxfield.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deck_cards (
    deck_id  TEXT    NOT NULL REFERENCES decks (public_id) ON DELETE CASCADE,
    card_id  BIGINT  NOT NULL REFERENCES cards (id),
    board    TEXT    NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (deck_id, card_id, board)
);

CREATE INDEX IF NOT EXISTS idx_deck_cards_card_id ON deck_cards (card_id);
CREATE INDEX IF NOT EXISTS idx_deck_cards_deck_id ON deck_cards (deck_id);


-- ---------------------------------------------------------------------------
-- collision_log
-- Written by the API when a scraper node submits cards for a deck that is
-- already marked 'done'. Monitors processing overlap in the distributed system.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS collision_log (
    id          BIGSERIAL    PRIMARY KEY,
    deck_id     TEXT         NOT NULL REFERENCES decks (public_id),
    worker_id   TEXT         NOT NULL,
    detected_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_collision_log_deck_id     ON collision_log (deck_id);
CREATE INDEX IF NOT EXISTS idx_collision_log_detected_at ON collision_log (detected_at);
