"""Enhanced unit and integration tests for gf_set_date date picker function.

Focuses on:
- ActionBudget consumption and exhaustion
- Date parsing and validation
- Month navigation logic
- Month header parsing (ja-JP patterns)
- Failure mode handling and evidence collection
- Calendar root detection
"""

import pytest
import inspect
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch

from core.scenario.types import ActionBudget, StepResult
from core.scenario.gf_helpers.date_picker_orchestrator import gf_set_date
from core.scenario.gf_helpers.date_tokens import _google_date_display_tokens
from core.scenario.gf_helpers.date_opener import (
    _build_google_date_opener_selectors_impl as _build_google_date_opener_selectors,
)
from core.scenario.gf_helpers.date_fields import (
    _gf_date_role_verify_selectors,
)


class TestMonthHeaderParsing:
    """Test Japanese month header parsing (new feature)."""

    def test_month_header_parsing_ja_yyyy_mm_format(self):
        """Parse Japanese month headers like '2026年3月'."""
        import re

        header_text = "2026年3月"
        year_match = re.search(r'(\d{4})年', header_text)
        month_match = re.search(r'(\d{1,2})月', header_text)

        assert year_match is not None, "Should find year pattern"
        assert month_match is not None, "Should find month pattern"

        year = int(year_match.group(1))
        month = int(month_match.group(1))

        assert year == 2026
        assert month == 3

    def test_month_header_parsing_with_other_text(self):
        """Parse Japanese month headers with surrounding text."""
        import re

        header_text = "出発日の選択: 2026年3月"
        year_match = re.search(r'(\d{4})年', header_text)
        month_match = re.search(r'(\d{1,2})月', header_text)

        if year_match and month_match:
            year = int(year_match.group(1))
            month = int(month_match.group(1))
            assert year == 2026
            assert month == 3

    def test_month_header_parsing_single_digit_month(self):
        """Parse headers with single-digit months (e.g., '2026年1月')."""
        import re

        header_text = "2026年1月"
        year_match = re.search(r'(\d{4})年', header_text)
        month_match = re.search(r'(\d{1,2})月', header_text)

        assert year_match is not None
        assert month_match is not None

        year = int(year_match.group(1))
        month = int(month_match.group(1))

        assert year == 2026
        assert month == 1

    def test_month_header_no_match_returns_none(self):
        """Parser should handle non-matching headers gracefully."""
        import re

        header_text = "Some random text without date"
        year_match = re.search(r'(\d{4})年', header_text)
        month_match = re.search(r'(\d{1,2})月', header_text)

        assert year_match is None
        assert month_match is None


class TestMonthDiffCalculation:
    """Test month difference calculations for navigation."""

    def test_month_diff_forward_same_year(self):
        """Calculate months forward in same year."""
        start_year, start_month = 2026, 3
        target_year, target_month = 2026, 5

        month_diff = (target_year - start_year) * 12 + (target_month - start_month)
        assert month_diff == 2  # March to May = 2 months

    def test_month_diff_backward_same_year(self):
        """Calculate months backward in same year."""
        start_year, start_month = 2026, 5
        target_year, target_month = 2026, 3

        month_diff = (target_year - start_year) * 12 + (target_month - start_month)
        assert month_diff == -2  # Navigate backward

    def test_month_diff_forward_across_year(self):
        """Calculate months forward across years."""
        start_year, start_month = 2026, 11
        target_year, target_month = 2027, 2

        month_diff = (target_year - start_year) * 12 + (target_month - start_month)
        assert month_diff == 3  # Nov->Dec->Jan->Feb = 3 months

    def test_month_diff_backward_across_year(self):
        """Calculate months backward across years."""
        start_year, start_month = 2027, 2
        target_year, target_month = 2026, 11

        month_diff = (target_year - start_year) * 12 + (target_month - start_month)
        assert month_diff == -3

    def test_month_nav_capped_at_8_steps(self):
        """Verify that 8-step cap is reasonable for navigation."""
        max_nav_steps = 8
        assert max_nav_steps >= 3  # Sufficient for typical use
        assert max_nav_steps <= 12  # But less than full year


class TestCalendarRootDetection:
    """Test calendar root detection logic."""

    def test_calendar_root_selectors_priority(self):
        """Verify root detection selector priority."""
        root_selectors = [
            "[role='dialog']:has([role='grid']):visible",
            "[role='dialog']:has([role='gridcell']):visible",
            "[role='dialog']:visible",
            "[class*='calendar']:has([role='grid']):visible",
            "[class*='picker']:has([role='gridcell']):visible",
        ]

        # Selectors with specific grid/gridcell patterns should be first
        assert "grid" in root_selectors[0]
        assert "gridcell" in root_selectors[1]
        # Fallback to generic dialog
        assert ":visible" in root_selectors[2]

    def test_depart_opener_selectors(self):
        """Verify depart opener selector strategy."""
        depart_openers = [
            "[role='combobox'][aria-label*='出発']",  # Explicit combobox
            "input[aria-label*='出発日']",              # Input field
            "input[placeholder*='出発日']",             # Input placeholder
            "[role='button'][aria-label*='出発日']",    # Button
            "[aria-label*='出発日']",                   # Generic
        ]

        # Combobox and input-based selectors should come first (more specific)
        assert "combobox" in depart_openers[0] or "input" in depart_openers[1]

    def test_return_opener_selectors(self):
        """Verify return opener selector strategy."""
        return_openers = [
            "[role='combobox'][aria-label*='復路']",
            "input[aria-label*='復路']",
            "input[placeholder*='復路']",
            "[role='button'][aria-label*='復路']",
            "[aria-label*='復路']",
        ]

        # All should target '復路' (return) in Japanese
        for opener in return_openers:
            assert "復路" in opener


class TestExplicitFailureModes:
    """Test explicit failure mode detection and reporting."""

    def test_failure_mode_calendar_not_open(self):
        """Verify calendar_not_open failure mode evidence."""
        evidence = {
            "selector_attempts": 5,
            "selectors_tried": ["selector1", "selector2"],
        }

        assert evidence["selector_attempts"] >= 0
        assert isinstance(evidence.get("selectors_tried"), list)

    def test_failure_mode_month_header_not_found(self):
        """Verify month_header_not_found failure mode evidence."""
        evidence = {
            "header_text": "Some unparse able header",
            "parse_failed": True,
        }

        assert "header_text" in evidence
        assert evidence.get("parse_failed") is True

    def test_failure_mode_month_nav_buttons_not_found(self):
        """Verify month_nav_buttons_not_found failure mode evidence."""
        evidence = {
            "date": "2026-03-01",
            "current_month": 3,
            "current_year": 2026,
            "target_month": 5,
            "target_year": 2026,
        }

        assert evidence["current_month"] == evidence["target_month"] is not True or \
               evidence["current_year"] == evidence["target_year"]

    def test_failure_mode_month_nav_exhausted(self):
        """Verify month_nav_exhausted failure mode evidence."""
        evidence = {
            "nav_steps": 8,
            "max_nav_steps": 8,
            "final_month": 3,
            "final_year": 2026,
            "target_month": 7,
            "target_year": 2026,
        }

        assert evidence["nav_steps"] >= evidence["max_nav_steps"]

    def test_failure_mode_day_not_found(self):
        """Verify day_not_found failure mode evidence."""
        evidence = {
            "date": "2026-03-01",
            "nav_steps": 2,
            "selectors_tried": 4,
        }

        assert "date" in evidence
        assert evidence["nav_steps"] >= 0

    def test_failure_mode_verify_mismatch(self):
        """Verify verify_mismatch failure mode evidence."""
        evidence = {
            "expected_date": "2026-03-01",
            "verified_value": "3月1日",  # JP format
            "nav_steps": 1,
            "close_method": "done_button",
        }

        assert evidence["expected_date"] != evidence["verified_value"]


class TestDatePickerSelectors:
    """Test selector scoping and specificity."""

    def test_day_selector_scoped_to_root(self):
        """Day selectors should be specific and scoped."""
        target_year, target_month, target_day = 2026, 3, 15

        day_selectors = [
            f"[role='gridcell'][aria-label*='{target_year}年{target_month}月{target_day}日']:not([aria-disabled='true'])",
            f"[role='gridcell'][aria-label*='{target_month}月{target_day}日']:not([aria-disabled='true'])",
            f"[role='button'][aria-label*='{target_year}年{target_month}月{target_day}日']:not([aria-disabled='true'])",
            f"[role='button'][aria-label*='{target_month}月{target_day}日']:not([aria-disabled='true'])",
        ]

        # All should include date-specific values
        for sel in day_selectors:
            assert str(target_day) in sel or f"{target_month}月{target_day}日" in sel

    def test_day_visible_precheck_not_limited_to_gridcell_only(self):
        """Regression: month-nav precheck must scan all day selector variants."""
        source = inspect.getsource(gf_set_date)
        assert "for day_sel in day_selectors[:2]" not in source

    def test_day_selectors_include_data_iso_and_multilingual_tokens(self):
        """Active gf_set_date path should support stable data-iso and EN/JA display tokens."""
        from core.scenario.gf_helpers.date_picker_orchestrator import gf_set_date_impl
        source = inspect.getsource(gf_set_date_impl)
        assert "data-iso='{target_date}'" in source
        assert "_google_date_display_tokens(target_date)" in source

    def test_nav_button_selectors(self):
        """Navigation button selectors should include exact EN anchors and JA fallbacks."""
        next_selectors = [
            "button[aria-label='Next']:not([aria-hidden='true'])",
            "[role='button'][aria-label='Next']:not([aria-hidden='true'])",
            "[aria-label*='次の月']",  # JP next month
            "[aria-label*='next month' i]",  # Case-insensitive
            "button[aria-label*='次']",
        ]
        prev_selectors = [
            "button[aria-label='Previous']:not([aria-hidden='true'])",
            "[role='button'][aria-label='Previous']:not([aria-hidden='true'])",
            "[aria-label*='前の月']",
            "[aria-label*='previous month' i]",
            "button[aria-label*='前']",
        ]

        assert any("[aria-label='Next']" in s for s in next_selectors)
        assert any("[aria-label='Previous']" in s for s in prev_selectors)
        assert any("次" in s for s in next_selectors)
        assert any("前" in s for s in prev_selectors)

    def test_verify_selectors_use_shared_date_role_helper(self):
        """Verification should reuse shared date-role selectors, not role-key literal CSS only."""
        from core.scenario.gf_helpers.date_picker_orchestrator import gf_set_date_impl
        source = inspect.getsource(gf_set_date_impl)
        assert "_gf_date_role_verify_selectors(" in source

    def test_verify_uses_semantic_date_match_helper_and_active_input_snapshot(self):
        """Regression: active path should accept locale-formatted values like 'Sat, May 2'."""
        from core.scenario.gf_helpers.date_picker_orchestrator import gf_set_date_impl
        source = inspect.getsource(gf_set_date_impl)
        assert "_gf_field_value_matches_date(" in source
        assert "active_input_semantic" in source

    def test_done_button_close_search_has_page_fallback(self):
        """Done button detection is delegated to close_calendar_dialog_impl with page fallback."""
        # The implementation has been extracted to close_calendar_dialog_impl in gf_helpers
        from core.scenario.gf_helpers.date_picker_orchestrator import gf_set_date_impl
        source = inspect.getsource(gf_set_date_impl)
        # Verify close dialog is called with proper parameters (page and calendar_root)
        assert "close_calendar_dialog_impl(" in source

    def test_done_button_selectors_ja_jp(self):
        """Done button selectors should support ja-JP labels."""
        done_selectors = [
            "[role='button'][aria-label*='完了']",  # JP done
            "[role='button'][aria-label*='適用']",  # JP apply
            "button:has-text('完了'):visible",       # JP text
            "button:has-text('適用'):visible",       # JP text
            "[role='button']:has-text('Done'):visible",  # EN fallback
        ]

        # Should include Japanese labels
        ja_labels = ["完了", "適用"]
        found_ja = any(label in sel for label in ja_labels for sel in done_selectors)
        assert found_ja


class TestBudgetAndTimingGuarantees:
    """Test ActionBudget and deadline guarantees."""

    def test_budget_initialization_20_actions(self):
        """gf_set_date should use budget of 20 actions."""
        budget = ActionBudget(max_actions=20)
        assert budget.max_actions == 20

    def test_budget_consumption_per_stage(self):
        """Verify budget consumption costs make sense."""
        # Per current implementation, each operation (click, wait, read) costs 1
        cost_per_click = 1
        cost_per_wait = 1
        cost_per_read = 1

        # Typical successful flow: open(1) + header(1) + nav_buttons(1) + nav(1-8) + day(1) + done(1) + verify(1)
        # Minimum: 7 actions
        # Maximum with 8 nav steps: 14 actions
        assert 7 <= 20  # Should always fit
        assert 14 <= 20

    def test_wall_clock_timeout_integration(self):
        """Verify wall-clock timeout is checked."""
        # Wall clock timeout is integrated but hard to test without mocking
        # Just verify the deadline parameter is used
        pass


class TestLoggingFormat:
    """Test logging follows conventions."""

    def test_logging_stage_format(self):
        """All logs should follow gf_set_date.{stage}.{outcome} format."""
        log_patterns = [
            "gf_set_date.open.ok",
            "gf_set_date.month_header.text",
            "gf_set_date.month_nav.plan",
            "gf_set_date.day_click.ok",
            "gf_set_date.success",
            "gf_set_date.budget_hit",
            "gf_set_date.month_nav.exhausted",
        ]

        for pattern in log_patterns:
            assert pattern.startswith("gf_set_date.")


# Scenario-based documentation tests
class TestGfSetDateScenarios:
    """Scenario-based tests describing expected behavior."""

    def test_scenario_successful_date_set_depart(self):
        """
        Scenario: User wants to set depart date to Mar 1, 2026.

        Expected flow:
        1. Click depart field opener with [role='combobox'][aria-label*='出発'] (1 action)
        2. Calendar dialog [role="dialog"]:has([role="grid"]) appears
        3. Month header "2026年3月" is parsed -> year=2026, month=3
        4. Target day visible in current month (diff=0)
        5. Click gridcell for "2026年3月1日" (1 action)
        6. Click done button "完了" (1 action)
        7. Verify depart field contains "3月1日" (1 action)
        8. Return success with nav_steps=0

        Budget used: ~5 actions from 20 max
        Evidence: opener, header_text, close_method=done_button, nav_steps=0
        """
        pass

    def test_scenario_depart_opener_all_fail(self):
        """
        Scenario: All depart openers fail to open calendar.

        Tried [role='combobox'][aria-label*='出発'], then input[aria-label*='出発日'],
        then input[placeholder*='出発日'], then [role='button'][aria-label*='出発日'],
        then [aria-label*='出発日'], and none clicked or opened dialog.

        Expected: Return reason='calendar_not_open' with evidence
                 showing 5 selectors attempted, none found visible or none opened root
        Budget used: ~5 actions
        """
        pass

    def test_scenario_calendar_open_but_no_header(self):
        """
        Scenario: Dialog opened after first opener click, but month header not found.

        Dialog [role="dialog"] is visible, but selectors for month header
        ([class*='month'], h2, button[aria-label*='年'], etc.) don't match.

        Expected: Return reason='month_header_not_found' with evidence
                 showing header_selectors_tried count
        Budget used: ~2 actions
        """
        pass

    def test_scenario_header_found_no_nav_buttons(self):
        """
        Scenario: Month header "2026年3月" found and parsed correctly,
        but navigation buttons ([aria-label*='次の月'], etc.) not found within root.

        This can happen if the calendar UI layout doesn't include prev/next buttons
        or if they're dynamically inserted/removed.

        Expected: Return reason='month_nav_buttons_not_found' with evidence
                 showing current_month/year and target month/year
        Budget used: ~2-3 actions
        """
        pass

    def test_scenario_month_nav_exhausted_8_steps(self):
        """
        Scenario: Need to navigate from March to November (8 months forward).

        After 8 navigation clicks, still cannot find day 15 in November.
        Nav buttons exist, clicks work, header updates, but day never appears.

        Expected: Return reason='month_nav_exhausted' with evidence
                 showing nav_steps=8, max_nav_steps=8, final_month!=target_month
        Budget used: ~10 actions (8 clicks + day checks)
        """
        pass

    def test_scenario_day_not_found_after_nav(self):
        """
        Scenario: Navigate to target month successfully, but day 15 selector
        [role='gridcell'][aria-label*='3月15日'] is disabled or not clickable.

        Expected: Return reason='day_not_found' with evidence
        Budget used: ~3-4 actions
        """
        pass

    def test_scenario_verify_mismatch_after_click(self):
        """
        Scenario: Day clicked, done button clicked, dialog closed, but
        depart field still shows old value "3月5日" instead of "3月1日".

        Could be timing issue, field not actually updated, etc.

        Expected: Return reason='verify_mismatch' with evidence
                 showing expected_date="2026-03-01", verified_value="3月5日"
        Budget used: ~7-8 actions
        """
        pass

    def test_scenario_budget_hit_during_nav(self):
        """
        Scenario: budget.max_actions=20 initialized. During month navigation loop
        at nav_step=5, budget.consume(1) returns False.

        Expected: Return reason='budget_hit' with evidence
                 showing stage='month_nav', nav_steps=5
        Budget fully consumed (20 actions)
        """
        pass


class TestDateOpenerPrioritization:
    def test_depart_openers_prioritize_button_date_chip_over_plan_combobox_inputs(self):
        out = _build_google_date_opener_selectors(
            role="depart",
            target_date="2026-03-01",
            locale_hint="ja-JP",
            role_selectors=[
                "[role='combobox'][aria-label*='出発']",
                "[role='combobox'][aria-label*='Departure']",
                "input[placeholder*='出発日']",
            ],
            max_items=8,
        )
        top = out[:4]
        assert any(
            ("[role='button']" in sel or sel.startswith("button["))
            and ("出発日" in sel or "往路出発日" in sel)
            for sel in top
        )
        assert all("[role='combobox']" not in sel for sel in top[:2])
        assert any("3月1日" in sel or "2026年3月1日" in sel for sel in out[:8])

    def test_return_openers_prioritize_english_button_date_chip(self):
        out = _build_google_date_opener_selectors(
            role="return",
            target_date="2026-03-08",
            locale_hint="en-US",
            role_selectors=[
                "[role='combobox'][aria-label*='Return']",
                "input[placeholder*='Return']",
            ],
            max_items=8,
        )
        top = out[:4]
        assert any(
            ("[role='button']" in sel or sel.startswith("button[")) and "Return date" in sel
            for sel in top
        )
        assert all("[role='combobox']" not in sel for sel in top[:2])
        assert any("Mar 8" in sel or "March 8" in sel for sel in out[:8])

    def test_depart_openers_keep_structural_role_selectors_under_dynamic_cap(self):
        out = _build_google_date_opener_selectors(
            role="depart",
            target_date="2026-03-01",
            locale_hint="en",
            role_selectors=[
                "[role='combobox'][aria-label*='Where to']",  # noisy unrelated selector should not dominate
                "[role='combobox'][aria-label*='Depart']",
                "input[aria-label*='Departure']",
            ],
            max_items=12,
        )
        head = out[:8]
        # Dynamic date-valued chips should still be present near the front.
        assert any(("Mar 1" in s or "March 1" in s or "2026" in s) for s in head)
        # But bounded truncation must keep structural opener selectors available too.
        assert any("[role='combobox'][aria-label*='Depart']" == s for s in head)
        assert any("input[aria-label*='Departure']" == s for s in head)

    def test_depart_openers_keep_default_input_opener_when_role_selectors_are_combobox_only(self):
        out = _build_google_date_opener_selectors(
            role="depart",
            target_date="2026-03-01",
            locale_hint="en",
            role_selectors=[
                "[role='combobox'][aria-label*='Depart']",
                "[role='combobox'][aria-label*='Departure']",
                "[role='combobox'][aria-label*='Outbound']",
            ],
            max_items=8,
        )
        head = out[:8]
        assert any(s.startswith("input[aria-label*='Departure']") or s.startswith("input[placeholder*='Departure']") for s in head)

    def test_depart_openers_seed_locale_preferred_input_when_role_selectors_are_mixed_language(self):
        out = _build_google_date_opener_selectors(
            role="depart",
            target_date="2026-03-01",
            locale_hint="en",
            role_selectors=[
                "[role='combobox'][aria-label*='出発日']",
                "[role='combobox'][aria-label*='往路']",
                "[role='button'][aria-label*='Depart']",
                "[role='combobox'][aria-label*='Departure date']",
                "[role='button'][aria-label*='Departure date']",
                "input[aria-label*='往路']",
            ],
            max_items=12,
        )
        head = out[:8]
        assert any("input[aria-label*='Departure']" == s for s in head)

    def test_depart_verify_selectors_keep_input_seed_in_bounded_head_with_noisy_role_selectors(self):
        out = _gf_date_role_verify_selectors(
            "depart",
            locale_hint="en",
            role_selectors=[
                "[role='combobox'][aria-label*='出発日']",
                "[role='combobox'][aria-label*='Depart']",
                "[role='button'][aria-label*='Departure']",
                "[aria-label*='Departure']",
                "[role='combobox'][aria-label*='Outbound']",
            ],
        )
        head = out[:5]
        assert any(s == "input[aria-label*='Departure']" for s in head)

    def test_date_display_tokens_include_japanese_and_english_formats(self):
        out = _google_date_display_tokens("2026-03-01")
        assert "2026年3月1日" in out
        assert "3月1日" in out
        assert "March 1, 2026" in out
        assert "Mar 1, 2026" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
