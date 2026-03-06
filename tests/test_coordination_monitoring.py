"""Tests for coordination monitoring and observability."""

import pytest
from core.scenario.coordination_monitoring import (
    GateDecisionMetrics,
    ExtractionMetrics,
    BudgetMetrics,
    CoordinationMetricsCollector,
    ExtractionObserver,
    format_metrics_report,
)


class TestGateDecisionMetrics:
    """Tests for GateDecisionMetrics."""

    def test_gate_decision_pass(self):
        """Record a passing gate decision."""
        decision = GateDecisionMetrics(gate_name="route_bound", passed=True)
        assert decision.gate_name == "route_bound"
        assert decision.passed is True
        assert decision.reason is None

    def test_gate_decision_fail(self):
        """Record a failing gate decision."""
        decision = GateDecisionMetrics(
            gate_name="is_flight",
            passed=False,
            reason="non_flight_scope",
            context={"page_class": "hotel_search"},
        )
        assert decision.gate_name == "is_flight"
        assert decision.passed is False
        assert decision.reason == "non_flight_scope"
        assert decision.context["page_class"] == "hotel_search"


class TestExtractionMetrics:
    """Tests for ExtractionMetrics."""

    def test_extraction_gated(self):
        """Record extraction that was gated."""
        metric = ExtractionMetrics(
            extraction_id="ext_123",
            gated_at="route_mismatch",
            gates_passed=["budget"],
        )
        assert metric.extraction_id == "ext_123"
        assert metric.gated_at == "route_mismatch"
        assert "budget" in metric.gates_passed
        assert not metric.llm_called

    def test_extraction_succeeded(self):
        """Record successful extraction."""
        metric = ExtractionMetrics(
            extraction_id="ext_456",
            gates_passed=["route_bound", "is_flight", "budget"],
            domslice_built=True,
            domslice_selector=".price",
            domslice_text_len=500,
            llm_called=True,
            price_extracted=True,
            price_value=250.50,
            duration_ms=1200,
        )
        assert metric.extraction_id == "ext_456"
        assert len(metric.gates_passed) == 3
        assert metric.llm_called is True
        assert metric.price_extracted is True
        assert metric.price_value == 250.50


class TestBudgetMetrics:
    """Tests for BudgetMetrics."""

    def test_budget_tracking(self):
        """Track budget consumption."""
        metric = BudgetMetrics(
            budget_total_s=240.0,
            budget_consumed_s=120.0,
            budget_remaining_s=120.0,
            extraction_count=50,
            gated_count=5,
            circuit_opens=1,
        )
        assert metric.budget_total_s == 240.0
        assert metric.budget_consumed_s == 120.0
        assert metric.budget_remaining_s == 120.0
        assert metric.extraction_count == 50
        assert metric.gated_count == 5
        assert metric.circuit_opens == 1


class TestCoordinationMetricsCollector:
    """Tests for CoordinationMetricsCollector."""

    def test_collector_initialization(self):
        """Collector initializes empty."""
        collector = CoordinationMetricsCollector()
        assert len(collector.gate_decisions) == 0
        assert len(collector.extractions) == 0
        assert len(collector.budget_measurements) == 0

    def test_record_gate_decision(self):
        """Record gate decisions."""
        collector = CoordinationMetricsCollector()
        collector.record_gate_decision("route_bound", passed=True)
        collector.record_gate_decision("is_flight", passed=False, reason="non_flight_scope")

        assert len(collector.gate_decisions) == 2
        assert collector.gate_decisions[0].passed is True
        assert collector.gate_decisions[1].passed is False
        assert collector.blocking_reasons["non_flight_scope"] == 1

    def test_record_extraction(self):
        """Record extraction metrics."""
        collector = CoordinationMetricsCollector()
        collector.record_extraction(
            extraction_id="ext_001",
            gates_passed=["route_bound"],
            domslice_selector=".price",
            domslice_text_len=400,
            llm_called=True,
            price_extracted=True,
            price_value=199.99,
        )

        assert len(collector.extractions) == 1
        assert collector.extractions[0].extraction_id == "ext_001"
        assert collector.domslice_selectors[".price"] == 1

    def test_record_multiple_extractions(self):
        """Record multiple extractions with different outcomes."""
        collector = CoordinationMetricsCollector()

        # Gated extraction
        collector.record_extraction(
            extraction_id="ext_001",
            gated_at="route_mismatch",
            gates_passed=["budget"],
        )

        # Successful extraction
        collector.record_extraction(
            extraction_id="ext_002",
            gates_passed=["route_bound", "is_flight", "budget"],
            domslice_selector=".price-container",
            domslice_text_len=600,
            llm_called=True,
            price_extracted=True,
            price_value=250.0,
        )

        assert len(collector.extractions) == 2
        assert collector.extractions[0].gated_at == "route_mismatch"
        assert collector.extractions[1].price_extracted is True

    def test_record_budget_measurement(self):
        """Record budget measurement."""
        collector = CoordinationMetricsCollector()
        collector.record_budget_measurement(
            budget_total_s=240.0,
            budget_consumed_s=100.0,
            budget_remaining_s=140.0,
            extraction_count=45,
            gated_count=3,
        )

        assert len(collector.budget_measurements) == 1
        assert collector.budget_measurements[0].budget_total_s == 240.0

    def test_get_summary(self):
        """Get summary metrics."""
        collector = CoordinationMetricsCollector()

        # Add mix of extractions
        collector.record_extraction(
            extraction_id="ext_001",
            gated_at="route_mismatch",
            gates_passed=[],
            duration_ms=100,
        )
        collector.record_extraction(
            extraction_id="ext_002",
            gates_passed=["route_bound", "is_flight", "budget"],
            llm_called=True,
            price_extracted=True,
            price_value=199.99,
            duration_ms=500,
        )
        collector.record_extraction(
            extraction_id="ext_003",
            gates_passed=["route_bound", "is_flight", "budget"],
            llm_called=True,
            price_extracted=False,
            duration_ms=400,
        )

        summary = collector.get_summary()

        assert summary["total_extractions"] == 3
        assert summary["gated_extractions"] == 1
        assert summary["gating_rate"] == 1 / 3
        assert summary["llm_called"] == 2
        assert summary["price_extracted"] == 1
        assert summary["extraction_success_rate"] == 0.5

    def test_log_summary(self):
        """Log summary (should not raise)."""
        collector = CoordinationMetricsCollector()
        collector.record_extraction(extraction_id="ext_001")
        # Should not raise
        collector.log_summary()


class TestExtractionObserver:
    """Tests for ExtractionObserver."""

    def test_observer_initialization(self):
        """Observer initializes with metrics collector."""
        observer = ExtractionObserver()
        assert observer.metrics is not None

    def test_observer_tracks_extraction_lifecycle(self):
        """Observer tracks full extraction lifecycle."""
        observer = ExtractionObserver()

        # Start extraction
        observer.on_extraction_start("ext_001")
        assert observer.current_extraction_id == "ext_001"

        # Gate evaluations
        observer.on_gate_evaluation("route_bound", passed=True)
        observer.on_gate_evaluation("is_flight", passed=True)
        observer.on_gate_evaluation("budget", passed=True)

        # Complete extraction
        observer.on_extraction_complete(
            gates_passed=["route_bound", "is_flight", "budget"],
            domslice_selector=".price",
            domslice_text_len=500,
            llm_called=True,
            price_extracted=True,
            price_value=299.99,
        )

        metrics = observer.get_metrics()
        assert len(metrics.extractions) == 1
        assert metrics.extractions[0].price_extracted is True
        assert metrics.extractions[0].price_value == 299.99

    def test_observer_gate_blocking_scenario(self):
        """Observer records blocking scenario."""
        observer = ExtractionObserver()

        observer.on_extraction_start("ext_002")
        observer.on_gate_evaluation("route_bound", passed=False, reason="route_mismatch")
        observer.on_extraction_complete(
            gated_at="route_mismatch",
            gates_passed=[],
        )

        metrics = observer.get_metrics()
        assert metrics.extractions[0].gated_at == "route_mismatch"
        assert metrics.blocking_reasons["route_mismatch"] == 1

    def test_observer_multiple_extractions(self):
        """Observer tracks multiple extractions."""
        observer = ExtractionObserver()

        for i in range(3):
            observer.on_extraction_start(f"ext_{i:03d}")
            observer.on_gate_evaluation("route_bound", passed=True)
            observer.on_extraction_complete(
                gates_passed=["route_bound"],
                llm_called=i < 2,  # Only first 2 call LLM
                price_extracted=i == 0,  # Only first extracts price
            )

        metrics = observer.get_metrics()
        assert len(metrics.extractions) == 3
        assert sum(1 for e in metrics.extractions if e.llm_called) == 2
        assert sum(1 for e in metrics.extractions if e.price_extracted) == 1

    def test_observer_get_metrics(self):
        """Observer returns collected metrics."""
        observer = ExtractionObserver()
        observer.on_extraction_start("ext_001")
        observer.on_extraction_complete()

        metrics = observer.get_metrics()
        summary = metrics.get_summary()

        assert summary["total_extractions"] == 1


class TestMetricsReporting:
    """Tests for metrics reporting."""

    def test_format_metrics_report(self):
        """Format metrics into readable report."""
        collector = CoordinationMetricsCollector()
        collector.record_extraction(
            extraction_id="ext_001",
            gates_passed=["route_bound", "is_flight"],
            llm_called=True,
            price_extracted=True,
            price_value=199.99,
        )
        collector.record_extraction(
            extraction_id="ext_002",
            gated_at="route_mismatch",
        )

        metrics = collector.get_summary()
        report = format_metrics_report(metrics)

        assert "COORDINATION LAYER METRICS REPORT" in report
        assert "Total Extractions" in report
        assert "Gating Rate" in report
        assert "LLM Call" in report


class TestMetricsIntegration:
    """Integration tests for monitoring system."""

    def test_full_monitoring_workflow(self):
        """Full workflow: observer records metrics through complete lifecycle."""
        observer = ExtractionObserver()

        # Scenario 1: Route blocked
        observer.on_extraction_start("ext_001")
        observer.on_gate_evaluation("route_bound", passed=False, reason="route_mismatch")
        observer.on_extraction_complete(gated_at="route_mismatch")

        # Scenario 2: Flight page, budget OK, LLM success
        observer.on_extraction_start("ext_002")
        observer.on_gate_evaluation("route_bound", passed=True)
        observer.on_gate_evaluation("is_flight", passed=True)
        observer.on_gate_evaluation("budget", passed=True)
        observer.on_extraction_complete(
            gates_passed=["route_bound", "is_flight", "budget"],
            domslice_selector=".price",
            domslice_text_len=400,
            llm_called=True,
            price_extracted=True,
            price_value=250.0,
        )

        # Scenario 3: Hotel page
        observer.on_extraction_start("ext_003")
        observer.on_gate_evaluation("route_bound", passed=True)
        observer.on_gate_evaluation("is_flight", passed=False, reason="non_flight_scope")
        observer.on_extraction_complete(gated_at="non_flight_scope")

        metrics = observer.get_metrics()
        summary = metrics.get_summary()

        assert summary["total_extractions"] == 3
        assert summary["gated_extractions"] == 2  # ext_001, ext_003
        assert summary["gating_rate"] == 2 / 3

    def test_budget_metrics_tracking(self):
        """Track budget metrics through multiple measurements."""
        collector = CoordinationMetricsCollector()

        # Initial measurement
        collector.record_budget_measurement(
            budget_total_s=240.0,
            budget_consumed_s=20.0,
            budget_remaining_s=220.0,
            extraction_count=10,
            gated_count=0,
        )

        # After more extractions
        collector.record_budget_measurement(
            budget_total_s=240.0,
            budget_consumed_s=150.0,
            budget_remaining_s=90.0,
            extraction_count=50,
            gated_count=5,
        )

        # Near end
        collector.record_budget_measurement(
            budget_total_s=240.0,
            budget_consumed_s=230.0,
            budget_remaining_s=10.0,
            extraction_count=80,
            gated_count=10,
        )

        assert len(collector.budget_measurements) == 3
        assert collector.budget_measurements[0].budget_remaining_s == 220.0
        assert collector.budget_measurements[2].budget_remaining_s == 10.0

    def test_selector_usage_tracking(self):
        """Track which DOM selectors are used."""
        collector = CoordinationMetricsCollector()

        selectors = [".price", ".price-container", "[data-price]", ".price", ".price"]

        for i, selector in enumerate(selectors):
            collector.record_extraction(
                extraction_id=f"ext_{i:03d}",
                domslice_selector=selector,
                domslice_text_len=400,
            )

        # .price used 3 times, .price-container 1 time, [data-price] 1 time
        assert collector.domslice_selectors[".price"] == 3
        assert collector.domslice_selectors[".price-container"] == 1
        assert collector.domslice_selectors["[data-price]"] == 1
