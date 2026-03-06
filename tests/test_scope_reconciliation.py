"""Tests for dual-layer scope reconciliation (Phase 3)."""

import pytest
from core.scope_reconciliation import (
    ScopeOverrideTracker,
    evaluate_vlm_scope_override,
    reconcile_scope_layers,
)


def test_scope_override_tracker_initializes():
    """Tracker should initialize with default settings."""
    tracker = ScopeOverrideTracker()
    assert tracker.max_overrides == 2
    assert tracker.context_key == "_scope_override_count"


def test_scope_override_tracker_get_count_empty():
    """Get count should return 0 for empty context."""
    tracker = ScopeOverrideTracker()
    assert tracker.get_override_count({}) == 0
    assert tracker.get_override_count(None) == 0


def test_scope_override_tracker_increment():
    """Incrementing should update context."""
    tracker = ScopeOverrideTracker()
    ctx = {}
    count1 = tracker.increment_override_count(ctx)
    assert count1 == 1
    assert tracker.get_override_count(ctx) == 1

    count2 = tracker.increment_override_count(ctx)
    assert count2 == 2
    assert tracker.get_override_count(ctx) == 2


def test_scope_override_tracker_can_apply():
    """Can apply should respect max_overrides limit."""
    tracker = ScopeOverrideTracker(max_overrides=2)
    ctx = {}

    assert tracker.can_apply_override(ctx) is True
    tracker.increment_override_count(ctx)
    assert tracker.can_apply_override(ctx) is True
    tracker.increment_override_count(ctx)
    assert tracker.can_apply_override(ctx) is False


def test_evaluate_vlm_scope_override_affirms_flight():
    """VLM affirming flight should trigger override on heuristic fail."""
    vlm_signal = {"page_class": "flight_only"}
    ctx = {}
    tracker = ScopeOverrideTracker()

    should_override, reason = evaluate_vlm_scope_override(vlm_signal, "fail", ctx)
    assert should_override is True
    assert "override" in reason
    assert tracker.get_override_count(ctx) == 1


def test_evaluate_vlm_scope_override_no_need():
    """VLM affirm with heuristic pass needs no override."""
    vlm_signal = {"page_class": "flight_only"}
    ctx = {}

    should_override, reason = evaluate_vlm_scope_override(vlm_signal, "pass", ctx)
    assert should_override is False
    assert "needed" in reason


def test_evaluate_vlm_scope_override_limit_reached():
    """Override should be rejected once limit reached."""
    vlm_signal = {"page_class": "flight_only"}
    tracker = ScopeOverrideTracker(max_overrides=1)
    ctx = {tracker.context_key: 1}  # Already at limit

    should_override, reason = evaluate_vlm_scope_override(
        vlm_signal, "fail", ctx, max_overrides=1
    )
    assert should_override is False
    assert "limit" in reason


def test_evaluate_vlm_scope_override_unavailable():
    """Missing VLM signal should not override."""
    ctx = {}
    should_override, reason = evaluate_vlm_scope_override(None, "fail", ctx)
    assert should_override is False
    assert "unavailable" in reason


def test_reconcile_scope_layers_both_pass():
    """Both layers passing should resolve as pass."""
    ctx = {}
    result = reconcile_scope_layers(
        method1_verdict="pass",
        method1_basis="heuristic",
        method2_signal={"page_class": "flight_only"},
        method2_basis="vlm",
        context=ctx,
    )
    assert result["resolved"] is True
    assert result["final_verdict"] == "pass"
    assert result["override_applied"] is False


def test_reconcile_scope_layers_heuristic_fail_vlm_save():
    """Heuristic fail + VLM affirm should resolve with override."""
    ctx = {}
    result = reconcile_scope_layers(
        method1_verdict="fail",
        method1_basis="heuristic",
        method2_signal={"page_class": "flight_only"},
        method2_basis="vlm",
        context=ctx,
    )
    assert result["resolved"] is True
    assert result["final_verdict"] == "pass"
    assert result["override_applied"] is True
    assert result["override_count"] == 1


def test_reconcile_scope_layers_unresolved_conflict():
    """Unresolvable conflict should return unresolved."""
    tracker = ScopeOverrideTracker()
    ctx = {tracker.context_key: 2}  # At limit
    result = reconcile_scope_layers(
        method1_verdict="fail",
        method1_basis="heuristic",
        method2_signal={"page_class": "flight_only"},
        method2_basis="vlm",
        context=ctx,
    )
    assert result["resolved"] is False
    assert result["final_verdict"] == "fail"


def test_reconcile_scope_layers_vlm_weak_signal():
    """Weak VLM signal with heuristic fail might resolve with override."""
    ctx = {}
    result = reconcile_scope_layers(
        method1_verdict="fail",
        method1_basis="heuristic",
        method2_signal={"page_class": "flight_hotel_package"},  # Weak for flight
        method2_basis="vlm",
        context=ctx,
    )
    # Weak signal with fail should attempt override
    assert result["resolved"] is True
    assert result["final_verdict"] == "pass"
    assert result["override_applied"] is True


def test_reconcile_scope_layers_override_count_tracking():
    """Override count should increase only when override applied."""
    ctx = {}

    # First call with override
    result1 = reconcile_scope_layers("fail", "heuristic", {"page_class": "flight_only"}, "vlm", ctx)
    assert result1["override_applied"] is True
    assert result1["override_count"] == 1

    # Second call with another override
    result2 = reconcile_scope_layers("fail", "heuristic", {"page_class": "flight_only"}, "vlm", ctx)
    assert result2["override_applied"] is True
    assert result2["override_count"] == 2

    # Third call should fail due to limit
    result3 = reconcile_scope_layers("fail", "heuristic", {"page_class": "flight_only"}, "vlm", ctx)
    assert result3["resolved"] is False
    assert result3["override_count"] == 2
