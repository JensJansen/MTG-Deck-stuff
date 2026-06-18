"""Tests for pure functions in src/analysis/."""
from precompute_layout import decode_colors
from query import SORT_CHOICES
from group_query import SORT_CHOICES as GQ_SORT_CHOICES
from constants.moxfield import encode_colors


class TestDecodeColors:
    def test_zero_mask_returns_empty_string(self):
        assert decode_colors(0) == ""

    def test_falsy_mask_returns_empty_string(self):
        # None and 0 are both falsy — function returns "" for any falsy input
        assert decode_colors(None) == ""

    def test_white(self):  assert decode_colors(1)  == "W"
    def test_blue(self):   assert decode_colors(2)  == "U"
    def test_black(self):  assert decode_colors(4)  == "B"
    def test_red(self):    assert decode_colors(8)  == "R"
    def test_green(self):  assert decode_colors(16) == "G"

    def test_multi_color_returns_comma_separated(self):
        # W=1, U=2 → mask 3 → "W,U" (dict preserves insertion order)
        assert decode_colors(3) == "W,U"

    def test_all_five_colors(self):
        result = decode_colors(1 | 2 | 4 | 8 | 16)
        assert set(result.split(",")) == {"W", "U", "B", "R", "G"}

    def test_roundtrip_with_encode_colors(self):
        colors = ["R", "G"]
        mask = encode_colors(colors)
        decoded = decode_colors(mask)
        assert set(decoded.split(",")) == set(colors)


class TestSortChoices:
    def test_sort_choices_exact(self):
        assert SORT_CHOICES == ["lift", "pmi", "jaccard", "confidence", "cooccurrence_count"]

    def test_group_query_exposes_same_sort_choices(self):
        assert GQ_SORT_CHOICES == SORT_CHOICES

    def test_lift_is_default_first_choice(self):
        assert SORT_CHOICES[0] == "lift"

    def test_confidence_included(self):
        assert "confidence" in SORT_CHOICES
