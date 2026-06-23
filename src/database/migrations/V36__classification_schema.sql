-- V36: Recreate classification tables after V25 dropped the legacy global tables.
--
-- The original V2 schema referenced decks.public_id via a FK on deck_archetypes,
-- but the global decks table was dropped in V25. deck_id is kept as plain TEXT;
-- referential integrity is enforced by the pipeline's clear-before-write pattern.

CREATE TABLE IF NOT EXISTS archetypes (
    id             SERIAL       PRIMARY KEY,
    format         TEXT         NOT NULL,
    name           TEXT,
    level          SMALLINT     NOT NULL,
    parent_id      INTEGER      REFERENCES archetypes (id) ON DELETE SET NULL,
    centroid       BYTEA,
    keystone_cards JSONB,
    member_count   INTEGER      NOT NULL DEFAULT 0,
    run_id         TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_archetypes_format       ON archetypes (format);
CREATE INDEX IF NOT EXISTS idx_archetypes_parent       ON archetypes (parent_id);
CREATE INDEX IF NOT EXISTS idx_archetypes_format_level ON archetypes (format, level);


CREATE TABLE IF NOT EXISTS deck_archetypes (
    deck_id       TEXT         NOT NULL,
    archetype_id  INTEGER      NOT NULL REFERENCES archetypes (id) ON DELETE CASCADE,
    level         SMALLINT     NOT NULL,
    confidence    REAL,
    classified_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (deck_id, level)
);

CREATE INDEX IF NOT EXISTS idx_deck_archetypes_archetype ON deck_archetypes (archetype_id);
CREATE INDEX IF NOT EXISTS idx_deck_archetypes_deck      ON deck_archetypes (deck_id);
