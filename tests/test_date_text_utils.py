from utils.date_text import (
    english_month_name_to_number,
    parse_english_month_day_text,
    parse_english_month_year_text,
)


def test_english_month_name_to_number_supports_abbrev_and_full_name():
    assert english_month_name_to_number("Mar") == 3
    assert english_month_name_to_number("march") == 3
    assert english_month_name_to_number("Sept.") == 9
    assert english_month_name_to_number("unknown") is None


def test_parse_english_month_day_text_normalizes_weekday_month_day():
    assert parse_english_month_day_text("Sun, Mar 8", reference_year=2030) == "2030-03-08"
    assert parse_english_month_day_text("March 1, 2031") == "2031-03-01"


def test_parse_english_month_year_text_parses_calendar_header():
    assert parse_english_month_year_text("March 2026") == (2026, 3)
    assert parse_english_month_year_text("Sep 2027") == (2027, 9)
    assert parse_english_month_year_text("2027/09") == (None, None)
