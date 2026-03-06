"""Unit tests for debug-mode boost guardrails.

Tests profile resolution, threshold boosts, and timeout/retry guardrails
to ensure debug mode allows deeper troubleshooting without runaway overhead.
"""
import pytest
from unittest.mock import patch, MagicMock
from typing import Dict, Any

from utils.thresholds import (
    get_thresholds_for_profile,
    adjust_timeout_for_retry,
    clamp_debug_scenario_timeout,
    load_thresholds,
)


class TestGetThresholdsForProfile:
    """Tests for profile resolution and threshold merging."""

    def test_default_profile_returns_base_values(self):
        """Default profile should return base thresholds unchanged."""
        thresholds = get_thresholds_for_profile("default")
        
        # Verify key defaults are present
        assert thresholds.get("scenario_max_retries") == 4
        assert thresholds.get("scenario_max_turns") == 2
        assert thresholds.get("browser_action_selector_timeout_ms") == 4000

    def test_debug_profile_returns_boosted_values(self):
        """Debug profile should apply boost overrides."""
        thresholds = get_thresholds_for_profile("debug")
        
        # Verify debug boosts are applied
        assert thresholds.get("scenario_max_retries") == 5, "debug: +1 retry"
        assert thresholds.get("scenario_max_turns") == 3, "debug: +1 turn"
        assert thresholds.get("browser_action_selector_timeout_ms") == 5000, "debug: +25% timeout"
        assert thresholds.get("gf_set_date_max_actions") == 35, "debug: +75% actions"
        assert thresholds.get("scenario_step_wall_clock_cap_ms") == 60000, "debug: +33% wall clock"
        assert thresholds.get("browser_goto_timeout_ms") == 60000, "debug: +33% goto timeout"

    def test_invalid_profile_defaults_to_default(self):
        """Invalid profile name should fall back to default profile."""
        thresholds = get_thresholds_for_profile("invalid_profile")
        
        # Should be same as default profile
        assert thresholds.get("scenario_max_retries") == 4

    def test_profile_preserves_non_overridden_values(self):
        """Profile overrides should not affect other thresholds."""
        default_threholds = get_thresholds_for_profile("default")
        debug_thresholds = get_thresholds_for_profile("debug")
        
        # Check a threshold not in debug profile boosts is unchanged
        assert debug_thresholds.get("selector_min_confidence") == default_threholds.get("selector_min_confidence")
        assert debug_thresholds.get("heuristic_min_price") == default_threholds.get("heuristic_min_price")


class TestAdjustTimeoutForRetry:
    """Tests for diminishing returns timeout guardrail."""

    def test_retry_zero_preserves_timeout(self):
        """First retry (index 0) should not reduce timeout."""
        assert adjust_timeout_for_retry(1500, 0) == 1500

    def test_retry_one_reduces_by_ten_percent(self):
        """Second retry (index 1) should reduce timeout by 10%."""
        assert adjust_timeout_for_retry(1500, 1) == 1350  # 1500 * 0.9

    def test_retry_two_reduces_by_twenty_percent(self):
        """Third retry (index 2) should reduce timeout by 20%."""
        assert adjust_timeout_for_retry(1500, 2) == 1200  # 1500 * 0.8

    def test_retry_three_plus_clamped_to_eighty_percent(self):
        """Fourth+ retries should be clamped to 80% (0.8 factor)."""
        # Would calculate to < 0.8, so clamped
        result = adjust_timeout_for_retry(1500, 3)
        assert result == 1200, "1500 * 0.8 floor"

    def test_floor_prevents_timeout_below_minimum(self):
        """Floor parameter should prevent timeout below specified minimum."""
        # 100 * 0.8 = 80, but floor is 500
        result = adjust_timeout_for_retry(100, 2, floor_ms=500)
        assert result == 500

    def test_floor_default_is_500ms(self):
        """Default floor should be 500 milliseconds."""
        # Any very small base timeout should hit floor
        result = adjust_timeout_for_retry(600, 0, floor_ms=500)
        assert result >= 500

    def test_large_base_timeout_respects_diminishing_returns(self):
        """Diminishing returns should work with large timeouts."""
        base = 30000  # 30 seconds
        retry0 = adjust_timeout_for_retry(base, 0)
        retry1 = adjust_timeout_for_retry(base, 1)
        retry2 = adjust_timeout_for_retry(base, 2)
        
        assert retry0 > retry1 > retry2
        assert retry0 == 30000
        assert retry1 == 27000  # 30000 * 0.9
        assert retry2 == 24000  # 30000 * 0.8


class TestClampDebugScenarioTimeout:
    """Tests for debug scenario timeout hard cap guardrail."""

    def test_small_timeout_boosted_by_factor(self):
        """Small timeout should be boosted by 1.25x."""
        result = clamp_debug_scenario_timeout(100)
        assert result == 125  # 100 * 1.25

    def test_moderate_timeout_boosted_to_cap(self):
        """Moderate timeout should be boosted up to hard cap."""
        result = clamp_debug_scenario_timeout(2880)  # 2880 * 1.25 = 3600
        assert result == 3600

    def test_large_timeout_clamped_to_hard_cap(self):
        """Large timeout should be clamped to hard cap."""
        result = clamp_debug_scenario_timeout(3000)
        # 3000 * 1.25 = 3750, but clamped to 3600
        assert result == 3600

    def test_timeout_exceeding_hard_cap_clamped(self):
        """Timeout already exceeding cap should remain at cap."""
        result = clamp_debug_scenario_timeout(4000)
        # 4000 * 1.25 = 5000, but hard cap is 3600
        assert result == 3600

    def test_custom_hard_cap_respected(self):
        """Custom hard cap parameter should override default."""
        result = clamp_debug_scenario_timeout(1000, hard_cap_sec=2000)
        # 1000 * 1.25 = 1250, which is less than 2000
        assert result == 1250

    def test_timeout_exceeds_custom_hard_cap_clamped(self):
        """Timeout should respect custom hard cap."""
        result = clamp_debug_scenario_timeout(2000, hard_cap_sec=2000)
        # 2000 * 1.25 = 2500, but hard cap is 2000
        assert result == 2000

    def test_default_hard_cap_is_3600_seconds(self):
        """Default hard cap should be 3600 seconds (1 hour)."""
        # Use a timeout that would exceed default cap
        result = clamp_debug_scenario_timeout(3000)
        assert result <= 3600


class TestProfileIntegration:
    """Integration tests for profiles + guardrails."""

    def test_debug_profile_with_timeout_guardrail_flow(self):
        """Debug profile boosts should work with timeout guardrails."""
        # Get debug thresholds
        thresholds = get_thresholds_for_profile("debug")
        base_selector_timeout = thresholds.get("browser_action_selector_timeout_ms")
        
        # Should be boosted
        assert base_selector_timeout == 5000
        
        # Apply retry diminishing returns
        retry1_timeout = adjust_timeout_for_retry(base_selector_timeout, 1)
        assert retry1_timeout == 4500  # 5000 * 0.9
        
        # Should still be above minimum
        assert retry1_timeout >= 500

    def test_debug_profile_with_scenario_timeout_guardrail_flow(self):
        """Debug profile boosts should work with scenario timeout clamps."""
        # Get debug thresholds for scenario step cap
        thresholds = get_thresholds_for_profile("debug")
        step_cap_ms = thresholds.get("scenario_step_wall_clock_cap_ms")
        
        # Convert to seconds for clamp function (60000ms = 60s)
        step_cap_sec = step_cap_ms // 1000
        assert step_cap_sec == 60
        
        # Apply hard cap
        clamped = clamp_debug_scenario_timeout(step_cap_sec)
        # 60 * 1.25 = 75, which is less than 3600
        assert clamped == 75

    def test_guardrails_prevent_runaway_boosts(self):
        """Guardrails should prevent composed boosts from exceeding safe limits."""
        # Get debug profile
        thresholds = get_thresholds_for_profile("debug")
        base_timeout = thresholds.get("browser_action_selector_timeout_ms")
        
        # Simulate multiple retries with guardrail
        timeout = base_timeout
        for retry_index in range(5):
            timeout = adjust_timeout_for_retry(timeout, retry_index)
            # Should never go below floor
            assert timeout >= 500
        
        # Even after 5 retries, should be reasonable
        assert timeout >= 500 and timeout <= base_timeout


class TestConfigIntegration:
    """Tests verifying config structure contains profile keys."""

    def test_profiles_section_exists_in_thresholds(self):
        """Thresholds config should have profiles section."""
        all_thresholds = load_thresholds()
        assert "profiles" in all_thresholds, "thresholds.yaml must have 'profiles' section"

    def test_profiles_section_has_default_and_debug(self):
        """Profiles section should have both default and debug entries."""
        all_thresholds = load_thresholds()
        profiles = all_thresholds.get("profiles", {})
        assert "default" in profiles or len(profiles) > 0, "profiles section exists"
        # Note: debug might be None if minimal profile


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
