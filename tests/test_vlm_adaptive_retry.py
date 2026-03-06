"""Hermetic tests for VLM adaptive retry selection."""

import pytest

from llm import code_model as cm

pytestmark = [pytest.mark.vlm, pytest.mark.heavy]


def _install_threshold_overrides(monkeypatch, overrides):
    def _get_threshold(key, default=None):
        return overrides.get(key, default)

    monkeypatch.setattr(cm, "get_threshold", _get_threshold)
    monkeypatch.setattr(cm, "_threshold_bool", lambda key, default: bool(_get_threshold(key, default)))
    monkeypatch.setattr(cm, "_threshold_int", lambda key, default: int(_get_threshold(key, default)))
    monkeypatch.setattr(cm, "_threshold_float", lambda key, default: float(_get_threshold(key, default)))


def test_vlm_adaptive_retry_chooses_retry_on_configured_reason(monkeypatch):
    """Retry should run once on configured reason and win when it finds a price."""
    _install_threshold_overrides(
        monkeypatch,
        {
            "vlm_extract_adaptive_retry_enabled": True,
            "vlm_extract_adaptive_retry_max_attempts": 1,
            "vlm_extract_adaptive_retry_on_reasons": "non_flight_scope,price_not_found",
            "vlm_extract_adaptive_retry_variant_profile_primary": "default",
            "vlm_extract_adaptive_retry_variant_profile_retry": "diverse",
            "vlm_extract_adaptive_retry_timeout_backoff_ratio": 0.8,
            "vlm_extract_adaptive_retry_min_timeout_sec": 120,
        },
    )
    calls = []
    responses = [
        {"price": None, "currency": None, "confidence": "low", "reason": "non_flight_scope"},
        {"price": 12345.0, "currency": "JPY", "confidence": "medium", "reason": "price_found"},
    ]

    def _fake_once(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    monkeypatch.setattr(cm, "_extract_price_with_vlm_once", _fake_once)
    out = cm.extract_price_with_vlm(
        "/tmp/fake.png",
        site="google_flights",
        timeout_sec=300,
    )
    assert len(calls) == 2
    assert calls[0]["variant_profile"] == "default"
    assert calls[1]["variant_profile"] == "diverse"
    assert calls[1]["timeout_sec"] == 240
    assert out["price"] == 12345.0
    assert out["vlm_adaptive_retry_attempted"] is True
    assert out["vlm_adaptive_retry_chosen"] == "retry"
    assert out["vlm_adaptive_retry_reason"] == "non_flight_scope"


def test_vlm_adaptive_retry_keeps_primary_when_retry_not_better(monkeypatch):
    """Retry should not override primary when retry output does not improve score."""
    _install_threshold_overrides(
        monkeypatch,
        {
            "vlm_extract_adaptive_retry_enabled": True,
            "vlm_extract_adaptive_retry_max_attempts": 1,
            "vlm_extract_adaptive_retry_on_reasons": "fabricated_or_unreadable,price_not_found",
            "vlm_extract_adaptive_retry_variant_profile_primary": "default",
            "vlm_extract_adaptive_retry_variant_profile_retry": "diverse",
            "vlm_extract_adaptive_retry_timeout_backoff_ratio": 0.8,
            "vlm_extract_adaptive_retry_min_timeout_sec": 120,
        },
    )
    calls = []
    responses = [
        {"price": None, "currency": None, "confidence": "low", "reason": "fabricated_or_unreadable"},
        {"price": None, "currency": None, "confidence": "low", "reason": "price_not_found"},
    ]

    def _fake_once(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    monkeypatch.setattr(cm, "_extract_price_with_vlm_once", _fake_once)
    out = cm.extract_price_with_vlm("/tmp/fake.png", site="google_flights", timeout_sec=300)
    assert len(calls) == 2
    assert out["price"] is None
    assert out["reason"] == "fabricated_or_unreadable"
    assert out["vlm_adaptive_retry_attempted"] is True
    assert out["vlm_adaptive_retry_chosen"] == "primary"


def test_vlm_adaptive_retry_not_attempted_for_accepted_primary(monkeypatch):
    """Accepted primary result should return directly without retry."""
    _install_threshold_overrides(
        monkeypatch,
        {
            "vlm_extract_adaptive_retry_enabled": True,
            "vlm_extract_adaptive_retry_max_attempts": 1,
            "vlm_extract_adaptive_retry_on_reasons": "non_flight_scope,price_not_found",
            "vlm_extract_adaptive_retry_variant_profile_primary": "default",
            "vlm_extract_adaptive_retry_variant_profile_retry": "diverse",
        },
    )
    calls = []

    def _fake_once(**kwargs):
        calls.append(kwargs)
        return {"price": 45678.0, "currency": "JPY", "confidence": "high", "reason": "price_found"}

    monkeypatch.setattr(cm, "_extract_price_with_vlm_once", _fake_once)
    out = cm.extract_price_with_vlm("/tmp/fake.png", site="google_flights", timeout_sec=300)
    assert len(calls) == 1
    assert out["price"] == 45678.0
    assert out["vlm_adaptive_retry_attempted"] is False
    assert out["vlm_adaptive_retry_chosen"] == "primary"


def test_vlm_adaptive_retry_timeout_backoff_clamps_to_min(monkeypatch):
    """Retry timeout should be reduced by ratio then clamped by minimum timeout."""
    _install_threshold_overrides(
        monkeypatch,
        {
            "vlm_extract_adaptive_retry_enabled": True,
            "vlm_extract_adaptive_retry_max_attempts": 1,
            "vlm_extract_adaptive_retry_on_reasons": "price_not_found",
            "vlm_extract_adaptive_retry_variant_profile_primary": "default",
            "vlm_extract_adaptive_retry_variant_profile_retry": "diverse",
            "vlm_extract_adaptive_retry_timeout_backoff_ratio": 0.5,
            "vlm_extract_adaptive_retry_min_timeout_sec": 120,
        },
    )
    calls = []
    responses = [
        {"price": None, "currency": None, "confidence": "low", "reason": "price_not_found"},
        {"price": None, "currency": None, "confidence": "low", "reason": "price_not_found"},
    ]

    def _fake_once(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    monkeypatch.setattr(cm, "_extract_price_with_vlm_once", _fake_once)
    out = cm.extract_price_with_vlm("/tmp/fake.png", site="google_flights", timeout_sec=100)
    assert len(calls) == 2
    assert calls[1]["timeout_sec"] == 120
    assert out["vlm_adaptive_retry_attempted"] is True
