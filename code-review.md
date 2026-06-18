# Code Review

Findings sorted by file. Severity indicators: **[bug]**, **[design]**, **[quality]**.

---

## Cross-Cutting Issues

### [design] Singleton blind spot across the entire analytics + ML pipeline

After the `singleton_decks` split, every file below still queries only `decks`/`deck_cards`. Once commander/canlander decks are migrated, each will silently produce empty or wrong results for those formats:

- `src/analysis/compute_stats.py` — `get_scraped_formats` returns `SELECT DISTINCT format FROM decks`; commander disappears
- `src/analysis/group_query.py` — pair stats query joins `deck_cards → decks`; returns nothing for migrated decks
- `src/analysis/precompute_layout.py` — reads from `card_stats`/`card_layout` which are populated by `compute_stats`; same gap
- `src/deck-classification/features.py` — all three stream queries join `decks`; pipeline will see zero decks for commander
- `src/deck-builder/vocabulary.py` — vocabulary built from `decks`/`deck_cards`; vocab will be empty after migration
- `src/deck-builder/preprocess.py` — `_load_decks` joins `decks`/`deck_cards`; training data disappears after migration

### [bug] `deck_scraper.py` routes singleton formats to wrong tables

`deck_scraper.py` calls `CALL replace_deck_cards(...)` for every format including commander and highlanderCanadian. That procedure writes to `deck_cards`. After the `singleton_decks` split these decks should write to `singleton_decks.cards`. The single-machine scraper has not been updated.

---

## `src/analysis/compute_stats.py`

**[quality]** `_print_sample` hardcodes `heapq.nlargest(5, ...)` — the 5 is unrelated to any CLI parameter or constant. Any future caller who changes the sample size has to know to look here.

**[quality]** `load_card_stats` passes `fmt` three times in the params tuple (WHERE filter, WHERE filter again, literal SELECT column). A comment or named-param refactor would make this less fragile.

---

## `src/analysis/group_query.py`

**[design]** Both query round-trips join `deck_cards` to `decks` for format filtering. The `format` column added to `deck_cards` in V14 is ignored. `WHERE dc.format = %s AND dc.board = ANY(%s)` would replace the join.

**[quality]** `n_total` is computed independently in round-trip 1 and round-trip 2. The first result could be passed into the second query as a literal to avoid recomputing.

**[quality]** `sort_by` inserted directly into f-string SQL (`ORDER BY {sort_by} DESC`). Safe because argparse validates against `SORT_CHOICES`, but any future non-argparse caller would be a SQL injection vector.

---

## `src/analysis/precompute_layout.py`

**[quality]** Card row columns accessed by positional index (`r[0]`, `r[2]`) with no local documentation. You must read the SQL stored-function definition to know what each index means.

**[quality]** `_COLOR_BITS` redefined locally (line 24). `COLOR_BITS` already exists in `src/constants/moxfield.py`. If the bit assignments change, this file will not be updated.

---

## `src/analysis/query.py`

**[quality]** `sort_by` inserted directly into f-string SQL. Same concern as `group_query.py`.

**[quality]** `fuzzy_candidates` loads all card names from `card_stats` into a Python list (full table scan, unbounded allocation).

---

## `src/constants/moxfield.py`

No issues.

---

## `src/constants/env.py`

No issues.

---

## `src/deck-builder/config.py`

No issues.

---

## `src/deck-builder/model.py`

No issues. Design decisions (no positional encoding, weight tying) are well-documented.

---

## `src/deck-builder/preprocess.py`

**[bug]** `_load_decks` joins `decks`/`deck_cards` (`WHERE d.format = 'commander' AND d.status = 'done'`). After the migration of commander decks to `singleton_decks`, this returns zero rows. Training data pipeline breaks silently.

---

## `src/deck-builder/vocabulary.py`

**[bug]** All three queries (`card_rows`, `totals`, `freq_rows`) join `decks`/`deck_cards` filtering `format = 'commander'`. Same break as `preprocess.py` after migration.

---

## `src/deck-classification/classify.py`

**[bug]** `classify_deck_id` queries `deck_cards` for mainboard cards. After commander decks move to `singleton_decks`, cards are in `singleton_decks.cards` (JSONB), not `deck_cards`. Classification returns "No mainboard cards found" for all migrated decks.

**[quality]** `_load_embeddings` re-imports `sys` and `Path` inside the function body (lines 96–97), shadowing the module-level imports unnecessarily.

**[quality]** Broad `except Exception` in `_load_embeddings` (line 118) silently demotes to keystone-only classification on any failure — including programming errors. A model load failure that should surface as a traceback is printed as a warning and swallowed.

**[quality]** `_classify_l1` fallback (no embeddings) returns `confidence=0.0` with the same dict shape as a real classification result. Callers cannot distinguish "unclassified, fell back to largest archetype" from "classified with zero confidence."

---

## `src/deck-classification/config.py`

**[quality]** Hardcoded default password: `"postgresql://postgres:password@localhost/deckgen"`. A wrong default is worse than no default — it silently connects if the password matches, masking a missing env var.

**[quality]** `DECK_BUILDER_DIR = "../deck-builder"` is a relative path — only resolves correctly when running from `src/deck-classification/`.

---

## `src/deck-classification/features.py`

**[bug]** `_load_deck_ids`, `_load_card_data`, and `_stream_deck_cards` all query `decks`/`deck_cards`. After migration, the classification pipeline will see zero decks for commander.

**[quality]** Named cursor `"card_stream"` is hardcoded (line 94). If `_stream_deck_cards` were called twice concurrently on the same connection, the second call would fail. Not an issue today (single-threaded) but fragile.

**[quality]** `load_structural` and `load_presence` return `deck_ids` as their first element; callers in `pipeline.py` discard it (`_,`). The return value is vestigial.

---

## `src/deck-classification/keystone.py`

No issues.

---

## `src/deck-classification/level1.py`

**[quality]** `reduce` and `cluster` are public functions but never called externally — only from `run`. Should be `_reduce` and `_cluster` to match the private-function convention used in `level2.py`.

---

## `src/deck-classification/level2.py`

**[quality]** `results[cid]["variable_cards"]` stores the full card name list but nothing in `pipeline.py` or `keystone.py` reads it. If it's for debugging, a comment would clarify intent; otherwise it is dead data held in memory per cluster.

---

## `src/deck-classification/pipeline.py`

**[quality]** `_compute_or_load_features` calls `feat._load_deck_ids` (private function, leading-underscore convention violated by the caller) up to three times — once per feature type — when the `recompute` path is active. A single upfront load would be cleaner.

**[design]** `store.clear_format` commits before `write_archetypes` runs. If `write_archetypes` fails, the format's archetypes are permanently deleted with no rollback path. Both operations should be wrapped in the same transaction.

---

## `src/deck-classification/store.py`

**[design]** `clear_format` commits immediately (line 36: `conn.commit()`). See `pipeline.py` note above — this creates a window where archetypes for a format are absent from the DB.

**[quality]** `json.dumps(keystone_cards)` before inserting into a `JSONB` column (line 78). `psycopg2.extras.Json` accepts a native Python list and lets the driver handle serialization. Double-encoding works but is unnecessary.

---

## `src/distributed scraper/api.py`

**[design]** `report_error` only updates `decks` (line 358). Singleton decks that error stay in `claimed` state indefinitely. The claim timeout will reclaim them, but there is no explicit error state for `singleton_decks`.

**[quality]** `_API_KEY` raises `RuntimeError` at module import time if `API_KEY` is unset (lines 40–42). This makes the module untestable without the env var and prevents graceful startup error handling.

**[quality]** `get_cards` and `mark_card_swept` build column names via f-string (`legal_{format}`, `swept_{format}`). Validated against `ALL_FORMATS` above the query, so safe in practice, but a future change that removes the validation would be a SQL injection.

---

## `src/distributed scraper/central_node.py`

No issues after the `ALL_FORMATS` update.

---

## `src/distributed scraper/config.py`

No issues.

---

## `src/distributed scraper/db.py`

**[quality]** Module docstring says pool is "initialised once at import time" — actually lazy-initialized on the first `get_connection()` call.

---

## `src/distributed scraper/scraper_node.py`

**[quality]** `run_loop` exits immediately on an empty batch ("No claimable decks — exiting", line 193) but uses `--delay` only when the API errors. A transient empty queue causes a permanent exit rather than a sleep-and-retry. The `--delay` flag is effectively dead code in the normal empty-queue case.

**[quality]** `api_report_error` catches `requests.RequestException` and returns without any log output (lines 86–87). If the error report itself fails, the operator gets no indication — the deck stalls in `claimed` state until the claim timeout with no visible trace.

---

## `src/scraping/deck_scraper.py`

**[bug]** Writes to `decks` and calls `CALL replace_deck_cards(...)` for all formats, including commander and highlanderCanadian. After the singleton split these should route to `singleton_decks`. This script has not been updated.

**[quality]** `get_cards_for_mode` builds `WHERE legal_{fmt} = 'legal'` via f-string. Safe via argparse choices, fragile pattern.

**[quality]** `--format` accepts only a single value; `central_node.py` accepts multiple. The two scrapers have diverged in CLI interface.

---

## `src/scraping/scryfall.py`

**[quality]** `download_bulk` reads the full response as `response.content` (bytes) then parses it with `json.loads`. For `all_cards` (~200 MB), this holds both the raw bytes and the parsed Python objects in memory simultaneously. Streaming to disk first or using `response.json()` with `stream=True` and `ijson` would halve peak memory.

---

## `src/scraping/scryfall_bulk_cards_importer.py`

**[quality]** `print_db_stats` hardcodes `legal_pauper` (line 209). If pauper is ever removed from `LEGAL_FORMATS`, this silently produces a wrong count or query error.

**[quality]** The per-row `"legal" if fmt == "highlanderCanadian" else legalities.get(fmt)` in `parse_card` is correct but redundant — V6 migration adds `legal_highlanderCanadian TEXT DEFAULT 'legal'`, so new rows would have the right value even if this branch were removed. Harmless but misleading.

**[quality]** Broad `except Exception` in the card parse loop counts errors but does not re-raise. A systematic bug in `parse_card` (e.g. wrong column after a `LEGAL_FORMATS` change) would produce a high error count with no traceback.

---

## `src/viz/visualize.py`

**[quality]** `load_ego` (lines 111/113): `if card_a in card_names` and `if card_b in card_names` guards are always true — the SQL already filters `AND card_a = ANY(%s) AND card_b = ANY(%s)`. Dead code.

**[quality]** `load_ego` and `load_focus` are near-identical: same query skeleton, same symmetry loop, same `defaultdict(list)` pattern. They differ only in selected columns and the per-card top-N cap in `load_ego`. Could be one function with an optional limit parameter.

**[quality]** Manifest merge silently swallows a malformed `manifest.json` with `except Exception: pass` (lines ~280–283). A corrupt manifest is invisibly replaced with an empty list, dropping all previously exported formats from the output manifest with no warning.

**[quality]** `color_mask_from_identity` (lines 46–48) reimplements `encode_colors` from `src/constants/moxfield.py`. Can be replaced with a direct import.

---

## `src/database/migrations/`

All migrations use `IF EXISTS` / `IF NOT EXISTS` guards and are versioned correctly. No issues.
