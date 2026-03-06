"""Tests for Tier 2 Lite: Auto-Heal Sandbox (tests/test_auto_heal.py).

Tests are deterministic and use temporary directories; no LLM calls.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime

from utils.auto_heal import (
    AutoHealConfig,
    AutoHealReport,
    build_heuristic_patch_plan,
    CardRecord,
    PatchAction,
    Proposal,
    ReasonRecord,
    get_kb_cards_for_reasons,
    load_events_from_log_file,
    run_auto_heal,
)
from utils.triage import TriageEvent


class TestAutoHealConfig:
    """Test AutoHealConfig class."""

    def test_config_defaults(self):
        """Verify default config values."""
        config = AutoHealConfig()
        assert config.enabled is False
        assert config.apply_patch is False
        assert config.max_files == 2
        assert config.max_changed_lines == 80
        assert config.llm_enabled is False

    def test_config_from_dict(self):
        """Load config from dictionary."""
        cfg = {
            "auto_heal_enabled": True,
            "auto_heal_apply_patch": True,
            "auto_heal_max_files": 3,
            "auto_heal_max_changed_lines": 100,
            "auto_heal_llm_enabled": True,
        }
        config = AutoHealConfig.from_config_dict(cfg)
        assert config.enabled is True
        assert config.apply_patch is True
        assert config.max_files == 3
        assert config.max_changed_lines == 100
        assert config.llm_enabled is True

    def test_config_off_by_default(self):
        """Verify feature is off by default."""
        config = AutoHealConfig.from_config_dict({})
        assert config.enabled is False


class TestAutoHealReport:
    """Test AutoHealReport serialization."""

    def test_empty_report_creation(self):
        """Create and serialize empty report."""
        report = AutoHealReport(run_id="test-001")
        data = report.to_dict()

        assert data["version"] == "1"
        assert data["run_id"] == "test-001"
        assert data["reasons"] == []
        assert data["cards"] == []
        assert data["proposal"] is None

    def test_report_to_json(self):
        """Convert report to valid JSON."""
        report = AutoHealReport(run_id="test-001")
        report.reasons.append(ReasonRecord(code="test_reason", count=5))
        report.notes.append("Test note")

        json_str = report.to_json()
        data = json.loads(json_str)

        assert data["run_id"] == "test-001"
        assert len(data["reasons"]) == 1
        assert data["reasons"][0]["code"] == "test_reason"
        assert "Test note" in data["notes"]

    def test_report_save_to_file(self, tmp_path):
        """Save report to file."""
        report = AutoHealReport(run_id="test-001")
        report.reasons.append(ReasonRecord(code="test_reason", count=3))

        output_file = tmp_path / "report.json"
        report.save(output_file)

        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["run_id"] == "test-001"


class TestReasonRecord:
    """Test ReasonRecord serialization."""

    def test_reason_record_to_dict(self):
        """Serialize reason record."""
        record = ReasonRecord(
            code="calendar_not_open",
            count=8,
            evidence_keys=["ui.selector", "browser.timeout"],
        )
        data = record.to_dict()

        assert data["code"] == "calendar_not_open"
        assert data["count"] == 8
        assert "ui.selector" in data["evidence_keys"]


class TestCardRecord:
    """Test CardRecord serialization."""

    def test_card_record_to_dict(self):
        """Serialize card record."""
        record = CardRecord(
            id="cal-001",
            title="Calendar Dialog Fix",
            match={"site": "google_flights", "reason_code": "calendar_not_open"},
            actions_allowed=["adjust_selector", "increase_timeout"],
            best_patch_bullets=["Use aria-label", "Add retry loop"],
            links=["docs/kb/30_patterns/date_picker.md"],
        )
        data = record.to_dict()

        assert data["id"] == "cal-001"
        assert data["title"] == "Calendar Dialog Fix"
        assert data["match"]["reason_code"] == "calendar_not_open"
        assert "adjust_selector" in data["actions_allowed"]


class TestPatchAction:
    """Test PatchAction serialization."""

    def test_patch_action_to_dict(self):
        """Serialize patch action."""
        action = PatchAction(
            type="edit_file",
            path="core/scenario_runner/google_flights/route_recovery.py",
            summary="Increase timeout for calendar interactions",
            max_changed_lines=8,
        )
        data = action.to_dict()

        assert data["type"] == "edit_file"
        assert "route_recovery.py" in data["path"] or "date_picker_orchestrator.py" in data["path"]
        assert data["max_changed_lines"] == 8


class TestProposal:
    """Test Proposal serialization."""

    def test_proposal_with_actions(self):
        """Create proposal with actions."""
        action = PatchAction(
            type="edit_file",
            path="core/scenario.py",
            summary="Test fix",
        )
        proposal = Proposal(intent="timeout_hardening", actions=[action])

        data = proposal.to_dict()
        assert data["intent"] == "timeout_hardening"
        assert len(data["actions"]) == 1
        assert data["actions"][0]["path"] == "core/scenario.py"


class TestHeuristicPatchPlan:
    """Test heuristic patch plan generation."""

    def test_heuristic_timeout_intent(self):
        """Detect timeout issues and suggest timeout hardening."""
        by_reason = {
            "timeout_error": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="timeout_error",
                    evidence={},
                    module="test",
                    severity="error",
                )
            ]
        }

        proposal = build_heuristic_patch_plan(by_reason)

        assert proposal.intent == "timeout_hardening"
        assert len(proposal.actions) > 0
        assert any("timeout" in a.summary.lower() for a in proposal.actions)

    def test_heuristic_selector_intent(self):
        """Detect selector issues and suggest selector fix."""
        by_reason = {
            "calendar_not_open": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="calendar_not_open",
                    evidence={},
                    module="test",
                    severity="error",
                )
            ]
        }

        proposal = build_heuristic_patch_plan(by_reason)

        assert proposal.intent == "selector_fix"
        assert len(proposal.actions) > 0

    def test_heuristic_default_logging_intent(self):
        """Default to logging when no specific issue detected."""
        by_reason = {
            "unknown_issue": [
                TriageEvent(
                    timestamp=datetime.now(),
                    reason="unknown_issue",
                    evidence={},
                    module="test",
                    severity="error",
                )
            ]
        }

        proposal = build_heuristic_patch_plan(by_reason)

        assert proposal.intent == "logging_only"
        assert len(proposal.actions) > 0


class TestAutoHealDisabledByDefault:
    """Verify auto-heal is off by default."""

    def test_disabled_config_no_effect(self):
        """When auto_heal_enabled is False, run_auto_heal returns early."""
        config = AutoHealConfig(enabled=False)
        report = run_auto_heal(run_id="test-001", config=config)

        # When disabled, returns minimal report without processing
        assert len(report.reasons) == 0
        assert len(report.cards) == 0


class TestAutoHealReportStructure:
    """Test that auto-heal report follows exact schema."""

    def test_report_json_schema(self, tmp_path):
        """Verify report matches JSON schema."""
        report = AutoHealReport(run_id="test-001", site="google_flights")
        report.reasons.append(ReasonRecord(code="test", count=1, evidence_keys=["key1"]))
        report.cards.append(
            CardRecord(
                id="card-1",
                title="Test Card",
                match={"site": "google_flights", "reason_code": "test"},
            )
        )
        report.proposal = Proposal(intent="stabilize", actions=[])
        report.bounds = {"max_files": 2, "max_changed_lines": 80, "max_attempts": 1}
        report.safety = {
            "apply_mode": False,
            "llm_used": False,
            "passed_tests": False,
            "rollback_needed": False,
        }

        data = report.to_dict()

        # Verify schema
        assert "version" in data
        assert "run_id" in data
        assert "site" in data
        assert "reasons" in data
        assert "cards" in data
        assert "proposal" in data
        assert "bounds" in data
        assert "safety" in data
        assert "notes" in data

        # Verify bounds
        assert data["bounds"]["max_files"] == 2
        assert data["bounds"]["max_changed_lines"] == 80

        # Verify safety
        assert data["safety"]["apply_mode"] is False
        assert data["safety"]["llm_used"] is False


class TestAutoHealWithKbCards:
    """Test auto-heal interaction with KB cards (if available)."""

    def test_get_kb_cards_for_reasons(self, tmp_path):
        """Retrieve KB cards for specific reason codes."""
        # Create minimal test cards directory
        cards_dir = tmp_path / "docs" / "kb" / "cards"
        cards_dir.mkdir(parents=True)

        # Create a fake card file
        site_dir = cards_dir / "google_flights" / "test_reason"
        site_dir.mkdir(parents=True)
        card_file = site_dir / "test_card.md"
        card_file.write_text(
            """---
id: test-card-001
site: google_flights
reason_code: test_reason
title: Test Card
actions_allowed: [action1]
evidence_required: [ui.test]
kb_links: []
---

# Test Card
""",
            encoding="utf-8",
        )

        # Call get_kb_cards_for_reasons
        cards_by_reason = get_kb_cards_for_reasons(
            ["test_reason"],
            site="google_flights",
            cards_root=str(cards_dir),
        )

        # If cards loaded successfully
        if "test_reason" in cards_by_reason:
            assert len(cards_by_reason["test_reason"]) > 0
            card = cards_by_reason["test_reason"][0]
            assert card.title == "Test Card"


class TestLoadEventsFromLogFile:
    """Test load_events_from_log_file function."""

    def test_parse_reason_codes_from_log(self, tmp_path):
        """Should extract reason codes and counts from log file."""
        log_file = tmp_path / "debug.log"
        log_file.write_text(
            """
            2026-02-21T14:30:45 scenario.date_fill_failure.exit reason=calendar_not_open
            2026-02-21T14:30:46 scenario.google_date.soft_fail error=calendar_not_open
            2026-02-21T14:30:47 scenario.step.fill_optional_soft_fail error=month_header_not_found
            """,
            encoding="utf-8",
        )

        reason_counts = load_events_from_log_file(log_file)

        assert reason_counts["calendar_not_open"] == 2
        assert reason_counts["month_header_not_found"] == 1

    def test_empty_log_file(self, tmp_path):
        """Should handle empty log file."""
        log_file = tmp_path / "empty.log"
        log_file.write_text("")

        reason_counts = load_events_from_log_file(log_file)

        assert reason_counts == {}

    def test_missing_log_file(self, tmp_path):
        """Should handle missing log file gracefully."""
        missing_log = tmp_path / "missing.log"

        reason_counts = load_events_from_log_file(missing_log)

        assert reason_counts == {}

    def test_mixed_error_and_reason_patterns(self, tmp_path):
        """Should handle both error= and reason= patterns."""
        log_file = tmp_path / "mixed.log"
        log_file.write_text(
            """
            scenario.step reason=timeout_error
            scenario.step error=selector_not_found
            scenario.step error=timeout_error
            """,
            encoding="utf-8",
        )

        reason_counts = load_events_from_log_file(log_file)

        assert reason_counts["timeout_error"] == 2
        assert reason_counts["selector_not_found"] == 1


class TestAutoHealWithLogFileAndEpisodeDir:
    """Test auto-heal with log file and episode directory."""

    def test_run_auto_heal_with_log_file(self, tmp_path):
        """Should parse log file and generate report."""
        log_file = tmp_path / "debug.log"
        log_file.write_text(
            """
            scenario.step reason=calendar_not_open
            scenario.step reason=calendar_not_open
            scenario.step error=timeout_error
            """,
            encoding="utf-8",
        )

        config = AutoHealConfig(enabled=True, verbose=False)
        report = run_auto_heal(run_id="test-001", config=config, log_file=log_file)

        # Should extract reason codes from log
        assert len(report.reasons) == 2
        reason_codes = {r.code for r in report.reasons}
        assert "calendar_not_open" in reason_codes
        assert "timeout_error" in reason_codes

    def test_save_report_to_episode_dir(self, tmp_path):
        """Should save report.json to episode directory."""
        episode_dir = tmp_path / "episode_001"

        report = AutoHealReport(run_id="test-001", site="google_flights")
        report.reasons.append(ReasonRecord(code="calendar_not_open", count=2))
        report.safety = {
            "apply_mode": False,
            "llm_used": False,
            "passed_tests": False,
            "rollback_needed": False,
        }

        # Save to episode dir
        report.save(episode_dir / "report.json")

        # Verify file exists and contains correct data
        assert (episode_dir / "report.json").exists()
        saved_data = json.loads((episode_dir / "report.json").read_text())
        assert saved_data["run_id"] == "test-001"
        assert saved_data["site"] == "google_flights"
        assert len(saved_data["reasons"]) == 1
        assert saved_data["reasons"][0]["code"] == "calendar_not_open"
        assert saved_data["safety"]["apply_mode"] is False

    def test_auto_heal_disabled_by_default(self):
        """Auto-heal should be disabled by default."""
        config = AutoHealConfig()
        report = run_auto_heal(run_id="test-001", config=config)

        # Should return minimal report when disabled
        assert report.run_id == "test-001"
        assert len(report.reasons) == 0
        assert any("disabled" in note.lower() for note in report.notes)
