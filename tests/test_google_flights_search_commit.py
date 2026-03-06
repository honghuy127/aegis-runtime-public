"""Tests for Google Flights search/commit optimization."""

import pytest
import core.scenario_runner as sr
from core.service_ui_profiles import get_service_ui_profile
from core.scenario_runner import _service_search_click_fallbacks


def test_google_flights_search_selectors_prioritization():
    """Verify that Google Flights search selectors prioritize role-based and text-based buttons."""
    profile = get_service_ui_profile("google_flights")
    search_selectors = profile.get("search_selectors", [])

    # Check that search_selectors is not empty
    assert search_selectors, "search_selectors should not be empty for google_flights"

    # Check that role-based and text-based selectors come before aria-label variants
    ja_button_role = "[role='button']:has-text('検索')"
    ja_button_has_text = "button:has-text('検索')"
    en_button_role = "[role='button']:has-text('Search')"
    en_button_has_text = "button:has-text('Search')"

    # Get indices of key selectors
    indices = {}
    for selector in search_selectors:
        if selector == ja_button_role:
            indices['ja_button_role'] = search_selectors.index(selector)
        elif selector == ja_button_has_text:
            indices['ja_button_has_text'] = search_selectors.index(selector)
        elif selector == en_button_role:
            indices['en_button_role'] = search_selectors.index(selector)
        elif selector == en_button_has_text:
            indices['en_button_has_text'] = search_selectors.index(selector)
        elif "button[type='submit']" in selector:
            indices['submit_button'] = search_selectors.index(selector)

    # Verify the selectors exist
    assert len(indices) >= 4, "Should have at least role-based, text-based, and submit selectors"

    # Verify that text-based selectors come before submit buttons (if present)
    if 'en_button_has_text' in indices and 'submit_button' in indices:
        assert indices['en_button_has_text'] < indices['submit_button'], \
            "button:has-text('Search') should come before button[type='submit']"


def test_google_flights_search_selectors_via_service_fallback():
    """Verify that _service_search_click_fallbacks returns role/text selectors first for google_flights."""
    fallback_selectors = _service_search_click_fallbacks("google_flights")

    # Should have multiple selectors
    assert isinstance(fallback_selectors, list)
    assert len(fallback_selectors) > 0, "Should have at least one search selector"

    # The very first selectors should include role-based or text-based buttons for predictability
    # Check if first few include text-based variants
    first_few = str(fallback_selectors[:5]).lower()

    # Should mention either "has-text" (Playwright syntax) or role/button
    has_strong_selectors = any([
        "has-text" in str(s) or "[role='button']" in str(s)
        for s in fallback_selectors[:5]
    ])
    assert has_strong_selectors, \
        f"First 5 selectors should include role-based or text-based buttons, got: {fallback_selectors[:5]}"


def test_google_flights_search_selectors_en_override_prefers_english_before_japanese():
    fallback_selectors = _service_search_click_fallbacks(
        "google_flights",
        locale_hint_override="en",
    )

    en_idx = next(
        (i for i, s in enumerate(fallback_selectors) if "Search" in str(s)),
        None,
    )
    ja_idx = next(
        (i for i, s in enumerate(fallback_selectors) if "検索" in str(s)),
        None,
    )

    assert en_idx is not None
    assert ja_idx is not None
    assert en_idx < ja_idx


def test_google_flights_search_selectors_apply_runtime_hint_overlay(monkeypatch):
    monkeypatch.setattr(
        sr,
        "get_selector_hints",
        lambda **_kwargs: ["button[data-learned='search']"],
    )
    out = sr._service_search_click_fallbacks("google_flights", locale_hint_override="en")
    assert out[0] == "button[data-learned='search']"
    assert any("Search" in str(s) for s in out)


def test_google_flights_search_selectors_ignore_nonsemantic_runtime_hint(monkeypatch):
    monkeypatch.setattr(
        sr,
        "get_selector_hints",
        lambda **_kwargs: ["#search-visible"],
    )
    out = sr._service_search_click_fallbacks("google_flights", locale_hint_override="en")
    assert out
    assert out[0] != "#search-visible"


def test_google_flights_search_selectors_ja_locale():
    """Verify that Japanese-specific search selectors are present for google_flights."""
    profile = get_service_ui_profile("google_flights")
    search_selectors = profile.get("search_selectors", [])

    selectors_str = str(search_selectors).lower()

    # Should include Japanese "search" text
    assert "検索" in str(search_selectors), \
        "Should include Japanese search button selector for JA locale"


def test_google_flights_search_selectors_support_aria_label():
    """Verify that aria-label based selectors are present as fallback for google_flights."""
    profile = get_service_ui_profile("google_flights")
    search_selectors = profile.get("search_selectors", [])

    selectors_str = "".join(search_selectors)

    # Should include aria-label variants
    assert "aria-label" in selectors_str, \
        "Should include aria-label based selectors as fallback for google_flights"


def test_google_flights_search_selector_no_hardcoded_submit_first():
    """Verify that submit buttons are NOT first in the search selector list."""
    profile = get_service_ui_profile("google_flights")
    search_selectors = profile.get("search_selectors", [])

    # First selector should NOT be a generic button[type='submit']
    if search_selectors:
        first_selector = search_selectors[0]
        assert first_selector != "button[type='submit']", \
            "First search selector should not be generic button[type='submit']; should be role/text-based"
        assert first_selector != "input[type='submit']", \
            "First search selector should not be generic input[type='submit']; should be role/text-based"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
