"""
Keystone card rule generation.

For a pair of sub-archetypes (or any two groups of decks), identifies the cards
that most reliably discriminate between them using differential frequency analysis.

A card is a keystone for sub-archetype S if:
    P(card | S)                >= KEYSTONE_P_IN
    P(card | all other groups) <  KEYSTONE_P_OUT

The differential frequency  P(card | S) - P(card | others)  is used to rank
candidates. The top KEYSTONE_MAX cards per sub-archetype are stored.
"""
import sys
from pathlib import Path

import numpy as np
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).parent))
from config import KEYSTONE_MAX, KEYSTONE_P_IN, KEYSTONE_P_OUT


def _presence_rates(presence: sp.csr_matrix, row_indices: np.ndarray) -> np.ndarray:
    """P(card present) for the given row indices. Returns (V,) float32."""
    n = len(row_indices)
    if n == 0:
        return np.zeros(presence.shape[1], dtype=np.float32)
    return np.asarray(presence[row_indices].mean(axis=0)).ravel().astype(np.float32)


def find_keystones(
    presence: sp.csr_matrix,
    card_vocab: list[str],
    group_rows: np.ndarray,
    other_rows: np.ndarray,
) -> list[dict]:
    """
    Find keystone cards that characterise group_rows relative to other_rows.

    Returns a list of dicts sorted by differential frequency (best first):
        [{"card": str, "p_in": float, "p_out": float, "diff": float}, ...]
    """
    p_in  = _presence_rates(presence, group_rows)
    p_out = _presence_rates(presence, other_rows)
    diff  = p_in - p_out

    candidates = np.where((p_in >= KEYSTONE_P_IN) & (p_out < KEYSTONE_P_OUT))[0]
    if len(candidates) == 0:
        return []

    # Sort by differential frequency descending
    order      = np.argsort(-diff[candidates])
    top        = candidates[order[:KEYSTONE_MAX]]

    return [
        {
            "card":  card_vocab[i],
            "p_in":  round(float(p_in[i]),  3),
            "p_out": round(float(p_out[i]), 3),
            "diff":  round(float(diff[i]),  3),
        }
        for i in top
    ]


def generate_for_cluster(
    presence: sp.csr_matrix,
    card_vocab: list[str],
    cluster_deck_indices: np.ndarray,
    sub_labels: np.ndarray,
) -> dict[int, list[dict]]:
    """
    For each sub-cluster within a Level 1 cluster, find its keystone cards
    relative to all other sub-clusters in the same parent.

    Args:
        presence             : full presence matrix (N_format, V)
        card_vocab           : card names for columns
        cluster_deck_indices : row indices in presence for decks in this L1 cluster
        sub_labels           : sub-cluster label per deck in this cluster (-1 = noise)

    Returns:
        {sub_cluster_id: [keystone_dict, ...]}
    """
    keystones: dict[int, list[dict]] = {}
    sub_ids = sorted(set(sub_labels[sub_labels >= 0]))

    if len(sub_ids) < 2:
        return keystones

    for sid in sub_ids:
        is_this   = sub_labels == sid
        is_other  = (sub_labels >= 0) & (sub_labels != sid)

        group_rows = cluster_deck_indices[is_this]
        other_rows = cluster_deck_indices[is_other]

        keystones[sid] = find_keystones(presence, card_vocab, group_rows, other_rows)

    return keystones
