-- V2: Deck archetype classification schema — archetypes, deck_archetypes

-- ---------------------------------------------------------------------------
-- archetypes
-- One row per discovered cluster at each classification level.
-- Level 1 rows are coarse archetypes (HDBSCAN on deck embeddings).
-- Level 2 rows are sub-archetypes; parent_id points to the Level 1 row.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS archetypes (
    id             SERIAL       PRIMARY KEY,
    format         TEXT         NOT NULL,
    name           TEXT,                          -- NULL until human-curated
    level          SMALLINT     NOT NULL,         -- 1 = coarse, 2 = sub-archetype
    parent_id      INTEGER      REFERENCES archetypes (id) ON DELETE SET NULL,
    centroid       BYTEA,                         -- serialised float32 numpy array (EMBEDDING_DIM,)
    keystone_cards JSONB,                         -- [{card, p_in, p_out, diff}, ...]
    member_count   INTEGER      NOT NULL DEFAULT 0,
    run_id         TEXT,                          -- ISO timestamp of the pipeline run
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_archetypes_format       ON archetypes (format);
CREATE INDEX IF NOT EXISTS idx_archetypes_parent       ON archetypes (parent_id);
CREATE INDEX IF NOT EXISTS idx_archetypes_format_level ON archetypes (format, level);


-- ---------------------------------------------------------------------------
-- deck_archetypes
-- Maps each deck to its Level 1 and (if applicable) Level 2 archetype.
-- A deck has at most one row per level (PK enforces this).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deck_archetypes (
    deck_id       TEXT         NOT NULL REFERENCES decks (public_id) ON DELETE CASCADE,
    archetype_id  INTEGER      NOT NULL REFERENCES archetypes (id)   ON DELETE CASCADE,
    level         SMALLINT     NOT NULL,
    confidence    REAL,                           -- HDBSCAN soft membership probability (0–1)
    classified_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (deck_id, level)
);

CREATE INDEX IF NOT EXISTS idx_deck_archetypes_archetype ON deck_archetypes (archetype_id);
CREATE INDEX IF NOT EXISTS idx_deck_archetypes_deck      ON deck_archetypes (deck_id);
