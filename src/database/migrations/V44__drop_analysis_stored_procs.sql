-- V44: Drop all stored procedures and functions used by the analysis pipeline.
--
-- Computation previously handled by these objects is now entirely in Python:
--   refresh_stats.py  — co-occurrence stats (M.T @ M) + card-space UMAP layout
--   visualize.py      — JSON export only (pure reads, no stored functions needed)
--
-- refresh_singleton_format_stats / refresh_format_stats:
--   Replaced by refresh_stats.py for all formats. The SQL self-join approach
--   generated O(N_decks × cards²) staging rows and could not scale past a few
--   hundred thousand decks. The Python sparse multiply handles commander at 5M+.
--
-- get_layout_cards / get_layout_pairs / decode_color_mask / store_card_layout:
--   Replaced by inline queries and NumPy decoding in refresh_stats.py.
--   The card-space UMAP now runs directly on the in-memory jaccard matrix,
--   eliminating the write-to-DB → read-back round-trip.

DROP PROCEDURE IF EXISTS refresh_singleton_format_stats(TEXT, INT, INT);
DROP PROCEDURE IF EXISTS refresh_format_stats(TEXT, INT, INT);

DROP FUNCTION IF EXISTS get_layout_cards(TEXT, INT);
DROP FUNCTION IF EXISTS get_layout_pairs(TEXT, INT, INT);
DROP PROCEDURE IF EXISTS store_card_layout(TEXT, JSONB);
DROP FUNCTION IF EXISTS decode_color_mask(INT);
