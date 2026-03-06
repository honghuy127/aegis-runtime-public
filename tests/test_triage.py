"""Tests for triage helper utilities."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from utils.triage import (
    TriageEvent,
    aggregate_by_reason,
    format_human_report,
    format_json_report,
    load_events_from_log_text,
    parse_error_json_file,
)


class TestTriageEventDataclass:
    """Tests for TriageEvent dataclass."""

    def test_triage_event_creation(self):
        """Should create TriageEvent with required fields."""
        ts = datetime.now()
        event = TriageEvent(timestamp=ts, reason="test_reason", evidence={})
        assert event.timestamp == ts
        assert event.reason == "test_reason"
        assert event.evidence == {}

    def test_triage_event_with_metadata(self):
        """Should support optional metadata fields."""
        event = TriageEvent(
            timestamp=datetime.now(),
            reason="calendar_not_open",
            evidence={"selector_attempts": 3},
            module="gf_set_date",
            severity="error",
        )
        assert event.module == "gf_set_date"
        assert event.severity == "error"


class TestParseErrorJson:
    """Tests for parse_error_json_file function."""

    def test_parse_error_json_valid(self):
        """Should parse valid error JSON file."""
        with TemporaryDirectory() as tmpdir:
            json_file = Path(tmpdir) / "error.json"
            json_file.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-02-21T14:30:45",
                        "reason": "calendar_not_open",
                        "evidence": {"selector_attempts": 5},
                        "module": "gf_set_date",
                        "severity": "error",
                    }
                )
            )

            event = parse_error_json_file(json_file)
            assert event is not None
            assert event.reason == "calendar_not_open"
            assert event.evidence["selector_attempts"] == 5
            assert event.module == "gf_set_date"
            assert event.severity == "error"

    def test_parse_error_json_missing_file(self):
        """Should return None for non-existent file."""
        missing_path = Path("/nonexistent/error.json")
        event = parse_error_json_file(missing_path)
        assert event is None

    def test_parse_error_json_invalid_json(self):
        """Should handle invalid JSON gracefully."""
        with TemporaryDirectory() as tmpdir:
            json_file = Path(tmpdir) / "invalid.json"
            json_file.write_text("{ invalid json }")

            event = parse_error_json_file(json_file)
            assert event is None

    def test_parse_error_json_missing_fields(self):
        """Should handle missing fields with defaults."""
        with TemporaryDirectory() as tmpdir:
            json_file = Path(tmpdir) / "minimal.json"
            json_file.write_text(json.dumps({"reason": "budget_hit"}))

            event = parse_error_json_file(json_file)
            assert event is not None
            assert event.reason == "budget_hit"
            assert event.evidence == {}
            assert event.module == ""
            assert event.severity == "error"

    def test_parse_error_json_iso_timestamp(self):
        """Should parse ISO format timestamps."""
        with TemporaryDirectory() as tmpdir:
            json_file = Path(tmpdir) / "ts.json"
            ts_str = "2026-02-21T14:30:45Z"
            json_file.write_text(json.dumps({"timestamp": ts_str, "reason": "test"}))

            event = parse_error_json_file(json_file)
            assert event is not None
            assert event.timestamp.year == 2026
            assert event.timestamp.month == 2


class TestAggregateByReason:
    """Tests for aggregate_by_reason function."""

    def test_aggregate_empty_list(self):
        """Should handle empty event list."""
        result = aggregate_by_reason([])
        assert result == {}

    def test_aggregate_single_reason(self):
        """Should group single reason."""
        events = [
            TriageEvent(datetime.now(), "reason1", {}),
            TriageEvent(datetime.now(), "reason1", {}),
        ]
        result = aggregate_by_reason(events)
        assert len(result) == 1
        assert "reason1" in result
        assert len(result["reason1"]) == 2

    def test_aggregate_multiple_reasons(self):
        """Should group multiple reasons correctly."""
        events = [
            TriageEvent(datetime.now(), "reason1", {}),
            TriageEvent(datetime.now(), "reason2", {}),
            TriageEvent(datetime.now(), "reason1", {}),
            TriageEvent(datetime.now(), "reason3", {}),
        ]
        result = aggregate_by_reason(events)
        assert len(result) == 3
        assert len(result["reason1"]) == 2
        assert len(result["reason2"]) == 1
        assert len(result["reason3"]) == 1

    def test_aggregate_preserves_events(self):
        """Should preserve event data during aggregation."""
        now = datetime.now()
        event1 = TriageEvent(now, "test_reason", {"key": "value1"})
        event2 = TriageEvent(now, "test_reason", {"key": "value2"})

        result = aggregate_by_reason([event1, event2])
        grouped = result["test_reason"]

        assert event1 in grouped
        assert event2 in grouped

    def test_aggregate_normalizes_aliases_to_canonical(self):
        """Known aliases should aggregate under canonical reason key."""
        events = [
            TriageEvent(datetime.now(), "calendar_not_open", {}),
            TriageEvent(datetime.now(), "calendar_dialog_not_found", {}),
        ]
        result = aggregate_by_reason(events)
        assert "calendar_dialog_not_found" in result
        assert len(result["calendar_dialog_not_found"]) == 2


class TestFormatHumanReport:
    """Tests for format_human_report function."""

    def test_format_human_report_empty(self):
        """Should handle empty event dict."""
        output = format_human_report({})
        assert "No failure events found" in output

    def test_format_human_report_single_reason(self):
        """Should format single reason in report."""
        events = [
            TriageEvent(datetime.now(), "calendar_not_open", {}),
        ]
        by_reason = aggregate_by_reason(events)

        output = format_human_report(by_reason)
        assert "calendar_dialog_not_found" in output
        assert "Raw reasons: calendar_not_open (1)" in output
        assert "1 occurrences" in output

    def test_format_human_report_multiple_reasons(self):
        """Should format and sort multiple reasons by frequency."""
        events = [
            TriageEvent(datetime.now(), "reason1", {}),
            TriageEvent(datetime.now(), "reason1", {}),
            TriageEvent(datetime.now(), "reason1", {}),
            TriageEvent(datetime.now(), "reason2", {}),
        ]
        by_reason = aggregate_by_reason(events)

        output = format_human_report(by_reason)
        # reason1 should come before reason2 (more frequent)
        assert output.find("reason1") < output.find("reason2")

    def test_format_human_report_percentage(self):
        """Should include percentage of failures."""
        events = [
            TriageEvent(datetime.now(), "reason1", {}),
            TriageEvent(datetime.now(), "reason1", {}),
            TriageEvent(datetime.now(), "reason2", {}),
        ]
        by_reason = aggregate_by_reason(events)

        output = format_human_report(by_reason)
        # reason1: 2/3 = 66.7%
        assert "66.7%" in output or "66" in output


class TestFormatJsonReport:
    """Tests for format_json_report function."""

    def test_format_json_report_empty(self):
        """Should format empty dict as valid JSON."""
        output = format_json_report({})
        data = json.loads(output)
        assert "timestamp" in data
        assert "total_events" in data
        assert data["total_events"] == 0
        assert data["reasons"] == []

    def test_format_json_report_single_reason(self):
        """Should include reason in JSON."""
        events = [TriageEvent(datetime.now(), "test_reason", {})]
        by_reason = aggregate_by_reason(events)

        output = format_json_report(by_reason)
        data = json.loads(output)

        assert data["total_events"] == 1
        assert len(data["reasons"]) == 1
        assert data["reasons"][0]["code"] == "test_reason"
        assert data["reasons"][0]["count"] == 1
        assert "raw_reason_counts" in data["reasons"][0]

    def test_format_json_report_valid_json(self):
        """Should produce valid JSON structure."""
        events = [
            TriageEvent(datetime.now(), "reason1", {}),
            TriageEvent(datetime.now(), "reason1", {}),
            TriageEvent(datetime.now(), "reason2", {}),
        ]
        by_reason = aggregate_by_reason(events)

        output = format_json_report(by_reason)
        data = json.loads(output)

        assert isinstance(data, dict)
        assert isinstance(data["reasons"], list)
        assert all("code" in r for r in data["reasons"])
        assert all("count" in r for r in data["reasons"])

    def test_format_json_report_sorted_by_frequency(self):
        """Should sort reasons by frequency."""
        events = [
            TriageEvent(datetime.now(), "frequent", {}),
            TriageEvent(datetime.now(), "frequent", {}),
            TriageEvent(datetime.now(), "rare", {}),
        ]
        by_reason = aggregate_by_reason(events)

        output = format_json_report(by_reason)
        data = json.loads(output)

        assert data["reasons"][0]["code"] == "frequent"
        assert data["reasons"][0]["count"] == 2
        assert data["reasons"][1]["code"] == "rare"
        assert data["reasons"][1]["count"] == 1


class TestLoadEventsFromLogText:
    """Tests for load_events_from_log_text function."""

    def test_parse_reason_pattern_from_log(self):
        """Should extract reason codes from log lines with reason=."""
        log_text = """
        2026-02-21T14:30:45 scenario.date_fill_failure.exit reason=calendar_not_open
        2026-02-21T14:30:46 scenario.google_date.soft_fail ... error=calendar_not_open
        2026-02-21T14:30:47 scenario.step.fill_optional_soft_fail ... error=month_header_not_found
        """
        events, run_id = load_events_from_log_text(log_text)

        # Should extract all three unique reason codes
        reason_codes = {e.reason for e in events}
        assert "calendar_not_open" in reason_codes
        assert "month_header_not_found" in reason_codes
        assert len(reason_codes) == 2  # calendar_not_open appears twice, so 2 unique

    def test_parse_error_pattern_from_log(self):
        """Should extract error codes from log lines with error=."""
        log_text = """
        scenario.step error=timeout_error
        scenario.step error=selector_not_found
        """
        events, run_id = load_events_from_log_text(log_text)

        reason_codes = {e.reason for e in events}
        assert "timeout_error" in reason_codes
        assert "selector_not_found" in reason_codes

    def test_count_duplicate_reasons_in_log(self):
        """Should count occurrences of same reason code."""
        log_text = """
        scenario.step reason=calendar_not_open
        scenario.step reason=calendar_not_open
        scenario.step reason=calendar_not_open
        """
        events, run_id = load_events_from_log_text(log_text)

        # Should have one event with count=3
        assert len(events) == 1
        assert events[0].reason == "calendar_not_open"
        assert events[0].evidence.get("count") == 3

    def test_extract_run_id_from_log(self):
        """Should extract run_id from log when present."""
        log_text = """
        2026-02-21T14:30:45 scenario.span run_id=20260220_173410_417177
        scenario.step reason=calendar_not_open
        """
        events, run_id = load_events_from_log_text(log_text)

        assert run_id == "20260220_173410_417177"

    def test_run_id_override_parameter(self):
        """Should use provided run_id if log contains no run_id."""
        log_text = "scenario.step reason=calendar_not_open"
        events, run_id = load_events_from_log_text(log_text, run_id="override_id")

        assert run_id == "override_id"

    def test_empty_log_text(self):
        """Should handle empty log gracefully."""
        log_text = ""
        events, run_id = load_events_from_log_text(log_text)

        assert len(events) == 0
        assert run_id is None

    def test_mixed_patterns_in_log(self):
        """Should handle both reason= and error= patterns."""
        log_text = """
        scenario.date_fill reason=calendar_not_open
        scenario.google error=timeout_error
        scenario.step reason=selector_not_found
        scenario.step error=month_nav_exhausted
        """
        events, run_id = load_events_from_log_text(log_text)

        reason_codes = {e.reason for e in events}
        assert reason_codes == {
            "calendar_not_open",
            "timeout_error",
            "selector_not_found",
            "month_nav_exhausted",
        }


class TestIntegration:
    """Integration tests for triage workflow."""

    def test_full_workflow_parse_aggregate_format(self):
        """Should handle full workflow: parse -> aggregate -> format."""
        with TemporaryDirectory() as tmpdir:
            # Create test error files
            error_dir = Path(tmpdir)

            for i in range(3):
                json_file = error_dir / f"error_{i}.json"
                json_file.write_text(
                    json.dumps(
                        {
                            "timestamp": (datetime.now() - timedelta(hours=1)).isoformat(),
                            "reason": "calendar_not_open",
                            "evidence": {},
                        }
                    )
                )

            # Parse all files
            events = []
            for json_file in error_dir.glob("*.json"):
                event = parse_error_json_file(json_file)
                if event:
                    events.append(event)

            # Aggregate
            by_reason = aggregate_by_reason(events)

            # Format
            text_output = format_human_report(by_reason)
            json_output = format_json_report(by_reason)

            # Verify
            assert len(events) == 3
            assert "calendar_dialog_not_found" in by_reason
            assert "calendar_dialog_not_found" in text_output
            assert "calendar_not_open" in text_output
            data = json.loads(json_output)
            assert data["total_events"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
