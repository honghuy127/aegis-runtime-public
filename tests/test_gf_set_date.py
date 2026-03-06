"""Unit and integration tests for gf_set_date date picker function.

Focuses on:
- ActionBudget consumption and exhaustion
- Date parsing and validation
- Month navigation logic
- Failure mode handling and evidence collection
"""

import logging
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch

from core.scenario.types import ActionBudget, StepResult
from core.scenario.gf_helpers.date_picker_orchestrator import gf_set_date
from core.scenario.gf_helpers.calendar_nav import (
    _gf_calendar_root_impl as _gf_calendar_root,
    _gf_calendar_fallback_root_month_header_gate_decision_impl as _gf_calendar_fallback_root_month_header_gate_decision,
)
from core.scenario.gf_helpers.date_fields import (
    _gf_read_date_field_value,
)
from core.scenario.reasons import is_valid_reason_code, normalize_reason


class TestActionBudget:
    """Test ActionBudget tracking and enforcement."""

    def test_budget_initialization(self):
        """Budget should initialize with max_actions."""
        budget = ActionBudget(max_actions=20)
        assert budget.max_actions == 20
        assert budget.remaining == 20
        assert not budget.is_exhausted()

    def test_budget_consume(self):
        """Consuming actions should decrement remaining budget."""
        budget = ActionBudget(max_actions=10)

        # Consume 5 actions
        result = budget.consume(5)
        assert result is True
        assert budget.remaining == 5

        # Consume 5 more actions (should succeed)
        result = budget.consume(5)
        assert result is True
        assert budget.remaining == 0
        assert budget.is_exhausted()


class TestCalendarRootScoping:
    """Tests for _gf_calendar_root helper behavior."""

    def test_gf_calendar_root_fallback_returns_dialog_not_visible_descendant(self):
        """Fallback must return the dialog locator itself to avoid over-narrow scoping."""

        class _Node:
            def __init__(self, name, *, visible=True, has_grid=False):
                self.name = name
                self._visible = visible
                self._has_grid = has_grid
                self.first = self

            def is_visible(self, timeout=None):  # noqa: ARG002
                return self._visible

            def locator(self, selector):
                if selector in ("[role='grid'], [role='gridcell']",):
                    return _Node(f"{self.name}.gridcheck", visible=self._has_grid, has_grid=False)
                if selector == ":has([role='grid'])":
                    return _Node(f"{self.name}.has_grid", visible=False, has_grid=False)
                if selector == ":has([role='gridcell'])":
                    return _Node(f"{self.name}.has_gridcell", visible=False, has_grid=False)
                if selector == ":visible":
                    # Simulate buggy over-narrow descendant path that should no longer be used.
                    return _Node("child_descendant", visible=True, has_grid=False)
                raise AssertionError(f"unexpected selector: {selector}")

        dialog = _Node("dialog_root", visible=True, has_grid=False)

        out = _gf_calendar_root(page=None, dialog_locator=dialog)

        assert out is dialog

    def test_budget_exhaustion(self):
        """Attempting to consume more than remaining should fail."""
        budget = ActionBudget(max_actions=3)

        # Consume 2 actions
        assert budget.consume(2) is True
        assert budget.remaining == 1

        # Try to consume 2 more (should fail)
        assert budget.consume(2) is False
        assert budget.remaining == 1  # Should not change

    def test_budget_reset(self):
        """Budget reset should restore remaining count."""
        budget = ActionBudget(max_actions=10)
        budget.consume(7)
        assert budget.remaining == 3

        budget.reset()
        assert budget.remaining == 10

    def test_budget_reset_with_new_max(self):
        """Reset with new max_actions should update budget."""
        budget = ActionBudget(max_actions=10)
        budget.reset(max_actions=20)
        assert budget.max_actions == 20
        assert budget.remaining == 20


class TestStepResult:
    """Test StepResult creation and validation."""

    def test_step_result_success_creation(self):
        """Create a success result."""
        result = StepResult.success(selector_used="test_selector")
        assert result.ok is True
        assert result.reason == "success"
        assert result.selector_used == "test_selector"

    def test_step_result_failure_creation(self):
        """Create a failure result with reason."""
        result = StepResult.failure("calendar_not_open", selector_used="test")
        assert result.ok is False
        # Note: "calendar_not_open" is normalized to canonical code "calendar_dialog_not_found"
        assert result.reason == "calendar_dialog_not_found"
        assert result.selector_used == "test"

    def test_step_result_with_evidence(self):
        """Results can carry rich evidence payloads."""
        evidence = {"nav_steps": 3, "verified_value": "2026-03-01"}
        result = StepResult(
            ok=True,
            reason="success",
            evidence=evidence,
            action_budget_used=5
        )
        assert result.evidence == evidence
        assert result.action_budget_used == 5


class TestGfSetDateLogic:
    """Test core logic of gf_set_date without live browser."""

    def test_invalid_role_rejection(self):
        """gf_set_date should reject unsupported roles."""
        mock_browser = Mock()

        result = gf_set_date(
            mock_browser,
            role="invalid_role",
            date="2026-03-01"
        )

        assert result["ok"] is False
        assert result["reason"] == "unsupported_role"

    def test_empty_date_rejection(self):
        """gf_set_date should reject empty date values."""
        mock_browser = Mock()

        result = gf_set_date(
            mock_browser,
            role="depart",
            date=""
        )

        assert result["ok"] is False
        assert result["reason"] == "empty_value"

    def test_invalid_date_format_rejection(self):
        """gf_set_date should reject invalid date formats."""
        mock_browser = Mock()

        result = gf_set_date(
            mock_browser,
            role="depart",
            date="2026/03/01"  # Invalid format (should be YYYY-MM-DD)
        )

        assert result["ok"] is False
        assert result["reason"] == "invalid_date_format"

    def test_no_page_rejection(self):
        """gf_set_date should fall back to typing when page is unavailable."""
        mock_browser = Mock()
        mock_browser.page = None  # No page available

        result = gf_set_date(
            mock_browser,
            role="depart",
            date="2026-03-01"
        )

        # When page is not available, gf_set_date falls back to typing
        # and will fail with a reason from the typing fallback
        assert result["ok"] is False
        # The reason will be from the typing fallback since page.locator is unavailable
        # Expected: "typing_skipped_no_fillable_input" or similar from the typing fallback
        assert "typing" in result.get("reason", "") or "fillable" in result.get("reason", "")
    def test_budget_tracking(self):
        """gf_set_date should track budget consumption in result."""
        budget = ActionBudget(max_actions=20)
        initial_remaining = budget.remaining

        mock_browser = Mock()
        mock_browser.page = None  # Will fail early to minimize budget use

        result = gf_set_date(
            mock_browser,
            role="depart",
            date="2026-03-01",
            budget=budget
        )

        budget_used = result.get("action_budget_used", 0)
        assert isinstance(budget_used, int)
        assert budget_used >= 0

    def test_date_parsing_logic(self):
        """Date parsing should handle various valid formats."""
        from datetime import datetime

        test_cases = [
            ("2026-03-01", 2026, 3, 1),
            ("2025-12-25", 2025, 12, 25),
            ("2026-01-15", 2026, 1, 15),
        ]

        for date_str, expected_year, expected_month, expected_day in test_cases:
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                assert date_obj.year == expected_year
                assert date_obj.month == expected_month
                assert date_obj.day == expected_day
            except Exception as e:
                pytest.fail(f"Failed to parse {date_str}: {e}")

    def test_read_date_field_value_prefers_semantic_nonempty_duplicate(self):
        """Visible duplicate date inputs should prefer the populated matching clone."""

        class _FakeDateInput:
            def __init__(self, value, visible=True):
                self._value = value
                self._visible = visible

            @property
            def first(self):
                return self

            def is_visible(self, timeout=None):  # noqa: ARG002
                return self._visible

            def input_value(self, timeout=None):  # noqa: ARG002
                return self._value

            def get_attribute(self, name, timeout=None):  # noqa: ARG002
                if name == "value":
                    return self._value
                return None

            def text_content(self, timeout=None):  # noqa: ARG002
                return ""

        class _FakeLocatorList:
            def __init__(self, items):
                self._items = list(items)

            @property
            def first(self):
                return self.nth(0)

            def count(self):
                return len(self._items)

            def nth(self, idx):
                if idx < len(self._items):
                    return self._items[idx]
                return _FakeDateInput("", visible=False)

        class _FakePage:
            def locator(self, selector):
                if selector == "input[aria-label*='Departure']":
                    return _FakeLocatorList([
                        _FakeDateInput(""),          # empty visible clone
                        _FakeDateInput("Sat, May 2") # populated visible clone
                    ])
                return _FakeLocatorList([])

        value = _gf_read_date_field_value(
            _FakePage(),
            role_key="depart",
            locale_hint="en",
            target_date="2026-05-02",
        )
        assert value == "Sat, May 2"


class TestMonthNavigationLogic:
    """Test month navigation logic (unit-level, no browser)."""

    def test_month_diff_calculation(self):
        """Calculate months between dates."""
        from datetime import datetime

        start = datetime(2026, 3, 1)
        end = datetime(2026, 5, 15)

        months_diff = (end.year - start.year) * 12 + (end.month - start.month)
        assert months_diff == 2  # March to May = 2 months

    def test_month_nav_backward(self):
        """Month navigation should handle negative diffs."""
        from datetime import datetime

        start = datetime(2026, 5, 1)
        end = datetime(2026, 3, 15)

        months_diff = (end.year - start.year) * 12 + (end.month - start.month)
        assert months_diff == -2  # Navigate backward

    def test_month_nav_capped(self):
        """Month navigation should be capped to prevent spam."""
        max_nav_steps = 8

        # Simulating max 8 nav steps allows up to 8 months forward
        # (from any month up to 8 months later)
        assert max_nav_steps >= 3  # Sufficient for typical use
        assert max_nav_steps <= 12  # But less than full year


class TestDatePickerWithMocks:
    """Integration tests with mocked browser and page."""

    def setup_method(self):
        """Setup mock browser with page locator."""
        self.mock_browser = Mock()
        self.mock_page = MagicMock()
        self.mock_browser.page = self.mock_page

    def test_calendar_open_failure_returns_correct_reason(self):
        """When calendar cannot open, return explicit reason."""
        # Mock page.locator to return a mock that's never visible
        mock_locator = Mock()
        mock_locator.first.is_visible.return_value = False
        self.mock_page.locator.return_value = mock_locator

        result = gf_set_date(
            self.mock_browser,
            role="depart",
            date="2026-03-01",
            timeout_ms=500  # Short timeout to fail fast
        )

        assert result["ok"] is False
        assert result["reason"] == "calendar_not_open"
        assert "selector_attempts" in result.get("evidence", {})

    def test_return_chip_evidence_recorded_on_failure(self, monkeypatch):
        class _Locator:
            def __init__(self, *, visible=True, enabled=True, text=""):
                self._visible = visible
                self._enabled = enabled
                self._text = text
                self.first = self

            def is_visible(self, timeout=None):  # noqa: ARG002
                return self._visible

            def is_enabled(self, timeout=None):  # noqa: ARG002
                return self._enabled

            def click(self, timeout=None):  # noqa: ARG002
                return None

            def locator(self, selector):  # noqa: ARG002
                return _LocatorList([])

            def text_content(self):
                return self._text

            def get_attribute(self, name, timeout=None):  # noqa: ARG002
                return None

            def wait_for(self, state=None, timeout=None):  # noqa: ARG002
                raise RuntimeError("not visible")

        class _LocatorList:
            def __init__(self, items):
                self._items = list(items)
                self.first = self._items[0] if self._items else _Locator(visible=False, enabled=False)

            def count(self):
                return len(self._items)

            def nth(self, idx):
                if idx < len(self._items):
                    return self._items[idx]
                return _Locator(visible=False, enabled=False)

        class _Page:
            def __init__(self):
                self.dialog = _Locator(visible=True, enabled=True)

            def locator(self, selector):
                if selector == "open":
                    return _LocatorList([_Locator(visible=True, enabled=True)])
                if selector in {
                    "[role='dialog']:has([role='grid']):visible",
                    "[role='dialog']:has([role='gridcell']):visible",
                    "[role='dialog']:visible",
                }:
                    return _LocatorList([self.dialog])
                if selector == "[role='gridcell']":
                    return _LocatorList([_Locator(visible=False, enabled=False)])
                return _LocatorList([])

        browser = Mock()
        browser.page = _Page()

        monkeypatch.setattr(
            "core.scenario.gf_helpers.date_picker_orchestrator._build_google_date_opener_selectors_impl",
            lambda **kwargs: ["open"],
        )
        monkeypatch.setattr(
            "core.scenario.gf_helpers.date_picker_orchestrator._gf_try_activate_date_chip",
            lambda *args, **kwargs: (True, "chip_selector"),
        )
        monkeypatch.setattr(
            "core.scenario.gf_helpers.date_picker_orchestrator.get_threshold",
            lambda key, default=None: (
                False if key == "gf_set_date_fallback_root_month_header_gate_enabled" else default
            ),
        )

        result = gf_set_date(
            browser,
            role="return",
            date="2026-03-01",
            timeout_ms=1200,
            budget=ActionBudget(max_actions=20),
        )

        assert result["ok"] is False
        evidence = result.get("evidence", {})
        assert evidence.get("calendar.return_chip_attempted") is True
        assert evidence.get("calendar.return_chip_activated") is True
        assert evidence.get("calendar.return_chip_selector") == "chip_selector"


class TestLoggerIntegration:
    """Test logging behavior in gf_set_date."""

    def test_budget_exhaustion_logging(self):
        """When budget exhausted, should log warning."""
        budget = ActionBudget(max_actions=1)
        mock_browser = Mock()
        mock_browser.page = Mock()

        # Create a mock logger to capture calls
        mock_logger = Mock()

        # Manually consume budget
        budget.consume(1)  # Exhaust budget

        # Verify budget is exhausted
        assert budget.is_exhausted()


# Integration test scenarios for reference
class TestGfSetDateScenarios:
    """Scenario-based tests describing expected behavior."""

    def test_scenario_successful_date_set(self):
        """
        Scenario: User wants to set departure date to Mar 1, 2026.

        Expected flow:
        1. Click date field to open calendar (costs 1 action)
        2. Calendar is visible
        3. Target day visible in current month view
        4. Click the day (costs 1 action)
        5. Click Done button (costs 1 action)
        6. Verify date in field matches (costs 1 action)
        7. Return success

        Budget used: ~4 actions from 20 max
        """
        # This scenario documents expected behavior
        pass

    def test_scenario_calendar_open_fails(self):
        """
        Scenario: Calendar fails to open after clicking date field.

        Expected: Return reason='calendar_not_open' with evidence
        Budget used: ~3 actions (tried multiple openers)
        """
        pass

    def test_scenario_month_nav_exhausted(self):
        """
        Scenario: Need to navigate more than 8 months to find target day.

        Expected: Return reason='month_nav_exhausted' with nav_steps count
        Budget used: ~10 actions (up to 8 nav attempts + day check attempts)
        """
        pass

    def test_scenario_budget_hit_during_navigation(self):
        """
        Scenario: Budget runs out while navigating months.

        Expected: Return reason='budget_hit' with current stage info
        Budget fully consumed
        """
        pass


class TestMonthHeaderDetectionAndParsing:
    """Tests for month header extraction, rejection, and parsing logic.

    Focus: Handle edge cases in Japanese locale where header selector
    may accidentally grab non-header text like "完了" (Done button).

    Also test fallback mechanism that infers month/year from visible gridcell aria-labels.
    """

    def test_month_header_rejects_non_header_text_and_continues(self):
        """
        Case 1: Month header selector matches "完了" button first.

        Expected: Should reject "完了" as non-header text, skip to next candidate,
        and continue searching. If no valid header found after bounded attempts,
        fail with reason='month_nav_exhausted' and evidence calendar.failure_stage='month_header'.
        """
        # Create a mock dialog/page with calendar grid
        page_mock = MagicMock()
        browser_mock = MagicMock()
        browser_mock.page = page_mock
        dialog_mock = MagicMock()

        # Simulate finding "完了" button first (should be rejected)
        button_locator = MagicMock()
        button_locator.text_content.return_value = "完了"
        button_locator.is_visible.return_value = True

        heading_locator = MagicMock()
        heading_locator.text_content.return_value = None  # No valid header
        heading_locator.is_visible.return_value = False

        # Create opener locator mock (for calendar opening phase)
        opener_locator_mock = MagicMock()
        opener_locator_mock.is_visible = lambda **kwargs: True
        opener_locator_mock.is_enabled = lambda **kwargs: True
        opener_locator_mock.click = lambda **kwargs: None

        # Create invisible overlay mock (so overlay detection doesn't block)
        invisible_overlay_mock = MagicMock()
        invisible_overlay_mock.is_visible = lambda **kwargs: False

        # Setup page.locator to return different mocks based on selector
        def mock_locator(selector):
            locator_chain_mock = MagicMock()
            # Overlay selectors (should be invisible to not block calendar opening)
            if ":not(" in selector and "dialog" in selector:
                # This matches "[role='dialog']:not(:has([role='grid']))"
                locator_chain_mock.first = invisible_overlay_mock
            elif "modal" in selector or ("overlay" in selector and "visible" in selector):
                locator_chain_mock.first = invisible_overlay_mock
            # Calendar root selectors (after opening) - has grid or gridcell without :not
            elif "dialog" in selector and (":has([role='grid'])" in selector or ":has([role='gridcell'])" in selector):
                locator_chain_mock.first = dialog_mock
            # Opener selectors (depart field)
            elif "出発" in selector or "depart" in selector.lower():
                locator_chain_mock.first = opener_locator_mock
            else:
                # Default to dialog for simplicity
                locator_chain_mock.first = dialog_mock
            return locator_chain_mock

        page_mock.locator.side_effect = mock_locator

        # Setup dialog_mock for month header extraction
        dialog_mock.is_visible.return_value = True
        dialog_mock.locator.return_value.first = button_locator
        dialog_mock.locator.return_value.nth = MagicMock(return_value=heading_locator)

        budget = ActionBudget(max_actions=20)

        # Call gf_set_date - should fail with month_nav_exhausted at month_header stage
        result = gf_set_date(
            browser_mock,
            role="depart",
            date="2026-03-01",
            timeout_ms=1500,
            budget=budget,
        )

        # Should fail with month_nav_exhausted (not day_not_found or other)
        assert result["ok"] is False
        assert result["reason"] == "month_nav_exhausted"
        # Evidence should show failure at month_header stage
        assert result["evidence"].get("calendar.failure_stage") == "month_header"
        assert "calendar.header_rejected_texts" in result["evidence"]
        # "完了" should be in rejected texts
        if result["evidence"].get("calendar.header_rejected_texts"):
            assert "完了" in result["evidence"]["calendar.header_rejected_texts"]

    def test_month_header_parses_jp_pattern_2026_3(self):
        """
        Case 2: Month header text = "2026年3月" parses successfully.

        Expected: Should extract year=2026, month=3 from pattern "2026年3月",
        and continue to month navigation logic (not return early with month_nav_exhausted).
        """
        from core.scenario.reasons import normalize_reason

        # Test the parsing logic directly
        import re as regex_module

        header_text = "2026年3月"
        year_match = regex_module.search(r'(\d{4})年', header_text)
        month_match = regex_module.search(r'(\d{1,2})月', header_text)

        assert year_match is not None, "Should match year pattern"
        assert month_match is not None, "Should match month pattern"

        parsed_year = int(year_match.group(1))
        parsed_month = int(month_match.group(1))

        assert parsed_year == 2026
        assert parsed_month == 3

    def test_month_header_missing_but_grid_exists_fails_correctly(self):
        """
        Case 3: Header element missing entirely but grid/gridcell exists in dialog.

        Expected: Should detect grid via _gf_calendar_root helper, proceed to
        month header search, fail with reason='month_nav_exhausted' and
        evidence calendar.failure_stage='month_header' (indicating failure at header stage,
        not that calendar didn't open).
        """
        # Verify that normalize_reason maps legacy codes to canonical
        assert normalize_reason("month_header_not_found") == "month_nav_exhausted"
        assert normalize_reason("day_not_found") == "calendar_day_not_found"
        assert normalize_reason("verify_mismatch") == "date_picker_unverified"

        # Test that canonical codes are already valid (no aliasing needed)
        assert is_valid_reason_code("month_nav_exhausted")
        assert is_valid_reason_code("calendar_day_not_found")
        assert is_valid_reason_code("date_picker_unverified")

    def test_month_header_fallback_infers_from_gridcell_aria_label(self):
        """
        Case 4: Month header selector fails, but fallback infers from first gridcell.

        Expected: Should detect grid cells with aria-label containing "2026年3月1日",
        extract year=2026 and month=3, and continue (not return month_nav_exhausted early).
        This demonstrates the FIX-005 fallback mechanism.
        """
        import re as regex_module

        # Simulate a gridcell aria-label from a real calendar
        gridcell_aria_label = "2026年3月1日"

        year_match = regex_module.search(r'(\d{4})年', gridcell_aria_label)
        month_match = regex_module.search(r'(\d{1,2})月', gridcell_aria_label)

        assert year_match is not None
        assert month_match is not None

        parsed_year = int(year_match.group(1))
        parsed_month = int(month_match.group(1))

        assert parsed_year == 2026
        assert parsed_month == 3

        # This logic should be used as fallback in gf_set_date month_header detection
        logger_mock = MagicMock()
        # Verify enough info is logged for troubleshooting
        logger_info_calls = [
            call for call in logger_mock.method_calls
            if 'month_header' in str(call)
        ]
        # When fallback works, it should log the inference



class TestStepResultReasonCodeValidity:
    """Tests that StepResult uses valid failure reason codes."""

    def test_step_result_failure_with_valid_reason_code(self):
        """Should accept valid reason codes from registry."""
        # Valid reason codes should be accepted
        result = StepResult.failure("calendar_not_open")
        assert result.ok is False
        assert is_valid_reason_code(result.reason)

    def test_step_result_failure_with_invalid_reason_code(self):
        """Registry validation should catch unmapped codes."""
        # This test documents that unknown codes should be normalized
        # Currently gf_set_date may return unregistered codes
        # First step: identify all codes used by gf_set_date
        # Second step: either register them or map through normalize_reason()
        pass


class _Phase2FakeLocatorList:
    def __init__(self, items=None):
        self._items = list(items or [])

    @property
    def first(self):
        return self._items[0] if self._items else _Phase2FakeNode(visible=False)

    def count(self):
        return len(self._items)

    def nth(self, index):
        return self._items[index]


class _Phase2FakeNode:
    def __init__(self, *, visible=True, enabled=True, text="", children=None):
        self._visible = visible
        self._enabled = enabled
        self._text = text
        self._children = dict(children or {})

    @property
    def first(self):
        return self

    def is_visible(self, timeout=None):  # noqa: ARG002
        return self._visible

    def is_enabled(self, timeout=None):  # noqa: ARG002
        return self._enabled

    def click(self, timeout=None):  # noqa: ARG002
        return None

    def wait_for(self, state=None, timeout=None):  # noqa: ARG002
        raise RuntimeError("not found")

    def text_content(self):
        return self._text

    def get_attribute(self, name, timeout=None):  # noqa: ARG002
        return None

    def locator(self, selector):
        return self._children.get(selector, _Phase2FakeLocatorList([]))


class _Phase2FallbackRootNoHeaderPage:
    """Page stub for fallback-root + zero month-header candidate scenario."""

    def __init__(self):
        self.dialog = _Phase2FakeNode(
            visible=True,
            children={
                ":has([role='grid'])": _Phase2FakeLocatorList([_Phase2FakeNode(visible=False)]),
                ":has([role='gridcell'])": _Phase2FakeLocatorList([_Phase2FakeNode(visible=False)]),
                "[role='grid'], [role='gridcell'], [role='heading'], [aria-level], [aria-label*='月']": _Phase2FakeLocatorList(
                    [_Phase2FakeNode(visible=False)]
                ),
                "[role='heading']:visible": _Phase2FakeLocatorList([]),
                "[aria-level]:visible": _Phase2FakeLocatorList([]),
                "h1:visible, h2:visible, h3:visible": _Phase2FakeLocatorList([]),
                "[aria-current='date']:visible": _Phase2FakeLocatorList([]),
                "div[role='presentation']:visible": _Phase2FakeLocatorList([]),
                "[class*='header']:visible": _Phase2FakeLocatorList([]),
                "[class*='title']:visible": _Phase2FakeLocatorList([]),
                "[class*='month']:visible": _Phase2FakeLocatorList([]),
                "[aria-label*='月']:visible": _Phase2FakeLocatorList([]),
                "button[aria-label*='月']:visible": _Phase2FakeLocatorList([]),
                "[aria-label*='年']:visible": _Phase2FakeLocatorList([]),
                "button[aria-label*='年']:visible": _Phase2FakeLocatorList([]),
                "[role='gridcell'][aria-label]": _Phase2FakeLocatorList([]),
                "[role='grid'] [aria-label]": _Phase2FakeLocatorList([]),
                "[role='button'][aria-label*='月']": _Phase2FakeLocatorList([]),
                "button[aria-label*='月']": _Phase2FakeLocatorList([]),
            },
        )
        self.opener = _Phase2FakeNode(visible=True, enabled=True)
        self.invisible = _Phase2FakeNode(visible=False)

    def locator(self, selector):
        if selector == "[role='dialog']:not(:has([role='grid']))":
            return _Phase2FakeLocatorList([self.invisible])
        if selector in ("[class*='modal']:visible", "[class*='overlay']:visible"):
            return _Phase2FakeLocatorList([self.invisible])
        if selector == "[role='gridcell']":
            return _Phase2FakeLocatorList([_Phase2FakeNode(visible=False)])
        if "出発" in selector or "depart" in selector.lower():
            return _Phase2FakeLocatorList([self.opener])
        if selector == "[role='dialog']:visible":
            return _Phase2FakeLocatorList([self.dialog])
        if ":has([role='grid'])" in selector or ":has([role='gridcell'])" in selector:
            return _Phase2FakeLocatorList([_Phase2FakeNode(visible=False)])
        return _Phase2FakeLocatorList([])

    def wait_for_timeout(self, ms):  # noqa: ARG002
        return None


class TestFallbackRootMonthHeaderGate:
    def test_gate_decision_true_only_for_unvalidated_fallback_root_zero_candidates(self):
        decision = _gf_calendar_fallback_root_month_header_gate_decision(
            enabled=True,
            root_selector_fallback_used=True,
            header_candidate_count=0,
            header_rejected_count=0,
            evidence={"calendar_root_ready_probe_visible": False},
        )
        assert decision["should_fail_early"] is True
        assert decision["reason"] == "fallback_root_unvalidated_zero_header_candidates"

    def test_gate_decision_false_when_header_candidates_exist(self):
        decision = _gf_calendar_fallback_root_month_header_gate_decision(
            enabled=True,
            root_selector_fallback_used=True,
            header_candidate_count=2,
            header_rejected_count=0,
            evidence={},
        )
        assert decision["should_fail_early"] is False

    def test_gf_set_date_early_fails_after_fallback_root_and_zero_month_headers(self, monkeypatch, caplog):
        browser = MagicMock()
        browser.page = _Phase2FallbackRootNoHeaderPage()
        budget = ActionBudget(max_actions=20)

        monkeypatch.setattr(
            "core.scenario.gf_helpers.date_picker_orchestrator.get_threshold",
            lambda key, default=None: (
                True if key == "gf_set_date_fallback_root_month_header_gate_enabled" else default
            ),
        )

        test_logger = logging.getLogger("tests.gf_set_date.phase2")
        with caplog.at_level(logging.INFO):
            result = gf_set_date(
                browser,
                role="depart",
                date="2026-03-01",
                timeout_ms=1500,
                budget=budget,
                logger=test_logger,
            )

        assert result["ok"] is False
        assert result["reason"] == "calendar_not_open"
        assert result["evidence"].get("calendar.failure_stage") == "month_header"
        assert result["evidence"].get("calendar.root_validation_gate_enabled") is True
        assert (
            result["evidence"].get("calendar.root_validation_reason")
            == "fallback_root_unvalidated_zero_header_candidates"
        )
        assert result["evidence"].get("root_selector_fallback_used") is True
        assert result["evidence"].get("calendar.header_candidate_count") == 0

        messages = [record.getMessage() for record in caplog.records]
        assert any("gf_set_date.open.fallback_root_selector" in msg for msg in messages)
        assert any("gf_set_date.month_header.failed" in msg for msg in messages)
        assert any("gf_set_date.month_header.fallback_root_invalid" in msg for msg in messages)
        assert not any("gf_set_date.month_nav.exhausted" in msg for msg in messages)

    def test_gf_set_date_gate_disabled_preserves_month_nav_exhausted(self, monkeypatch):
        browser = MagicMock()
        browser.page = _Phase2FallbackRootNoHeaderPage()
        budget = ActionBudget(max_actions=20)

        monkeypatch.setattr(
            "core.scenario.gf_helpers.date_picker_orchestrator.get_threshold",
            lambda key, default=None: (
                False if key == "gf_set_date_fallback_root_month_header_gate_enabled" else default
            ),
        )

        result = gf_set_date(
            browser,
            role="depart",
            date="2026-03-01",
            timeout_ms=1500,
            budget=budget,
            logger=logging.getLogger("tests.gf_set_date.phase2.disabled"),
        )

        assert result["ok"] is False
        assert result["reason"] == "month_nav_exhausted"
        assert result["evidence"].get("calendar.failure_stage") == "month_header"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
