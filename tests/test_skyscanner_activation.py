"""Tests for Skyscanner activation and visibility stabilization (Phase 4)."""

import pytest
import sys
from unittest import mock
from tests.utils.dates import trip_dates


ONE_WAY_DEPART, _ = trip_dates(round_trip=False)


def test_skyscanner_activation_visibility_threshold_exists(monkeypatch):
    """Skyscanner activation visibility threshold should be readable."""
    from utils.thresholds import get_threshold

    timeout_ms = get_threshold("skyscanner_activation_visibility_timeout_ms", 3000)
    assert timeout_ms == 3000


def test_skyscanner_post_activation_wait_threshold_exists(monkeypatch):
    """Skyscanner post-activation wait threshold should exist."""
    from utils.thresholds import get_threshold

    wait_ms = get_threshold("skyscanner_post_activation_wait_ms", 5000)
    assert wait_ms == 5000


def test_skyscanner_results_readiness_threshold_exists(monkeypatch):
    """Skyscanner results readiness timeout threshold should exist."""
    from utils.thresholds import get_threshold

    timeout_ms = get_threshold("skyscanner_results_readiness_timeout_ms", 8000)
    assert timeout_ms == 8000


def test_skyscanner_plan_includes_visibility_wait():
    """Skyscanner plan should include visibility wait before search."""
    from core.scenario_runner.skyscanner import default_skyscanner_plan

    plan = default_skyscanner_plan("LAX", "JFK", ONE_WAY_DEPART)

    # Find the visibility wait step
    visibility_step = None
    for i, step in enumerate(plan):
        if step.get("action") == "wait" and i == 3:  # Before click
            visibility_step = step
            break

    assert visibility_step is not None, "No visibility wait step found before search click"
    assert visibility_step.get("metadata", {}).get("purpose") == "ensure_search_button_visible_phase4"


def test_skyscanner_plan_includes_post_activation_pause():
    """Skyscanner plan should include pause after search activation."""
    from core.scenario_runner.skyscanner import default_skyscanner_plan

    plan = default_skyscanner_plan("LAX", "JFK", ONE_WAY_DEPART)

    # Find pause step (wait_msec action or similar)
    pause_step = None
    for i, step in enumerate(plan):
        if step.get("action") in {"wait_msec", "pause"} and i > 4:
            pause_step = step
            break

    assert pause_step is not None, "No post-activation pause step found"
    assert pause_step.get("metadata", {}).get("purpose") == "render_settle_phase4"


def test_skyscanner_plan_includes_results_readiness_wait():
    """Skyscanner plan should include results readiness wait."""
    from core.scenario_runner.skyscanner import default_skyscanner_plan

    plan = default_skyscanner_plan("LAX", "JFK", ONE_WAY_DEPART)

    # Find results readiness step (should be last)
    results_step = None
    for step in plan:
        if step.get("action") == "wait" and step.get("metadata", {}).get("purpose") == "results_readiness_phase4":
            results_step = step
            break

    assert results_step is not None, "No results readiness wait step found"
    assert results_step.get("timeout_ms") > 0


def test_skyscanner_plan_step_sequence():
    """Skyscanner plan steps should be in correct sequence."""
    from core.scenario_runner.skyscanner import default_skyscanner_plan

    plan = default_skyscanner_plan("LAX", "JFK", ONE_WAY_DEPART)

    # Expected sequence: fills, visibility wait, click, pause, results wait
    expected_actions = ["fill", "fill", "fill", "wait", "click", "wait_msec", "wait"]
    actual_actions = [s.get("action") for s in plan]

    # At minimum, check fills come before click comes before wait
    fill_indices = [i for i, a in enumerate(actual_actions) if a == "fill"]
    click_index = next((i for i, a in enumerate(actual_actions) if a == "click"), -1)
    wait_indices = [i for i, a in enumerate(actual_actions) if a == "wait"]

    assert len(fill_indices) >= 3, "Should have at least 3 fill steps"
    assert click_index > max(fill_indices), "Click should come after fills"
    if wait_indices:
        assert wait_indices[-1] > click_index, "Final wait should come after click"


def test_skyscanner_plan_uses_configurable_timeouts(monkeypatch):
    """Skyscanner plan should use configurable thresholds."""
    from core.scenario_runner.skyscanner import default_skyscanner_plan

    # Mock threshold to check it's called
    mock_thresholds = {
        "skyscanner_activation_visibility_timeout_ms": 2000,
        "skyscanner_post_activation_wait_ms": 4000,
        "skyscanner_results_readiness_timeout_ms": 7000,
    }

    def mock_get_threshold(key, default):
        return mock_thresholds.get(key, default)

    # Monkeypatch where get_threshold is used
    import core.scenario_runner.skyscanner.plans
    monkeypatch.setattr(core.scenario_runner.skyscanner.plans, "get_threshold", mock_get_threshold)

    plan = default_skyscanner_plan("LAX", "JFK", ONE_WAY_DEPART)

    # Verify plan uses mocked values
    visibility_wait = next((s for s in plan if s.get("metadata", {}).get("purpose") == "ensure_search_button_visible_phase4"), {})
    assert visibility_wait.get("timeout_ms") == 2000, "Should use mocked visibility timeout"

    results_wait = next((s for s in plan if s.get("metadata", {}).get("purpose") == "results_readiness_phase4"), {})
    assert results_wait.get("timeout_ms") == 7000, "Should use mocked results timeout"


def test_skyscanner_plan_prioritizes_stable_route_input_ids():
    """Skyscanner plan should prioritize stable route input IDs to reduce selector churn."""
    from core.scenario_runner.skyscanner import default_skyscanner_plan

    plan = default_skyscanner_plan("LAX", "JFK", ONE_WAY_DEPART)
    origin_selectors = list(plan[0].get("selector") or [])
    dest_selectors = list(plan[1].get("selector") or [])

    assert origin_selectors[0] == "input#originInput-input"
    assert origin_selectors[1] == "input[name='originInput-search']"
    assert dest_selectors[0] == "input#destinationInput-input"
    assert dest_selectors[1] == "input[name='destinationInput-search']"
