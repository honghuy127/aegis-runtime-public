"""Tests for utils/triage.py graph stats integration

Tests that triage correctly loads and displays graph policy stats when available.
"""

import pytest
from pathlib import Path

from utils.graph_policy_stats import GraphPolicyStats
from utils.triage import format_human_report, format_json_report, TriageEvent
from datetime import datetime


class TestTriageGraphStatsIntegration:
    """Test triage output with graph stats."""

    def test_format_human_report_with_graph_stats(self):
        """Test that human report includes graph stats section when provided."""
        # Create mock triage events
        by_reason = {
            "selector_not_found": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="selector_not_found",
                    evidence={}
                )
            ]
        }

        # Create graph stats with failures
        stats = GraphPolicyStats()
        for i in range(10):
            stats.record_transition(
                run_id="test_run",
                attempt=0,
                turn=0,
                step_index=i,
                site="google_flights",
                page_kind="search_form",
                locale="ja-JP",
                role="origin",
                action="fill",
                selector="[role='textbox']",
                outcome="soft_fail",
                reason_code="selector_not_found",
                elapsed_ms=2000,
            )

        output = format_human_report(by_reason, graph_stats=stats)

        # Verify graph stats section is present
        assert "Graph-lite Stats" in output
        assert "Top Failing Transitions" in output
        assert "google_flights" in output
        assert "search_form" in output
        assert "origin" in output
        assert "Failures: 10" in output

    def test_format_human_report_without_graph_stats(self):
        """Test that human report works fine without graph stats."""
        by_reason = {
            "selector_not_found": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="selector_not_found",
                    evidence={}
                )
            ]
        }

        output = format_human_report(by_reason, graph_stats=None)

        # Should not have graph stats section
        assert "Graph-lite Stats" not in output

    def test_format_human_report_with_empty_graph_stats(self):
        """Test that human report handles empty graph stats gracefully."""
        by_reason = {
            "selector_not_found": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="selector_not_found",
                    evidence={}
                )
            ]
        }

        # Empty stats (no transitions)
        stats = GraphPolicyStats()

        output = format_human_report(by_reason, graph_stats=stats)

        # Should not include graph stats section for empty stats
        assert "Graph-lite Stats" not in output

    def test_format_json_report_with_graph_stats(self):
        """Test that JSON report includes graph stats when provided."""
        import json

        by_reason = {
            "selector_not_found": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="selector_not_found",
                    evidence={}
                )
            ]
        }

        # Create graph stats
        stats = GraphPolicyStats()
        stats.record_transition(
            run_id="test_run",
            attempt=0,
            turn=0,
            step_index=0,
            site="google_flights",
            page_kind="search_form",
            locale="ja-JP",
            role="origin",
            action="fill",
            selector="[role='textbox']",
            outcome="soft_fail",
            reason_code="selector_not_found",
            elapsed_ms=2000,
        )

        output = format_json_report(by_reason, graph_stats=stats)
        data = json.loads(output)

        # Verify graph stats are in JSON
        assert "graph_stats" in data
        assert "total_transitions" in data["graph_stats"]
        assert data["graph_stats"]["total_transitions"] == 1
        assert "outcome_counts" in data["graph_stats"]
        assert "selector_family_counts" in data["graph_stats"]
        assert "top_failures" in data["graph_stats"]

    def test_format_json_report_without_graph_stats(self):
        """Test that JSON report works fine without graph stats."""
        import json

        by_reason = {
            "selector_not_found": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="selector_not_found",
                    evidence={}
                )
            ]
        }

        output = format_json_report(by_reason, graph_stats=None)
        data = json.loads(output)

        # Should not have graph_stats key
        assert "graph_stats" not in data

    def test_graph_stats_selector_family_in_human_report(self):
        """Test that selector families are shown correctly in human report."""
        by_reason = {}

        # Create stats with different selector families
        stats = GraphPolicyStats()

        # Role+aria-label family
        for i in range(5):
            stats.record_transition(
                run_id="test",
                attempt=0,
                turn=0,
                step_index=i,
                site="google_flights",
                page_kind="search_form",
                locale="ja-JP",
                role="origin",
                action="fill",
                selector="[role='textbox'][aria-label='Origin']",
                outcome="soft_fail",
                reason_code="selector_not_found",
                elapsed_ms=2000,
            )

        output = format_human_report(by_reason, graph_stats=stats)

        # Verify selector family is shown
        assert "Selector: role+aria-label" in output or "role+aria" in output

    def test_graph_stats_top_reasons_in_output(self):
        """Test that top failure reasons are shown in graph stats output."""
        by_reason = {}

        stats = GraphPolicyStats()

        # Same signature, different reasons
        for i in range(3):
            stats.record_transition(
                run_id="test",
                attempt=0,
                turn=0,
                step_index=i,
                site="google_flights",
                page_kind="search_form",
                locale="ja-JP",
                role="origin",
                action="fill",
                selector="[role='textbox']",
                outcome="soft_fail",
                reason_code="selector_not_found",
                elapsed_ms=2000,
            )

        for i in range(2):
            stats.record_transition(
                run_id="test",
                attempt=0,
                turn=0,
                step_index=i + 3,
                site="google_flights",
                page_kind="search_form",
                locale="ja-JP",
                role="origin",
                action="fill",
                selector="[role='textbox']",
                outcome="soft_fail",
                reason_code="timeout_error",
                elapsed_ms=5000,
            )

        output = format_human_report(by_reason, graph_stats=stats)

        # Verify top reasons are shown
        assert "selector_not_found" in output
        assert "timeout_error" in output or "Top reasons" in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
