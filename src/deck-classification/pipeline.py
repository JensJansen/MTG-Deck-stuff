"""
End-to-end archetype classification pipeline.

    python pipeline.py --format pauper
    python pipeline.py --format commander
    python pipeline.py --format pauper --recompute-features   # force re-extraction from DB

Steps per format:
  1. Extract / load cached features (embeddings, pip volumes, CMC, card presence)
  2. Level 1: UMAP + HDBSCAN → coarse archetype labels
  3. Level 2: per-cluster sub-HDBSCAN on variable cards + pip + CMC
  4. Keystone card rules per sub-archetype
  5. Persist archetype records and deck assignments to Postgres
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1]))

import features as feat
import keystone
import level1
import level2
import store
from config import DATA_DIR, DATABASE_URL, MODEL_CHECKPOINT
from constants.env import load_env


def _compute_or_load_features(fmt: str, conn, recompute: bool):
    os.makedirs(DATA_DIR, exist_ok=True)
    cached = feat.cache_exists(fmt)

    # ── Embeddings ─────────────────────────────────────────────────────────────
    checkpoint = os.environ.get("MODEL_CHECKPOINT", MODEL_CHECKPOINT)
    if checkpoint and (recompute or not cached["embeddings"]):
        print("\n── Features: embeddings ──────────────────────────────────────────")
        deck_ids   = feat._load_deck_ids(conn, fmt)
        embeddings = feat.compute_embeddings(deck_ids, conn, fmt, checkpoint)
        feat.save_embeddings(fmt, deck_ids, embeddings)
    elif cached["embeddings"]:
        print(f"  Embeddings cache found → loading")
        deck_ids, embeddings = feat.load_embeddings(fmt)
    else:
        print("  No MODEL_CHECKPOINT set and no cached embeddings — using card presence only")
        deck_ids   = feat._load_deck_ids(conn, fmt)
        embeddings = None

    # ── Structural features (pip volume, CMC) ──────────────────────────────────
    if recompute or not cached["features"]:
        print("\n── Features: pip volumes + CMC distributions ─────────────────────")
        if embeddings is None:
            deck_ids = feat._load_deck_ids(conn, fmt)
        pip_volumes, cmc_dists = feat.compute_structural_features(deck_ids, conn, fmt)
        feat.save_structural(fmt, deck_ids, pip_volumes, cmc_dists)
    else:
        print(f"  Structural cache found → loading")
        _, pip_volumes, cmc_dists = feat.load_structural(fmt)

    # ── Card presence (sparse matrix) ──────────────────────────────────────────
    if recompute or not cached["presence"]:
        print("\n── Features: card presence matrix ────────────────────────────────")
        presence, card_vocab = feat.compute_card_presence(deck_ids, conn, fmt)
        feat.save_presence(fmt, deck_ids, presence, card_vocab)
    else:
        print(f"  Presence cache found → loading")
        _, presence, card_vocab = feat.load_presence(fmt)

    return deck_ids, embeddings, pip_volumes, cmc_dists, presence, card_vocab


def run(fmt: str, recompute: bool = False) -> None:
    load_env()
    db_url  = os.environ.get("DATABASE_URL", DATABASE_URL)
    run_id  = datetime.now(timezone.utc).isoformat()
    conn    = psycopg2.connect(db_url)

    try:
        # ── 1. Features ────────────────────────────────────────────────────────
        deck_ids, embeddings, pip_volumes, cmc_dists, presence, card_vocab = \
            _compute_or_load_features(fmt, conn, recompute)

        N = len(deck_ids)
        print(f"\nFormat: {fmt}  |  {N:,} decks  |  {presence.shape[1]:,} unique non-land cards")

        # ── 2. Level 1 ─────────────────────────────────────────────────────────
        # Fall back to card presence matrix if no embeddings available.
        # Sparse matrix is passed directly — no .toarray() needed for cosine UMAP.
        cluster_input = embeddings if embeddings is not None else presence
        reduced, l1_labels, l1_probs = level1.run(cluster_input, N, fmt)
        del reduced
        l1_summary = level1.cluster_summary(l1_labels)

        # ── 3. Level 2 ─────────────────────────────────────────────────────────
        l2_results = level2.run(l1_labels, presence, card_vocab, pip_volumes, cmc_dists)

        # ── 4. Keystone cards ──────────────────────────────────────────────────
        print("\n── Keystone cards ────────────────────────────────────────────────")
        keystone_map: dict[tuple, list] = {}
        for cid, l2 in l2_results.items():
            if not l2["eligible"] or l2["n_subclusters"] < 2:
                continue
            ks = keystone.generate_for_cluster(
                presence,
                card_vocab,
                l2["deck_indices"],
                l2["sub_labels"],
            )
            for sid, rules in ks.items():
                keystone_map[(cid, sid)] = rules
                n_rules = len(rules)
                top     = rules[0]["card"] if rules else "—"
                print(f"  L1={cid} sub={sid}: {n_rules} keystones  top={top!r}")

        # ── 5. Build archetype records ─────────────────────────────────────────
        archetype_records = []
        assignments: list[tuple] = []

        for cid, count in l1_summary.items():
            l1_mask    = l1_labels == cid
            l1_indices = np.where(l1_mask)[0]
            centroid   = embeddings[l1_indices].mean(axis=0) if embeddings is not None else None

            archetype_records.append({
                "level":          1,
                "local_id":       cid,
                "parent_local":   None,
                "centroid":       centroid,
                "keystone_cards": None,
                "member_count":   count,
            })

            l2 = l2_results.get(cid, {})
            if l2.get("n_subclusters", 0) >= 2:
                sub_labels = l2["sub_labels"]
                sub_probs  = l2["sub_probs"]
                d_indices  = l2["deck_indices"]

                for sid in sorted(set(sub_labels[sub_labels >= 0])):
                    sub_mask   = sub_labels == sid
                    sub_global = d_indices[sub_mask]
                    sub_cent   = embeddings[sub_global].mean(axis=0) if embeddings is not None else None
                    archetype_records.append({
                        "level":          2,
                        "local_id":       (cid, sid),
                        "parent_local":   cid,
                        "centroid":       sub_cent,
                        "keystone_cards": keystone_map.get((cid, sid)),
                        "member_count":   int(sub_mask.sum()),
                    })

        del embeddings

        # ── 6. Persist ─────────────────────────────────────────────────────────
        print("\n── Storing results ───────────────────────────────────────────────")
        store.clear_format(conn, fmt)
        local_to_db = store.write_archetypes(conn, fmt, run_id, archetype_records)

        # Build assignment rows for every deck that received a label
        for global_idx, deck_id in enumerate(deck_ids):
            cid = int(l1_labels[global_idx])
            if cid < 0:
                continue
            l1_db_id = local_to_db.get((1, cid))
            if l1_db_id:
                assignments.append((deck_id, l1_db_id, 1, float(l1_probs[global_idx])))

            l2 = l2_results.get(cid)
            if l2 and l2.get("n_subclusters", 0) >= 2:
                # Find this deck's position in the sub-label array
                local_pos = np.searchsorted(l2["deck_indices"], global_idx)
                if local_pos < len(l2["deck_indices"]) and l2["deck_indices"][local_pos] == global_idx:
                    sid = int(l2["sub_labels"][local_pos])
                    if sid >= 0:
                        l2_db_id = local_to_db.get((2, (cid, sid)))
                        if l2_db_id:
                            assignments.append((deck_id, l2_db_id, 2, float(l2["sub_probs"][local_pos])))

        store.write_assignments(conn, assignments)

        # ── Summary ────────────────────────────────────────────────────────────
        n_l1     = sum(1 for r in archetype_records if r["level"] == 1)
        n_l2     = sum(1 for r in archetype_records if r["level"] == 2)
        n_noise  = int((l1_labels == -1).sum())
        print(f"\nDone — {n_l1} archetypes  {n_l2} sub-archetypes  {n_noise:,} unclassified decks")

    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deck archetype classification for a format.")
    parser.add_argument("--format", "-f", required=True, help="MTG format (e.g. pauper, commander)")
    parser.add_argument("--recompute-features", action="store_true",
                        help="Re-extract all features from DB even if cache exists")
    args = parser.parse_args()
    run(args.format, recompute=args.recompute_features)


if __name__ == "__main__":
    main()
