-- V17: Drop collision_log table
--
-- Error and collision tracking have been removed from the scraper pipeline.
-- Failed decks stay in 'claimed' status and are automatically reclaimed
-- after CLAIM_TIMEOUT_MINUTES by the next available worker.

DROP TABLE IF EXISTS collision_log;
