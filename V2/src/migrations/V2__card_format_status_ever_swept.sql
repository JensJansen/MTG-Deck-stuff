-- V2: Add ever_swept to v2.card_format_status.
--
-- The scraper uses a global sweep cycle: a (card, format) is claimed only while
-- swept = FALSE, and once the whole deck queue drains, every swept flag resets
-- to FALSE to begin a new cycle.
--
-- That per-cycle `swept` flag cannot record whether a card has *ever* been fully
-- swept, which is what decides full vs. incremental discovery:
--   ever_swept = FALSE -> full sweep (paginate all pages; first-ever pass, and
--                         retried in full until one completes, so an interrupted
--                         first sweep can never leave a permanent coverage hole)
--   ever_swept = TRUE  -> incremental sweep (early-exit once a page is all-known)
--
-- /sweeps/complete sets both swept and ever_swept TRUE; the cycle reset clears
-- only swept. Defaults to FALSE: nothing has been swept yet.

ALTER TABLE v2.card_format_status
    ADD COLUMN ever_swept BOOLEAN NOT NULL DEFAULT FALSE;
