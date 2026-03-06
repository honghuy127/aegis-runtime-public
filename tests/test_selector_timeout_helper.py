"""Tests for selector timeout strategy centralization (Phase 1)."""

import pytest
from core.browser import apply_selector_timeout_strategy


def test_apply_selector_timeout_uses_wait_default():
    """Wait-type timeout should use browser_wait_selector_timeout_ms default."""
    timeout_ms = apply_selector_timeout_strategy(action_type="wait")
    # Default is 4000ms from thresholds.yaml
    assert timeout_ms == 4000


def test_apply_selector_timeout_uses_action_default():
    """Action-type timeout should use browser_action_selector_timeout_ms default."""
    timeout_ms = apply_selector_timeout_strategy(action_type="action")
    # Default is 4000ms from thresholds.yaml
    assert timeout_ms == 4000


def test_apply_selector_timeout_respects_explicit_value():
    """Explicit timeout_ms should be used when provided."""
    timeout_ms = apply_selector_timeout_strategy(base_timeout_ms=3500, action_type="wait")
    assert timeout_ms == 3500


def test_apply_selector_timeout_clamps_below_minimum():
    """Timeout below minimum should be clamped to browser_selector_timeout_min_ms."""
    timeout_ms = apply_selector_timeout_strategy(base_timeout_ms=200, action_type="wait")
    # Minimum is 800ms from thresholds.yaml
    assert timeout_ms == 800


def test_apply_selector_timeout_permits_minimum_boundary():
    """Timeout equal to minimum should pass through unchanged."""
    timeout_ms = apply_selector_timeout_strategy(base_timeout_ms=800, action_type="wait")
    assert timeout_ms == 800


def test_apply_selector_timeout_accepts_reasonable_value():
    """Timeout within reasonable range should pass through."""
    timeout_ms = apply_selector_timeout_strategy(base_timeout_ms=2000, action_type="wait")
    assert timeout_ms == 2000


def test_apply_selector_timeout_defaults_none_to_action_default():
    """None base_timeout_ms should default to action-type default."""
    timeout_ms = apply_selector_timeout_strategy(base_timeout_ms=None, action_type="action")
    assert timeout_ms == 4000


def test_apply_selector_timeout_prevents_aggressive_values():
    """Helper should prevent accidentally aggressive (<800ms) timeouts."""
    for aggressive_ms in [1, 10, 100, 500, 799]:
        timeout_ms = apply_selector_timeout_strategy(base_timeout_ms=aggressive_ms)
        assert timeout_ms >= 800, f"Failed for {aggressive_ms}ms: got {timeout_ms}ms"


def test_apply_selector_timeout_returns_integer():
    """Timeout should always be an integer."""
    timeout_ms = apply_selector_timeout_strategy(base_timeout_ms=3500.7)
    assert isinstance(timeout_ms, int)
    assert timeout_ms == 3500


def test_apply_selector_timeout_accepts_threshold_key_string():
    """Threshold key strings should resolve via get_threshold and clamp to minimum."""
    timeout_ms = apply_selector_timeout_strategy(
        base_timeout_ms="browser_optional_click_timeout_ms",
        action_type="action",
        site_key="google_flights",
        is_optional_click=True,
    )
    assert timeout_ms >= 800
