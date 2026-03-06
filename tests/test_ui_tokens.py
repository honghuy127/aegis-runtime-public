"""Tests for shared UI token helpers."""

from core.ui_tokens import (
    build_button_text_selectors,
    is_placeholder,
    normalize_visible_text,
    prioritize_tokens,
)


def test_normalize_visible_text_handles_full_width_spaces_and_case():
    """Normalization should fold full-width spaces, case, and extra spacing."""
    text = "  Ｔｅｓｔ\u3000 Search   TOKEN  "
    assert normalize_visible_text(text) == "ｔｅｓｔ search token"


def test_is_placeholder_uses_normalized_exact_match():
    """Placeholder match should work across spacing/case variations."""
    assert is_placeholder("  Explore   Destinations ", ["explore destinations"]) is True
    assert is_placeholder("Where to now", ["where to"]) is False


def test_prioritize_tokens_keeps_same_set_with_locale_ordering():
    """Locale hint should reorder preference while preserving token set."""
    ja = prioritize_tokens(["Search", "検索"], locale_hint="ja-JP")
    en = prioritize_tokens(["Search", "検索"], locale_hint="en-US")
    assert ja[0] == "検索"
    assert en[0] == "Search"
    assert sorted(ja) == sorted(en)


def test_build_button_text_selectors_never_emits_bare_text_selector():
    """Selector builder should only emit button/role/input selector patterns."""
    selectors = build_button_text_selectors(["検索", "Search"])
    assert selectors
    assert all(not selector.lower().startswith("text=") for selector in selectors)
