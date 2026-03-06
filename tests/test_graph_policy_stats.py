"""Tests for utils/graph_policy_stats.py

Tests graph-lite policy statistics module for selector normalization,
transition recording, serialization, and triage summaries.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, UTC

from utils.graph_policy_stats import (
    GraphPolicyStats,
    StateSignature,
    TransitionRecord,
    normalize_selector_family,
    load_graph_stats_for_run,
)


class TestNormalizeSelectorFamily:
    """Test selector family normalization for stable grouping."""

    def test_role_selector(self):
        """Test role-based selectors are normalized correctly."""
        assert normalize_selector_family("[role='textbox']") == "role"
        assert normalize_selector_family("[role~='button']") == "role"

    def test_role_aria_selector(self):
        """Test role+aria selectors are normalized correctly."""
        assert normalize_selector_family("[role='textbox'][aria-label='Origin']") == "role+aria-label"
        assert normalize_selector_family("[aria-label='Search'][role='button']") == "role+aria-label"

    def test_aria_label_selector(self):
        """Test aria-label selectors are normalized correctly."""
        assert normalize_selector_family("[aria-label='Search']") == "aria-label"
        assert normalize_selector_family("button[aria-label='Close']") == "aria-label"

    def test_data_attribute_selector(self):
        """Test data-* selectors are normalized correctly."""
        assert normalize_selector_family("[data-testid='search-btn']") == "data-attr"
        assert normalize_selector_family("div[data-id='123']") == "data-attr"

    def test_text_selector(self):
        """Test text-based selectors are normalized correctly."""
        assert normalize_selector_family(":has-text('Search')") == "text"
        assert normalize_selector_family("text='Search'") == "tag+text"  # text= matches tag+text pattern
        assert normalize_selector_family("button:has-text('Go')") == "tag+text"

    def test_tag_attr_selector(self):
        """Test tag+attribute selectors are normalized correctly."""
        assert normalize_selector_family("input[name='origin']") == "tag+attr"
        assert normalize_selector_family("button[type='submit']") == "tag+attr"

    def test_id_selector(self):
        """Test ID selectors are normalized correctly."""
        assert normalize_selector_family("#search-form") == "id"

    def test_class_selector(self):
        """Test class selectors are normalized correctly."""
        assert normalize_selector_family(".search-button") == "class"

    def test_tag_only_selector(self):
        """Test tag-only selectors are normalized correctly."""
        assert normalize_selector_family("button") == "tag"
        assert normalize_selector_family("input") == "tag"

    def test_positional_selector(self):
        """Test positional selectors are normalized correctly."""
        assert normalize_selector_family("div:nth-child(3)") == "positional"
        assert normalize_selector_family("li:nth-of-type(2)") == "positional"

    def test_complex_selector(self):
        """Test complex selectors fall back to 'complex'."""
        assert normalize_selector_family("div > button + span") == "complex"

    def test_unknown_selector(self):
        """Test unknown/empty selectors return 'unknown'."""
        assert normalize_selector_family("") == "unknown"
        assert normalize_selector_family(None) == "unknown"

    def test_normalization_stability(self):
        """Test that similar selectors are grouped consistently."""
        # Different origins but same role+aria should normalize to same family
        sel1 = "[role='textbox'][aria-label='Origin']"
        sel2 = "[role='textbox'][aria-label='Destination']"
        assert normalize_selector_family(sel1) == normalize_selector_family(sel2)

        # Different IDs but same tag+attr should normalize to same family
        sel3 = "input[name='from']"
        sel4 = "input[name='to']"
        assert normalize_selector_family(sel3) == normalize_selector_family(sel4)


class TestGraphPolicyStats:
    """Test GraphPolicyStats recording and serialization."""

    def test_record_transition(self):
        """Test basic transition recording."""
        stats = GraphPolicyStats()

        stats.record_transition(
            run_id="test_run_123",
            attempt=0,
            turn=0,
            step_index=0,
            site="google_flights",
            page_kind="search_form",
            locale="ja-JP",
            role="origin",
            action="fill",
            selector="[role='textbox'][aria-label='Origin']",
            strategy_id="direct_selector",
            outcome="ok",
            reason_code="success",
            elapsed_ms=150,
        )

        assert len(stats.transitions) == 1
        record = stats.transitions[0]
        assert record.run_id == "test_run_123"
        assert record.outcome == "ok"
        assert record.state_signature["selector_family"] == "role+aria-label"

    def test_multiple_transitions(self):
        """Test recording multiple transitions."""
        stats = GraphPolicyStats()

        for i in range(5):
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
                outcome="ok",
                reason_code="success",
                elapsed_ms=100 + i * 10,
            )

        assert len(stats.transitions) == 5

    def test_serialize_deserialize_roundtrip(self):
        """Test JSON serialization and deserialization roundtrip."""
        stats1 = GraphPolicyStats()

        stats1.record_transition(
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
            outcome="ok",
            reason_code="success",
            elapsed_ms=150,
        )

        # Serialize
        json_str = stats1.to_json()
        assert isinstance(json_str, str)

        # Deserialize
        stats2 = GraphPolicyStats.from_json(json_str)
        assert len(stats2.transitions) == 1

        # Verify data matches
        rec1 = stats1.transitions[0]
        rec2 = stats2.transitions[0]
        assert rec1.run_id == rec2.run_id
        assert rec1.outcome == rec2.outcome
        assert rec1.reason_code == rec2.reason_code
        assert rec1.elapsed_ms == rec2.elapsed_ms

    def test_merge(self):
        """Test merging two stats instances."""
        stats1 = GraphPolicyStats()
        stats1.record_transition(
            run_id="run1",
            attempt=0,
            turn=0,
            step_index=0,
            site="google_flights",
            page_kind="search_form",
            locale="ja-JP",
            role="origin",
            action="fill",
            selector="[role='textbox']",
            outcome="ok",
            reason_code="success",
            elapsed_ms=100,
        )

        stats2 = GraphPolicyStats()
        stats2.record_transition(
            run_id="run2",
            attempt=0,
            turn=0,
            step_index=0,
            site="google_flights",
            page_kind="search_results",
            locale="ja-JP",
            role="search_button",
            action="click",
            selector="button[type='submit']",
            outcome="soft_fail",
            reason_code="selector_not_found",
            elapsed_ms=200,
        )

        stats1.merge(stats2)
        assert len(stats1.transitions) == 2

    def test_summarize_top_failures(self):
        """Test failure summary generation."""
        stats = GraphPolicyStats()

        # Record multiple failures with same signature
        for i in range(10):
            stats.record_transition(
                run_id=f"run_{i}",
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

        # Record some failures with different signature
        for i in range(5):
            stats.record_transition(
                run_id=f"run_{i}",
                attempt=0,
                turn=0,
                step_index=1,
                site="google_flights",
                page_kind="search_form",
                locale="ja-JP",
                role="dest",
                action="fill",
                selector="[aria-label='Destination']",
                outcome="hard_fail",
                reason_code="timeout_error",
                elapsed_ms=5000,
            )

        summaries = stats.summarize_top_failures(limit=10)

        # Should have 2 failure groups
        assert len(summaries) == 2

        # First group (most failures) should have 10 failures
        assert summaries[0]["total_failures"] == 10
        assert summaries[0]["top_reasons"][0]["reason_code"] == "selector_not_found"
        assert summaries[0]["top_reasons"][0]["count"] == 10

        # Second group should have 5 failures
        assert summaries[1]["total_failures"] == 5
        assert summaries[1]["top_reasons"][0]["reason_code"] == "timeout_error"

    def test_count_by_outcome(self):
        """Test outcome counting."""
        stats = GraphPolicyStats()

        # Record mix of outcomes
        outcomes = ["ok", "ok", "ok", "soft_fail", "soft_fail", "hard_fail"]
        for i, outcome in enumerate(outcomes):
            stats.record_transition(
                run_id="test",
                attempt=0,
                turn=0,
                step_index=i,
                site="test_site",
                page_kind="test_page",
                locale="en-US",
                role="test_role",
                action="fill",
                selector="test",
                outcome=outcome,
                reason_code="success" if outcome == "ok" else "test_error",
                elapsed_ms=100,
            )

        counts = stats.count_by_outcome()
        assert counts["ok"] == 3
        assert counts["soft_fail"] == 2
        assert counts["hard_fail"] == 1

    def test_count_by_selector_family(self):
        """Test selector family counting."""
        stats = GraphPolicyStats()

        selectors = [
            "[role='textbox']",
            "[role='button']",
            "[aria-label='Search']",
            "[data-testid='btn']",
            "[data-testid='input']",
        ]

        for i, selector in enumerate(selectors):
            stats.record_transition(
                run_id="test",
                attempt=0,
                turn=0,
                step_index=i,
                site="test_site",
                page_kind="test_page",
                locale="en-US",
                role="test_role",
                action="fill",
                selector=selector,
                outcome="ok",
                reason_code="success",
                elapsed_ms=100,
            )

        counts = stats.count_by_selector_family()
        assert counts["role"] == 2
        assert counts["aria-label"] == 1
        assert counts["data-attr"] == 2


class TestGraphStatsFileOperations:
    """Test file save/load operations."""

    def test_save_and_load_file(self, tmp_path):
        """Test save_to_file and from_file roundtrip."""
        stats1 = GraphPolicyStats()

        stats1.record_transition(
            run_id="test",
            attempt=0,
            turn=0,
            step_index=0,
            site="google_flights",
            page_kind="search_form",
            locale="ja-JP",
            role="origin",
            action="fill",
            selector="[role='textbox']",
            outcome="ok",
            reason_code="success",
            elapsed_ms=150,
        )

        # Save to temp file
        stats_file = tmp_path / "graph_stats.json"
        stats1.save_to_file(stats_file)

        assert stats_file.exists()

        # Load from file
        stats2 = GraphPolicyStats.from_file(stats_file)
        assert len(stats2.transitions) == 1
        assert stats2.transitions[0].run_id == "test"

    def test_load_nonexistent_file(self, tmp_path):
        """Test loading from nonexistent file returns empty stats."""
        stats = GraphPolicyStats.from_file(tmp_path / "nonexistent.json")
        assert len(stats.transitions) == 0

    def test_load_graph_stats_for_run(self, tmp_path):
        """Test load_graph_stats_for_run helper."""
        # Create mock runs structure
        run_id = "20260222_120000_abc"
        artifacts_dir = tmp_path / run_id / "artifacts"
        artifacts_dir.mkdir(parents=True)

        # Create stats file
        stats1 = GraphPolicyStats()
        stats1.record_transition(
            run_id=run_id,
            attempt=0,
            turn=0,
            step_index=0,
            site="test",
            page_kind="test",
            locale="en",
            role="origin",
            action="fill",
            selector="test",
            outcome="ok",
            reason_code="success",
            elapsed_ms=100,
        )
        stats_file = artifacts_dir / "graph_policy_stats.json"
        stats1.save_to_file(stats_file)

        # Load using helper
        stats2 = load_graph_stats_for_run(run_id, runs_root=tmp_path)
        assert stats2 is not None
        assert len(stats2.transitions) == 1

    def test_load_graph_stats_for_run_not_found(self, tmp_path):
        """Test load_graph_stats_for_run returns None for missing run."""
        stats = load_graph_stats_for_run("nonexistent_run", runs_root=tmp_path)
        assert stats is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
