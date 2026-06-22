-- V20: Per-format table schema
--
-- Creates dedicated deck, deck_cards, card_stats, and card_pair_stats tables
-- for each supported format, eliminating cross-format filtering overhead.
--
-- Regular formats (5): pauper, standard, modern, vintage, legacy
--   → {format}_decks, {format}_deck_cards
--
-- Singleton formats (2): commander, canadian_highlander
--   → {format}_decks  (JSONB cards column, no deck_cards join table)
--
-- Analysis tables (7 × 2): {format}_card_stats, {format}_card_pair_stats
--
-- Existing tables (decks, deck_cards, singleton_decks, card_stats,
-- card_pair_stats) are left untouched pending data migration.


-- ============================================================================
-- REGULAR FORMATS
-- ============================================================================

-- ----------------------------------------------------------------------------
-- pauper
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pauper_decks (
    public_id        TEXT         PRIMARY KEY,
    name             TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TEXT,
    updated_at_utc   TEXT,
    scraped_at       TEXT,
    cards_fetched_at TEXT,
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX IF NOT EXISTS idx_pauper_decks_color_mask        ON pauper_decks (color_mask);
CREATE INDEX IF NOT EXISTS idx_pauper_decks_status_scraped    ON pauper_decks (status, scraped_at);

CREATE TABLE IF NOT EXISTS pauper_deck_cards (
    deck_id  TEXT    NOT NULL REFERENCES pauper_decks (public_id) ON DELETE CASCADE,
    card_id  BIGINT  NOT NULL REFERENCES cards (id),
    board    TEXT    NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (deck_id, card_id, board)
);

CREATE INDEX IF NOT EXISTS idx_pauper_deck_cards_card_id ON pauper_deck_cards (card_id);


-- ----------------------------------------------------------------------------
-- standard
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS standard_decks (
    public_id        TEXT         PRIMARY KEY,
    name             TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TEXT,
    updated_at_utc   TEXT,
    scraped_at       TEXT,
    cards_fetched_at TEXT,
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX IF NOT EXISTS idx_standard_decks_color_mask      ON standard_decks (color_mask);
CREATE INDEX IF NOT EXISTS idx_standard_decks_status_scraped  ON standard_decks (status, scraped_at);

CREATE TABLE IF NOT EXISTS standard_deck_cards (
    deck_id  TEXT    NOT NULL REFERENCES standard_decks (public_id) ON DELETE CASCADE,
    card_id  BIGINT  NOT NULL REFERENCES cards (id),
    board    TEXT    NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (deck_id, card_id, board)
);

CREATE INDEX IF NOT EXISTS idx_standard_deck_cards_card_id ON standard_deck_cards (card_id);


-- ----------------------------------------------------------------------------
-- modern
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS modern_decks (
    public_id        TEXT         PRIMARY KEY,
    name             TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TEXT,
    updated_at_utc   TEXT,
    scraped_at       TEXT,
    cards_fetched_at TEXT,
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX IF NOT EXISTS idx_modern_decks_color_mask        ON modern_decks (color_mask);
CREATE INDEX IF NOT EXISTS idx_modern_decks_status_scraped    ON modern_decks (status, scraped_at);

CREATE TABLE IF NOT EXISTS modern_deck_cards (
    deck_id  TEXT    NOT NULL REFERENCES modern_decks (public_id) ON DELETE CASCADE,
    card_id  BIGINT  NOT NULL REFERENCES cards (id),
    board    TEXT    NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (deck_id, card_id, board)
);

CREATE INDEX IF NOT EXISTS idx_modern_deck_cards_card_id ON modern_deck_cards (card_id);


-- ----------------------------------------------------------------------------
-- vintage
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vintage_decks (
    public_id        TEXT         PRIMARY KEY,
    name             TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TEXT,
    updated_at_utc   TEXT,
    scraped_at       TEXT,
    cards_fetched_at TEXT,
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX IF NOT EXISTS idx_vintage_decks_color_mask       ON vintage_decks (color_mask);
CREATE INDEX IF NOT EXISTS idx_vintage_decks_status_scraped   ON vintage_decks (status, scraped_at);

CREATE TABLE IF NOT EXISTS vintage_deck_cards (
    deck_id  TEXT    NOT NULL REFERENCES vintage_decks (public_id) ON DELETE CASCADE,
    card_id  BIGINT  NOT NULL REFERENCES cards (id),
    board    TEXT    NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (deck_id, card_id, board)
);

CREATE INDEX IF NOT EXISTS idx_vintage_deck_cards_card_id ON vintage_deck_cards (card_id);


-- ----------------------------------------------------------------------------
-- legacy
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS legacy_decks (
    public_id        TEXT         PRIMARY KEY,
    name             TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TEXT,
    updated_at_utc   TEXT,
    scraped_at       TEXT,
    cards_fetched_at TEXT,
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX IF NOT EXISTS idx_legacy_decks_color_mask        ON legacy_decks (color_mask);
CREATE INDEX IF NOT EXISTS idx_legacy_decks_status_scraped    ON legacy_decks (status, scraped_at);

CREATE TABLE IF NOT EXISTS legacy_deck_cards (
    deck_id  TEXT    NOT NULL REFERENCES legacy_decks (public_id) ON DELETE CASCADE,
    card_id  BIGINT  NOT NULL REFERENCES cards (id),
    board    TEXT    NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (deck_id, card_id, board)
);

CREATE INDEX IF NOT EXISTS idx_legacy_deck_cards_card_id ON legacy_deck_cards (card_id);


-- ============================================================================
-- SINGLETON FORMATS
-- ============================================================================

-- ----------------------------------------------------------------------------
-- commander
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS commander_decks (
    public_id        TEXT         PRIMARY KEY,
    name             TEXT,
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

CREATE INDEX IF NOT EXISTS idx_commander_decks_color_mask     ON commander_decks (color_mask);
CREATE INDEX IF NOT EXISTS idx_commander_decks_status_scraped ON commander_decks (status, scraped_at);
CREATE INDEX IF NOT EXISTS idx_commander_decks_cards_gin      ON commander_decks USING GIN (cards);


-- ----------------------------------------------------------------------------
-- canadian_highlander
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS canadian_highlander_decks (
    public_id        TEXT         PRIMARY KEY,
    name             TEXT,
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

CREATE INDEX IF NOT EXISTS idx_canadian_highlander_decks_color_mask     ON canadian_highlander_decks (color_mask);
CREATE INDEX IF NOT EXISTS idx_canadian_highlander_decks_status_scraped ON canadian_highlander_decks (status, scraped_at);
CREATE INDEX IF NOT EXISTS idx_canadian_highlander_decks_cards_gin      ON canadian_highlander_decks USING GIN (cards);


-- ============================================================================
-- ANALYSIS TABLES
-- ============================================================================

-- ----------------------------------------------------------------------------
-- pauper
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pauper_card_stats (
    card_name      TEXT    NOT NULL PRIMARY KEY,
    deck_count     INTEGER NOT NULL,
    total_decks    INTEGER NOT NULL,
    inclusion_rate REAL    NOT NULL,
    avg_quantity   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS pauper_card_pair_stats (
    card_a             TEXT    NOT NULL,
    card_b             TEXT    NOT NULL,
    cooccurrence_count INTEGER NOT NULL,
    lift               REAL    NOT NULL,
    pmi                REAL    NOT NULL,
    jaccard            REAL    NOT NULL,
    confidence_a_to_b  REAL    NOT NULL,
    confidence_b_to_a  REAL    NOT NULL,
    PRIMARY KEY (card_a, card_b)
);

CREATE INDEX IF NOT EXISTS idx_pauper_pair_card_a  ON pauper_card_pair_stats (card_a);
CREATE INDEX IF NOT EXISTS idx_pauper_pair_card_b  ON pauper_card_pair_stats (card_b);
CREATE INDEX IF NOT EXISTS idx_pauper_pair_lift     ON pauper_card_pair_stats (lift    DESC);
CREATE INDEX IF NOT EXISTS idx_pauper_pair_jaccard  ON pauper_card_pair_stats (jaccard DESC);
CREATE INDEX IF NOT EXISTS idx_pauper_pair_pmi      ON pauper_card_pair_stats (pmi     DESC);


-- ----------------------------------------------------------------------------
-- standard
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS standard_card_stats (
    card_name      TEXT    NOT NULL PRIMARY KEY,
    deck_count     INTEGER NOT NULL,
    total_decks    INTEGER NOT NULL,
    inclusion_rate REAL    NOT NULL,
    avg_quantity   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS standard_card_pair_stats (
    card_a             TEXT    NOT NULL,
    card_b             TEXT    NOT NULL,
    cooccurrence_count INTEGER NOT NULL,
    lift               REAL    NOT NULL,
    pmi                REAL    NOT NULL,
    jaccard            REAL    NOT NULL,
    confidence_a_to_b  REAL    NOT NULL,
    confidence_b_to_a  REAL    NOT NULL,
    PRIMARY KEY (card_a, card_b)
);

CREATE INDEX IF NOT EXISTS idx_standard_pair_card_a  ON standard_card_pair_stats (card_a);
CREATE INDEX IF NOT EXISTS idx_standard_pair_card_b  ON standard_card_pair_stats (card_b);
CREATE INDEX IF NOT EXISTS idx_standard_pair_lift     ON standard_card_pair_stats (lift    DESC);
CREATE INDEX IF NOT EXISTS idx_standard_pair_jaccard  ON standard_card_pair_stats (jaccard DESC);
CREATE INDEX IF NOT EXISTS idx_standard_pair_pmi      ON standard_card_pair_stats (pmi     DESC);


-- ----------------------------------------------------------------------------
-- modern
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS modern_card_stats (
    card_name      TEXT    NOT NULL PRIMARY KEY,
    deck_count     INTEGER NOT NULL,
    total_decks    INTEGER NOT NULL,
    inclusion_rate REAL    NOT NULL,
    avg_quantity   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS modern_card_pair_stats (
    card_a             TEXT    NOT NULL,
    card_b             TEXT    NOT NULL,
    cooccurrence_count INTEGER NOT NULL,
    lift               REAL    NOT NULL,
    pmi                REAL    NOT NULL,
    jaccard            REAL    NOT NULL,
    confidence_a_to_b  REAL    NOT NULL,
    confidence_b_to_a  REAL    NOT NULL,
    PRIMARY KEY (card_a, card_b)
);

CREATE INDEX IF NOT EXISTS idx_modern_pair_card_a  ON modern_card_pair_stats (card_a);
CREATE INDEX IF NOT EXISTS idx_modern_pair_card_b  ON modern_card_pair_stats (card_b);
CREATE INDEX IF NOT EXISTS idx_modern_pair_lift     ON modern_card_pair_stats (lift    DESC);
CREATE INDEX IF NOT EXISTS idx_modern_pair_jaccard  ON modern_card_pair_stats (jaccard DESC);
CREATE INDEX IF NOT EXISTS idx_modern_pair_pmi      ON modern_card_pair_stats (pmi     DESC);


-- ----------------------------------------------------------------------------
-- vintage
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vintage_card_stats (
    card_name      TEXT    NOT NULL PRIMARY KEY,
    deck_count     INTEGER NOT NULL,
    total_decks    INTEGER NOT NULL,
    inclusion_rate REAL    NOT NULL,
    avg_quantity   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS vintage_card_pair_stats (
    card_a             TEXT    NOT NULL,
    card_b             TEXT    NOT NULL,
    cooccurrence_count INTEGER NOT NULL,
    lift               REAL    NOT NULL,
    pmi                REAL    NOT NULL,
    jaccard            REAL    NOT NULL,
    confidence_a_to_b  REAL    NOT NULL,
    confidence_b_to_a  REAL    NOT NULL,
    PRIMARY KEY (card_a, card_b)
);

CREATE INDEX IF NOT EXISTS idx_vintage_pair_card_a  ON vintage_card_pair_stats (card_a);
CREATE INDEX IF NOT EXISTS idx_vintage_pair_card_b  ON vintage_card_pair_stats (card_b);
CREATE INDEX IF NOT EXISTS idx_vintage_pair_lift     ON vintage_card_pair_stats (lift    DESC);
CREATE INDEX IF NOT EXISTS idx_vintage_pair_jaccard  ON vintage_card_pair_stats (jaccard DESC);
CREATE INDEX IF NOT EXISTS idx_vintage_pair_pmi      ON vintage_card_pair_stats (pmi     DESC);


-- ----------------------------------------------------------------------------
-- legacy
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS legacy_card_stats (
    card_name      TEXT    NOT NULL PRIMARY KEY,
    deck_count     INTEGER NOT NULL,
    total_decks    INTEGER NOT NULL,
    inclusion_rate REAL    NOT NULL,
    avg_quantity   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_card_pair_stats (
    card_a             TEXT    NOT NULL,
    card_b             TEXT    NOT NULL,
    cooccurrence_count INTEGER NOT NULL,
    lift               REAL    NOT NULL,
    pmi                REAL    NOT NULL,
    jaccard            REAL    NOT NULL,
    confidence_a_to_b  REAL    NOT NULL,
    confidence_b_to_a  REAL    NOT NULL,
    PRIMARY KEY (card_a, card_b)
);

CREATE INDEX IF NOT EXISTS idx_legacy_pair_card_a  ON legacy_card_pair_stats (card_a);
CREATE INDEX IF NOT EXISTS idx_legacy_pair_card_b  ON legacy_card_pair_stats (card_b);
CREATE INDEX IF NOT EXISTS idx_legacy_pair_lift     ON legacy_card_pair_stats (lift    DESC);
CREATE INDEX IF NOT EXISTS idx_legacy_pair_jaccard  ON legacy_card_pair_stats (jaccard DESC);
CREATE INDEX IF NOT EXISTS idx_legacy_pair_pmi      ON legacy_card_pair_stats (pmi     DESC);


-- ----------------------------------------------------------------------------
-- commander
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS commander_card_stats (
    card_name      TEXT    NOT NULL PRIMARY KEY,
    deck_count     INTEGER NOT NULL,
    total_decks    INTEGER NOT NULL,
    inclusion_rate REAL    NOT NULL,
    avg_quantity   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS commander_card_pair_stats (
    card_a             TEXT    NOT NULL,
    card_b             TEXT    NOT NULL,
    cooccurrence_count INTEGER NOT NULL,
    lift               REAL    NOT NULL,
    pmi                REAL    NOT NULL,
    jaccard            REAL    NOT NULL,
    confidence_a_to_b  REAL    NOT NULL,
    confidence_b_to_a  REAL    NOT NULL,
    PRIMARY KEY (card_a, card_b)
);

CREATE INDEX IF NOT EXISTS idx_commander_pair_card_a  ON commander_card_pair_stats (card_a);
CREATE INDEX IF NOT EXISTS idx_commander_pair_card_b  ON commander_card_pair_stats (card_b);
CREATE INDEX IF NOT EXISTS idx_commander_pair_lift     ON commander_card_pair_stats (lift    DESC);
CREATE INDEX IF NOT EXISTS idx_commander_pair_jaccard  ON commander_card_pair_stats (jaccard DESC);
CREATE INDEX IF NOT EXISTS idx_commander_pair_pmi      ON commander_card_pair_stats (pmi     DESC);


-- ----------------------------------------------------------------------------
-- canadian_highlander
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS canadian_highlander_card_stats (
    card_name      TEXT    NOT NULL PRIMARY KEY,
    deck_count     INTEGER NOT NULL,
    total_decks    INTEGER NOT NULL,
    inclusion_rate REAL    NOT NULL,
    avg_quantity   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS canadian_highlander_card_pair_stats (
    card_a             TEXT    NOT NULL,
    card_b             TEXT    NOT NULL,
    cooccurrence_count INTEGER NOT NULL,
    lift               REAL    NOT NULL,
    pmi                REAL    NOT NULL,
    jaccard            REAL    NOT NULL,
    confidence_a_to_b  REAL    NOT NULL,
    confidence_b_to_a  REAL    NOT NULL,
    PRIMARY KEY (card_a, card_b)
);

CREATE INDEX IF NOT EXISTS idx_canadian_highlander_pair_card_a  ON canadian_highlander_card_pair_stats (card_a);
CREATE INDEX IF NOT EXISTS idx_canadian_highlander_pair_card_b  ON canadian_highlander_card_pair_stats (card_b);
CREATE INDEX IF NOT EXISTS idx_canadian_highlander_pair_lift     ON canadian_highlander_card_pair_stats (lift    DESC);
CREATE INDEX IF NOT EXISTS idx_canadian_highlander_pair_jaccard  ON canadian_highlander_card_pair_stats (jaccard DESC);
CREATE INDEX IF NOT EXISTS idx_canadian_highlander_pair_pmi      ON canadian_highlander_card_pair_stats (pmi     DESC);
