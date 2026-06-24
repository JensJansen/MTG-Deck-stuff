-- V39: Add color_mask to archetypes for singleton format color-identity partitioning.
--
-- Singleton formats (commander, highlanderCanadian) run the classification pipeline
-- once per color identity partition (32 partitions: all subsets of WUBRG) so that
-- each UMAP + HDBSCAN pass operates on a dataset comparable in size to smaller formats.
-- color_mask stores the integer bitmask (W=1 U=2 B=4 R=8 G=16) for the partition;
-- NULL means the archetype was produced by an unpartitioned run (regular formats).

ALTER TABLE archetypes ADD COLUMN IF NOT EXISTS color_mask SMALLINT;

DROP INDEX IF EXISTS idx_archetypes_format;
CREATE INDEX IF NOT EXISTS idx_archetypes_format ON archetypes (format, color_mask);
