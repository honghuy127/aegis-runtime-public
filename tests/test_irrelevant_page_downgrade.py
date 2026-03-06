"""Tests for irrelevant_page VLM downgrade logic (Phase 3.1)."""

import pytest
from core.scope_reconciliation import evaluate_irrelevant_page_downgrade, ScopeOverrideTracker


def test_irrelevant_page_downgrade_flight_results_high_confidence():
    """VLM flights_results with high confidence downgrades irrelevant_page block."""
    vlm_probe = {
        "page_kind": "flights_results",
        "confidence": "high",
        "evidence": "Flight prices visible",
    }
    context = {}
    result = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
        max_overrides=2,
    )
    assert result["should_downgrade"] is True
    assert result["override_applied"] is True
    assert result["override_count"] == 1
    assert "vlm_flights_results_downgrade_irrelevant_page_1" in result["reason"]


def test_irrelevant_page_downgrade_flight_results_medium_confidence():
    """VLM flights_results with medium confidence also downgrades."""
    vlm_probe = {
        "page_kind": "flights_results",
        "confidence": "medium",
    }
    context = {}
    result = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
    )
    assert result["should_downgrade"] is True
    assert result["override_count"] == 1


def test_irrelevant_page_downgrade_low_confidence_blocked():
    """VLM flights_results with low confidence does NOT downgrade."""
    vlm_probe = {
        "page_kind": "flights_results",
        "confidence": "low",
    }
    context = {}
    result = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
    )
    assert result["should_downgrade"] is False
    assert result["override_applied"] is False
    assert "confidence_too_low" in result["reason"]


def test_irrelevant_page_downgrade_flight_only_page_kind():
    """VLM flight_only page_kind also triggers downgrade."""
    vlm_probe = {
        "page_kind": "flight_only",
        "confidence": "medium",
    }
    context = {}
    result = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
    )
    assert result["should_downgrade"] is True
    assert result["override_count"] == 1


def test_irrelevant_page_downgrade_non_flight_page_kind_blocked():
    """Non-flight VLM page_kind does NOT downgrade."""
    vlm_probe = {
        "page_kind": "garbage_page",
        "confidence": "high",
    }
    context = {}
    result = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
    )
    assert result["should_downgrade"] is False
    assert "not_flight" in result["reason"]


def test_irrelevant_page_downgrade_non_irrelevant_heuristic_blocked():
    """Block reason other than irrelevant_page does NOT downgrade."""
    vlm_probe = {
        "page_kind": "flights_results",
        "confidence": "high",
    }
    context = {}
    result = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_garbage_page",
        context=context,
    )
    assert result["should_downgrade"] is False
    assert "not_irrelevant_page" in result["reason"]


def test_irrelevant_page_downgrade_vlm_unavailable():
    """Missing VLM probe does NOT downgrade."""
    context = {}
    result = evaluate_irrelevant_page_downgrade(
        vlm_probe=None,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
    )
    assert result["should_downgrade"] is False
    assert result["override_applied"] is False


def test_irrelevant_page_downgrade_override_limit_reached():
    """Max 2 overrides per scenario prevents further downgrades."""
    vlm_probe = {
        "page_kind": "flights_results",
        "confidence": "high",
    }
    context = {"_scope_override_count": 2}  # Already at limit
    result = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
        max_overrides=2,
    )
    assert result["should_downgrade"] is False
    assert "override_limit_reached" in result["reason"]


def test_irrelevant_page_downgrade_increments_counter():
    """Multiple downgrades increment the override counter correctly."""
    vlm_probe = {
        "page_kind": "flights_results",
        "confidence": "medium",
    }
    context = {}

    # First downgrade
    result1 = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
        max_overrides=2,
    )
    assert result1["override_count"] == 1

    # Second downgrade (still allowed)
    result2 = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
        max_overrides=2,
    )
    assert result2["override_count"] == 2
    assert result2["should_downgrade"] is True

    # Third downgrade (blocked by limit)
    result3 = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
        max_overrides=2,
    )
    assert result3["should_downgrade"] is False
    assert "override_limit_reached" in result3["reason"]


def test_irrelevant_page_downgrade_empty_vlm_probe():
    """Empty VLM probe does NOT downgrade."""
    context = {}
    result = evaluate_irrelevant_page_downgrade(
        vlm_probe={},
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
    )
    assert result["should_downgrade"] is False


def test_irrelevant_page_downgrade_case_insensitive_inputs():
    """Function handles case variations in VLM inputs."""
    vlm_probe = {
        "page_kind": "FLIGHTS_RESULTS",  # uppercase
        "confidence": "MEDIUM",  # uppercase
    }
    context = {}
    result = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
    )
    assert result["should_downgrade"] is True
    assert result["override_count"] == 1


def test_irrelevant_page_downgrade_preserves_context():
    """Override count persists in shared context dict."""
    vlm_probe = {
        "page_kind": "flights_results",
        "confidence": "high",
    }
    context = {"user_id": "test_user", "scenario_id": "test_scenario"}

    result = evaluate_irrelevant_page_downgrade(
        vlm_probe=vlm_probe,
        heuristic_reason="scope_guard_non_flight_irrelevant_page",
        context=context,
    )

    # Context should have override count tracked
    assert context["_scope_override_count"] == 1
    # Original context keys should be preserved
    assert context["user_id"] == "test_user"
    assert context["scenario_id"] == "test_scenario"


def test_optional_click_timeout_minimum_safety():
    """Optional click timeout never falls below 300ms minimum (Task D safety)."""
    from core.scenario_runner import _optional_click_timeout_ms
    from utils.thresholds import get_threshold

    # Get the minimum threshold
    min_ms = get_threshold("browser_selector_timeout_min_ms", 800)

    # Call with various site keys
    for site_key in ["google_flights", "unknown_site", None, ""]:
        timeout = _optional_click_timeout_ms(site_key or "")
        assert timeout >= min_ms, f"Optional click timeout {timeout}ms is below minimum {min_ms}ms for site {site_key}"


def test_selector_timeout_normalization_clamps_low_values():
    """_normalize_selector_timeout_ms properly clamps very low timeout values."""
    from core.scenario_runner import _normalize_selector_timeout_ms
    from utils.thresholds import get_threshold

    min_ms = get_threshold("browser_selector_timeout_min_ms", 800)

    # Test with very low requested values
    for requested in [1, 5, 10, 25, 50, 100]:
        result = _normalize_selector_timeout_ms(requested)
        assert result >= min_ms, f"Normalization failed for {requested}ms: got {result}ms, expected >={min_ms}ms"
