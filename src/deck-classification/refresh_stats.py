"""
refresh_stats.py — Fast replacement for the refresh_singleton_format_stats stored proc.

Uses scipy sparse matrix multiplication (M.T @ M) to compute card co-occurrence,
replacing the SQL self-join that generates O(N_decks × cards²) staging rows and does
not scale past a few hundred thousand decks.

At 5.38M commander decks × 99 non-land cards/deck the SQL approach produces ~26 billion
staging rows.  The sparse multiply handles the same data in a single BLAS call over
~530M non-zero entries.

Peak RAM: roughly 5–8 GB for commander at current scale.  If the pipeline.py presence
cache exists for the format it is reused, skipping the expensive DB streaming pass.

Works for both singleton and regular formats; the distinction is handled transparently
by features.py.

Usage:
    python refresh_stats.py --format commander
    python refresh_stats.py --format pauper --min-cooccur 5
    python refresh_stats.py --format commander --min-card-decks 50 --recompute
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

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1]))

import features as feat
from config import DATABASE_URL
from constants.env import load_env
from constants.mtg import ALL_FORMATS


# ---------------------------------------------------------------------------
# DB write helpers
# ---------------------------------------------------------------------------

def _write_card_stats(
    cur,
    fmt: str,
    card_vocab: list[str],
    deck_counts: np.ndarray,
    avg_qtys: np.ndarray,
    total_decks: int,
) -> int:
    table = f"{fmt}_card_stats"
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
    fmt: str,
    kept_vocab: list[str],
    kept_counts: np.ndarray,
    cooccur_upper: sp.csr_matrix,
    total_decks: int,
    min_cooccur: int,
    batch_size: int = 50_000,
) -> int:
    table = f"{fmt}_card_pair_stats"

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


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    fmt: str,
    min_cooccur: int = 5,
    min_card_decks: int = 20,
    recompute: bool = False,
) -> None:
    load_env()
    db_url = os.environ.get("DATABASE_URL", DATABASE_URL)
    t0     = time.monotonic()

    print(f"refresh_stats  format={fmt}  min_cooccur={min_cooccur}"
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
        n_cards = _write_card_stats(cur, fmt, card_vocab, deck_counts, avg_qtys, N)
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

    del presence, counts  # release memory before the multiply

    # ── 4. Sparse co-occurrence via M.T @ M ───────────────────────────────────
    print(f"\nComputing co-occurrence matrix ({n_kept:,} × {n_kept:,})...")
    t_mul   = time.monotonic()
    cooccur = M.T @ M
    print(f"  Matrix multiply done in {time.monotonic() - t_mul:.1f}s")

    del M

    cooccur_upper = sp.triu(cooccur, k=1)  # upper triangle only, diagonal excluded
    del cooccur

    # ── 5. Write pair stats ───────────────────────────────────────────────────
    print("\nWriting pair stats...")
    n_pairs = _write_pair_stats(
        conn, fmt, kept_vocab, kept_counts, cooccur_upper, N, min_cooccur,
    )

    conn.close()
    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.1f}s — {n_cards:,} card stats, {n_pairs:,} pair stats")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recompute card_stats and card_pair_stats for a format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python refresh_stats.py --format commander
  python refresh_stats.py --format pauper --min-cooccur 5 --min-card-decks 10
  python refresh_stats.py --format commander --recompute   # ignore presence cache
""",
    )
    parser.add_argument("--format", "-f", required=True, choices=ALL_FORMATS,
                        help="MTG format to recompute stats for.")
    parser.add_argument("--min-cooccur", type=int, default=5,
                        help="Minimum co-occurrence count to include a pair (default 5).")
    parser.add_argument("--min-card-decks", type=int, default=20,
                        help="Exclude cards from pair computation that appear in fewer than"
                             " this many decks (default 20). Does not affect card_stats output.")
    parser.add_argument("--recompute", action="store_true",
                        help="Re-stream deck-card data from DB even if a presence cache exists.")

    args = parser.parse_args()
    run(args.format, args.min_cooccur, args.min_card_decks, args.recompute)


if __name__ == "__main__":
    main()
