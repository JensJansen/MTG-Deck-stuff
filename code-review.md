# Code Review

Covers all Python source under `src/` except `src/deck-builder/` (ML training pipeline — not reviewed).
Findings are sorted by subdirectory, then by severity within each section.

---

## Cross-cutting / Global

### `_load_env` duplicated across 7+ files
`_load_env` and its companion `_ENV_TEMPLATE` are copy-pasted verbatim into:
`deck_scraper.py`, `scryfall_bulk_cards_importer.py`, `compute_stats.py`, `precompute_layout.py`, `query.py`, `group_query.py`, `visualize.py`, `pipeline.py` (classification).
This is the largest smell in the codebase. A single `src/utils/env.py` (or addition to `src/constants/`) would eliminate all copies.

~~**`moxfield_search_name` duplicated**~~ — fixed, moved to `src/constants/moxfield.py`; both callers import from there.

~~**`parse_deck` duplicated**~~ — fixed, moved to `src/constants/moxfield.py`; both callers import from there.

### `get_formats` has three independent implementations
- `compute_stats.py` — queries `decks` table
- `precompute_layout.py` — queries `card_stats` table
- `visualize.py` — queries `card_layout` table

All do `SELECT DISTINCT format ... ORDER BY format` from different tables. The different source tables are intentional (each script works from its own data), but the function name and signature are identical across all three, which is confusing. At minimum they should have distinct names or explicit docstrings explaining which table they pull from.

---

## src/analysis/

### `compute_stats.py`

**`DEFAULT_BOARDS` duplicated** (`group_query.py` defines the same frozenset independently). Should be imported from a shared location.

**`_print_sample` hardcodes 5** (line 212 — `heapq.nlargest(5, ...)`). This is not tied to any CLI parameter and will silently not show all top pairs if the caller passes a different limit in the future.

**`load_card_stats` positional params are easy to mis-count** — `fmt` appears three times in the params tuple (line 90) with different roles (WHERE filter, WHERE filter again, literal SELECT column). Switching to named parameters or a comment would make this less fragile.

### `group_query.py`

**`SORT_CHOICES` duplicated from `query.py`** — defined independently in both files. Should be imported or live in a shared constants location.

**`_load_env` imported as a private name** (line 21: `from query import _load_env`). Importing private-by-convention functions from sibling modules is a code smell. `_load_env` should be a public utility.

**`n_total` CTE is computed twice** — the first DB round-trip (lines 64-85) computes `n_total` to check `n_group`. The second round-trip (lines 92-148) recomputes the same `n_total` CTE. If `n_group > 0`, the first query's `n_total` result is discarded and recomputed. Could pass `n_total` into the second query as a parameter.

**`sort_by` used directly in f-string SQL** (line 139: `ORDER BY {sort_by} DESC`). Safe because `sort_by` is constrained by `choices=SORT_CHOICES` in argparse, but the pattern is fragile — any future code path that calls `query_group_stats` without argparse validation would be a SQL injection.

### `precompute_layout.py`

**`_COLOR_BITS` redefined locally** (line 24). `COLOR_BITS` already exists in `src/constants/moxfield.py`. This is a divergence point — if the bit assignments ever change, this file won't be updated.

**`decode_colors`** is a local function that inverts `encode_colors` from constants. It has no counterpart in constants and is only used here. Fine as-is but worth noting the asymmetry.

**Plain Python lists for COO construction** (lines 137-142 in `compute_umap_layout`): `row_idx`, `col_idx`, `vals` are Python lists. Smaller scale than the deck presence matrix issue but the same pattern.

### `query.py`

~~**`sort_col` alias is a no-op**~~ — fixed, `sort_col` removed and `sort_by` used directly.

**`sort_by` in f-string SQL** (line 162: `ORDER BY {sort_by} DESC`). Safe via argparse but fragile pattern — any future non-argparse caller would be a SQL injection risk.

**`fuzzy_candidates` loads all card names into memory** (line 70-71). For large datasets this is a full table scan into a Python list. Acceptable in practice but an unbounded allocation.

---

## src/constants/

### `moxfield.py`
No issues. Short and well-scoped.

---

## src/deck-classification/

### `classify.py`

**Redundant imports inside `_load_embeddings`** (lines 96-97). `sys` and `Path` are already imported at module level but are re-imported inside the function body. The inner imports shadow the outer ones unnecessarily.

**Broad `except Exception` in `_load_embeddings`** (line 118). Any failure during model loading — including programming errors — is silently swallowed and printed as a warning. This masks real bugs.

**New DB connection per call in `classify_deck_id`** (line 153). Each call to `classify_deck_id` opens and closes its own connection. If used in a loop to classify many decks this is expensive. `DeckClassifier` also opens a connection in `_load_archetypes` at init time. No connection reuse or pooling.

**`_classify_l1` fallback produces misleading output** — when no embeddings are available, it returns the largest archetype with `confidence=0.0`. Callers cannot distinguish "classified with low confidence" from "not classified at all — fell back to heuristic." The return structure is identical in both cases.

**`store.py` double-encodes keystones** — `store.py` calls `json.dumps(keystone_cards)` before inserting into a `JSONB` column. psycopg2 would accept a native Python list directly via `psycopg2.extras.Json`. The double-encoding works (Postgres parses the string as JSONB) but is unnecessary.

### `config.py`

**Hardcoded default password** (line 6): `"postgresql://postgres:password@localhost/deckgen"`. A wrong default is worse than no default — it will silently connect if someone has that password, masking missing env var configuration.

### `features.py`

**Named cursor `"card_stream"` is not unique** (line 94). psycopg2 named server-side cursors must have a unique name per connection. If `_stream_deck_cards` were ever called twice concurrently on the same connection, the second call would fail with a cursor name conflict. Not an issue today (single-threaded pipeline) but fragile.

**`load_structural` and `load_presence` return `deck_ids`** as their first element, but callers in `pipeline.py` always discard this value (using `_` or ignoring it). The return value is vestigial.

### `level1.py`

**`reduce` and `cluster` are public but never called externally**. They're only called from `run`. Should be `_reduce` and `_cluster` to match the private-function convention used in `level2.py`.

### `level2.py`

**`variable_cards` stored in results but unused downstream** — `results[cid]["variable_cards"]` stores the full list of card names, but nothing in `pipeline.py` or `keystone.py` reads it. If it's for debugging only, a comment to that effect would help; otherwise it's dead data being kept in memory.

### `pipeline.py`

**`_compute_or_load_features` calls `_load_deck_ids` up to three times** — the embeddings, structural, and presence branches each have their own `deck_ids = feat._load_deck_ids(conn, fmt)` call, with overlap depending on which caches exist. In the common path (all cached), it's called once (embeddings branch, line 65). In the recompute path it can be called multiple times. The logic is hard to follow; a single upfront load would be cleaner.

**`_compute_or_load_features` shadows outer `deck_ids`** — line 72 (`if embeddings is None: deck_ids = feat._load_deck_ids(...)`) conditionally reassigns `deck_ids` inside the structural block, but `deck_ids` may already be set from the embeddings block. The conditional is often redundant and confusing.

---

## src/distributed scraper/

### `api.py`

**New DB connection per request, no pooling** — every endpoint (`get_cards`, `post_decks`, `claim_batch`, `submit_cards`, `report_error`) opens and closes a fresh `psycopg2` connection. Under any meaningful load this will exhaust connection slots and is slow. FastAPI is designed to work with connection pools (`psycopg2.pool.ThreadedConnectionPool` or `asyncpg`).

**`report_error` silently drops the `detail` field** — `ErrorReport.detail` is accepted in the request model but never written to the database (line 302-306 only sets `status = 'error'`). This is either a bug or the field should be removed from the model.

**f-string SQL for format filter** (line 126: `f"SELECT card_name FROM cards WHERE legal_{format} = 'legal' ORDER BY card_name"`). Validated against `LEGAL_FORMATS` on the line above, so safe in practice. But if the validation were ever skipped or bypassed, this is SQL injection.

**`_API_KEY` raises at import time** (lines 36-38). `RuntimeError` is raised during module import if `API_KEY` is not set. This makes the module untestable without the env var and prevents any form of graceful startup error handling.

### `central_node.py`

**`--format` multi-value change not applied to `deck_scraper.py`** — `central_node.py` was updated to accept multiple `--format` flags, but `deck_scraper.py` (the single-machine scraper covering the same logic) still uses a single `--format` arg. These have diverged.

### `scraper_node.py`

**`run_loop` exits immediately on empty batch** (line 172: `"No claimable decks — exiting."`). If the API is transiently slow or another node just claimed the last batch, this exits permanently rather than sleeping and retrying. Contrast with the error case (line 167-169) which correctly sleeps and retries.

**`api_report_error` swallows all exceptions silently** (lines 86-87). If the API is down while a deck is failing, the error goes unrecorded and the deck stalls in `claimed` state until the claim timeout. This is documented in the module docstring but not surfaced to the operator at all (no log line on the swallowed exception).

---

## src/scraping/

### `deck_scraper.py`

**`--format` is single-value** — unlike `central_node.py` which now supports multiple `--format` flags, `deck_scraper.py` still only accepts one format at a time (line 386-388). The two scrapers have diverged in their CLI interface.

**`replace_deck_cards` always deletes all cards and re-inserts** (lines 136-146) even when card contents haven't changed. `deck_needs_card_fetch` guards entry into this function (checking `updated_at_utc`), so wasted work only happens when a deck was genuinely updated. Acceptable, but worth noting.

**`upsert_deck` commits are per-deck inside `_sweep_one_card`** (line 319: `conn.commit()`). Each page commits deck metadata row by row. This is many small commits rather than one per page. The card-fetch commit (line 323) is also per-page. Fine for durability, but creates a lot of transaction overhead.

### `scryfall.py`

**`download_bulk` loads entire JSON into RAM as bytes then parses** (lines 111-115). For `all_cards` (~110k cards, typically 200MB+ JSON), this means holding both the raw bytes and the parsed Python objects in memory simultaneously. Streaming to disk first then loading would halve peak memory.

**`ScryfallError` is defined but only raised internally** — external callers who catch it must import it, but there's no re-export or documented public API. Fine for a single-file client, but if the class grows it should be explicitly exported.

### `scryfall_bulk_cards_importer.py`

**`UPSERT_SQL` computed at module import** via `_build_upsert_sql()`. This is fine in isolation but means any module that imports from this file (e.g. in tests) triggers the SQL string construction. No real cost, but unusual.

**`print_db_stats` hardcodes `legal_pauper`** (line 209). If `pauper` is ever removed from `LEGAL_FORMATS`, this query silently fails (column doesn't exist). Should use a format from `LEGAL_FORMATS[0]` or be removed.

**Broad `except Exception` in `import_cards`** (line 164). Parse errors for individual cards are caught and counted but not re-raised. A systematic bug in `parse_card` (e.g. wrong column order after a `LEGAL_FORMATS` change) would silently produce a high error count rather than a traceback.

---

## src/viz/

### `visualize.py`

**`load_ego` redundant membership checks** (lines 146-148). The SQL query already filters `card_a = ANY(card_list) AND card_b = ANY(card_list)`, so the `if card_a in card_names` and `if card_b in card_names` guards in Python are always true. They can be removed.

**`load_ego` and `load_focus` are near-identical** — both query `card_pair_stats` with the same filters, the same `card_a`/`card_b` symmetry loop, and the same `defaultdict(list)` pattern. They differ only in selected columns and the per-card top-N cap in `load_ego`. Could be unified into one query with optional top-N truncation.

**Manifest merge silently ignores corruption** (lines 280-283). If `manifest.json` is malformed, `except Exception: pass` proceeds with `existing = []`, silently dropping previously exported formats from the manifest. Should at least log a warning.

**`color_mask_from_identity` reimplements `encode_colors`** (lines 46-48). `encode_colors` in `constants/moxfield.py` does the same conversion. This function can be replaced with a direct import.

---

## src/database/

### `migrate.ps1`
No issues after the linting removal. Clean.

### Migrations
All migrations are versioned correctly and use `IF EXISTS` / `IF NOT EXISTS` guards. No issues.
