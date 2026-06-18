"""
Level 1 — coarse archetype discovery via UMAP + HDBSCAN.

Takes deck embeddings (or a card-presence matrix as fallback) and returns
cluster labels and soft membership probabilities for each deck.

Typical call from pipeline.py:
    labels, probs = run(embeddings, n_decks=len(deck_ids), fmt="pauper")
"""
import sys
from pathlib import Path

import hdbscan
import numpy as np
import umap

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    L1_MIN_CLUSTER_ABS,
    L1_MIN_CLUSTER_FRAC,
    L1_MIN_SAMPLES,
    L1_CLUSTER_METHOD,
    UMAP_METRIC,
    UMAP_MIN_DIST,
    UMAP_N_COMPONENTS,
    UMAP_N_NEIGHBORS,
    UMAP_RANDOM_STATE,
)


def _reduce(embeddings: np.ndarray) -> np.ndarray:
    """UMAP: (N, D) → (N, UMAP_N_COMPONENTS)."""
    print(f"  UMAP: {embeddings.shape} → ({len(embeddings)}, {UMAP_N_COMPONENTS})...")
    reducer = umap.UMAP(
        n_components=UMAP_N_COMPONENTS,
        n_neighbors=UMAP_N_NEIGHBORS,
        min_dist=UMAP_MIN_DIST,
        metric=UMAP_METRIC,
        random_state=UMAP_RANDOM_STATE,
        low_memory=True,      # important for 1M+ decks
        verbose=False,
    )
    return reducer.fit_transform(embeddings).astype(np.float32)


def _cluster(reduced: np.ndarray, n_decks: int) -> tuple[np.ndarray, np.ndarray]:
    """
    HDBSCAN on UMAP-reduced embeddings.

    Returns:
        labels : (N,) int  — cluster id per deck; -1 = noise (no archetype)
        probs  : (N,) float32 — soft membership probability
    """
    min_cluster_size = max(L1_MIN_CLUSTER_ABS, int(n_decks * L1_MIN_CLUSTER_FRAC))
    print(f"  HDBSCAN: min_cluster_size={min_cluster_size}  min_samples={L1_MIN_SAMPLES}")

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=L1_MIN_SAMPLES,
        cluster_selection_method=L1_CLUSTER_METHOD,
        prediction_data=False,
    )
    clusterer.fit(reduced)

    labels = clusterer.labels_.astype(np.int32)
    probs  = clusterer.probabilities_.astype(np.float32)

    n_clusters = int(labels.max()) + 1
    n_noise    = int((labels == -1).sum())
    print(f"  → {n_clusters} clusters  |  {n_noise:,} noise decks ({100*n_noise/len(labels):.1f}%)")

    return labels, probs


def run(
    embeddings: np.ndarray,
    n_decks: int,
    fmt: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Full Level 1 pipeline.

    Returns:
        reduced : (N, UMAP_N_COMPONENTS) float32 — UMAP coordinates (used by Level 2)
        labels  : (N,) int32
        probs   : (N,) float32
    """
    print(f"\n── Level 1  [{fmt}]  {n_decks:,} decks ─────────────────────────────")
    reduced        = _reduce(embeddings)
    labels, probs  = _cluster(reduced, n_decks)
    return reduced, labels, probs


def cluster_summary(labels: np.ndarray) -> dict[int, int]:
    """Return {cluster_id: size} sorted by size descending, excluding noise."""
    unique, counts = np.unique(labels[labels >= 0], return_counts=True)
    order = np.argsort(-counts)
    return {int(unique[i]): int(counts[i]) for i in order}
