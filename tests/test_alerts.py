"""Tests for alert decision logic."""

from datetime import UTC, datetime

from core.alerts import dispatch_alert, evaluate_alert


def test_alert_triggers_on_price_drop_with_threshold():
    """Drop alerts should fire when configured thresholds are met."""
    cfg = {
        "enabled": True,
        "alert_direction": "drop",
        "min_absolute_change": 100.0,
        "min_percent_change": 5.0,
        "target_price": 0.0,
        "alert_on_first_observation": False,
        "alert_on_missing_price": False,
        "cooldown_minutes": 0,
    }
    decision = evaluate_alert(
        current_price=900.0,
        previous_price=1100.0,
        previous_created_at="2026-01-01T00:00:00",
        config=cfg,
    )
    assert decision["should_alert"] is True
    assert decision["reason"] == "threshold_met"


def test_alert_not_triggered_on_direction_mismatch():
    """Drop-only mode should ignore rising prices."""
    cfg = {
        "enabled": True,
        "alert_direction": "drop",
        "min_absolute_change": 1.0,
        "min_percent_change": 0.0,
        "target_price": 0.0,
        "alert_on_first_observation": False,
        "alert_on_missing_price": False,
        "cooldown_minutes": 0,
    }
    decision = evaluate_alert(
        current_price=1200.0,
        previous_price=1000.0,
        previous_created_at="2026-01-01T00:00:00",
        config=cfg,
    )
    assert decision["should_alert"] is False
    assert decision["reason"] == "direction_mismatch"


def test_alert_missing_price_when_enabled():
    """Missing price can trigger alert when explicitly enabled."""
    cfg = {
        "enabled": True,
        "alert_direction": "any",
        "min_absolute_change": 0.0,
        "min_percent_change": 0.0,
        "target_price": 0.0,
        "alert_on_first_observation": False,
        "alert_on_missing_price": True,
        "cooldown_minutes": 0,
    }
    decision = evaluate_alert(
        current_price=None,
        previous_price=1000.0,
        previous_created_at="2026-01-01T00:00:00",
        config=cfg,
    )
    assert decision["should_alert"] is True
    assert decision["reason"] == "missing_price"


def test_alert_triggers_when_target_price_reached_even_without_previous():
    """Target price should trigger alert on first observation."""
    cfg = {
        "enabled": True,
        "alert_direction": "drop",
        "min_absolute_change": 999999.0,
        "min_percent_change": 99.0,
        "target_price": 1000.0,
        "alert_on_first_observation": False,
        "alert_on_missing_price": False,
        "cooldown_minutes": 0,
    }
    decision = evaluate_alert(
        current_price=950.0,
        previous_price=None,
        previous_created_at=None,
        config=cfg,
    )
    assert decision["should_alert"] is True
    assert decision["reason"] == "target_price_reached"


def test_alert_missing_price_respects_cooldown_window():
    """Missing-price alerts should be rate-limited by cooldown."""
    cfg = {
        "enabled": True,
        "alert_direction": "any",
        "min_absolute_change": 0.0,
        "min_percent_change": 0.0,
        "target_price": 0.0,
        "alert_on_first_observation": False,
        "alert_on_missing_price": True,
        "cooldown_minutes": 30,
    }
    recent = datetime.now(UTC).isoformat()
    decision = evaluate_alert(
        current_price=None,
        previous_price=1000.0,
        previous_created_at=recent,
        config=cfg,
    )
    assert decision["should_alert"] is False
    assert decision["reason"] == "cooldown_active"


def test_alert_cooldown_handles_naive_legacy_timestamp_safely():
    """Naive timestamps should not crash cooldown arithmetic."""
    cfg = {
        "enabled": True,
        "alert_direction": "drop",
        "min_absolute_change": 10.0,
        "min_percent_change": 0.0,
        "target_price": 0.0,
        "alert_on_first_observation": False,
        "alert_on_missing_price": False,
        "cooldown_minutes": 60,
    }
    decision = evaluate_alert(
        current_price=900.0,
        previous_price=1000.0,
        previous_created_at="2026-03-06T10:00:00",
        config=cfg,
    )
    assert "should_alert" in decision


def test_dispatch_alert_unknown_channels_do_not_report_success():
    """Unsupported channel names should fail closed."""
    out = dispatch_alert(
        message="test",
        config={"enabled_channels": ["slack"]},
        service_key="google_flights",
    )
    assert out["ok"] is False
    assert out["reason"] == "no_channels_enabled"
