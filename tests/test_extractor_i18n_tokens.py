"""Tests for extractor token loading from configurable knowledge rules."""

import importlib

import pytest

import core.extractor as extractor
from utils import knowledge_rules as kr

pytestmark = [pytest.mark.llm, pytest.mark.vlm, pytest.mark.heavy]


def _reload_extractor_with(monkeypatch, *, grouped=None, legacy=None):
    """Reload extractor with patched token providers for hermetic token-source tests."""
    grouped = grouped or {}
    legacy = legacy or {}
    monkeypatch.setattr(
        kr,
        "get_tokens",
        lambda group, key: list(grouped.get((group, key), [])),
    )
    monkeypatch.setattr(
        kr,
        "get_knowledge_rule_tokens",
        lambda key: list(legacy.get(key, [])),
    )
    return importlib.reload(extractor)


def test_extractor_uses_grouped_knowledge_tokens(monkeypatch):
    """Grouped token config should drive extractor hint/page token sets."""
    reloaded = _reload_extractor_with(
        monkeypatch,
        grouped={
            ("hints", "results"): ["custom result token"],
            ("hints", "auth"): ["custom auth token"],
            ("hints", "route_fields"): ["custom route token"],
            ("page", "hotel"): ["custom hotel token"],
            ("page", "flight"): ["custom flight token"],
            ("google", "bundle_word"): ["custom bundle token"],
        },
        legacy={"url_package_tokens": ["/package/"]},
    )
    try:
        assert reloaded._RESULT_HINT_TOKENS == ["custom result token"]
        assert reloaded._AUTH_HINT_TOKENS == ["custom auth token"]
        assert reloaded._ROUTE_HINT_TOKENS == ["custom route token"]
        assert reloaded._HOTEL_TOKENS == ("custom hotel token",)
        assert reloaded._FLIGHT_TOKENS == ("custom flight token",)
        assert reloaded._BUNDLE_WORD_TOKENS == ("custom bundle token",)
    finally:
        importlib.reload(extractor)


def test_extractor_token_loading_fallback_safe_when_groups_missing(monkeypatch):
    """Missing grouped/legacy token config should fallback to conservative defaults."""
    reloaded = _reload_extractor_with(monkeypatch, grouped={}, legacy={})
    try:
        assert "hotel" in reloaded._HOTEL_TOKENS
        assert "flight" in reloaded._FLIGHT_TOKENS
        assert "search result" in [t.lower() for t in reloaded._RESULT_HINT_TOKENS]
        assert "email" in [t.lower() for t in reloaded._AUTH_HINT_TOKENS]
        assert "from" in [t.lower() for t in reloaded._ROUTE_HINT_TOKENS]
        html = "<div>flight deal + hotel package</div>"
        assert reloaded.looks_package_bundle_page(html=html, site="google_flights") is False
    finally:
        importlib.reload(extractor)
