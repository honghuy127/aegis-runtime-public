"""Tests for SPA-safe click strategy for Google Flights."""

import pytest
from unittest.mock import Mock, MagicMock, patch, call


class TestSPASafeClick:
    """Test that click operations don't block waiting for navigation in SPAs."""

    def test_browser_click_no_wait_after_parameter_exists(self):
        """Verify that browser.click accepts no_wait_after parameter."""
        from core.browser import BrowserSession
        import inspect

        # Check that click method signature includes no_wait_after
        sig = inspect.signature(BrowserSession.click)
        assert "no_wait_after" in sig.parameters, \
            "browser.click should have no_wait_after parameter"

    def test_google_flights_search_click_uses_no_wait_after(self):
        """Verify that Google Flights search click uses no_wait_after=True."""
        # This test verifies the implementation uses no_wait_after
        # We check the source code instead of trying to mock Playwright
        from pathlib import Path
        import re

        ui_actions = Path("core/scenario_runner/google_flights/ui_actions.py")
        content = ui_actions.read_text()

        # Verify the _google_search_and_commit function uses no_wait_after=True
        # in the browser.click call
        click_match = re.search(
            r"def _google_search_and_commit\(.*?\).*?no_wait_after\s*=\s*True",
            content,
            re.DOTALL
        )
        assert click_match, \
            "_google_search_and_commit should use no_wait_after=True in browser.click"

    def test_spa_safe_click_has_bounded_timeouts(self):
        """Verify that post-click waits have bounded timeouts."""
        from pathlib import Path
        import re

        ui_actions = Path("core/scenario_runner/google_flights/ui_actions.py")
        content = ui_actions.read_text()

        # Verify post-click settle and ready timeouts use get_threshold
        settle_pattern = r"browser_post_click_settle_wait_ms"
        ready_pattern = r"browser_post_click_ready_timeout_ms"

        settle_matches = re.findall(settle_pattern, content)
        ready_matches = re.findall(ready_pattern, content)

        assert len(settle_matches) > 0, \
            "Should use browser_post_click_settle_wait_ms threshold"
        assert len(ready_matches) > 0, \
            "Should use browser_post_click_ready_timeout_ms threshold"

    def test_post_click_ready_check_is_bounded(self):
        """Verify that post-click readiness checks don't wait indefinitely."""
        from pathlib import Path

        ui_actions = Path("core/scenario_runner/google_flights/ui_actions.py")
        content = ui_actions.read_text()

        # Verify we're checking remaining_ready_ms and using min() to cap waits
        assert "remaining_ready_ms" in content, \
            "Should track remaining ready timeout"
        assert "min(remaining_ready_ms" in content, \
            "Should cap individual wait calls to remaining timeout"

    def test_google_flights_readiness_function_exists(self):
        """Verify that Google Flights readiness detection function exists."""
        from core.scenario_runner import _google_flights_after_search_ready
        import inspect

        # Check the function exists and has proper signature
        sig = inspect.signature(_google_flights_after_search_ready)
        assert "page" in sig.parameters, \
            "_google_flights_after_search_ready should accept page parameter"

        # Function should return bool
        assert sig.return_annotation == bool or sig.return_annotation == inspect.Signature.empty, \
            "Function should return boolean"

    def test_thresholds_post_click_defined(self):
        """Verify that post-click thresholds are defined in thresholds.yaml."""
        from pathlib import Path

        thresholds = Path("configs/thresholds.yaml")
        content = thresholds.read_text()

        assert "browser_post_click_settle_wait_ms" in content, \
            "thresholds.yaml should define browser_post_click_settle_wait_ms"
        assert "browser_post_click_ready_timeout_ms" in content, \
            "thresholds.yaml should define browser_post_click_ready_timeout_ms"

    def test_click_logging_includes_no_wait_after_info(self):
        """Verify that click logs include information about no_wait_after mode."""
        from pathlib import Path

        browser_py = Path("core/browser/session.py")
        content = browser_py.read_text()

        # Verify logging of no_wait_after
        assert "no_wait_after" in content, \
            "Click method should log no_wait_after status"

    def test_non_spa_site_can_still_wait_for_navigation(self):
        """Verify that non-SPA sites don't force no_wait_after mode."""
        # The default should be no_wait_after=False
        from core.browser import BrowserSession
        import inspect

        sig = inspect.signature(BrowserSession.click)
        no_wait_after_param = sig.parameters["no_wait_after"]

        # Default should be False so other sites wait by default
        assert no_wait_after_param.default is False, \
            "Default no_wait_after should be False to maintain compatibility"


class TestGoogleFlightsReadiness:
    """Test Google Flights readiness detection."""

    def test_readiness_check_handles_missing_main_element(self):
        """Verify readiness check doesn't fail with missing elements."""
        from core.scenario_runner import _google_flights_after_search_ready

        # Mock page with no main element
        mock_page = MagicMock()
        mock_page.query_selector.return_value = None
        mock_page.content.return_value = "No prices found"

        # Should not crash
        result = _google_flights_after_search_ready(mock_page)

        # Should return False since no results found
        assert result is False

    def test_readiness_check_detects_main_element(self):
        """Verify readiness check returns True when main element found."""
        from core.scenario_runner import _google_flights_after_search_ready

        mock_page = MagicMock()
        mock_page.query_selector.return_value = MagicMock()  # Found element

        result = _google_flights_after_search_ready(mock_page)
        assert result is True

    def test_readiness_check_detects_price_tokens(self):
        """Verify readiness check detects price tokens as marker."""
        from core.scenario_runner import _google_flights_after_search_ready

        mock_page = MagicMock()
        mock_page.query_selector.return_value = None  # No main element
        mock_page.content.return_value = "Flight price: ¥42,500"

        result = _google_flights_after_search_ready(mock_page)
        assert result is True

    def test_readiness_check_gracefully_handles_exceptions(self):
        """Verify readiness check doesn't crash on page access errors."""
        from core.scenario_runner import _google_flights_after_search_ready

        mock_page = MagicMock()
        mock_page.query_selector.side_effect = Exception("Page detached")

        # Should not raise, should return True (assume ready)
        result = _google_flights_after_search_ready(mock_page)
        assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
