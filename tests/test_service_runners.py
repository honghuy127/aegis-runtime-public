"""Tests for service runner architecture and implementations.

Validates that:
1. Service runners can be instantiated and registered
2. ServiceRunner interface is properly implemented
3. Google Flights and Skyscanner runners work correctly
4. Backward compatibility is maintained
"""

import pytest
from tests.utils.dates import trip_dates
from core.service_runners import (
    ServiceRunner,
    GoogleFlightsRunner,
    SkyscannerRunner,
    get_service_runner,
    list_registered_services,
    is_service_supported,
)

ONE_WAY_DEPART, _ = trip_dates(round_trip=False)


class TestServiceRunnerRegistry:
    """Test service runner registry operations."""

    def test_list_registered_services(self):
        """Verify all services are registered."""
        services = list_registered_services()
        assert "google_flights" in services
        assert "skyscanner" in services

    def test_get_google_flights_runner(self):
        """Verify Google Flights runner can be retrieved."""
        runner = get_service_runner("google_flights")
        assert runner is not None
        assert isinstance(runner, GoogleFlightsRunner)

    def test_get_skyscanner_runner(self):
        """Verify Skyscanner runner can be retrieved."""
        runner = get_service_runner("skyscanner")
        assert runner is not None
        assert isinstance(runner, SkyscannerRunner)

    def test_get_nonexistent_service(self):
        """Verify nonexistent service returns None."""
        runner = get_service_runner("nonexistent_service")
        assert runner is None

    def test_is_service_supported(self):
        """Verify service support checking."""
        assert is_service_supported("google_flights")
        assert is_service_supported("skyscanner")
        assert not is_service_supported("nonexistent")

    def test_singleton_caching(self):
        """Verify runners are cached as singletons."""
        runner1 = get_service_runner("google_flights")
        runner2 = get_service_runner("google_flights")
        assert runner1 is runner2, "Runners should be cached as singletons"


class TestGoogleFlightsRunner:
    """Test Google Flights runner implementation."""

    def test_service_key(self):
        """Verify service key is correct."""
        runner = GoogleFlightsRunner()
        assert runner.service_key == "google_flights"

    def test_get_default_plan(self):
        """Verify default plan generation works."""
        runner = GoogleFlightsRunner()
        plan = runner.get_default_plan("JFK", "LHR", ONE_WAY_DEPART)
        assert isinstance(plan, list)
        assert len(plan) > 0

    def test_get_route_core_before_date_gate(self):
        """Verify route core gate works."""
        runner = GoogleFlightsRunner()
        result = runner.get_route_core_before_date_gate(
            html="<html></html>",
            expected_origin="JFK",
            expected_dest="LHR",
        )
        assert isinstance(result, dict)
        assert "ok" in result
        assert "reason" in result

    def test_verify_after_fill_with_value(self):
        """Verify after-fill verification accepts filled values."""
        runner = GoogleFlightsRunner()
        result = runner.verify_after_fill(
            None,
            filled_role="origin",
            filled_value="JFK",
        )
        assert result.get("ok") is True

    def test_verify_after_fill_empty_value(self):
        """Verify after-fill rejects empty values."""
        runner = GoogleFlightsRunner()
        result = runner.verify_after_fill(
            None,
            filled_role="origin",
            filled_value="",
        )
        assert result.get("ok") is False

    def test_get_recovery_limits(self):
        """Verify recovery limits are returned."""
        runner = GoogleFlightsRunner()
        limits = runner.get_recovery_limits()
        assert isinstance(limits, dict)
        assert "enabled" in limits
        assert "max_vlm" in limits

    def test_build_recovery_plan(self):
        """Verify recovery plan can be built."""
        runner = GoogleFlightsRunner()
        plan = runner.build_recovery_plan("JFK", "LHR", ONE_WAY_DEPART)
        assert isinstance(plan, list)

    def test_get_locale_aware_selectors(self):
        """Verify locale-aware selectors are returned."""
        runner = GoogleFlightsRunner()
        selectors = runner.get_locale_aware_selector("origin", action="fill")
        assert isinstance(selectors, list)

    def test_threshold_scope(self):
        """Verify threshold scope is correct."""
        runner = GoogleFlightsRunner()
        scope = runner.get_threshold_scope()
        assert scope == "google_flights"


class TestSkyscannerRunner:
    """Test Skyscanner runner implementation."""

    def test_service_key(self):
        """Verify service key is correct."""
        runner = SkyscannerRunner()
        assert runner.service_key == "skyscanner"

    def test_get_default_plan(self):
        """Verify default plan generation works."""
        runner = SkyscannerRunner()
        plan = runner.get_default_plan("LHR", "CDG", ONE_WAY_DEPART)
        assert isinstance(plan, list)
        assert len(plan) > 0

    def test_verify_after_fill(self):
        """Verify Skyscanner accepts filled values."""
        runner = SkyscannerRunner()
        result = runner.verify_after_fill(
            None,
            filled_role="origin",
            filled_value="LHR",
        )
        assert result.get("ok") is True

    def test_get_recovery_limits(self):
        """Verify recovery limits (disabled for Skyscanner)."""
        runner = SkyscannerRunner()
        limits = runner.get_recovery_limits()
        assert limits.get("enabled") is False

    def test_threshold_scope(self):
        """Verify threshold scope is correct."""
        runner = SkyscannerRunner()
        scope = runner.get_threshold_scope()
        assert scope == "skyscanner"


class TestServiceRunnerInterface:
    """Test that runners implement the ServiceRunner interface."""

    def test_google_flights_implements_interface(self):
        """Verify GoogleFlightsRunner implements ServiceRunner."""
        runner = GoogleFlightsRunner()
        assert isinstance(runner, ServiceRunner)

    def test_skyscanner_implements_interface(self):
        """Verify SkyscannerRunner implements ServiceRunner."""
        runner = SkyscannerRunner()
        assert isinstance(runner, ServiceRunner)

    def test_required_methods_on_google_flights(self):
        """Verify GoogleFlightsRunner has all required methods."""
        runner = GoogleFlightsRunner()
        assert hasattr(runner, "get_default_plan")
        assert hasattr(runner, "apply_step")
        assert hasattr(runner, "verify_after_fill")
        assert hasattr(runner, "get_route_core_before_date_gate")
        assert hasattr(runner, "get_recovery_limits")
        assert hasattr(runner, "get_force_bind_repair_policy")
        assert hasattr(runner, "build_recovery_plan")
        assert hasattr(runner, "build_non_flight_scope_repair_plan")
        assert hasattr(runner, "get_locale_aware_selector")

    def test_required_methods_on_skyscanner(self):
        """Verify SkyscannerRunner has all required methods."""
        runner = SkyscannerRunner()
        assert hasattr(runner, "get_default_plan")
        assert hasattr(runner, "apply_step")
        assert hasattr(runner, "verify_after_fill")
        assert hasattr(runner, "get_route_core_before_date_gate")
        assert hasattr(runner, "get_recovery_limits")
        assert hasattr(runner, "get_force_bind_repair_policy")
        assert hasattr(runner, "build_recovery_plan")
        assert hasattr(runner, "build_non_flight_scope_repair_plan")
        assert hasattr(runner, "get_locale_aware_selector")
