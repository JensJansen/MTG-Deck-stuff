"""Tests for pure functions in src/viz/visualize.py."""
import pytest
import visualize


class TestCategorizeColor:
    def test_empty_string_is_colorless(self):
        assert visualize.categorize_color("") == "Colorless"

    def test_none_is_colorless(self):
        assert visualize.categorize_color(None) == "Colorless"

    def test_single_color_returned_as_is(self):
        assert visualize.categorize_color("R") == "R"
        assert visualize.categorize_color("W") == "W"
        assert visualize.categorize_color("U") == "U"
        assert visualize.categorize_color("B") == "B"
        assert visualize.categorize_color("G") == "G"

    def test_two_colors_is_multi(self):
        assert visualize.categorize_color("R,G") == "Multi"

    def test_three_colors_is_multi(self):
        assert visualize.categorize_color("W,U,B") == "Multi"

    def test_whitespace_around_color_ignored(self):
        # The function strips each color entry
        assert visualize.categorize_color(" R ") == "R"


class TestStructural:
    def test_color_mask_from_identity_was_removed(self):
        assert not hasattr(visualize, "color_mask_from_identity"), \
            "color_mask_from_identity should have been replaced by encode_colors"

    def test_get_formats_was_renamed_to_get_layout_formats(self):
        assert not hasattr(visualize, "get_formats"), \
            "get_formats should have been renamed to get_layout_formats"
        assert hasattr(visualize, "get_layout_formats")

    def test_local_load_env_was_removed(self):
        assert not hasattr(visualize, "_load_env"), \
            "_load_env should have been replaced by constants.env.load_env"

    def test_encode_colors_is_used(self):
        # encode_colors from constants.moxfield should be imported, not a local copy
        from constants.moxfield import encode_colors as canonical
        assert visualize.encode_colors is canonical
