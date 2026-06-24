"""
refresh_stats.py — Card co-occurrence statistics and 2D card layout for all formats.

Replaces two separate steps that previously required a Postgres stored procedure
(refresh_format_stats / refresh_singleton_format_stats) and a separate visualize.py
layout pass:

  1. Streams deck-card presence from DB (or reuses the pipeline.py cache).
  2. Computes co-occurrence via sparse matrix multiplication (M.T @ M) in Python.
  3. Derives lift, PMI, jaccard, and confidence entirely in NumPy.
  4. Writes {format}_card_stats and {format}_card_pair_stats.
  5. Runs a card-space UMAP on the in-memory jaccard matrix and writes
     {format}_card_layout — no extra DB round-trip.

At 5.38M commander decks × 99 non-land cards/deck the SQL self-join approach
produced ~26 billion staging rows. The sparse multiply handles the same data in a
single BLAS call over ~530M non-zero entries.

Peak RAM: roughly 5–8 GB for commander at current scale. If the pipeline.py presence
cache exists for the format it is reused, skipping the expensive DB streaming pass.

Works for both singleton and regular formats.

Usage:
    python refresh_stats.py --format commander
    python refresh_stats.py                              # run all formats
    python refresh_stats.py --format pauper --min-cooccur 5 --min-card-decks 10
    python refresh_stats.py --format commander --recompute   # ignore presence cache
    python refresh_stats.py --format commander --skip-layout # stats only, no UMAP
"""

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
import scipy.sparse as sp
import umap

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1]))

import features as feat
from config import DATABASE_URL
from constants.env import load_env
from constants.mtg import ALL_FORMATS, COLOR_BITS, format_to_table_prefix


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_color_mask(mask: int | None) -> str:
    if not mask:
        return ""
    return ",".join(c for c, b in COLOR_BITS.items() if mask & b)


def _has_legality_column(conn, fmt: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
            " WHERE table_name = 'cards' AND column_name = %s)",
            (f"legal_{fmt}",),
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# DB write helpers
# ---------------------------------------------------------------------------

def _write_card_stats(
    cur,
    prefix: str,
    card_vocab: list[str],
    deck_counts: np.ndarray,
    avg_qtys: np.ndarray,
    total_decks: int,
) -> int:
    table = f"{prefix}_card_stats"
    cur.execute(f"TRUNCATE {table}")
    rows = [
        (
            card_vocab[i],
            int(deck_counts[i]),
            int(total_decks),
            float(deck_counts[i]) / total_decks,
            float(avg_qtys[i]),
        )
        for i in range(len(card_vocab))
        if deck_counts[i] > 0
    ]
    psycopg2.extras.execute_values(
        cur,
        f"INSERT INTO {table}"
        f" (card_name, deck_count, total_decks, inclusion_rate, avg_quantity)"
        f" VALUES %s",
        rows,
    )
    return len(rows)


def _write_pair_stats(
    conn,
    prefix: str,
    kept_vocab: list[str],
    kept_counts: np.ndarray,
    cooccur_upper: sp.csr_matrix,
    total_decks: int,
    min_cooccur: int,
    batch_size: int = 50_000,
) -> int:
    table = f"{prefix}_card_pair_stats"

    coo  = cooccur_upper.tocoo()
    mask = coo.data >= min_cooccur
    ai   = coo.row[mask]
    bi   = coo.col[mask]
    c    = coo.data[mask].astype(np.float64)

    na = kept_counts[ai].astype(np.float64)
    nb = kept_counts[bi].astype(np.float64)
    n  = float(total_decks)

    lift    = (c * n) / (na * nb)
    pmi     = np.log(lift)
    jaccard = c / (na + nb - c)
    conf_ab = c / na
    conf_ba = c / nb

    vocab_arr = np.asarray(kept_vocab)
    names_a   = vocab_arr[ai]
    names_b   = vocab_arr[bi]
    c_int     = c.astype(np.int32)

    n_pairs = len(c)
    print(f"  {n_pairs:,} pairs with cooccurrence >= {min_cooccur}")

    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {table}")
    conn.commit()

    written = 0
    for start in range(0, n_pairs, batch_size):
        end  = min(start + batch_size, n_pairs)
        rows = list(zip(
            names_a[start:end].tolist(),
            names_b[start:end].tolist(),
            c_int[start:end].tolist(),
            lift[start:end].tolist(),
            pmi[start:end].tolist(),
            jaccard[start:end].tolist(),
            conf_ab[start:end].tolist(),
            conf_ba[start:end].tolist(),
        ))
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO {table}"
                f" (card_a, card_b, cooccurrence_count, lift, pmi, jaccard,"
                f"  confidence_a_to_b, confidence_b_to_a) VALUES %s",
                rows,
            )
        conn.commit()
        written += len(rows)
        if written % 500_000 == 0:
            print(f"    {written:,} / {n_pairs:,} pairs written...")

    return written


def _print_sample(conn, prefix: str, limit: int = 5) -> None:
    table = f"{prefix}_card_pair_stats"
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT card_a, card_b, cooccurrence_count, lift, jaccard"
            f" FROM {table} ORDER BY lift DESC LIMIT %s",
            (limit,),
        )
        rows = cur.fetchall()
    if rows:
        print("  Top pairs by lift:")
        for r in rows:
            print(
                f"    {r[0]:35s} + {r[1]:35s}"
                f"  cooccur={r[2]:3d}  lift={r[3]:.2f}  jaccard={r[4]:.3f}"
            )


# ---------------------------------------------------------------------------
# Card-space UMAP layout
# ---------------------------------------------------------------------------

def _run_layout(
    conn,
    fmt: str,
    prefix: str,
    card_vocab: list[str],
    deck_counts: np.ndarray,
    cooccur_upper: sp.csr_matrix,
    kept_vocab: list[str],
    kept_counts: np.ndarray,
    min_decks: int,
    min_cooccur: int,
) -> None:
    # Legality filter: some formats have a legal_{fmt} column on cards;
    # singleton formats like highlanderCanadian do not (all cards pass).
    has_legal = _has_legality_column(conn, fmt)
    if has_legal:
        with conn.cursor() as cur:
            cur.execute(f"SELECT card_name FROM cards WHERE legal_{fmt} = 'legal'")
            legal_set: set[str] | None = {row[0] for row in cur.fetchall()}
    else:
        legal_set = None

    layout_cards: list[str] = [
        name for i, name in enumerate(card_vocab)
        if deck_counts[i] >= min_decks and (legal_set is None or name in legal_set)
    ]

    if len(layout_cards) < 2:
        print("  Too few cards for layout, skipping.")
        return

    print(f"  {len(layout_cards)} cards (deck_count >= {min_decks})")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT card_name, ci_mask FROM cards WHERE card_name = ANY(%s)",
            (layout_cards,),
        )
        color_ids = {row[0]: _decode_color_mask(row[1]) for row in cur.fetchall()}

    # Build sparse jaccard similarity matrix from the in-memory co-occurrence data.
    # Vectorised: filter by min_cooccur first, then by membership in layout_cards.
    layout_set = set(layout_cards)
    card_idx   = {name: i for i, name in enumerate(layout_cards)}
    n = len(layout_cards)

    coo          = cooccur_upper.tocoo()
    cooccur_mask = coo.data >= min_cooccur
    ri   = coo.row[cooccur_mask]
    ci   = coo.col[cooccur_mask]
    data = coo.data[cooccur_mask].astype(np.float64)

    vocab_arr = np.asarray(kept_vocab)
    names_a   = vocab_arr[ri]
    names_b   = vocab_arr[ci]

    in_layout = np.isin(names_a, list(layout_set)) & np.isin(names_b, list(layout_set))
    names_a   = names_a[in_layout]
    names_b   = names_b[in_layout]
    na        = kept_counts[ri[in_layout]].astype(np.float64)
    nb        = kept_counts[ci[in_layout]].astype(np.float64)
    c         = data[in_layout]
    jac_vals  = c / (na + nb - c)

    i_idx = np.array([card_idx[name] for name in names_a])
    j_idx = np.array([card_idx[name] for name in names_b])

    row_idx = np.concatenate([i_idx, j_idx])
    col_idx = np.concatenate([j_idx, i_idx])
    vals    = np.concatenate([jac_vals, jac_vals])

    X = sp.csr_matrix((vals, (row_idx, col_idx)), shape=(n, n))

    n_neighbors = min(15, n - 1)
    print(f"  Running UMAP ({n} cards, n_neighbors={n_neighbors})...", end=" ", flush=True)
    reducer = umap.UMAP(
        n_components=2,
        metric="cosine",
        n_neighbors=n_neighbors,
        min_dist=0.05,
        random_state=42,
        low_memory=True,
        verbose=False,
    )
    embedding = reducer.fit_transform(X)
    print("done")

    rows = [
        (
            layout_cards[i],
            float(embedding[i, 0]),
            float(embedding[i, 1]),
            color_ids.get(layout_cards[i], ""),
        )
        for i in range(n)
    ]

    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {prefix}_card_layout")
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO {prefix}_card_layout (card_name, x, y, color_identity) VALUES %s",
            rows,
        )
    conn.commit()
    print(f"  Layout stored: {len(rows)} cards.")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    fmt: str,
    min_cooccur: int = 5,
    min_card_decks: int = 20,
    recompute: bool = False,
    layout_min_decks: int = 5,
    layout_min_cooccur: int = 20,
    skip_layout: bool = False,
) -> None:
    load_env()
    db_url = os.environ.get("DATABASE_URL", DATABASE_URL)
    prefix = format_to_table_prefix(fmt)
    t0     = time.monotonic()

    print(f"\nrefresh_stats  format={fmt}  min_cooccur={min_cooccur}"
          f"  min_card_decks={min_card_decks}")

    conn = psycopg2.connect(db_url)

    # ── 1. Load / build presence matrix ───────────────────────────────────────
    cached = feat.cache_exists(fmt)
    if not recompute and cached["presence"]:
        print("\nLoading presence matrix from cache...")
        deck_ids, presence, counts, card_vocab = feat.load_presence(fmt)
    else:
        print("\nBuilding presence matrix from DB (streaming, may take several minutes)...")
        deck_ids = feat.load_deck_ids(conn, fmt)
        presence, counts, card_vocab = feat.compute_card_presence(deck_ids, conn, fmt)
        feat.save_presence(fmt, deck_ids, presence, counts, card_vocab)

    N = len(deck_ids)
    V = len(card_vocab)
    print(f"  {N:,} decks  ×  {V:,} unique non-land cards")

    # ── 2. Card-level stats (all cards, no threshold) ─────────────────────────
    deck_counts = np.asarray(presence.sum(axis=0)).flatten().astype(np.int32)

    if counts is not None:
        qty_sums = np.asarray(counts.sum(axis=0)).flatten()
        avg_qtys = np.where(deck_counts > 0, qty_sums / np.maximum(deck_counts, 1), 0.0)
    else:
        avg_qtys = np.ones(V, dtype=np.float32)

    print("\nWriting card stats...")
    with conn.cursor() as cur:
        n_cards = _write_card_stats(cur, prefix, card_vocab, deck_counts, avg_qtys, N)
    conn.commit()
    print(f"  Wrote {n_cards:,} rows")

    # ── 3. Filter vocabulary for co-occurrence ────────────────────────────────
    # Cards below min_card_decks can never reach min_cooccur with any partner
    # and would bloat the matrix with columns that produce no output rows.
    keep_mask   = deck_counts >= min_card_decks
    n_kept      = int(keep_mask.sum())
    n_dropped   = V - n_kept
    print(f"\nCo-occurrence filter: keeping {n_kept:,} cards"
          f" (≥{min_card_decks} decks), dropping {n_dropped:,}")

    M           = presence[:, keep_mask].astype(np.float32)
    kept_vocab  = [card_vocab[i] for i, k in enumerate(keep_mask) if k]
    kept_counts = deck_counts[keep_mask]

    del presence, counts

    # ── 4. Sparse co-occurrence via M.T @ M ───────────────────────────────────
    print(f"\nComputing co-occurrence matrix ({n_kept:,} × {n_kept:,})...")
    t_mul   = time.monotonic()
    cooccur = M.T @ M
    print(f"  Matrix multiply done in {time.monotonic() - t_mul:.1f}s")

    del M

    cooccur_upper = sp.triu(cooccur, k=1)
    del cooccur

    # ── 5. Write pair stats ───────────────────────────────────────────────────
    print("\nWriting pair stats...")
    n_pairs = _write_pair_stats(
        conn, prefix, kept_vocab, kept_counts, cooccur_upper, N, min_cooccur,
    )
    _print_sample(conn, prefix)

    # ── 6. Card-space UMAP layout ─────────────────────────────────────────────
    if not skip_layout:
        print(f"\nComputing card layout"
              f" (layout_min_decks={layout_min_decks}"
              f"  layout_min_cooccur={layout_min_cooccur})...")
        _run_layout(
            conn, fmt, prefix,
            card_vocab, deck_counts,
            cooccur_upper, kept_vocab, kept_counts,
            layout_min_decks, layout_min_cooccur,
        )

    conn.close()
    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.1f}s — {n_cards:,} card stats, {n_pairs:,} pair stats")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recompute card stats, pair stats, and card layout for one or all formats.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python refresh_stats.py --format commander
  python refresh_stats.py                               # run all formats
  python refresh_stats.py --format pauper --min-cooccur 5 --min-card-decks 10
  python refresh_stats.py --format commander --recompute   # ignore presence cache
  python refresh_stats.py --format commander --skip-layout # stats only, no UMAP
""",
    )
    parser.add_argument(
        "--format", "-f", dest="format", default=None,
        help=f"MTG format to process. Omit to run all formats. "
             f"Choices: {', '.join(ALL_FORMATS)}",
    )
    parser.add_argument(
        "--min-cooccur", type=int, default=5,
        help="Minimum co-occurrence count to store a pair in pair_stats (default 5).",
    )
    parser.add_argument(
        "--min-card-decks", type=int, default=20,
        help="Exclude cards from pair computation that appear in fewer than this many"
             " decks (default 20). Does not affect card_stats output.",
    )
    parser.add_argument(
        "--layout-min-decks", type=int, default=5,
        help="Minimum deck count for a card to appear in the layout (default 5).",
    )
    parser.add_argument(
        "--layout-min-cooccur", type=int, default=20,
        help="Minimum co-occurrence count for a pair to form a layout edge (default 20).",
    )
    parser.add_argument(
        "--recompute", action="store_true",
        help="Re-stream deck-card data from DB even if a presence cache exists.",
    )
    parser.add_argument(
        "--skip-layout", action="store_true",
        help="Skip the UMAP layout step (stats only).",
    )

    args = parser.parse_args()

    if args.format is not None and args.format not in ALL_FORMATS:
        print(f"ERROR: '{args.format}' is not a valid format.")
        print(f"  Choices: {', '.join(ALL_FORMATS)}")
        sys.exit(1)

    formats = [args.format] if args.format else list(ALL_FORMATS)

    for fmt in formats:
        run(
            fmt,
            min_cooccur=args.min_cooccur,
            min_card_decks=args.min_card_decks,
            recompute=args.recompute,
            layout_min_decks=args.layout_min_decks,
            layout_min_cooccur=args.layout_min_cooccur,
            skip_layout=args.skip_layout,
        )


if __name__ == "__main__":
    main()
