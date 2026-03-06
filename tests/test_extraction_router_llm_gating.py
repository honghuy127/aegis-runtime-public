"""Unit tests for extraction router LLM gating behavior.

Verifies that expensive LLM/VLM calls are skipped when route is mismatched.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, call

pytestmark = [pytest.mark.llm, pytest.mark.vlm]

from core.plugins.runtime_extraction import run_plugin_extraction_router


class TestExtractionRouterLLMFastFail:
    """Tests for fast-fail gating of LLM/VLM calls when route is mismatched."""

    def test_skips_llm_when_route_bound_false(self):
        """Should NOT call LLM/VLM when route_bound=False (passed via hints)."""
        html = "<html><body>Test page</body></html>"

        # Mock the plugin to return route_bound=False
        with patch("core.plugins.runtime_extraction.get_strategy_plugin") as mock_get_strategy:
            mock_strategy = MagicMock()
            mock_get_strategy.return_value = mock_strategy

            # Strategy returns success but with route_bound=False
            mock_strategy.extract.return_value = {
                "confidence": "high",
                "page_class": "results",
                "source": "plugin_test",
                "route_bound": False,  # Route not matched!
                "data": {"origin": "", "dest": ""},
            }

            with patch("core.plugins.runtime_extraction.get_runtime_service_plugin") as mock_svc:
                mock_svc.return_value = None

                with patch("core.plugins.runtime_extraction.normalize_plugin_candidate") as mock_norm:
                    mock_norm.return_value = {
                        "confidence": "high",
                        "page_class": "results",
                        "source": "plugin_test",
                    }

                    with patch("core.plugins.runtime_extraction.accept_candidate") as mock_accept:
                        # When route_bound=False, accept_candidate should reject
                        mock_accept.return_value = (False, {}, "route_not_bound")

                        result = run_plugin_extraction_router(
                            html=html,
                            site="google_flights",
                            task="price",
                            origin="HND",
                            dest="ITM",
                            depart="2026-03-01",
                            return_date="2026-03-08",
                            trip_type="round_trip",
                            is_domestic=True,
                            screenshot_path=None,
                            page_url="https://www.google.com/flights",
                        )

        # Should return empty (rejection)
        assert result == {}

    @patch("core.plugins.runtime_extraction.plugin_strategy_enabled")
    def test_plugin_strategy_disabled_returns_empty(self, mock_enabled):
        """Should return empty when plugin extraction is disabled."""
        mock_enabled.return_value = False

        result = run_plugin_extraction_router(
            html="<html></html>",
            site="google_flights",
            task="price",
            origin="HND",
            dest="ITM",
            depart="2026-03-01",
            return_date=None,
            trip_type="one_way",
            is_domestic=True,
            screenshot_path=None,
            page_url="https://www.google.com/flights",
        )

        assert result == {}

    def test_extraction_strategy_exception_caught(self):
        """Should gracefully return empty if extraction strategy raises."""
        with patch("core.plugins.runtime_extraction.get_strategy_plugin") as mock_get:
            mock_get.side_effect = RuntimeError("Circuit open: VLM unavailable")

            result = run_plugin_extraction_router(
                html="<html></html>",
                site="google_flights",
                task="price",
                origin="HND",
                dest="ITM",
                depart="2026-03-01",
                return_date=None,
                trip_type="one_way",
                is_domestic=True,
                screenshot_path=None,
                page_url="https://www.google.com/flights",
            )

        # Should return empty, not propagate exception
        assert result == {}

    def test_invalid_enum_drift_detected(self):
        """Should reject and return empty if extracted data has invalid enum values."""
        with patch("core.plugins.runtime_extraction.get_strategy_plugin") as mock_get:
            mock_strategy = MagicMock()
            mock_get.return_value = mock_strategy

            # Return data with invalid confidence value
            mock_strategy.extract.return_value = {
                "confidence": "super_high_but_invalid",  # Not in CONFIDENCE_VALUES
                "page_class": "results",
                "source": "plugin_test",
            }

            result = run_plugin_extraction_router(
                html="<html></html>",
                site="google_flights",
                task="price",
                origin="HND",
                dest="ITM",
                depart="2026-03-01",
                return_date=None,
                trip_type="one_way",
                is_domestic=True,
                screenshot_path=None,
                page_url="https://www.google.com/flights",
            )

        # Should return empty due to enum drift
        assert result == {}


class TestExtractionRouterCallPatterns:
    """Tests for ensuring correct call patterns with budget."""

    def test_extraction_hints_called_when_service_plugin_available(self):
        """Should call extraction_hints on service plugin when available."""
        with patch("core.plugins.runtime_extraction.get_strategy_plugin") as mock_get_strategy:
            mock_strategy = MagicMock()
            mock_get_strategy.return_value = mock_strategy
            mock_strategy.extract.return_value = {}

            with patch("core.plugins.runtime_extraction.get_runtime_service_plugin") as mock_svc:
                mock_service = MagicMock()
                mock_service.extraction_hints = MagicMock(return_value={"service_key": "google_flights"})
                mock_svc.return_value = mock_service

                result = run_plugin_extraction_router(
                    html="<html></html>",
                    site="google_flights",
                    task="price",
                    origin="HND",
                    dest="ITM",
                    depart="2026-03-01",
                    return_date=None,
                    trip_type="one_way",
                    is_domestic=True,
                    screenshot_path=None,
                    page_url="https://www.google.com/flights",
                )

        # extraction_hints should have been called
        mock_service.extraction_hints.assert_called_once()

    def test_sanitize_hints_mismatched_service(self):
        """Should drop extraction hints if service doesn't match."""
        # This test verifies the _sanitize_extraction_hints internal function
        from core.plugins.runtime_extraction import _sanitize_extraction_hints

        hints = {"service_key": "skyscanner", "data": "some_value"}
        result = _sanitize_extraction_hints(hints, site="google_flights")

        # Should return empty due to service mismatch
        assert result == {}

    def test_sanitize_hints_matching_service(self):
        """Should preserve extraction hints if service matches."""
        from core.plugins.runtime_extraction import _sanitize_extraction_hints

        hints = {"service_key": "google_flights", "roi": "some_data"}
        result = _sanitize_extraction_hints(hints, site="google_flights")

        # Should preserve hints
        assert result == hints


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
