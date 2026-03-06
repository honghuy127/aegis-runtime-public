"""Tests for triage KB cards integration (tests/test_triage_cards.py).

Tests are deterministic and use temporary directories; no browser or LLM calls.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime
from utils.triage import (
    format_human_report,
    format_json_report,
    TriageEvent,
    read_kb_cards_config,
)
from utils.kb_cards import Card


class TestReadKbCardsConfig:
    """Test reading KB cards config."""

    def test_config_read_true(self, tmp_path):
        """Read kb_cards_enabled when set to true."""
        config_file = tmp_path / "configs" / "run.yaml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("kb_cards_enabled: true\n", encoding="utf-8")

        # Temporarily change cwd to read config
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = read_kb_cards_config()
            assert result is True
        finally:
            os.chdir(old_cwd)

    def test_config_read_false(self, tmp_path):
        """Read kb_cards_enabled when set to false."""
        config_file = tmp_path / "configs" / "run.yaml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("kb_cards_enabled: false\n", encoding="utf-8")

        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = read_kb_cards_config()
            assert result is False
        finally:
            os.chdir(old_cwd)

    def test_config_read_missing(self, tmp_path):
        """Fallback to false when config missing."""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = read_kb_cards_config()
            assert result is False
        finally:
            os.chdir(old_cwd)


class TestHumanReportWithCards:
    """Test human-readable output with KB cards."""

    def test_without_cards(self, capsys):
        """Verify report is formatted without cards when none provided."""
        by_reason = {
            "test_reason": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="test_reason",
                    evidence={"key": "value"},
                    module="test_module",
                    severity="error",
                )
            ]
        }

        output = format_human_report(by_reason)

        assert "test_reason" in output
        assert "KB Cards" not in output

    def test_with_cards(self):
        """Verify report includes cards when provided."""
        by_reason = {
            "test_reason": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="test_reason",
                    evidence={},
                    module="test",
                    severity="error",
                )
            ]
        }

        cards = [
            Card(
                path="docs/kb/cards/test/card1.md",
                site="test_site",
                reason_code="test_reason",
                title="Test Card",
                actions_allowed=["action1", "action2"],
                evidence_required=["ui.test"],
                kb_links=["docs/kb/test.md"],
                code_refs=["test.py:123"],
            )
        ]

        cards_by_reason = {"test_reason": cards}
        output = format_human_report(by_reason, cards_by_reason=cards_by_reason)

        assert "KB Cards" in output
        assert "Test Card" in output
        assert "docs/kb/cards/test/card1.md" in output
        assert "action1, action2" in output
        assert "ui.test" in output

    def test_with_multiple_cards_limited_to_3(self):
        """Verify only top 3 cards are shown."""
        by_reason = {
            "test_reason": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="test_reason",
                    evidence={},
                    module="test",
                    severity="error",
                )
            ]
        }

        cards = [
            Card(
                path=f"docs/kb/cards/test/card{i}.md",
                site="test_site",
                reason_code="test_reason",
                title=f"Test Card {i}",
                actions_allowed=[],
                evidence_required=[],
                kb_links=[],
                code_refs=[],
            )
            for i in range(5)
        ]

        cards_by_reason = {"test_reason": cards}
        output = format_human_report(by_reason, cards_by_reason=cards_by_reason)

        # Should include first 3
        assert "Test Card 0" in output
        assert "Test Card 1" in output
        assert "Test Card 2" in output
        # Should not include 4 or 5
        assert "Test Card 3" not in output
        assert "Test Card 4" not in output


class TestJsonReportWithCards:
    """Test JSON output with KB cards."""

    def test_without_cards(self):
        """Verify JSON structure without cards."""
        by_reason = {
            "test_reason": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="test_reason",
                    evidence={},
                    module="test",
                    severity="error",
                )
            ]
        }

        output = format_json_report(by_reason)
        data = json.loads(output)

        assert "reasons" in data
        assert len(data["reasons"]) == 1
        assert data["reasons"][0]["code"] == "test_reason"
        assert "cards" not in data["reasons"][0]

    def test_with_cards(self):
        """Verify JSON includes cards when provided."""
        by_reason = {
            "test_reason": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="test_reason",
                    evidence={},
                    module="test",
                    severity="error",
                )
            ]
        }

        cards = [
            Card(
                path="docs/kb/cards/test/card1.md",
                site="test_site",
                reason_code="test_reason",
                title="Test Card",
                actions_allowed=["action1"],
                evidence_required=["ui.test"],
                kb_links=["docs/kb/test.md"],
                code_refs=["test.py:123"],
            )
        ]

        cards_by_reason = {"test_reason": cards}
        output = format_json_report(by_reason, cards_by_reason=cards_by_reason)
        data = json.loads(output)

        assert "cards" in data["reasons"][0]
        assert len(data["reasons"][0]["cards"]) == 1

        card = data["reasons"][0]["cards"][0]
        assert card["title"] == "Test Card"
        assert card["path"] == "docs/kb/cards/test/card1.md"
        assert card["actions_allowed"] == ["action1"]
        assert card["evidence_required"] == ["ui.test"]
        assert card["kb_links"] == ["docs/kb/test.md"]
        assert card["code_refs"] == ["test.py:123"]

    def test_json_respects_limit(self):
        """Verify JSON includes only top N cards."""
        by_reason = {
            "test_reason": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="test_reason",
                    evidence={},
                    module="test",
                    severity="error",
                )
            ]
        }

        cards = [
            Card(
                path=f"docs/kb/cards/test/card{i}.md",
                site="test_site",
                reason_code="test_reason",
                title=f"Test Card {i}",
                actions_allowed=[],
                evidence_required=[],
                kb_links=[],
                code_refs=[],
            )
            for i in range(5)
        ]

        cards_by_reason = {"test_reason": cards}
        output = format_json_report(by_reason, cards_by_reason=cards_by_reason)
        data = json.loads(output)

        # Should have 3 cards max
        assert len(data["reasons"][0]["cards"]) <= 3
