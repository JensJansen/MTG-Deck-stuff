"""Tests for src/constants/moxfield.py and src/constants/env.py."""
import os
import pytest
from constants.moxfield import (
    COLOR_BITS, DEFAULT_BOARDS, LEGAL_FORMATS,
    encode_colors, moxfield_search_name, parse_deck,
)


# ── encode_colors ──────────────────────────────────────────────────────────────

class TestEncodeColors:
    def test_list_single_white(self):   assert encode_colors(["W"]) == 1
    def test_list_single_blue(self):    assert encode_colors(["U"]) == 2
    def test_list_single_black(self):   assert encode_colors(["B"]) == 4
    def test_list_single_red(self):     assert encode_colors(["R"]) == 8
    def test_list_single_green(self):   assert encode_colors(["G"]) == 16

    def test_list_multi_color(self):
        assert encode_colors(["W", "U"]) == 3    # 1 | 2
        assert encode_colors(["R", "G"]) == 24   # 8 | 16

    def test_string_comma_separated(self):
        assert encode_colors("W,U") == 3
        assert encode_colors("R") == 8

    def test_none_returns_zero(self):
        assert encode_colors(None) == 0

    def test_empty_list_returns_zero(self):
        assert encode_colors([]) == 0

    def test_empty_string_returns_zero(self):
        assert encode_colors("") == 0

    def test_unknown_color_ignored(self):
        assert encode_colors(["X", "W"]) == 1   # X has no bit, W = 1

    def test_case_insensitive_list(self):
        assert encode_colors(["w"]) == 1
        assert encode_colors(["u", "b"]) == 6

    def test_case_insensitive_string(self):
        assert encode_colors("w,u") == 3

    def test_all_five_colors(self):
        assert encode_colors(["W", "U", "B", "R", "G"]) == 31


# ── moxfield_search_name ──────────────────────────────────────────────────────

class TestMoxfieldSearchName:
    def test_dfc_strips_back_face(self):
        assert moxfield_search_name("Delver of Secrets // Insectile Aberration") == "Delver of Secrets"

    def test_normal_card_unchanged(self):
        assert moxfield_search_name("Lightning Bolt") == "Lightning Bolt"

    def test_slash_without_spaces_unchanged(self):
        # Only ' // ' (with surrounding spaces) triggers the split
        assert moxfield_search_name("Fire/Ice") == "Fire/Ice"

    def test_multiple_double_slashes_takes_first(self):
        assert moxfield_search_name("A // B // C") == "A"

    def test_preserves_leading_trailing_whitespace_in_name(self):
        # The split is on " // " so the front face retains its own spacing
        assert moxfield_search_name("Front Face // Back Face") == "Front Face"


# ── parse_deck ────────────────────────────────────────────────────────────────

class TestParseDeck:
    RAW = {
        "publicId":         "abc123",
        "name":             "Test Deck",
        "format":           "pauper",
        "colorIdentity":    ["R", "U"],
        "createdByUser":    {"userName": "jens"},
        "createdAtUtc":     "2025-01-01T00:00:00Z",
        "lastUpdatedAtUtc": "2025-06-01T00:00:00Z",
    }

    def test_public_id(self):
        assert parse_deck(self.RAW, None)["public_id"] == "abc123"

    def test_name(self):
        assert parse_deck(self.RAW, None)["name"] == "Test Deck"

    def test_format_from_raw(self):
        assert parse_deck(self.RAW, None)["format"] == "pauper"

    def test_format_falls_back_to_param_when_raw_is_none(self):
        raw = {**self.RAW, "format": None}
        assert parse_deck(raw, "modern")["format"] == "modern"

    def test_author_from_created_by_user(self):
        assert parse_deck(self.RAW, None)["author"] == "jens"

    def test_author_username_fallback(self):
        raw = {"publicId": "x", "authorUserName": "alice"}
        assert parse_deck(raw, None)["author"] == "alice"

    def test_color_mask_matches_encode_colors(self):
        result = parse_deck(self.RAW, None)
        assert result["color_mask"] == encode_colors(["R", "U"])

    def test_timestamps(self):
        result = parse_deck(self.RAW, None)
        assert result["created_at_utc"] == "2025-01-01T00:00:00Z"
        assert result["updated_at_utc"] == "2025-06-01T00:00:00Z"

    def test_scraped_at_is_set(self):
        assert parse_deck(self.RAW, None)["scraped_at"] is not None

    def test_public_id_falls_back_to_id(self):
        raw = {"id": "fallback-id", "name": "Y"}
        assert parse_deck(raw, None)["public_id"] == "fallback-id"

    def test_public_id_falls_back_to_slug(self):
        raw = {"slug": "my-slug", "name": "Y"}
        assert parse_deck(raw, None)["public_id"] == "my-slug"

    def test_missing_colors_defaults_to_zero(self):
        raw = {"publicId": "q"}
        assert parse_deck(raw, None)["color_mask"] == 0

    def test_colors_field_used_when_color_identity_absent(self):
        raw = {"publicId": "z", "colors": ["G"]}
        assert parse_deck(raw, None)["color_mask"] == encode_colors(["G"])


# ── Constants ─────────────────────────────────────────────────────────────────

class TestConstants:
    def test_legal_formats_exact(self):
        assert LEGAL_FORMATS == [
            "commander", "pauper", "standard", "modern",
            "vintage", "legacy", "highlanderCanadian",
        ]

    def test_default_boards_exact(self):
        assert DEFAULT_BOARDS == frozenset({"mainboard", "commanders", "companions", "signatureSpells"})

    def test_color_bits_exact(self):
        assert COLOR_BITS == {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}

    def test_default_boards_is_frozenset(self):
        assert isinstance(DEFAULT_BOARDS, frozenset)

    def test_legal_formats_has_highlander(self):
        assert "highlanderCanadian" in LEGAL_FORMATS


# ── load_env ──────────────────────────────────────────────────────────────────

class TestLoadEnv:
    def test_loads_vars_from_existing_file(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("DATABASE_URL=postgres://test\nAPI_KEY=secret\n")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("API_KEY", raising=False)

        import constants.env as env_mod
        monkeypatch.setattr(env_mod, "_ENV_FILE", env_file)
        from constants.env import load_env
        load_env()

        assert os.environ["DATABASE_URL"] == "postgres://test"
        assert os.environ["API_KEY"] == "secret"

    def test_does_not_overwrite_existing_env_vars(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("DATABASE_URL=from_file\n")
        monkeypatch.setenv("DATABASE_URL", "already_set")

        import constants.env as env_mod
        monkeypatch.setattr(env_mod, "_ENV_FILE", env_file)
        from constants.env import load_env
        load_env()

        assert os.environ["DATABASE_URL"] == "already_set"

    def test_skips_blank_lines_and_comments(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nMY_VAR=hello\n")
        monkeypatch.delenv("MY_VAR", raising=False)

        import constants.env as env_mod
        monkeypatch.setattr(env_mod, "_ENV_FILE", env_file)
        from constants.env import load_env
        load_env()

        assert os.environ["MY_VAR"] == "hello"

    def test_strips_whitespace_from_key_and_value(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("  MY_KEY  =  my_value  \n")
        monkeypatch.delenv("MY_KEY", raising=False)

        import constants.env as env_mod
        monkeypatch.setattr(env_mod, "_ENV_FILE", env_file)
        from constants.env import load_env
        load_env()

        assert os.environ["MY_KEY"] == "my_value"

    def test_creates_template_when_env_file_missing(self, tmp_path, monkeypatch, capsys):
        env_file = tmp_path / "subdir" / ".env"

        import constants.env as env_mod
        monkeypatch.setattr(env_mod, "_ENV_FILE", env_file)
        from constants.env import load_env
        load_env()

        assert env_file.exists()
        content = env_file.read_text()
        assert "DATABASE_URL" in content
        assert "API_KEY" in content
        captured = capsys.readouterr()
        assert "placeholder" in captured.out
