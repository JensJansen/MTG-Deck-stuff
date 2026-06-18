"""Tests for pure functions in src/analysis/."""
import query
import group_query
from query import SORT_CHOICES, _SORT_COL_SAFE
from group_query import SORT_CHOICES as GQ_SORT_CHOICES


class TestSortChoices:
    def test_sort_choices_exact(self):
        assert SORT_CHOICES == ["lift", "pmi", "jaccard", "confidence", "cooccurrence_count"]

    def test_group_query_exposes_same_sort_choices(self):
        assert GQ_SORT_CHOICES == SORT_CHOICES

    def test_lift_is_default_first_choice(self):
        assert SORT_CHOICES[0] == "lift"

    def test_confidence_included(self):
        assert "confidence" in SORT_CHOICES


class TestSortColSafe:
    def test_is_identity_map_over_sort_choices(self):
        # Each key maps to itself — used as an allowlist before SQL interpolation
        assert _SORT_COL_SAFE == {c: c for c in SORT_CHOICES}

    def test_keys_match_sort_choices(self):
        assert set(_SORT_COL_SAFE.keys()) == set(SORT_CHOICES)

    def test_no_extra_keys(self):
        assert len(_SORT_COL_SAFE) == len(SORT_CHOICES)

    def test_group_query_has_same_safe_map(self):
        gq_safe = group_query._SORT_COL_SAFE
        assert gq_safe == _SORT_COL_SAFE
