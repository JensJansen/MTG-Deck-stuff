-- V4: Visualization layout schema — card_layout
--
-- Populated by src/analysis/precompute_layout.py.
-- Stores 2D UMAP coordinates per (card, format) for rendering card maps.

CREATE TABLE IF NOT EXISTS card_layout (
    card_name      TEXT  NOT NULL,
    format         TEXT  NOT NULL,
    x              REAL  NOT NULL,
    y              REAL  NOT NULL,
    color_identity TEXT  NOT NULL DEFAULT '',
    PRIMARY KEY (card_name, format)
);
