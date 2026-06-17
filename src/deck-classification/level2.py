"""
Level 2 — sub-archetype discovery within high-variance Level 1 clusters.

For each coarse cluster that shows high internal card-presence variance, we:
  1. Identify the high-variance cards (those with presence rate in [L2_VARIANCE_LO, L2_VARIANCE_HI]).
  2. Build an enriched feature matrix: high-variance card presence + pip volume + CMC distribution.
  3. Run a second HDBSCAN pass within the cluster.

Returns a dict mapping each Level 1 cluster_id that was split to its
sub-cluster assignment array and metadata.
"""
import sys
from pathlib import Path

import hdbscan
import numpy as np
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    L2_MIN_CLUSTER_ABS,
    L2_MIN_CLUSTER_FRAC,
    L2_MIN_SAMPLES,
    L2_MIN_VARIABLE_CARDS,
    L2_VARIANCE_HI,
    L2_VARIANCE_LO,
)


def _high_variance_cards(
    presence: sp.csr_matrix,
    mask: np.ndarray,
    card_vocab: list[str],
) -> tuple[np.ndarray, list[str]]:
    """
    Within a cluster (rows selected by boolean mask), identify cards whose
    presence rate falls in [L2_VARIANCE_LO, L2_VARIANCE_HI].

    Returns:
        col_indices : int array — column indices in the full presence matrix
        names       : matching card names
    """
    sub    = presence[mask]
    rates  = np.asarray(sub.mean(axis=0)).ravel()   # P(card present | cluster)
    keep   = np.where((rates >= L2_VARIANCE_LO) & (rates <= L2_VARIANCE_HI))[0]
    names  = [card_vocab[i] for i in keep]
    return keep, names


def _build_feature_matrix(
    presence: sp.csr_matrix,
    mask: np.ndarray,
    col_indices: np.ndarray,
    pip_volumes: np.ndarray,
    cmc_dists: np.ndarray,
) -> np.ndarray:
    """
    Concatenate high-variance card presence, pip volume, and CMC distribution
    into a single dense feature matrix for the rows selected by mask.
    """
    card_feats = presence[mask][:, col_indices].toarray().astype(np.float32)
    pip_feats  = pip_volumes[mask]
    cmc_feats  = cmc_dists[mask]
    return np.concatenate([card_feats, pip_feats, cmc_feats], axis=1)


def _run_hdbscan(X: np.ndarray, parent_size: int) -> tuple[np.ndarray, np.ndarray]:
    min_cluster_size = max(L2_MIN_CLUSTER_ABS, int(parent_size * L2_MIN_CLUSTER_FRAC))
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=L2_MIN_SAMPLES,
        cluster_selection_method="eom",
        prediction_data=True,
    )
    clusterer.fit(X)
    return clusterer.labels_.astype(np.int32), clusterer.probabilities_.astype(np.float32)


def run(
    l1_labels: np.ndarray,
    presence: sp.csr_matrix,
    card_vocab: list[str],
    pip_volumes: np.ndarray,
    cmc_dists: np.ndarray,
) -> dict[int, dict]:
    """
    Attempt sub-clustering on every Level 1 cluster.

    Returns a dict keyed by Level 1 cluster_id:
    {
        cluster_id: {
            "eligible":         bool,   # True if enough variable cards were found
            "n_subclusters":    int,    # 0 if not split or no meaningful split found
            "variable_cards":   list[str],
            "sub_labels":       np.ndarray (shape = cluster_size),  # -1 = noise
            "sub_probs":        np.ndarray,
            "deck_indices":     np.ndarray,  # indices into the original deck array
        }
    }
    """
    unique_clusters = sorted(set(l1_labels[l1_labels >= 0]))
    results: dict[int, dict] = {}

    print(f"\n── Level 2  {len(unique_clusters)} clusters to examine ──────────────────────────────")

    for cid in unique_clusters:
        mask         = l1_labels == cid
        deck_indices = np.where(mask)[0]
        parent_size  = int(mask.sum())

        col_indices, var_cards = _high_variance_cards(presence, mask, card_vocab)

        if len(var_cards) < L2_MIN_VARIABLE_CARDS:
            print(f"  Cluster {cid:4d} ({parent_size:6,} decks): {len(var_cards)} variable cards — skip")
            results[cid] = {
                "eligible":       False,
                "n_subclusters":  0,
                "variable_cards": var_cards,
                "sub_labels":     np.full(parent_size, -1, dtype=np.int32),
                "sub_probs":      np.zeros(parent_size, dtype=np.float32),
                "deck_indices":   deck_indices,
            }
            continue

        X = _build_feature_matrix(presence, mask, col_indices, pip_volumes, cmc_dists)
        sub_labels, sub_probs = _run_hdbscan(X, parent_size)

        n_sub    = int(sub_labels.max()) + 1 if sub_labels.max() >= 0 else 0
        n_noise  = int((sub_labels == -1).sum())

        print(
            f"  Cluster {cid:4d} ({parent_size:6,} decks): "
            f"{len(var_cards)} variable cards → {n_sub} sub-clusters  "
            f"({n_noise} noise)"
        )

        results[cid] = {
            "eligible":       True,
            "n_subclusters":  n_sub,
            "variable_cards": var_cards,
            "sub_labels":     sub_labels,
            "sub_probs":      sub_probs,
            "deck_indices":   deck_indices,
        }

    return results
