-- V46: v2 schema — clean-slate scraping and card/deck tables.
--
-- Design decisions vs. the public (v1) schema:
--   - Lives in a dedicated 'v2' schema; public schema is untouched.
--   - Integer primary keys throughout; Moxfield/Scryfall IDs stored as plain TEXT.
--   - Deck tables: moxfield_id carries a UNIQUE constraint for upsert correctness.
--   - Cards table: INTEGER PK (fits well beyond 40k cards); no extra indexes on
--     external IDs or card attributes.
--   - Singleton formats (commander, canadian_highlander): cards stored as INTEGER[]
--     arrays of v2.cards.id references; commander also has a commander_ids INTEGER[].
--   - card_format_status: normalized (card_id, format) rows instead of the
--     wide-column card_sweep_status design.
--   - Only the 6 actively scraped formats are tracked; no stats or archetype tables.

CREATE SCHEMA IF NOT EXISTS v2;


-- ============================================================================
-- CARDS
-- Seeded from a Scryfall bulk export. No indexes beyond the PK; the API layer
-- resolves card names to IDs in memory at startup.
-- ============================================================================

CREATE TABLE v2.cards (
    id               INTEGER      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
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

    -- Only the 6 formats actively scraped. 'legal' | 'not_legal' | 'banned' | 'restricted' | NULL.
    -- Canadian Highlander has no Scryfall legality entry; all cards are treated as legal there
    -- via the card_format_status trigger logic.
    legal_commander  TEXT,
    legal_pauper     TEXT,
    legal_modern     TEXT,
    legal_vintage    TEXT,
    legal_legacy     TEXT
);


-- ============================================================================
-- CARD FORMAT STATUS
-- Normalized replacement for the wide card_sweep_status table.
-- One row per card × format. A trigger populates this on card insert.
-- ============================================================================

CREATE TABLE v2.card_format_status (
    card_id    INTEGER      NOT NULL REFERENCES v2.cards(id) ON DELETE CASCADE,
    format     TEXT         NOT NULL
                            CHECK (format IN (
                                'commander', 'highlanderCanadian',
                                'pauper', 'modern', 'vintage', 'legacy'
                            )),
    swept      BOOLEAN      NOT NULL DEFAULT FALSE,
    claimed_at TIMESTAMPTZ,
    claimed_by TEXT,
    PRIMARY KEY (card_id, format)
);

-- Insert a status row for every format a new card is legal in.
-- Canadian Highlander has no Scryfall legality column — all cards are eligible.
CREATE OR REPLACE FUNCTION v2._on_card_insert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO v2.card_format_status (card_id, format)
    SELECT NEW.id, f.format
    FROM (VALUES
        ('commander'),
        ('highlanderCanadian'),
        ('pauper'),
        ('modern'),
        ('vintage'),
        ('legacy')
    ) AS f(format)
    WHERE
        f.format = 'highlanderCanadian'
        OR (f.format = 'commander' AND NEW.legal_commander = 'legal')
        OR (f.format = 'pauper'    AND NEW.legal_pauper    = 'legal')
        OR (f.format = 'modern'    AND NEW.legal_modern    = 'legal')
        OR (f.format = 'vintage'   AND NEW.legal_vintage   = 'legal')
        OR (f.format = 'legacy'    AND NEW.legal_legacy    = 'legal')
    ON CONFLICT DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_v2_cards_insert
AFTER INSERT ON v2.cards
FOR EACH ROW EXECUTE FUNCTION v2._on_card_insert();


-- ============================================================================
-- REGULAR FORMATS: pauper, modern, vintage, legacy
-- Each format gets a decks table and a deck_cards table.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- pauper
-- ----------------------------------------------------------------------------
CREATE TABLE v2.pauper_decks (
    id               INTEGER      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    moxfield_id      TEXT         NOT NULL UNIQUE,
    name             TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TIMESTAMPTZ,
    updated_at_utc   TIMESTAMPTZ,
    scraped_at       TIMESTAMPTZ  NOT NULL,
    cards_fetched_at TIMESTAMPTZ,
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX idx_v2_pauper_decks_claim
    ON v2.pauper_decks (status, scraped_at);

CREATE TABLE v2.pauper_deck_cards (
    deck_id   INTEGER  NOT NULL REFERENCES v2.pauper_decks(id) ON DELETE CASCADE,
    card_id   INTEGER  NOT NULL REFERENCES v2.cards(id),
    board     TEXT     NOT NULL,
    quantity  INTEGER  NOT NULL DEFAULT 1,
    PRIMARY KEY (deck_id, card_id, board)
);

CREATE INDEX idx_v2_pauper_deck_cards_card
    ON v2.pauper_deck_cards (card_id);


-- ----------------------------------------------------------------------------
-- modern
-- ----------------------------------------------------------------------------
CREATE TABLE v2.modern_decks (
    id               INTEGER      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    moxfield_id      TEXT         NOT NULL UNIQUE,
    name             TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TIMESTAMPTZ,
    updated_at_utc   TIMESTAMPTZ,
    scraped_at       TIMESTAMPTZ  NOT NULL,
    cards_fetched_at TIMESTAMPTZ,
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX idx_v2_modern_decks_claim
    ON v2.modern_decks (status, scraped_at);

CREATE TABLE v2.modern_deck_cards (
    deck_id   INTEGER  NOT NULL REFERENCES v2.modern_decks(id) ON DELETE CASCADE,
    card_id   INTEGER  NOT NULL REFERENCES v2.cards(id),
    board     TEXT     NOT NULL,
    quantity  INTEGER  NOT NULL DEFAULT 1,
    PRIMARY KEY (deck_id, card_id, board)
);

CREATE INDEX idx_v2_modern_deck_cards_card
    ON v2.modern_deck_cards (card_id);


-- ----------------------------------------------------------------------------
-- vintage
-- ----------------------------------------------------------------------------
CREATE TABLE v2.vintage_decks (
    id               INTEGER      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    moxfield_id      TEXT         NOT NULL UNIQUE,
    name             TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TIMESTAMPTZ,
    updated_at_utc   TIMESTAMPTZ,
    scraped_at       TIMESTAMPTZ  NOT NULL,
    cards_fetched_at TIMESTAMPTZ,
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX idx_v2_vintage_decks_claim
    ON v2.vintage_decks (status, scraped_at);

CREATE TABLE v2.vintage_deck_cards (
    deck_id   INTEGER  NOT NULL REFERENCES v2.vintage_decks(id) ON DELETE CASCADE,
    card_id   INTEGER  NOT NULL REFERENCES v2.cards(id),
    board     TEXT     NOT NULL,
    quantity  INTEGER  NOT NULL DEFAULT 1,
    PRIMARY KEY (deck_id, card_id, board)
);

CREATE INDEX idx_v2_vintage_deck_cards_card
    ON v2.vintage_deck_cards (card_id);


-- ----------------------------------------------------------------------------
-- legacy
-- ----------------------------------------------------------------------------
CREATE TABLE v2.legacy_decks (
    id               INTEGER      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    moxfield_id      TEXT         NOT NULL UNIQUE,
    name             TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TIMESTAMPTZ,
    updated_at_utc   TIMESTAMPTZ,
    scraped_at       TIMESTAMPTZ  NOT NULL,
    cards_fetched_at TIMESTAMPTZ,
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX idx_v2_legacy_decks_claim
    ON v2.legacy_decks (status, scraped_at);

CREATE TABLE v2.legacy_deck_cards (
    deck_id   INTEGER  NOT NULL REFERENCES v2.legacy_decks(id) ON DELETE CASCADE,
    card_id   INTEGER  NOT NULL REFERENCES v2.cards(id),
    board     TEXT     NOT NULL,
    quantity  INTEGER  NOT NULL DEFAULT 1,
    PRIMARY KEY (deck_id, card_id, board)
);

CREATE INDEX idx_v2_legacy_deck_cards_card
    ON v2.legacy_deck_cards (card_id);


-- ============================================================================
-- SINGLETON FORMATS: commander, highlandercanadian
-- Cards stored as INTEGER[] arrays of v2.cards.id values.
-- Commander also tracks the commander slot(s) in a separate array.
-- No deck_cards join table — the arrays carry all card membership.
-- Table prefixes are the Moxfield format token lowercased, so the API derives
-- them with a plain token.lower() and needs no format->table mapping.
-- ============================================================================

CREATE TABLE v2.commander_decks (
    id               INTEGER      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    moxfield_id      TEXT         NOT NULL UNIQUE,
    name             TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TIMESTAMPTZ,
    updated_at_utc   TIMESTAMPTZ,
    scraped_at       TIMESTAMPTZ  NOT NULL,
    cards_fetched_at TIMESTAMPTZ,
    card_ids         INTEGER[],
    commander_ids    INTEGER[],
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX idx_v2_commander_decks_claim
    ON v2.commander_decks (status, scraped_at);


CREATE TABLE v2.highlandercanadian_decks (
    id               INTEGER      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    moxfield_id      TEXT         NOT NULL UNIQUE,
    name             TEXT,
    author           TEXT,
    color_mask       INTEGER      NOT NULL DEFAULT 0,
    created_at_utc   TIMESTAMPTZ,
    updated_at_utc   TIMESTAMPTZ,
    scraped_at       TIMESTAMPTZ  NOT NULL,
    cards_fetched_at TIMESTAMPTZ,
    card_ids         INTEGER[],
    status           TEXT         NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered', 'claimed', 'done', 'error')),
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT
);

CREATE INDEX idx_v2_highlandercanadian_decks_claim
    ON v2.highlandercanadian_decks (status, scraped_at);
