"""Selector stability and confidence downgrade tests."""

import pytest

from core import extractor as ex
from llm.selector_quality import classify_selector_stability

pytestmark = [pytest.mark.llm, pytest.mark.vlm, pytest.mark.heavy]


def test_classify_selector_stability_stable_data_testid():
    assert classify_selector_stability("[data-testid='price']") == "stable"


def test_classify_selector_stability_stable_aria_button():
    assert classify_selector_stability("button[aria-label*='検索']") == "stable"


def test_classify_selector_stability_brittle_class_chain():
    assert classify_selector_stability(".hXU5Ud.aA5Mwe") == "brittle"


def test_classify_selector_stability_brittle_nth_child_chain():
    assert classify_selector_stability("div.foo > span.bar:nth-child(2)") == "brittle"


def test_normalize_extractor_output_downgrades_confidence_for_brittle_selector(monkeypatch):
    """Brittle selector hints should reduce confidence by one level by default."""

    def _threshold(key, default=None):
        values = {
            "extract_selector_stability_normalize_enabled": True,
            "extract_confidence_downgrade_on_brittle_selector": True,
            "extract_confidence_downgrade_min": "low",
        }
        return values.get(key, default)

    monkeypatch.setattr(ex, "get_threshold", _threshold)
    out = ex._normalize_extractor_output(  # noqa: SLF001
        {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": {"css": ".hXU5Ud.aA5Mwe"},
            "source": "llm",
            "reason": "ok",
        },
        llm_mode="full",
    )
    assert out["selector_hint"]["stability"] == "brittle"
    assert out["confidence"] == "medium"


def test_normalize_extractor_output_keeps_confidence_when_downgrade_disabled(monkeypatch):
    """Feature flag can preserve legacy confidence behavior."""

    def _threshold(key, default=None):
        values = {
            "extract_selector_stability_normalize_enabled": True,
            "extract_confidence_downgrade_on_brittle_selector": False,
            "extract_confidence_downgrade_min": "low",
        }
        return values.get(key, default)

    monkeypatch.setattr(ex, "get_threshold", _threshold)
    out = ex._normalize_extractor_output(  # noqa: SLF001
        {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": {"css": ".hXU5Ud.aA5Mwe"},
            "source": "llm",
            "reason": "ok",
        },
        llm_mode="full",
    )
    assert out["selector_hint"]["stability"] == "brittle"
    assert out["confidence"] == "high"


def test_normalize_extractor_output_brittle_cached_selector_bypasses_on_strong_route(monkeypatch):
    """Cached selector confidence should not be downgraded when strong route bind is present."""

    def _threshold(key, default=None):
        values = {
            "extract_selector_stability_normalize_enabled": True,
            "extract_confidence_downgrade_on_brittle_selector": True,
            "extract_confidence_downgrade_min": "low",
        }
        return values.get(key, default)

    monkeypatch.setattr(ex, "get_threshold", _threshold)
    out = ex._normalize_extractor_output(  # noqa: SLF001
        {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": {"css": ".hXU5Ud.aA5Mwe"},
            "source": "cached_selector",
            "reason": "ok",
            "route_bound": True,
            "route_bind_support": "strong",
        },
        llm_mode="full",
    )
    assert out["selector_hint"]["stability"] == "brittle"
    assert out["confidence"] == "high"
