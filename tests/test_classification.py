"""Tests for pure functions in src/deck-classification/keystone.py."""
import numpy as np
import pytest
import scipy.sparse as sp
from config import KEYSTONE_MAX, KEYSTONE_P_IN, KEYSTONE_P_OUT
from keystone import _presence_rates, find_keystones, generate_for_cluster


def make_presence(rows: list) -> sp.csr_matrix:
    """Build a CSR presence matrix from a nested list of 0/1 values."""
    return sp.csr_matrix(np.array(rows, dtype=np.float32))


# ── _presence_rates ───────────────────────────────────────────────────────────

class TestPresenceRates:
    def test_empty_indices_returns_zeros(self):
        presence = make_presence([[1, 0, 1], [0, 1, 0]])
        result = _presence_rates(presence, np.array([], dtype=int))
        assert result.shape == (3,)
        np.testing.assert_array_equal(result, [0.0, 0.0, 0.0])

    def test_single_row(self):
        presence = make_presence([[1, 0, 1]])
        result = _presence_rates(presence, np.array([0]))
        np.testing.assert_allclose(result, [1.0, 0.0, 1.0], atol=1e-6)

    def test_multiple_rows_averages(self):
        presence = make_presence([
            [1, 0],
            [1, 1],
            [0, 1],
        ])
        result = _presence_rates(presence, np.array([0, 1, 2]))
        # col 0: (1+1+0)/3 = 0.667, col 1: (0+1+1)/3 = 0.667
        np.testing.assert_allclose(result, [2 / 3, 2 / 3], atol=1e-5)

    def test_returns_float32(self):
        presence = make_presence([[1, 0]])
        result = _presence_rates(presence, np.array([0]))
        assert result.dtype == np.float32

    def test_shape_matches_card_count(self):
        presence = make_presence([[1, 0, 0, 1]])
        result = _presence_rates(presence, np.array([0]))
        assert result.shape == (4,)


# ── find_keystones ────────────────────────────────────────────────────────────

class TestFindKeystones:
    """
    Keystone criteria: p_in >= KEYSTONE_P_IN (0.60), p_out < KEYSTONE_P_OUT (0.15).
    """

    def test_no_candidates_returns_empty_list(self):
        # CardA: p_in=0.5 (below threshold), CardB: p_out=0.5 (above threshold)
        presence = make_presence([
            [1, 1],   # group row 0
            [0, 1],   # group row 1  → CardA p_in=0.5, CardB p_in=1.0
            [0, 1],   # other row    → CardA p_out=0.0, CardB p_out=1.0
            [0, 0],   # other row
        ])
        result = find_keystones(
            presence,
            ["CardA", "CardB"],
            group_rows=np.array([0, 1]),
            other_rows=np.array([2, 3]),
        )
        assert result == []

    def test_finds_discriminating_keystone(self):
        # CardA: appears in all group decks (p_in=1.0) and none of the others (p_out=0.0)
        # CardB: appears in all decks (p_out=1.0 → disqualified)
        presence = make_presence([
            [1, 1],   # group
            [1, 1],   # group
            [0, 1],   # other
            [0, 1],   # other
        ])
        result = find_keystones(
            presence,
            ["KeyCard", "CommonCard"],
            group_rows=np.array([0, 1]),
            other_rows=np.array([2, 3]),
        )
        assert len(result) == 1
        assert result[0]["card"] == "KeyCard"
        assert result[0]["p_in"]  == 1.0
        assert result[0]["p_out"] == 0.0
        assert result[0]["diff"]  == 1.0

    def test_result_contains_required_keys(self):
        presence = make_presence([[1, 0], [1, 0], [0, 0], [0, 0]])
        result = find_keystones(
            presence,
            ["A", "B"],
            group_rows=np.array([0, 1]),
            other_rows=np.array([2, 3]),
        )
        assert len(result) == 1
        assert set(result[0].keys()) == {"card", "p_in", "p_out", "diff"}

    def test_respects_keystone_max(self):
        n_cards = KEYSTONE_MAX + 3
        data = [
            [1] * n_cards,   # group
            [1] * n_cards,   # group
            [0] * n_cards,   # other
            [0] * n_cards,   # other
        ]
        presence = make_presence(data)
        vocab = [f"Card{i}" for i in range(n_cards)]
        result = find_keystones(
            presence,
            vocab,
            group_rows=np.array([0, 1]),
            other_rows=np.array([2, 3]),
        )
        assert len(result) == KEYSTONE_MAX

    def test_sorted_by_diff_descending(self):
        # CardA: p_in=1.0, p_out=0.0 → diff=1.0
        # CardB: p_in=0.8, p_out=0.0 → diff=0.8 (2 of 4 group rows have it? No.)
        # Build: 4 group rows; CardA in all 4, CardB in 3 of 4; no others
        presence = make_presence([
            [1, 1],   # group
            [1, 1],   # group
            [1, 1],   # group
            [1, 0],   # group (CardB absent here)
            [0, 0],   # other
            [0, 0],   # other
        ])
        result = find_keystones(
            presence,
            ["CardA", "CardB"],
            group_rows=np.array([0, 1, 2, 3]),
            other_rows=np.array([4, 5]),
        )
        # Both qualify (p_in >= 0.60, p_out < 0.15); CardA has higher diff
        assert len(result) == 2
        assert result[0]["card"] == "CardA"
        assert result[0]["diff"] >= result[1]["diff"]


# ── generate_for_cluster ──────────────────────────────────────────────────────

class TestGenerateForCluster:
    def test_single_sub_cluster_returns_empty_dict(self):
        presence = make_presence([[1, 0], [0, 1]])
        sub_labels = np.array([0, 0])
        result = generate_for_cluster(
            presence,
            ["A", "B"],
            cluster_deck_indices=np.array([0, 1]),
            sub_labels=sub_labels,
        )
        assert result == {}

    def test_all_noise_returns_empty_dict(self):
        presence = make_presence([[1, 0], [0, 1]])
        sub_labels = np.array([-1, -1])
        result = generate_for_cluster(
            presence,
            ["A", "B"],
            cluster_deck_indices=np.array([0, 1]),
            sub_labels=sub_labels,
        )
        assert result == {}

    def test_two_sub_clusters_produce_keystones(self):
        # Decks 0,1 in sub-cluster 0 exclusively use CardA
        # Decks 2,3 in sub-cluster 1 exclusively use CardB
        presence = make_presence([
            [1, 0],
            [1, 0],
            [0, 1],
            [0, 1],
        ])
        sub_labels = np.array([0, 0, 1, 1])
        result = generate_for_cluster(
            presence,
            ["CardA", "CardB"],
            cluster_deck_indices=np.array([0, 1, 2, 3]),
            sub_labels=sub_labels,
        )
        assert 0 in result
        assert 1 in result
        assert any(k["card"] == "CardA" for k in result[0])
        assert any(k["card"] == "CardB" for k in result[1])

    def test_noise_decks_excluded_from_group_and_other(self):
        # Deck 0: sub 0; Deck 1: noise; Deck 2: sub 1
        # CardA: present only in deck 0 (sub 0)
        # CardB: present only in deck 2 (sub 1)
        presence = make_presence([
            [1, 0],
            [0, 0],   # noise deck
            [0, 1],
        ])
        sub_labels = np.array([0, -1, 1])
        result = generate_for_cluster(
            presence,
            ["CardA", "CardB"],
            cluster_deck_indices=np.array([0, 1, 2]),
            sub_labels=sub_labels,
        )
        assert 0 in result
        assert 1 in result

    def test_returns_dict_keyed_by_sub_cluster_id(self):
        presence = make_presence([[1, 0], [1, 0], [0, 1], [0, 1]])
        sub_labels = np.array([0, 0, 1, 1])
        result = generate_for_cluster(
            presence,
            ["CardA", "CardB"],
            cluster_deck_indices=np.array([0, 1, 2, 3]),
            sub_labels=sub_labels,
        )
        assert all(isinstance(k, (int, np.integer)) for k in result)
        assert all(isinstance(v, list) for v in result.values())
