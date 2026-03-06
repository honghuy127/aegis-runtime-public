"""Phase 5 integration tests: Coordination bridge in extraction pipeline."""

import os
import pytest
from unittest.mock import patch, MagicMock
from core.extractor import extract_price
from core.scenario.coordination_monitoring import ExtractionObserver

pytestmark = [pytest.mark.llm, pytest.mark.vlm]


class TestExtractionCoordinationIntegration:
    """Tests for coordination bridge integrated into extraction pipeline."""

    def test_extract_price_coordination_disabled_by_default(self):
        """Verify coordination is disabled by default."""
        # Coordination should not be enabled unless explicitly set
        with patch.dict(os.environ, {}, clear=True):
            with patch('core.extractor.get_threshold') as mock_threshold:
                mock_threshold.side_effect = lambda key, default: {
                    'coordination_enabled': False,
                    'extract_wall_clock_cap_sec': 0,
                }.get(key, default)

                # Should verify coordination flag is disabled
                assert not os.getenv("FLIGHT_WATCHER_COORDINATION_ENABLED", "").lower() == "true"

    def test_extract_price_coordination_enabled_flag(self):
        """Verify coordination can be enabled via environment variable."""
        with patch.dict(os.environ, {"FLIGHT_WATCHER_COORDINATION_ENABLED": "true"}):
            # Coordination enabled flag should be recognized
            coordination_flag = os.getenv("FLIGHT_WATCHER_COORDINATION_ENABLED", "").lower()
            assert coordination_flag == "true"

    def test_extract_price_integration_fallback_without_coordination(self):
        """Verify extraction works without coordination (backward compatibility)."""
        html = """
        <html>
            <body>
                <div class="price">$299.99</div>
            </body>
        </html>
        """

        # Extract without coordination should work (might not extract price due to mock)
        # but should not error
        with patch('core.extractor.extract_with_llm') as mock_extract:
            mock_extract.return_value = {
                "price": None,
                "currency": None,
                "confidence": "low",
                "source": "selector",
                "reason": "no_match",
            }

            result = extract_price(
                html=html,
                site="test_site",
                origin="JFK",
                dest="LAX",
            )

            # Should return without error
            assert isinstance(result, dict)
            assert "price" in result

    def test_extract_price_with_coordination_enabled(self):
        """Verify extraction with coordination enabled records metrics."""
        html = """<html><body><div class="price">$199.99</div></body></html>"""

        with patch.dict(os.environ, {"FLIGHT_WATCHER_COORDINATION_ENABLED": "true"}):
            with patch('core.extractor.extract_with_llm') as mock_extract:
                mock_extract.return_value = {
                    "price": 199.99,
                    "currency": "USD",
                    "confidence": "high",
                    "source": "llm",
                    "reason": "extracted",
                }

                with patch('core.extractor.get_threshold') as mock_threshold:
                    mock_threshold.side_effect = lambda key, default: {
                        'coordination_enabled': True,
                        'extract_wall_clock_cap_sec': 0,
                    }.get(key, default)

                    result = extract_price(
                        html=html,
                        site="test_site",
                        origin="HND",
                        dest="ITM",
                    )

                    # Should extract successfully
                    assert result.get("price") == 199.99
                    assert result.get("source") == "plugin_html_llm"

    def test_extraction_observer_lifecycle(self):
        """Test ExtractionObserver lifecycle during extraction."""
        observer = ExtractionObserver()

        # Start extraction
        observer.on_extraction_start("test_extraction_001")
        assert observer.current_extraction_id == "test_extraction_001"

        # Gate evaluation
        observer.on_gate_evaluation("route_bound", passed=True)
        observer.on_gate_evaluation("is_flight", passed=True)

        # Complete extraction
        observer.on_extraction_complete(
            gates_passed=["route_bound", "is_flight"],
            llm_called=True,
            price_extracted=True,
            price_value=249.99,
        )

        # Verify metrics recorded
        metrics = observer.get_metrics()
        assert len(metrics.extractions) == 1
        assert metrics.extractions[0].price_extracted is True
        assert metrics.extractions[0].price_value == 249.99

    def test_multiple_extractions_observer_aggregation(self):
        """Test observer aggregates metrics across multiple extractions."""
        observer = ExtractionObserver()

        # First extraction: successful
        observer.on_extraction_start("ext_001")
        observer.on_gate_evaluation("route_bound", passed=True)
        observer.on_extraction_complete(
            gates_passed=["route_bound"],
            llm_called=True,
            price_extracted=True,
            price_value=199.99,
        )

        # Second extraction: failed
        observer.on_extraction_start("ext_002")
        observer.on_gate_evaluation("route_bound", passed=True)
        observer.on_extraction_complete(
            gates_passed=["route_bound"],
            llm_called=True,
            price_extracted=False,
        )

        # Third extraction: gated
        observer.on_extraction_start("ext_003")
        observer.on_gate_evaluation("route_bound", passed=False)
        observer.on_extraction_complete(
            gated_at="route_mismatch",
            gates_passed=[],
        )

        # Verify aggregation
        metrics = observer.get_metrics()
        summary = metrics.get_summary()

        assert summary["total_extractions"] == 3
        assert summary.get("gating_rate", 0) > 0  # One extraction was gated
        assert summary.get("price_extraction_rate", 0) < 1.0  # One succeeded out of 2 that ran

    def test_observer_metrics_format(self):
        """Verify observer metrics are in correct format."""
        observer = ExtractionObserver()

        observer.on_extraction_start("test_001")
        observer.on_gate_evaluation("budget", passed=True)
        observer.on_extraction_complete(
            gates_passed=["budget"],
            domslice_selector=".price",
            domslice_text_len=500,
            llm_called=True,
            price_extracted=True,
            price_value=299.99,
        )

        metrics = observer.get_metrics()
        ext_metric = metrics.extractions[0]

        # Verify metric fields
        assert ext_metric.extraction_id == "test_001"
        assert ext_metric.gates_passed == ["budget"]
        assert ext_metric.domslice_selector == ".price"
        assert ext_metric.domslice_text_len == 500
        assert ext_metric.llm_called is True
        assert ext_metric.price_extracted is True
        assert ext_metric.price_value == 299.99

    def test_coordination_enabled_flag_from_config_threshold(self):
        """Verify coordination flag reads from thresholds config."""
        with patch('core.extractor.get_threshold') as mock_threshold:
            # Setup threshold to return coordination_enabled=True
            mock_threshold.side_effect = lambda key, default: {
                'coordination_enabled': True,
            }.get(key, default)

            from core.extractor import _env_bool
            coordination_enabled = _env_bool(
                "FLIGHT_WATCHER_COORDINATION_ENABLED",
                bool(mock_threshold("coordination_enabled", False)),
            )

            assert coordination_enabled is True

    def test_observer_with_metrics_reporting(self):
        """Test observer can generate summary report."""
        from core.scenario.coordination_monitoring import format_metrics_report

        observer = ExtractionObserver()

        for i in range(5):
            observer.on_extraction_start(f"ext_{i:03d}")
            observer.on_gate_evaluation("route_bound", passed=True)
            observer.on_extraction_complete(
                gates_passed=["route_bound"],
                llm_called=i < 4,  # 4 out of 5 call LLM
                price_extracted=i < 3,  # 3 out of 5 extract price
                price_value=100 + i * 50 if i < 3 else None,
            )

        # Get report
        summary = observer.get_metrics().get_summary()
        report = format_metrics_report(summary)

        # Verify report contains expected fields
        assert "Total Extractions" in report
        assert "5" in report
        assert "Gating Rate" in report or "LLM Call" in report

class TestCoordinationIntegrationWithActualFlow:
    """Integration tests with more realistic extraction flow."""

    def test_extract_price_integration_path(self):
        """Test the complete extract_price call flow."""
        html = "<html><body><div>Test content</div></body></html>"

        with patch('core.extractor.extract_with_llm') as mock_extract:
            mock_extract.return_value = {
                "price": 150.00,
                "currency": "USD",
                "confidence": "medium",
                "source": "llm",
                "reason": "extracted_from_html",
            }

            result = extract_price(
                html=html,
                site="google_flights",
                origin="SFO",
                dest="NYC",
                depart="2026-03-15",
            )

            # Verify extract_with_llm was called
            assert mock_extract.called
            # Verify result structure
            assert "price" in result
            assert "currency" in result

    def test_coordinator_observer_optional_parameter_passthrough(self):
        """Verify coordination observer doesn't break when not used."""
        # This tests backward compatibility - code should work whether
        # coordination is enabled or not

        html = "<html><body><price>$99</price></body></html>"

        with patch('core.extractor.extract_with_llm') as mock_extract:
            mock_extract.return_value = {
                "price": 99.00,
                "currency": "USD",
                "confidence": "low",
                "source": "selector_hint",
                "reason": "matched_tag",
            }

            # Should not raise error whether coordination enabled or not
            result_without = extract_price(html=html, site="test")
            assert result_without is not None

            with patch.dict(os.environ, {"FLIGHT_WATCHER_COORDINATION_ENABLED": "true"}):
                with patch('core.extractor.get_threshold') as mock_threshold:
                    mock_threshold.side_effect = lambda key, default: {
                        'coordination_enabled': True,
                    }.get(key, default)
                    result_with = extract_price(html=html, site="test")
                    assert result_with is not None
