-- V12: card_sweep_status — tracks per-format full-sweep completion per card
--
-- One row per card, one boolean column per format defined in LEGAL_FORMATS.
-- Defaults to FALSE (not swept). Set to TRUE by the central node only when a
-- full sweep of that card+format completes (all pages paginated, not just an
-- incremental catch-up).
--
-- A trigger keeps the table in sync as new cards are added via the Scryfall
-- importer, so no changes are needed to that script.

CREATE TABLE card_sweep_status (
    card_name                TEXT    PRIMARY KEY
                                     REFERENCES cards (card_name) ON DELETE CASCADE,
    swept_commander          BOOLEAN NOT NULL DEFAULT FALSE,
    swept_pauper             BOOLEAN NOT NULL DEFAULT FALSE,
    swept_standard           BOOLEAN NOT NULL DEFAULT FALSE,
    swept_modern             BOOLEAN NOT NULL DEFAULT FALSE,
    swept_vintage            BOOLEAN NOT NULL DEFAULT FALSE,
    swept_legacy             BOOLEAN NOT NULL DEFAULT FALSE,
    swept_highlanderCanadian BOOLEAN NOT NULL DEFAULT FALSE
);

-- Pre-populate for all cards already in the database.
INSERT INTO card_sweep_status (card_name)
SELECT card_name FROM cards;

-- Keep the table in sync when new cards are imported.
CREATE OR REPLACE FUNCTION _card_sweep_status_insert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO card_sweep_status (card_name) VALUES (NEW.card_name)
    ON CONFLICT DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_cards_insert_sweep_status
AFTER INSERT ON cards
FOR EACH ROW EXECUTE FUNCTION _card_sweep_status_insert();
