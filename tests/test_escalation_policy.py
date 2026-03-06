"""Unit tests for adaptive escalation policy.

Tests signal extraction, decision logic, and artifact persistence.
All tests are deterministic and do not require Playwright.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from tempfile import TemporaryDirectory

from utils.escalation_policy import (
    extract_signals_from_log,
    decide_escalation,
    write_escalation_artifact,
    load_escalation_artifact,
    EscalationDecision,
)


class TestSignalExtraction:
    """Tests for extract_signals_from_log."""

    def test_extract_reason_codes(self):
        """Extract reason codes from log."""
        log = """
        [INFO] step_result reason_code=calendar_not_open action=wait
        [INFO] step_result reason_code=calendar_not_open action=wait
        [INFO] step_result reason_code=month_nav_exhausted action=fill
        """
        signals = extract_signals_from_log(log)

        assert signals["reason_code_counts"]["calendar_not_open"] == 2
        assert signals["reason_code_counts"]["month_nav_exhausted"] == 1
        assert signals["calendar_not_open_count"] == 2
        assert signals["month_nav_exhausted_count"] == 1

    def test_extract_soft_fail_count(self):
        """Count soft_fail patterns in log."""
        log = """
        [WARN] soft_fail detected in action fill
        [WARN] soft_fail detected in action click
        [WARN] soft_fail detected in action wait
        [INFO] step completed with ok
        """
        signals = extract_signals_from_log(log)
        assert signals["soft_fail_count"] == 3

    def test_extract_ready_false_count(self):
        """Count ready=False patterns in log."""
        log = """
        [DEBUG] step_state ready=False progress=10%
        [DEBUG] step_state ready=False progress=20%
        [DEBUG] step_state ready=True progress=100%
        """
        signals = extract_signals_from_log(log)
        assert signals["ready_false_count"] == 2

    def test_extract_turn_count(self):
        """Extract maximum turn number from log."""
        log = """
        [INFO] turn_1 starting
        [INFO] turn_2 starting
        [INFO] turn_3 starting
        [INFO] turn_4 starting
        """
        signals = extract_signals_from_log(log)
        assert signals["turn_count"] == 4

    def test_extract_route_fill_mismatch(self):
        """Extract route_fill_mismatch count."""
        log = """
        [INFO] step_result reason_code=route_fill_mismatch expected=HND got=NRT
        [INFO] step_result reason_code=route_fill_mismatch expected=ITM got=KIX
        [INFO] step_result reason_code=other_reason
        """
        signals = extract_signals_from_log(log)
        assert signals["route_fill_mismatch_count"] == 2

    def test_extract_multiple_signals(self):
        """Extract all signals from realistic log."""
        log = """
        [INFO] turn_1 starting
        [INFO] step_result reason_code=calendar_not_open
        [WARN] soft_fail detected
        [DEBUG] ready=False
        [INFO] turn_2 starting
        [INFO] step_result reason_code=calendar_not_open
        [INFO] step_result reason_code=month_nav_exhausted
        [WARN] soft_fail detected
        [DEBUG] ready=False
        [INFO] turn_3 starting
        [INFO] step_result reason_code=month_nav_exhausted
        [WARN] soft_fail detected
        [DEBUG] ready=True
        """
        signals = extract_signals_from_log(log)

        assert signals["calendar_not_open_count"] == 2
        assert signals["month_nav_exhausted_count"] == 2
        assert signals["soft_fail_count"] == 3
        assert signals["ready_false_count"] == 2
        assert signals["turn_count"] == 3
        assert signals["reason_code_counts"]["calendar_not_open"] == 2
        assert signals["reason_code_counts"]["month_nav_exhausted"] == 2

    def test_extract_empty_log(self):
        """Empty log should return zero signals."""
        signals = extract_signals_from_log("")
        assert signals["soft_fail_count"] == 0
        assert signals["turn_count"] == 0
        assert signals["ready_false_count"] == 0
        assert signals["reason_code_counts"] == {}


class TestEscalationDecision:
    """Tests for decide_escalation function."""

    def test_no_escalation_default_profile_no_signals(self):
        """Default profile with no stuckness should not escalate."""
        signals = {
            "reason_code_counts": {},
            "soft_fail_count": 0,
            "turn_count": 1,
            "ready_false_count": 0,
            "route_fill_mismatch_count": 0,
            "calendar_not_open_count": 0,
            "month_nav_exhausted_count": 0,
        }
        decision = decide_escalation(signals, current_profile="default")

        assert decision.should_escalate is False
        assert decision.next_profile == "default"

    def test_escalate_on_repeated_reason(self):
        """Should escalate when reason code repeats >= threshold."""
        signals = {
            "reason_code_counts": {"month_nav_exhausted": 2},
            "soft_fail_count": 0,
            "turn_count": 2,
            "ready_false_count": 0,
            "route_fill_mismatch_count": 0,
            "calendar_not_open_count": 0,
            "month_nav_exhausted_count": 2,
        }
        config = {"escalation_reason_repeat_threshold": 2}
        decision = decide_escalation(signals, current_profile="default", config=config)

        assert decision.should_escalate is True
        assert decision.next_profile == "debug"
        assert "reason_repeat" in decision.reason

    def test_escalate_on_soft_fail_loop(self):
        """Should escalate when soft fail count exceeds threshold."""
        signals = {
            "reason_code_counts": {},
            "soft_fail_count": 4,
            "turn_count": 3,
            "ready_false_count": 0,
            "route_fill_mismatch_count": 0,
            "calendar_not_open_count": 0,
            "month_nav_exhausted_count": 0,
        }
        config = {"escalation_soft_fail_threshold": 3}
        decision = decide_escalation(signals, current_profile="default", config=config)

        assert decision.should_escalate is True
        assert decision.next_profile == "debug"
        assert "soft_fail_loop" in decision.reason

    def test_escalate_on_route_fill_mismatch(self):
        """Should escalate when route_fill_mismatch repeats."""
        signals = {
            "reason_code_counts": {},
            "soft_fail_count": 0,
            "turn_count": 2,
            "ready_false_count": 0,
            "route_fill_mismatch_count": 2,
            "calendar_not_open_count": 0,
            "month_nav_exhausted_count": 0,
        }
        config = {"escalation_route_fill_mismatch_threshold": 2}
        decision = decide_escalation(signals, current_profile="default", config=config)

        assert decision.should_escalate is True
        assert "route_mismatch_loop" in decision.reason

    def test_escalate_on_calendar_loop(self):
        """Should escalate when both calendar_not_open and month_nav_exhausted occur."""
        signals = {
            "reason_code_counts": {},
            "soft_fail_count": 0,
            "turn_count": 2,
            "ready_false_count": 0,
            "route_fill_mismatch_count": 0,
            "calendar_not_open_count": 1,
            "month_nav_exhausted_count": 1,
        }
        config = {"escalation_calendar_loop_detection": True}
        decision = decide_escalation(signals, current_profile="default", config=config)

        assert decision.should_escalate is True
        assert "calendar_loop" in decision.reason

    def test_no_escalate_on_calendar_loop_if_disabled(self):
        """Calendar loop detection can be disabled."""
        signals = {
            "reason_code_counts": {},
            "soft_fail_count": 0,
            "turn_count": 2,
            "ready_false_count": 0,
            "route_fill_mismatch_count": 0,
            "calendar_not_open_count": 1,
            "month_nav_exhausted_count": 1,
        }
        config = {"escalation_calendar_loop_detection": False}
        decision = decide_escalation(signals, current_profile="default", config=config)

        assert decision.should_escalate is False

    def test_escalate_on_low_progress(self):
        """Should escalate on high turns with ready=False persisting."""
        signals = {
            "reason_code_counts": {},
            "soft_fail_count": 0,
            "turn_count": 3,
            "ready_false_count": 2,  # ready=False in 2+ turns
            "route_fill_mismatch_count": 0,
            "calendar_not_open_count": 0,
            "month_nav_exhausted_count": 0,
        }
        config = {"escalation_max_turns_without_ready": 2}
        decision = decide_escalation(signals, current_profile="default", config=config)

        assert decision.should_escalate is True
        assert "low_progress" in decision.reason

    def test_no_escalate_when_already_debug(self):
        """No escalation when already at debug profile."""
        signals = {
            "reason_code_counts": {"month_nav_exhausted": 5},
            "soft_fail_count": 10,
            "turn_count": 5,
            "ready_false_count": 5,
            "route_fill_mismatch_count": 5,
            "calendar_not_open_count": 5,
            "month_nav_exhausted_count": 5,
        }
        decision = decide_escalation(signals, current_profile="debug")

        assert decision.should_escalate is False
        assert decision.next_profile == "debug"
        assert "Already at debug" in decision.reason

    def test_multiple_rules_fired(self):
        """Report which rules fired when multiple conditions met."""
        signals = {
            "reason_code_counts": {"month_nav_exhausted": 2, "calendar_not_open": 2},
            "soft_fail_count": 5,
            "turn_count": 4,
            "ready_false_count": 3,
            "route_fill_mismatch_count": 2,
            "calendar_not_open_count": 2,
            "month_nav_exhausted_count": 2,
        }
        config = {
            "escalation_reason_repeat_threshold": 2,
            "escalation_soft_fail_threshold": 3,
            "escalation_max_turns_without_ready": 2,
            "escalation_route_fill_mismatch_threshold": 2,
            "escalation_calendar_loop_detection": True,
        }
        decision = decide_escalation(signals, current_profile="default", config=config)

        assert decision.should_escalate is True
        assert len(decision.fired_rules) >= 3  # At least 3 rules fired

    def test_default_config_if_none(self):
        """Use default config if config parameter is None."""
        signals = {
            "reason_code_counts": {"some_reason": 10},
            "soft_fail_count": 100,
            "turn_count": 10,
            "ready_false_count": 10,
            "route_fill_mismatch_count": 10,
            "calendar_not_open_count": 10,
            "month_nav_exhausted_count": 10,
        }
        # Pass config=None (triggers defaults)
        decision = decide_escalation(signals, current_profile="default", config=None)

        # With such high signals, should escalate even with default thresholds
        assert decision.should_escalate is True


class TestArtifactPersistence:
    """Tests for writing and loading escalation artifacts."""

    def test_write_escalation_artifact(self):
        """Write escalation decision to storage/runs/<run_id>/escalation.json."""
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "storage" / "runs" / "test_run_123"
            decision = EscalationDecision(
                should_escalate=True,
                current_profile="default",
                next_profile="debug",
                reason="Repeated month_nav_exhausted",
                key_counts={"month_nav_exhausted": 2},
                fired_rules=["reason_repeat(month_nav_exhausted=2 >= 2)"],
            )

            escalation_path = write_escalation_artifact("test_run_123", decision, run_dir)

            assert escalation_path.exists()
            assert escalation_path.name == "escalation.json"

            content = json.loads(escalation_path.read_text())
            assert content["run_id"] == "test_run_123"
            assert content["from_profile"] == "default"
            assert content["to_profile"] == "debug"
            assert content["should_escalate"] is True

    def test_write_escalation_artifact_idempotent(self):
        """Write is idempotent: doesn't overwrite existing escalation.json."""
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "storage" / "runs" / "test_run_234"

            decision1 = EscalationDecision(
                should_escalate=True,
                current_profile="default",
                next_profile="debug",
                reason="First decision",
                key_counts={},
                fired_rules=[],
            )

            path1 = write_escalation_artifact("test_run_234", decision1, run_dir)

            # Try to write again with different decision
            decision2 = EscalationDecision(
                should_escalate=False,
                current_profile="default",
                next_profile="default",
                reason="Second decision (should not overwrite)",
                key_counts={},
                fired_rules=[],
            )

            path2 = write_escalation_artifact("test_run_234", decision2, run_dir)

            assert path1 == path2
            # First write should be preserved
            content = json.loads(path1.read_text())
            assert content["reason"] == "First decision"

    def test_load_escalation_artifact(self):
        """Load escalation artifact from run_dir."""
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "storage" / "runs" / "test_run_345"

            decision = EscalationDecision(
                should_escalate=True,
                current_profile="default",
                next_profile="debug",
                reason="Soft fail loop detected",
                key_counts={"soft_fails": 5},
                fired_rules=["soft_fail_loop(count=5 >= 3)"],
            )

            write_escalation_artifact("test_run_345", decision, run_dir)

            loaded = load_escalation_artifact(run_dir)

            assert loaded is not None
            assert loaded["run_id"] == "test_run_345"
            assert loaded["to_profile"] == "debug"
            assert loaded["should_escalate"] is True

    def test_load_escalation_artifact_not_exists(self):
        """Load returns None if escalation.json doesn't exist."""
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "storage" / "runs" / "test_run_456"
            run_dir.mkdir(parents=True, exist_ok=True)

            loaded = load_escalation_artifact(run_dir)
            assert loaded is None


class TestIntegration:
    """Integration tests combining extraction and decision."""

    def test_full_pipeline_should_escalate(self):
        """Full pipeline: log -> signals -> decision -> artifact."""
        log = """
        [INFO] turn_1 starting
        [INFO] step_result reason_code=month_nav_exhausted
        [WARN] soft_fail detected
        [DEBUG] ready=False
        [INFO] turn_2 starting
        [INFO] step_result reason_code=month_nav_exhausted
        [WARN] soft_fail detected
        [DEBUG] ready=False
        [INFO] completed with failure
        """

        signals = extract_signals_from_log(log)
        assert signals["month_nav_exhausted_count"] == 2
        assert signals["soft_fail_count"] == 2

        config = {
            "escalation_reason_repeat_threshold": 2,
            "escalation_soft_fail_threshold": 3,
        }
        decision = decide_escalation(signals, current_profile="default", config=config)

        assert decision.should_escalate is True
        assert decision.next_profile == "debug"

    def test_full_pipeline_no_escalate(self):
        """Full pipeline with healthy signals should not escalate."""
        log = """
        [INFO] turn_1 starting
        [INFO] step_result reason_code=ok
        [DEBUG] ready=True
        [INFO] completed with success
        """

        signals = extract_signals_from_log(log)
        decision = decide_escalation(signals, current_profile="default")

        assert decision.should_escalate is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
