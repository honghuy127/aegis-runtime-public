#!/usr/bin/env python3
"""Quick smoke test for Tier-0 Model Router."""

import pytest
import sys
from pathlib import Path

pytestmark = [pytest.mark.llm]

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.model_router import ModelRouter, FailureEvent
from llm.code_model import PLANNER_MODEL, CODER_MODEL


def test_router_basic():
    """Test basic router instantiation and event recording."""
    router = ModelRouter()
    assert len(router.events) == 0, "Router should start with no events"

    # Record a route_fill_mismatch
    router.record_event("route_fill_mismatch", expected="HND", observed="ITM", role="dest")
    assert len(router.events) == 1, "Should have 1 event after recording"

    # Get summary
    summary = router.get_event_summary()
    assert summary == {"route_fill_mismatch": 1}, f"Unexpected summary: {summary}"
    print("✓ Basic event recording works")


def test_router_hard_rules():
    """Test hard routing rules."""
    router = ModelRouter()

    # Test: ≥2 route_fill_mismatch → planner
    router.record_event("route_fill_mismatch", expected="HND", observed="ITM", role="dest")
    router.record_event("route_fill_mismatch", expected="KIX", observed="ITM", role="dest")

    model, reason = router.decide_model("plan")
    assert model == PLANNER_MODEL, f"Expected {PLANNER_MODEL}, got {model}"
    assert reason == "repeated_route_mismatch", f"Expected repeated_route_mismatch, got {reason}"
    print("✓ Hard rule: ≥2 route_fill_mismatch → planner")

    # Test: foreign_timeout → coder
    router2 = ModelRouter()
    router2.record_event("foreign_timeout", elapsed_sec=5.0, remaining_sec=50.0)

    model, reason = router2.decide_model("plan")
    assert model == CODER_MODEL, f"Expected {CODER_MODEL}, got {model}"
    assert reason == "foreign_timeout_detected", f"Expected foreign_timeout_detected, got {reason}"
    print("✓ Hard rule: foreign_timeout → coder")

    # Test: stuck_step → planner
    router3 = ModelRouter()
    router3.record_event("stuck_step", step_index=5, action="fill", elapsed_ms=60000)

    model, reason = router3.decide_model("plan")
    assert model == PLANNER_MODEL, f"Expected {PLANNER_MODEL}, got {model}"
    assert reason == "stuck_step_detected", f"Expected stuck_step_detected, got {reason}"
    print("✓ Hard rule: stuck_step → planner")

    # Test: ui_commit_failed → planner
    router4 = ModelRouter()
    router4.record_event("ui_commit_failed", role="origin", selector="#origin-input")

    model, reason = router4.decide_model("plan")
    assert model == PLANNER_MODEL, f"Expected {PLANNER_MODEL}, got {model}"
    assert reason == "ui_commit_failed", f"Expected ui_commit_failed, got {reason}"
    print("✓ Hard rule: ui_commit_failed → planner")

    # Test: deadline_bug → coder
    router5 = ModelRouter()
    router5.record_event("deadline_bug", remaining_ms=100, configured_timeout_ms=120000)

    model, reason = router5.decide_model("plan")
    assert model == CODER_MODEL, f"Expected {CODER_MODEL}, got {model}"
    assert reason == "deadline_bug_detected", f"Expected deadline_bug_detected, got {reason}"
    print("✓ Hard rule: deadline_bug → coder")


def test_router_defaults():
    """Test default fallback behavior."""
    router = ModelRouter()

    # No events: plan task → planner
    model, reason = router.decide_model("plan")
    assert model == PLANNER_MODEL, f"Expected {PLANNER_MODEL}, got {model}"
    assert reason == "default_plan", f"Expected default_plan, got {reason}"
    print("✓ Default: plan task → planner")

    # No events: repair task → planner
    model, reason = router.decide_model("repair")
    assert model == PLANNER_MODEL, f"Expected {PLANNER_MODEL}, got {model}"
    assert reason == "default_repair", f"Expected default_repair, got {reason}"
    print("✓ Default: repair task → planner")

    # No events: extract task → coder
    model, reason = router.decide_model("extract")
    assert model == CODER_MODEL, f"Expected {CODER_MODEL}, got {model}"
    assert reason == "default_extract", f"Expected default_extract, got {reason}"
    print("✓ Default: extract task → coder")


def test_event_summary():
    """Test event summary aggregation."""
    router = ModelRouter()

    router.record_event("route_fill_mismatch", expected="A", observed="B")
    router.record_event("route_fill_mismatch", expected="C", observed="D")
    router.record_event("stuck_step", step_index=3, action="click")
    router.record_event("ui_commit_failed", role="dest")

    summary = router.get_event_summary()
    expected = {
        "route_fill_mismatch": 2,
        "stuck_step": 1,
        "ui_commit_failed": 1,
    }
    assert summary == expected, f"Expected {expected}, got {summary}"
    print("✓ Event summary aggregation works")


if __name__ == "__main__":
    print("Running Tier-0 Model Router tests...")
    print()

    test_router_basic()
    test_router_hard_rules()
    test_router_defaults()
    test_event_summary()

    print()
    print("✅ All router tests passed!")
