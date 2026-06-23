-- V37: Add precomputed per-archetype fields for visualization.
--
-- top_cards:     [{card, pct}, ...] top 20 non-land cards by inclusion rate
-- color_profile: {W, U, B, R, G}   fractional pip distribution (sums to 1)
-- cmc_curve:     [f0, f1, ..., f6+] average CMC distribution (7 bins, sums to 1)
--
-- These are computed during pipeline.py from the in-memory feature matrices
-- so that the visualization export (precompute_archetypes.py) is a simple
-- DB read with no heavy computation.

ALTER TABLE archetypes ADD COLUMN IF NOT EXISTS top_cards     JSONB;
ALTER TABLE archetypes ADD COLUMN IF NOT EXISTS color_profile JSONB;
ALTER TABLE archetypes ADD COLUMN IF NOT EXISTS cmc_curve     JSONB;
