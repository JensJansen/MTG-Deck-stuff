"""Tests for pure functions in src/analysis/."""
from query import SORT_CHOICES
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
