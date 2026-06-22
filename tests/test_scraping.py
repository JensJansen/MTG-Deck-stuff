"""Tests for pure functions in src/scraping/scryfall_bulk_cards_importer.py."""
from scryfall_bulk_cards_importer import COLUMNS, _bool, _image_uri, parse_card
from constants.moxfield import LEGAL_FORMATS


class TestBool:
    def test_true_bool(self):    assert _bool(True)    == 1
    def test_false_bool(self):   assert _bool(False)   == 0
    def test_true_string(self):  assert _bool("true")  == 1
    def test_false_string(self): assert _bool("false") == 0
    def test_True_string(self):  assert _bool("True")  == 1
    def test_integer_one(self):  assert _bool(1)       == 1
    def test_integer_zero(self): assert _bool(0)       == 0
    def test_none(self):         assert _bool(None)    == 0


class TestImageUri:
    def test_normal_image_uri(self):
        raw = {"image_uris": {"normal": "http://example.com/normal.jpg", "large": "http://example.com/large.jpg"}}
        assert _image_uri(raw) == "http://example.com/normal.jpg"

    def test_large_fallback_when_no_normal(self):
        raw = {"image_uris": {"large": "http://example.com/large.jpg"}}
        assert _image_uri(raw) == "http://example.com/large.jpg"

    def test_card_faces_fallback(self):
        raw = {"card_faces": [{"image_uris": {"normal": "http://example.com/face.jpg"}}]}
        assert _image_uri(raw) == "http://example.com/face.jpg"

    def test_no_uris_returns_none(self):
        assert _image_uri({}) is None

    def test_empty_card_faces_returns_none(self):
        assert _image_uri({"card_faces": []}) is None

    def test_card_face_large_fallback(self):
        raw = {"card_faces": [{"image_uris": {"large": "http://example.com/face_large.jpg"}}]}
        assert _image_uri(raw) == "http://example.com/face_large.jpg"


class TestParseCard:
    FULL_CARD = {
        "name":           "Lightning Bolt",
        "id":             "abc-123",
        "oracle_id":      "oracle-abc",
        "layout":         "normal",
        "mana_cost":      "{R}",
        "cmc":            1.0,
        "type_line":      "Instant",
        "oracle_text":    "Deal 3 damage to any target.",
        "colors":         ["R"],
        "color_identity": ["R"],
        "rarity":         "common",
        "reserved":       False,
        "textless":       False,
        "game_changer":   False,
        "legalities": {
            "commander": "legal",
            "pauper":    "legal",
            "modern":    "legal",
            "vintage":   "legal",
            "legacy":    "legal",
        },
        "image_uris":     {"normal": "http://example.com/bolt.jpg"},
        "keywords":       ["Instant"],
    }

    def test_returns_tuple_with_correct_length(self):
        row = parse_card(self.FULL_CARD)
        assert len(row) == len(COLUMNS)

    def test_card_name(self):
        row = parse_card(self.FULL_CARD)
        assert row[COLUMNS.index("card_name")] == "Lightning Bolt"

    def test_highlander_canadian_always_legal(self):
        row = parse_card(self.FULL_CARD)
        assert row[COLUMNS.index("legal_highlanderCanadian")] == "legal"

    def test_highlander_canadian_legal_even_when_absent_from_scryfall(self):
        raw = {**self.FULL_CARD, "legalities": {}}
        row = parse_card(raw)
        assert row[COLUMNS.index("legal_highlanderCanadian")] == "legal"

    def test_normal_format_legality_respected(self):
        row = parse_card(self.FULL_CARD)
        assert row[COLUMNS.index("legal_pauper")] == "legal"

    def test_missing_legality_is_none(self):
        raw = {**self.FULL_CARD, "legalities": {}}
        row = parse_card(raw)
        assert row[COLUMNS.index("legal_modern")] is None

    def test_all_legal_formats_present_as_columns(self):
        for fmt in LEGAL_FORMATS:
            assert f"legal_{fmt}" in COLUMNS

    def test_cmc_defaults_to_zero_when_missing(self):
        raw = {**self.FULL_CARD, "cmc": None}
        row = parse_card(raw)
        assert row[COLUMNS.index("cmc")] == 0.0

    def test_reserved_bool_to_int(self):
        raw = {**self.FULL_CARD, "reserved": True}
        row = parse_card(raw)
        assert row[COLUMNS.index("reserved")] == 1

    def test_image_uri_extracted(self):
        row = parse_card(self.FULL_CARD)
        assert row[COLUMNS.index("image_uri")] == "http://example.com/bolt.jpg"
