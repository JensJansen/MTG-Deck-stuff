-- V3: Analysis statistics schema — card_stats, card_pair_stats
--
-- Populated by src/analysis/compute_stats.py.
-- Tables are recreated from scratch on each stats run (TRUNCATE + re-insert),
-- so the schema here is the authoritative definition only.

-- ---------------------------------------------------------------------------
-- card_stats
-- Per-(card, format) inclusion rate across all processed decks.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS card_stats (
    card_name      TEXT     NOT NULL,
    format         TEXT     NOT NULL,
    deck_count     INTEGER  NOT NULL,
    total_decks    INTEGER  NOT NULL,
    inclusion_rate REAL     NOT NULL,
    avg_quantity   REAL     NOT NULL,
    PRIMARY KEY (card_name, format)
);


-- ---------------------------------------------------------------------------
-- card_pair_stats
-- Co-occurrence metrics for card pairs within a format.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS card_pair_stats (
    card_a             TEXT     NOT NULL,
    card_b             TEXT     NOT NULL,
    format             TEXT     NOT NULL,
    cooccurrence_count INTEGER  NOT NULL,
    lift               REAL     NOT NULL,
    pmi                REAL     NOT NULL,
    jaccard            REAL     NOT NULL,
    confidence_a_to_b  REAL     NOT NULL,
    confidence_b_to_a  REAL     NOT NULL,
    PRIMARY KEY (card_a, card_b, format)
);

CREATE INDEX IF NOT EXISTS idx_pair_card_a  ON card_pair_stats (card_a, format);
CREATE INDEX IF NOT EXISTS idx_pair_card_b  ON card_pair_stats (card_b, format);
CREATE INDEX IF NOT EXISTS idx_pair_lift    ON card_pair_stats (lift    DESC);
CREATE INDEX IF NOT EXISTS idx_pair_jaccard ON card_pair_stats (jaccard DESC);
CREATE INDEX IF NOT EXISTS idx_pair_pmi     ON card_pair_stats (pmi     DESC);
