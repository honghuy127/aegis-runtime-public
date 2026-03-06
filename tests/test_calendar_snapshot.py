"""Unit tests for calendar snapshot capture module.

Tests snapshot creation, HTML extraction, truncation, and I/O operations.
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import pytest

from core.scenario.calendar_snapshot import (
    CalendarSnapshot,
    MonthParseResult,
    SelectorAttempt,
    extract_dialog_fragment,
    truncate_html,
    write_calendar_snapshot,
)


class TestTruncateHtml:
    """Test HTML truncation with safe boundary detection."""

    def test_no_truncation_needed(self):
        """Short HTML should pass through unchanged."""
        html = "<div>Hello</div>"
        truncated, was_truncated = truncate_html(html, max_chars=100)
        assert truncated == html
        assert was_truncated is False

    def test_truncation_with_max_chars(self):
        """Long HTML should be truncated and marked."""
        html = "<div>" + "x" * 10000 + "</div>"
        truncated, was_truncated = truncate_html(html, max_chars=1000)
        assert was_truncated is True
        assert len(truncated) <= 1050  # 1000 + "...TRUNCATED..." buffer
        assert "...TRUNCATED..." in truncated

    def test_truncation_at_safe_boundary(self):
        """Should try to truncate at tag boundary."""
        html = "<div>Content</div><span>More</span><footer>Food</footer>"
        truncated, was_truncated = truncate_html(html, max_chars=30)
        assert was_truncated is True
        # Should end near a tag boundary (or with TRUNCATED marker)
        assert "</div>" in truncated or "...TRUNCATED..." in truncated

    def test_empty_html(self):
        """Empty string should return empty."""
        truncated, was_truncated = truncate_html("", max_chars=100)
        assert truncated == ""
        assert was_truncated is False


class TestExtractDialogFragment:
    """Test dialog/calendar fragment extraction from HTML."""

    def test_extract_dialog_role(self):
        """Should extract role='dialog' container."""
        html = '<body><div role="dialog" class="calendar"><calendar-grid /></div></body>'
        fragment, source = extract_dialog_fragment(html)
        assert "role" in fragment.lower() or "dialog" in fragment.lower()
        assert source in ["dialog", "head_fallback"]

    def test_extract_grid_role(self):
        """Should extract role='grid' for calendar grid."""
        html = '<body><div><div role="grid" class="day-grid"><span>1</span></div></div></body>'
        fragment, source = extract_dialog_fragment(html)
        assert "grid" in fragment.lower()
        assert source in ["grid", "head_fallback"]

    def test_extract_with_month_label(self):
        """Should extract section with aria-label containing month."""
        html = '<body><div aria-label="March 2026" role="heading">March 2026</div></body>'
        fragment, source = extract_dialog_fragment(html)
        assert len(fragment) > 0
        assert source in ["month_label", "head_fallback"]

    def test_fallback_head_only(self):
        """Should fall back to head section if dialog not found."""
        html = '<head><title>Test</title></head><body><div>No calendar</div></body>'
        fragment, source = extract_dialog_fragment(html)
        assert len(fragment) > 0
        assert source in ["head_fallback"]

    def test_empty_html_returns_empty(self):
        """Empty HTML should return empty fragment."""
        fragment, source = extract_dialog_fragment("")
        assert source == "empty"

    def test_respects_max_chars(self):
        """Fragment should not exceed max_chars."""
        html = "<div>" + "x" * 100000 + "</div>"
        fragment, source = extract_dialog_fragment(html, max_chars=5000)
        assert len(fragment) <= 5500  # Allow some margin


class TestSelectorAttempt:
    """Test SelectorAttempt dataclass."""

    def test_selector_attempt_creation(self):
        """Should create attempt with all fields."""
        attempt = SelectorAttempt(
            step="open_dialog",
            selector="[role='combobox']",
            action="click",
            outcome="ok",
            visible=True,
            enabled=True,
            timeout_ms=100,
        )
        assert attempt.step == "open_dialog"
        assert attempt.outcome == "ok"
        assert attempt.visible is True

    def test_selector_attempt_defaults(self):
        """Unset fields should have defaults."""
        attempt = SelectorAttempt(
            step="click_day",
            selector="[role='gridcell']",
            action="click",
            outcome="timeout",
        )
        assert attempt.visible is False
        assert attempt.enabled is False
        assert attempt.timeout_ms is None


class TestMonthParseResult:
    """Test MonthParseResult dataclass."""

    def test_successful_parse(self):
        """Should create result with parsed values."""
        result = MonthParseResult(
            ok=True,
            year=2026,
            month=3,
            source_text="2026年3月",
            parsing_method="parse_month_year",
        )
        assert result.ok is True
        assert result.year == 2026
        assert result.month == 3

    def test_failed_parse(self):
        """Should create failed result."""
        result = MonthParseResult(ok=False, source_text="garbage text")
        assert result.ok is False
        assert result.year is None


class TestCalendarSnapshot:
    """Test CalendarSnapshot dataclass and serialization."""

    def test_snapshot_creation(self):
        """Should create snapshot with required fields."""
        snapshot = CalendarSnapshot(
            run_id="20260221_120000_001",
            site="google_flights",
            role="depart",
            locale="ja-JP",
            target_date="2026-03-15",
            strategy_used="direct_input",
            failure_reason_code="calendar_not_open",
            failure_stage="open",
        )
        assert snapshot.run_id == "20260221_120000_001"
        assert snapshot.role == "depart"
        assert snapshot.month_header_texts == []

    def test_snapshot_with_attempts(self):
        """Should accept selector attempts."""
        attempts = [
            SelectorAttempt(
                step="open",
                selector="[role='combobox']",
                action="click",
                outcome="ok",
            ),
        ]
        snapshot = CalendarSnapshot(
            run_id="test_001",
            site="google_flights",
            role="depart",
            locale="en-US",
            target_date="2026-05-01",
            strategy_used="nav_scan_pick",
            failure_reason_code="month_nav_exhausted",
            failure_stage="navigate",
            selector_attempts=attempts,
        )
        assert len(snapshot.selector_attempts) == 1
        assert snapshot.selector_attempts[0].step == "open"


class TestWriteCalendarSnapshot:
    """Test snapshot file I/O."""

    def test_write_json_snapshot(self):
        """Should write snapshot to JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            snapshot = CalendarSnapshot(
                run_id="test_001",
                site="google_flights",
                role="depart",
                locale="ja-JP",
                target_date="2026-03-15",
                strategy_used="direct_input",
                failure_reason_code="date_not_committed",
                failure_stage="verify",
                month_header_texts=["2026年3月"],
                html_fragment="<div>Calendar</div>",
            )

            json_path, md_path = write_calendar_snapshot(snapshot, run_dir, include_md=False)

            assert json_path.exists()
            assert json_path.name.startswith("calendar_snapshot_depart")
            assert md_path is None

            # Verify JSON structure
            with open(json_path) as f:
                data = json.load(f)
            assert data["run_id"] == "test_001"
            assert data["failure_reason_code"] == "date_not_committed"

    def test_write_snapshot_with_md(self):
        """Should write .md file when enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            snapshot = CalendarSnapshot(
                run_id="test_002",
                site="google_flights",
                role="return",
                locale="en-US",
                target_date="2026-04-10",
                strategy_used="nav_scan_pick",
                failure_reason_code="month_nav_exhausted",
                failure_stage="navigate",
                month_header_texts=["April 2026", "May 2026"],
            )

            json_path, md_path = write_calendar_snapshot(snapshot, run_dir, include_md=True)

            assert json_path.exists()
            assert md_path is not None
            assert md_path.exists()
            assert md_path.name.endswith(".md")

            # Verify MD content
            md_content = md_path.read_text()
            assert "Calendar Snapshot" in md_content
            assert "return" in md_content
            assert "month_nav_exhausted" in md_content

    def test_snapshot_bounded_size(self):
        """Snapshot JSON should stay under 200 KB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)

            # Create snapshot with large HTML
            large_html = "<div>" + "x" * 100000 + "</div>"
            snapshot = CalendarSnapshot(
                run_id="test_003",
                site="google_flights",
                role="depart",
                locale="ja-JP",
                target_date="2026-03-15",
                strategy_used="direct_input",
                failure_reason_code="calendar_not_open",
                failure_stage="open",
                html_fragment=large_html,
            )

            json_path, _ = write_calendar_snapshot(snapshot, run_dir)

            # Check file size
            file_size = json_path.stat().st_size
            assert file_size < 200 * 1024, f"Snapshot too large: {file_size} bytes"

    def test_snapshot_with_parse_result(self):
        """Should serialize month parse result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            parse_result = MonthParseResult(
                ok=True,
                year=2026,
                month=3,
                source_text="2026年3月",
                parsing_method="parse_month_year",
            )
            snapshot = CalendarSnapshot(
                run_id="test_004",
                site="google_flights",
                role="depart",
                locale="ja-JP",
                target_date="2026-03-15",
                strategy_used="direct_input",
                failure_reason_code="success",
                failure_stage="verify",
                month_parse=parse_result,
            )

            json_path, _ = write_calendar_snapshot(snapshot, run_dir)

            with open(json_path) as f:
                data = json.load(f)
            assert data["month_parse"]["ok"] is True
            assert data["month_parse"]["year"] == 2026


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_snapshot_roundtrip(self):
        """Snapshot should serialize and deserialize cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)

            # Create complex snapshot
            attempts = [
                SelectorAttempt(
                    step="open",
                    selector="[role='combobox']",
                    action="click",
                    outcome="ok",
                    visible=True,
                    enabled=True,
                    timeout_ms=100,
                ),
                SelectorAttempt(
                    step="detect_month",
                    selector="[role='heading']",
                    action="read",
                    outcome="not_visible",
                ),
            ]
            parse_result = MonthParseResult(
                ok=True,
                year=2026,
                month=3,
                source_text="2026年3月",
                parsing_method="parse_month_year",
            )
            snapshot = CalendarSnapshot(
                run_id="integration_001",
                site="google_flights",
                role="depart",
                locale="ja-JP",
                target_date="2026-03-15",
                strategy_used="nav_scan_pick",
                failure_reason_code="date_not_committed",
                failure_stage="verify",
                month_header_texts=["2026年3月", "2026年4月"],
                month_parse=parse_result,
                selector_attempts=attempts,
                html_fragment="<div>Calendar Fragment</div>",
                html_source="dialog_extracted",
            )

            # Write snapshot
            json_path, md_path = write_calendar_snapshot(
                snapshot, run_dir, include_md=True
            )

            # Read back and verify
            with open(json_path) as f:
                data = json.load(f)

            assert data["run_id"] == "integration_001"
            assert len(data["selector_attempts"]) == 2
            assert data["month_parse"]["year"] == 2026
            assert data["html_source"] == "dialog_extracted"

            # Verify MD exists and has content
            assert md_path.exists()
            md_text = md_path.read_text()
            assert "integration_001" in md_text or "depart" in md_text
            assert "nav_scan_pick" in md_text
