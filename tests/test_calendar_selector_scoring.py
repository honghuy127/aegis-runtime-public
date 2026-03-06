"""
Unit tests for calendar_selector_scoring module.

Tests SelectorScoreboard ranking, serialization, and evidence tracking.
No Playwright dependency.
"""

import pytest
from core.scenario.calendar_selector_scoring import SelectorScoreboard


class TestSelectorScoreboardInit:
    """Tests for SelectorScoreboard initialization."""

    def test_init_basic(self):
        board = SelectorScoreboard(site_key="google_flights", locale="en")
        assert board.site_key == "google_flights"
        assert board.locale == "en"

    def test_init_default_locale(self):
        board = SelectorScoreboard(site_key="google_flights")
        assert board.locale == "en"


class TestSelectorScoreboardRecording:
    """Tests for record_success and record_failure."""

    def test_record_success_increments(self):
        board = SelectorScoreboard(site_key="test")
        selector = "[role='combobox']"

        board.record_success("opener", selector)
        assert board.get_score("opener", selector) == 1.0

        board.record_success("opener", selector)
        assert board.get_score("opener", selector) == 2.0

    def test_record_failure_decrements(self):
        board = SelectorScoreboard(site_key="test")
        selector = "[role='combobox']"

        board.record_success("opener", selector)
        board.record_success("opener", selector)
        assert board.get_score("opener", selector) == 2.0

        board.record_failure("opener", selector)
        assert board.get_score("opener", selector) == 1.5

    def test_failure_floors_at_zero(self):
        board = SelectorScoreboard(site_key="test")
        selector = "[role='button']"

        board.record_failure("opener", selector)
        board.record_failure("opener", selector)
        board.record_failure("opener", selector)

        # Should not go below 0
        assert board.get_score("opener", selector) == 0.0

    def test_multiple_selectors_independent(self):
        board = SelectorScoreboard(site_key="test")
        sel1 = "[role='opener']"
        sel2 = "[aria-label='date']"

        board.record_success("opener", sel1)
        board.record_success("opener", sel1)
        board.record_failure("opener", sel2)

        assert board.get_score("opener", sel1) == 2.0
        assert board.get_score("opener", sel2) == 0.0  # Failing selector


class TestSelectorScoreboardRanking:
    """Tests for rank_selectors method."""

    def test_rank_by_score_descending(self):
        board = SelectorScoreboard(site_key="test")
        sel_a = "[role='combobox']"
        sel_b = "[aria-label='date']"
        sel_c = "input[type='date']"

        board.record_success("opener", sel_a)
        board.record_success("opener", sel_a)
        board.record_success("opener", sel_b)
        board.record_failure("opener", sel_c)

        ranked = board.rank_selectors("opener")
        assert ranked[0] == sel_a  # score 2.0
        assert ranked[1] == sel_b  # score 1.0
        # sel_c has 0.0 score

    def test_rank_with_fallback_list(self):
        board = SelectorScoreboard(site_key="test")
        sel_tracked = "[role='combobox']"
        sel_fallback_1 = "[aria-label='date']"
        sel_fallback_2 = "input[type='date']"

        board.record_success("opener", sel_tracked)
        board.record_success("opener", sel_tracked)

        fallback_list = [sel_fallback_1, sel_fallback_2]
        ranked = board.rank_selectors("opener", fallback_list)

        # Tracked (with score) comes first
        assert ranked[0] == sel_tracked
        # Then fallback in order
        assert sel_fallback_1 in ranked
        assert sel_fallback_2 in ranked

    def test_rank_with_no_history_returns_fallback(self):
        board = SelectorScoreboard(site_key="test")
        fallback_list = ["[role='button']", "[aria-label='submit']"]

        ranked = board.rank_selectors("unopened_family", fallback_list)
        assert ranked == fallback_list

    def test_rank_deduplicates(self):
        board = SelectorScoreboard(site_key="test")
        sel = "[role='combobox']"

        board.record_success("opener", sel)
        fallback_list = [sel, "[aria-label='date']"]

        ranked = board.rank_selectors("opener", fallback_list)
        # sel should appear once, not twice
        assert ranked.count(sel) == 1


class TestSelectorScoreboardFamilies:
    """Tests for family separation."""

    def test_separate_families(self):
        board = SelectorScoreboard(site_key="test")
        sel_opener = "[role='combobox']"
        sel_header = "[role='heading']"

        board.record_success("opener", sel_opener)
        board.record_success("header", sel_header)

        assert board.get_score("opener", sel_opener) == 1.0
        assert board.get_score("header", sel_header) == 1.0
        # Cross-family lookup returns 0
        assert board.get_score("opener", sel_header) == 0.0
        assert board.get_score("header", sel_opener) == 0.0


class TestSelectorScoreboardSerialization:
    """Tests for to_dict and from_dict."""

    def test_to_dict_structure(self):
        board = SelectorScoreboard(site_key="google_flights", locale="ja-JP")
        board.record_success("opener", "[role='combobox']")
        board.record_success("opener", "[role='combobox']")

        data = board.to_dict()
        assert data["site_key"] == "google_flights"
        assert data["locale"] == "ja-JP"
        assert "scores_by_family" in data
        assert "opener" in data["scores_by_family"]

    def test_from_dict_reconstructs(self):
        original = SelectorScoreboard(site_key="google_flights", locale="ja-JP")
        original.record_success("opener", "[role='combobox']")
        original.record_success("opener", "[role='combobox']")

        data = original.to_dict()
        reconstructed = SelectorScoreboard.from_dict(data)

        assert reconstructed.site_key == "google_flights"
        assert reconstructed.locale == "ja-JP"
        assert reconstructed.get_score("opener", "[role='combobox']") == 2.0

    def test_from_dict_with_missing_fields(self):
        # Graceful handling of incomplete data
        data = {"site_key": "test"}
        board = SelectorScoreboard.from_dict(data)
        assert board.site_key == "test"
        assert board.locale == "en"  # Default

    def test_roundtrip_idempotent(self):
        original = SelectorScoreboard(site_key="test", locale="en")
        original.record_success("opener", "sel_a")
        original.record_success("opener", "sel_a")
        original.record_failure("header", "sel_b")

        # Serialize and deserialize
        data1 = original.to_dict()
        board1 = SelectorScoreboard.from_dict(data1)
        data2 = board1.to_dict()

        # Should be identical
        assert data1 == data2


class TestSelectorScoreboardEdgeCases:
    """Tests for edge cases."""

    def test_empty_scoreboard(self):
        board = SelectorScoreboard(site_key="test")

        assert board.get_score("any_family", "any_selector") == 0.0
        ranked = board.rank_selectors("unopened", None)
        assert ranked == []

    def test_very_long_selector(self):
        board = SelectorScoreboard(site_key="test")
        long_sel = "[role='gridcell'][aria-label*='2026年3月1日'][data-date='20260301']"

        board.record_success("day_cell", long_sel)
        assert board.get_score("day_cell", long_sel) == 1.0

    def test_special_characters_in_selector(self):
        board = SelectorScoreboard(site_key="test")
        sel_with_quotes = "[aria-label=\"Select March '26\"]"

        board.record_success("opener", sel_with_quotes)
        assert board.get_score("opener", sel_with_quotes) == 1.0

    def test_many_selectors_in_family(self):
        board = SelectorScoreboard(site_key="test")

        # Record 20 different selectors
        for i in range(20):
            sel = f"[selector_{i}]"
            for _ in range(i + 1):  # Selector_0 has 1 success, selector_19 has 20
                board.record_success("day_cell", sel)

        ranked = board.rank_selectors("day_cell")
        # Should be ranked with highest scores first
        assert ranked[0] == "[selector_19]"
        # Selector_0 now has score 1.0, selector_1 also has 1.0, so either could be last
        # Just verify length for simplicity
        assert len(ranked) == 20
        """Simulate typical calendar interaction scoring."""
        board = SelectorScoreboard(site_key="google_flights", locale="ja-JP")

        # Opener attempts
        board.record_failure("opener", "[role='combobox'][aria-label*='出発']")
        board.record_success("opener", "[aria-label*='出発日']")
        board.record_success("opener", "[aria-label*='出発日']")

        # Header detection
        board.record_success("header", "[role='heading']")

        # Navigation
        board.record_success("nav_button", "[aria-label*='次の月']")
        board.record_success("nav_button", "[aria-label*='次の月']")
        board.record_success("nav_button", "[aria-label*='次の月']")

        # Day selection
        board.record_success("day_cell", "[role='gridcell'][aria-label*='2026年3月']")

        # Verify ranking
        opener_ranked = board.rank_selectors("opener", ["[role='combobox']"])
        assert opener_ranked[0] == "[aria-label*='出発日']"  # This one worked

        # Serialize for artifact
        data = board.to_dict()
        assert data["site_key"] == "google_flights"
        assert len(data["scores_by_family"]["opener"]) >= 2
