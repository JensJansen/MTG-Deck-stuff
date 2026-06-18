-- V11: Drop the 3-parameter overload of refresh_format_stats left by V7.
-- V10 added a 4th parameter (p_batch_size) with a default, but CREATE OR REPLACE
-- only replaces an exact signature match — it created a new overload rather than
-- replacing the original. The two coexisting signatures cause an AmbiguousFunction
-- error when calling with 3 arguments.
DROP PROCEDURE IF EXISTS refresh_format_stats(TEXT, TEXT[], INT);
