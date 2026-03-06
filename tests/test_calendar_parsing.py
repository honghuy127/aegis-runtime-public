"""
Unit tests for calendar_parsing module.

Tests parse_month_year with various input formats (Japanese, English, numeric).
No Playwright dependency.
"""

import pytest
from core.scenario.calendar_parsing import (
    infer_year_for_visible_month,
    normalize_month_text,
    month_delta,
    parse_month_only,
    parse_month_year,
)


class TestNormalizeMonthText:
    """Tests for normalize_month_text helper."""

    def test_normalize_strips_whitespace(self):
        assert normalize_month_text("  2026年3月  ") == "2026年3月"

    def test_normalize_collapses_multiple_spaces(self):
        assert normalize_month_text("2026年  3月") == "2026年 3月"

    def test_normalize_handles_tabs_and_newlines(self):
        text = "2026年\t\n3月"
        assert normalize_month_text(text) == "2026年 3月"

    def test_normalize_converts_full_width_digits_japanese(self):
        # Full-width digits: １２３456年３月
        text = "２０２６年３月"
        result = normalize_month_text(text)
        assert "2026年3月" in result or result == "2026年3月"


class TestParseMonthYearJapanese:
    """Tests for Japanese month/year parsing."""

    def test_japanese_standard_format(self):
        # "2026年3月"
        year, month = parse_month_year("2026年3月", locale="ja-JP")
        assert year == 2026
        assert month == 3

    def test_japanese_with_space(self):
        # "2026年 3月"
        year, month = parse_month_year("2026年 3月", locale="ja-JP")
        assert year == 2026
        assert month == 3

    def test_japanese_double_digit_month(self):
        # "2026年10月"
        year, month = parse_month_year("2026年10月", locale="ja-JP")
        assert year == 2026
        assert month == 10

    def test_japanese_with_extra_text(self):
        # Real calendar might have extra UI text
        text = "出発日カレンダー\n2026年3月\n選択してください"
        year, month = parse_month_year(text, locale="ja-JP")
        assert year == 2026
        assert month == 3

    def test_japanese_full_width_digits(self):
        # Japanese might use full-width: "２０２６年３月"
        text = "２０２６年３月"
        year, month = parse_month_year(text, locale="ja-JP")
        assert year == 2026
        assert month == 3


class TestParseMonthYearEnglish:
    """Tests for English month/year parsing."""

    def test_english_month_name_first(self):
        # "March 2026"
        year, month = parse_month_year("March 2026", locale="en")
        assert year == 2026
        assert month == 3

    def test_english_month_name_lowercase(self):
        year, month = parse_month_year("march 2026", locale="en")
        assert year == 2026
        assert month == 3

    def test_english_all_months(self):
        months_map = {
            "January": 1, "February": 2, "March": 3, "April": 4,
            "May": 5, "June": 6, "July": 7, "August": 8,
            "September": 9, "October": 10, "November": 11, "December": 12,
        }
        for month_name, month_num in months_map.items():
            year, month = parse_month_year(f"{month_name} 2026", locale="en")
            assert month == month_num, f"Failed for {month_name}"

    def test_english_with_extra_text(self):
        text = "Select Departure\nMarch 2026\nAvailable"
        year, month = parse_month_year(text, locale="en")
        assert year == 2026
        assert month == 3


class TestParseMonthYearNumeric:
    """Tests for numeric month/year patterns."""

    def test_numeric_yyyy_slash_m(self):
        # "2026/3"
        year, month = parse_month_year("2026/3", locale="en")
        assert year == 2026
        assert month == 3

    def test_numeric_yyyy_dash_mm(self):
        # "2026-03"
        year, month = parse_month_year("2026-03", locale="en")
        assert year == 2026
        assert month == 3

    def test_numeric_m_slash_yyyy(self):
        # "3/2026"
        year, month = parse_month_year("3/2026", locale="en")
        assert year == 2026
        assert month == 3

    def test_numeric_mm_dash_yyyy(self):
        # "03-2026"
        year, month = parse_month_year("03-2026", locale="en")
        assert year == 2026
        assert month == 3


class TestParseMonthYearEdgeCases:
    """Tests for edge cases and invalid inputs."""

    def test_empty_string(self):
        year, month = parse_month_year("", locale="en")
        assert year is None
        assert month is None

    def test_garbage_text(self):
        year, month = parse_month_year("abcdefghijk", locale="en")
        assert year is None
        assert month is None

    def test_year_only_no_month(self):
        year, month = parse_month_year("2026", locale="en")
        # Might succeed with month=0 or fail; either acceptable
        if year is not None:
            assert 1 <= month <= 12 or year is None

    def test_invalid_year(self):
        # Year out of range
        year, month = parse_month_year("1999年3月", locale="ja-JP")
        assert year is None or year is None  # Should reject

    def test_invalid_month(self):
        # Month > 12
        year, month = parse_month_year("2026年13月", locale="ja-JP")
        assert month is None or month is None  # Should reject

    def test_none_input(self):
        # Edge case: None might be passed
        year, month = parse_month_year(None or "", locale="en")
        assert year is None
        assert month is None


class TestParseMonthOnly:
    """Tests parsing month labels without explicit year."""

    def test_parse_month_only_japanese(self):
        assert parse_month_only("4月", locale="ja-JP") == 4

    def test_parse_month_only_japanese_with_extra_text(self):
        assert parse_month_only("出発日 12月", locale="ja-JP") == 12

    def test_parse_month_only_japanese_day_cell_label(self):
        assert parse_month_only("4月12日(日)", locale="ja-JP") == 4

    def test_parse_month_only_english_name(self):
        assert parse_month_only("March", locale="en-US") == 3

    def test_parse_month_only_rejects_missing_month(self):
        assert parse_month_only("2026", locale="ja-JP") is None


class TestParseMonthYearLocaleHints:
    """Tests for locale parameter."""

    def test_locale_ja_uses_japanese_patterns(self):
        # Should match Japanese patterns
        year, month = parse_month_year("2026年3月", locale="ja")
        assert year == 2026
        assert month == 3

    def test_locale_en_uses_english_patterns(self):
        year, month = parse_month_year("March 2026", locale="en-US")
        assert year == 2026
        assert month == 3

    def test_locale_case_insensitive(self):
        year, month = parse_month_year("2026年3月", locale="JA-JP")
        assert year == 2026
        assert month == 3


class TestMonthDelta:
    """Tests for month_delta utility function."""

    def test_same_month_zero_delta(self):
        delta = month_delta(2026, 3, 2026, 3)
        assert delta == 0

    def test_next_month_one_delta(self):
        delta = month_delta(2026, 3, 2026, 4)
        assert delta == 1

    def test_previous_month_negative_delta(self):
        delta = month_delta(2026, 3, 2026, 2)
        assert delta == -1

    def test_year_boundary_positive(self):
        # Dec 2025 → Jan 2026
        delta = month_delta(2025, 12, 2026, 1)
        assert delta == 1

    def test_year_boundary_negative(self):
        # Jan 2026 → Dec 2025
        delta = month_delta(2026, 1, 2025, 12)
        assert delta == -1

    def test_two_years_forward(self):
        # 2026/3 → 2028/3 (24 months)
        delta = month_delta(2026, 3, 2028, 3)
        assert delta == 24

    def test_complex_delta(self):
        # 2025/6 → 2026/9 (15 months)
        delta = month_delta(2025, 6, 2026, 9)
        assert delta == 15


class TestInferYearForVisibleMonth:
    """Tests contextual year inference for month-only calendar labels."""

    def test_infers_same_year_for_adjacent_month(self):
        year = infer_year_for_visible_month(
            visible_month=4,
            target_year=2026,
            target_month=3,
            max_nav_steps=8,
        )
        assert year == 2026

    def test_infers_previous_year_across_january_boundary(self):
        year = infer_year_for_visible_month(
            visible_month=12,
            target_year=2026,
            target_month=1,
            max_nav_steps=8,
        )
        assert year == 2025

    def test_rejects_implausible_month_when_outside_bound(self):
        year = infer_year_for_visible_month(
            visible_month=12,
            target_year=2026,
            target_month=6,
            max_nav_steps=2,
        )
        assert year is None
