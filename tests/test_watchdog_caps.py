"""Tests for scenario/extraction watchdog cap helpers."""

from core import extractor as ex
from core import scenario_runner as sr
from tests.utils.dates import trip_dates


ONE_WAY_DEPART, _ = trip_dates(round_trip=False)


def test_wall_clock_cap_reached_helper():
    """Wall-clock helper should trigger only when cap is enabled and elapsed exceeded."""
    assert sr._wall_clock_cap_reached(started_at=0.0, cap_sec=10, now=10.1)
    assert not sr._wall_clock_cap_reached(started_at=0.0, cap_sec=10, now=9.9)
    assert not sr._wall_clock_cap_reached(started_at=0.0, cap_sec=0, now=999.0)


def test_extract_price_respects_wall_clock_cap_before_legacy(monkeypatch):
    """When watchdog cap is hit after plugin attempt, legacy extraction should be skipped."""
    monkeypatch.setattr(ex, "plugin_strategy_enabled", lambda: True)
    called = {"legacy": 0, "plugin": 0}

    def _plugin(**kwargs):  # noqa: ARG001
        called["plugin"] += 1
        return {}

    monkeypatch.setattr(ex, "run_plugin_extraction_router", _plugin)

    def _legacy(**kwargs):  # noqa: ARG001
        called["legacy"] += 1
        return {"price": 123, "currency": "JPY", "confidence": "high"}

    monkeypatch.setattr(ex, "extract_with_llm", _legacy)
    original_get_threshold = ex.get_threshold

    def _fake_get_threshold(key, default=None):
        if key == "extract_wall_clock_cap_sec":
            return 1
        return original_get_threshold(key, default)

    times = iter([0.0, 0.5, 2.0, 2.0])
    monkeypatch.setattr(ex, "get_threshold", _fake_get_threshold)
    monkeypatch.setattr(ex.time, "monotonic", lambda: next(times, 2.0))

    out = ex.extract_price(
        "<html><body></body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart=ONE_WAY_DEPART,
    )

    assert out["price"] is None
    assert out["confidence"] == "low"
    assert out["reason"] == "extract_wall_clock_cap"
    assert called["plugin"] == 1
    assert called["legacy"] == 0
