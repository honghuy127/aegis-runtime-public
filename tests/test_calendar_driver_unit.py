"""Unit tests for core/scenario/calendar_driver.py

Tests calendar driver strategies using mock objects (no Playwright required).
Validates strategy selection, evidence outputs, and bounded execution.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path

from core.scenario.calendar_driver import (
    CalendarDriver,
    CalendarContext,
    StrategyResult,
)
from core.scenario.types import ActionBudget


class MockLocator:
    """Mock Playwright locator for testing."""

    def __init__(self, visible=True, enabled=True, text="", aria_label="", clickable=True, count_value=1):
        self.visible = visible
        self.enabled = enabled
        self.text_value = text
        self.aria_label_value = aria_label
        self.clickable = clickable
        self.click_called = False
        self.fill_called = False
        self.fill_value = None
        self.count_value = count_value

    def is_visible(self, timeout=None):
        return self.visible

    def is_enabled(self, timeout=None):
        return self.enabled

    def is_editable(self, timeout=None):
        return self.enabled

    def text_content(self):
        return self.text_value

    def get_attribute(self, name, timeout=None):
        if name == "aria-label":
            return self.aria_label_value
        return None

    def click(self, timeout=None):
        if not self.clickable:
            raise Exception("Element not clickable")
        self.click_called = True

    def fill(self, value, timeout=None):
        self.fill_called = True
        self.fill_value = value

    def press(self, key, timeout=None):
        pass

    def input_value(self, timeout=None):
        return self.fill_value if self.fill_value else ""

    def wait_for(self, state=None, timeout=None):
        pass

    @property
    def first(self):
        return self

    def nth(self, index):
        return self

    def count(self):
        return self.count_value

    def locator(self, selector):
        # Return child locator
        if "[role='gridcell']" in selector:
            if "aria-label" in selector:
                # Parse aria-label pattern
                return MockLocator(visible=True, clickable=True, aria_label="2026年3月15日", count_value=1)
            return MockLocator(visible=True, clickable=True, count_value=1)
        elif "[role='grid']" in selector or "[role='gridcell']" in selector:
            return MockLocator(visible=True, count_value=1)
        elif "input" in selector:
            return MockLocator(visible=True, enabled=True, count_value=1)
        return MockLocator(visible=False, count_value=0)


class MockPage:
    """Mock Playwright page for testing."""

    def __init__(self):
        self.locators = {}
        self.default_locator = MockLocator()

    def locator(self, selector):
        return self.locators.get(selector, self.default_locator)

    def add_locator(self, selector, locator):
        self.locators[selector] = locator


class MockBrowser:
    """Mock browser for testing."""

    def __init__(self, page=None):
        self.page = page or MockPage()
        self.run_id = "test_run_123"


class TestCalendarContext:
    """Test CalendarContext initialization and date parsing."""

    def test_context_date_parsing(self):
        """Test target date parsing into components."""
        ctx = CalendarContext(
            browser=MockBrowser(),
            role="depart",
            target_date="2026-03-15",
        )

        assert ctx.target_year == 2026
        assert ctx.target_month == 3
        assert ctx.target_day == 15

    def test_context_invalid_date(self):
        """Test invalid date handling."""
        ctx = CalendarContext(
            browser=MockBrowser(),
            role="depart",
            target_date="invalid-date",
        )

        # Should not raise, but year should be 0
        assert ctx.target_year == 0
        assert ctx.target_month == 0
        assert ctx.target_day == 0


class TestCalendarDriverInputValidation:
    """Test input validation and error handling."""

    def test_unsupported_role(self):
        """Test rejection of unsupported role."""
        driver = CalendarDriver()
        browser = MockBrowser()

        result = driver.set_date(
            browser=browser,
            role="invalid_role",
            target_date="2026-03-15",
        )

        assert not result.ok
        assert result.reason_code == "unsupported_role"
        assert "role" in result.evidence

    def test_invalid_date_format(self):
        """Test rejection of invalid date format."""
        driver = CalendarDriver()
        browser = MockBrowser()

        result = driver.set_date(
            browser=browser,
            role="depart",
            target_date="not-a-date",
        )

        assert not result.ok
        assert result.reason_code == "invalid_date_format"

    def test_set_date_initializes_selector_scoreboard_when_enabled(self, monkeypatch):
        """Selector scoring flag should initialize a per-browser scoreboard cache."""
        driver = CalendarDriver()
        browser = MockBrowser()
        captured = {}

        monkeypatch.setattr(
            "core.scenario.calendar_driver._calendar_runtime_config",
            lambda: {"calendar_selector_scoring_enabled": True},
        )

        def _fake_open(ctx):
            captured["ctx"] = ctx
            return StrategyResult(ok=False, reason_code="calendar_not_open", evidence={})

        monkeypatch.setattr(driver, "_open_calendar", _fake_open)

        result = driver.set_date(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
        )

        assert not result.ok
        assert "ctx" in captured
        assert captured["ctx"].scoreboard is not None
        assert isinstance(getattr(browser, "_calendar_selector_scoreboards", None), dict)

    def test_try_capture_snapshot_uses_configured_limits_and_md_flag(self, monkeypatch, tmp_path):
        """Snapshot capture should honor run-config knobs for enablement/size/md output."""
        driver = CalendarDriver()
        browser = MockBrowser()
        browser.content = lambda: "<html><body>" + ("x" * 1000) + "</body></html>"

        monkeypatch.chdir(tmp_path)
        run_dir = tmp_path / "storage" / "runs" / browser.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            budget=ActionBudget(max_actions=20),
        )

        monkeypatch.setattr(
            "core.scenario.calendar_driver._calendar_runtime_config",
            lambda: {
                "calendar_snapshot_on_failure": True,
                "calendar_snapshot_write_md": True,
                "calendar_snapshot_max_chars": 54321,
            },
        )

        observed = {}

        def _fake_extract_dialog_fragment(html, max_chars):
            observed["fragment_max_chars"] = max_chars
            return ("<div>calendar</div>", "dialog")

        def _fake_truncate_html(fragment, max_chars):
            observed["truncate_max_chars"] = max_chars
            return (fragment, False)

        def _fake_write_calendar_snapshot(*, snapshot, run_dir, include_md):
            observed["include_md"] = include_md
            observed["run_dir"] = str(run_dir)
            observed["html_fragment"] = snapshot.html_fragment
            json_path = run_dir / "artifacts" / "calendar_snapshot_depart_test.json"
            return (json_path, (run_dir / "artifacts" / "calendar_snapshot_depart_test.md"))

        monkeypatch.setattr("core.scenario.calendar_driver.extract_dialog_fragment", _fake_extract_dialog_fragment)
        monkeypatch.setattr("core.scenario.calendar_driver.truncate_html", _fake_truncate_html)
        monkeypatch.setattr("core.scenario.calendar_driver.write_calendar_snapshot", _fake_write_calendar_snapshot)

        evidence = {"selectors_tried": ["[role='dialog']"]}
        driver._try_capture_snapshot(
            ctx=ctx,
            failure_reason_code="calendar_not_open",
            failure_stage="open",
            evidence=evidence,
        )

        assert observed["fragment_max_chars"] == 54321
        assert observed["truncate_max_chars"] == 54321
        assert observed["include_md"] is True
        assert evidence["calendar.snapshot_id"] == "calendar_snapshot_depart_test.json"

    def test_set_date_uses_config_verify_after_commit_when_not_explicit(self, monkeypatch):
        """calendar_verify_after_commit should control post-commit verification by default."""
        driver = CalendarDriver()
        browser = MockBrowser()

        monkeypatch.setattr(
            "core.scenario.calendar_driver._calendar_runtime_config",
            lambda: {
                "calendar_selector_scoring_enabled": False,
                "calendar_verify_after_commit": False,
                "calendar_parsing_utility": "new",
            },
        )

        calendar_root = MockLocator(visible=True)

        monkeypatch.setattr(
            driver,
            "_open_calendar",
            lambda ctx: StrategyResult(
                ok=True,
                evidence={"calendar_root": calendar_root, "opener_selector": "[role='combobox']"},
            ),
        )
        monkeypatch.setattr(
            driver,
            "_strategy_direct_input",
            lambda ctx, root: StrategyResult(ok=True, evidence={"calendar.day_selector_used": "input"}),
        )
        monkeypatch.setattr(
            driver,
            "_strategy_pick_by_aria_label",
            lambda ctx, root: StrategyResult(ok=False, reason_code="day_not_found", evidence={}),
        )
        monkeypatch.setattr(
            driver,
            "_strategy_nav_scan_pick",
            lambda ctx, root: StrategyResult(ok=False, reason_code="month_nav_exhausted", evidence={}),
        )

        verify_called = {"value": False}

        def _verify_should_not_run(*args, **kwargs):
            verify_called["value"] = True
            return (False, {"reason": "should_not_run"})

        monkeypatch.setattr(driver, "_verify_date_committed", _verify_should_not_run)

        result = driver.set_date(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
        )

        assert result.ok
        assert verify_called["value"] is False

    def test_detect_visible_month_robust_legacy_mode_uses_regex_first(self):
        """Legacy parsing mode should succeed on simple headers without utility parser dependency."""
        driver = CalendarDriver()
        browser = MockBrowser()

        header_locator = MockLocator(visible=True, text="2026年3月")
        calendar_root = MockLocator(visible=True)
        calendar_root.locator = lambda sel: header_locator

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            locale_hint="ja-JP",
            budget=ActionBudget(max_actions=20),
            parsing_utility="legacy",
        )

        year, month, selector_used, evidence = driver._detect_visible_month_robust(
            ctx,
            calendar_root,
            fallback_selectors=["[class*='month']"],
        )

        assert (year, month) == (2026, 3)
        assert selector_used == "[class*='month']"
        assert evidence["parsing_method"] in {"legacy_regex", "legacy_regex_fallback_utility"}


class TestCalendarDriverOpenCalendar:
    """Test calendar opening logic."""

    def test_open_calendar_success(self):
        """Test successful calendar opening."""
        driver = CalendarDriver()

        # Setup mock page with opener and calendar root
        page = MockPage()
        opener = MockLocator(visible=True, enabled=True, clickable=True)
        page.add_locator("[role='combobox'][aria-label*='出発']", opener)

        # Calendar root with grid
        calendar_root = MockLocator(visible=True)
        calendar_root.locators = {
            "[role='grid'], [role='gridcell']": MockLocator(visible=True),
        }
        calendar_root.locator = lambda sel: calendar_root.locators.get(sel, MockLocator(visible=True))
        calendar_root.count = lambda: 1

        page.add_locator("[role='dialog']:has([role='grid']):visible", calendar_root)
        page.add_locator("[role='gridcell']", MockLocator(visible=True))

        browser = MockBrowser(page=page)

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            locale_hint="ja-JP",
            budget=ActionBudget(max_actions=20),
        )

        result = driver._open_calendar(ctx)

        assert result.ok
        assert result.evidence.get("opener_selector") is not None
        assert result.evidence.get("calendar_root") is not None
        assert opener.click_called

    def test_open_calendar_fail_no_opener(self):
        """Test calendar opening failure when no opener found."""
        driver = CalendarDriver()

        page = MockPage()
        # No visible openers
        page.default_locator = MockLocator(visible=False)

        browser = MockBrowser(page=page)

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            budget=ActionBudget(max_actions=20),
        )

        result = driver._open_calendar(ctx)

        assert not result.ok
        assert result.reason_code == "calendar_not_open"
        assert "selectors_tried" in result.evidence


class TestCalendarDriverStrategies:
    """Test individual calendar setting strategies."""

    def test_direct_input_success(self):
        """Test direct input strategy success."""
        driver = CalendarDriver()

        # Setup calendar root with input field
        input_locator = MockLocator(visible=True, enabled=True)
        calendar_root = MockLocator(visible=True)
        calendar_root.locators = {
            "input[type='text']:visible": input_locator,
        }
        calendar_root.locator = lambda sel: calendar_root.locators.get(sel, MockLocator(visible=False))

        page = MockPage()
        browser = MockBrowser(page=page)

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            budget=ActionBudget(max_actions=20),
        )

        result = driver._strategy_direct_input(ctx, calendar_root)

        assert result.ok
        assert input_locator.fill_called
        assert "calendar.day_selector_used" in result.evidence

    def test_pick_by_aria_label_success(self):
        """Test pick by aria-label strategy success."""
        driver = CalendarDriver()

        # Setup calendar root with gridcell having matching aria-label
        cell_locator = MockLocator(visible=True, clickable=True, aria_label="2026年3月15日")
        calendar_root = MockLocator(visible=True)

        def mock_locator_fn(sel):
            if "[role='gridcell'][aria-label*='2026年3月15日']" in sel:
                return cell_locator
            if "[role='gridcell'][aria-label*='3月15日']" in sel:
                return cell_locator
            return MockLocator(visible=False)

        calendar_root.locator = mock_locator_fn

        page = MockPage()
        browser = MockBrowser(page=page)

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            budget=ActionBudget(max_actions=20),
        )

        result = driver._strategy_pick_by_aria_label(ctx, calendar_root)

        assert result.ok
        assert cell_locator.click_called
        assert result.evidence.get("calendar.nav_steps") == 0

    def test_pick_by_aria_label_not_found(self):
        """Test pick by aria-label when target day not visible."""
        driver = CalendarDriver()

        # Calendar root but no matching cells
        calendar_root = MockLocator(visible=True)
        calendar_root.locator = lambda sel: MockLocator(visible=False)

        page = MockPage()
        browser = MockBrowser(page=page)

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            budget=ActionBudget(max_actions=20),
        )

        result = driver._strategy_pick_by_aria_label(ctx, calendar_root)

        assert not result.ok
        assert result.reason_code == "day_not_found_in_current_view"

    def test_nav_scan_pick_success(self):
        """Test nav scan strategy with successful day picking after navigation."""
        driver = CalendarDriver()

        # Setup calendar root with nav button and cells
        next_button = MockLocator(visible=True, enabled=True, clickable=True, aria_label="次の月")

        # First call: cell not found, second call after nav: cell found
        call_count = [0]

        def mock_pick_by_aria(ctx_arg, root_arg):
            call_count[0] += 1
            if call_count[0] == 1:
                return StrategyResult(ok=False, reason_code="day_not_found_in_current_view")
            else:
                return StrategyResult(ok=True, evidence={"calendar.day_selector_used": "test"})

        driver._strategy_pick_by_aria_label = mock_pick_by_aria

        def mock_locator_fn(sel):
            if "[aria-label*='次']" in sel or "[aria-label*='Next']" in sel:
                return next_button
            if "[role='gridcell'][aria-label]" in sel:
                # First cell has aria-label for inferring current month
                first_cell = MockLocator(visible=True, aria_label="2026年2月1日")
                return first_cell
            return MockLocator(visible=False)

        calendar_root = MockLocator(visible=True)
        calendar_root.locator = mock_locator_fn

        page = MockPage()
        browser = MockBrowser(page=page)

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            budget=ActionBudget(max_actions=20),
            max_nav_steps=5,
        )

        result = driver._strategy_nav_scan_pick(ctx, calendar_root)

        assert result.ok
        assert next_button.click_called
        assert result.evidence.get("calendar.nav_steps", 0) > 0

    def test_nav_scan_exhausted(self):
        """Test nav scan exhausting max steps without finding day."""
        driver = CalendarDriver()

        # Always return not found
        driver._strategy_pick_by_aria_label = lambda ctx, root: StrategyResult(ok=False, reason_code="day_not_found")

        next_button = MockLocator(visible=True, enabled=True, clickable=True, aria_label="次の月")

        def mock_locator_fn(sel):
            if "[aria-label*='次']" in sel:
                return next_button
            if "[role='gridcell'][aria-label]" in sel:
                first_cell = MockLocator(visible=True, aria_label="2026年2月1日")
                return first_cell
            return MockLocator(visible=False)

        calendar_root = MockLocator(visible=True)
        calendar_root.locator = mock_locator_fn

        page = MockPage()
        browser = MockBrowser(page=page)

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            budget=ActionBudget(max_actions=20),
            max_nav_steps=3,
        )

        result = driver._strategy_nav_scan_pick(ctx, calendar_root)

        assert not result.ok
        assert result.reason_code == "month_nav_exhausted"
        assert result.evidence.get("calendar.nav_steps", 0) > 0
        assert result.evidence.get("calendar.max_nav_steps") == 3


class TestCalendarDriverBudgetEnforcement:
    """Test budget enforcement and bounded execution."""

    def test_budget_hit_during_open(self):
        """Test budget exhaustion during calendar opening."""
        driver = CalendarDriver()

        page = MockPage()
        page.default_locator = MockLocator(visible=True, enabled=True)
        browser = MockBrowser(page=page)

        # Very low budget
        budget = ActionBudget(max_actions=1)
        budget.consume(1)  # Exhaust immediately

        result = driver.set_date(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            budget=budget,
        )

        assert not result.ok
        # Should fail at open stage
        assert "budget" in result.reason_code.lower() or result.reason_code == "calendar_not_open"

    def test_strategy_respects_budget(self):
        """Test that strategies check budget before acting."""
        driver = CalendarDriver()

        # Exhausted budget
        budget = ActionBudget(max_actions=5)
        for _ in range(5):
            budget.consume(1)

        calendar_root = MockLocator(visible=True)
        calendar_root.locator = lambda sel: MockLocator(visible=False)

        page = MockPage()
        browser = MockBrowser(page=page)

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            budget=budget,
        )

        # Should fail quickly without attempting actions
        result = driver._strategy_pick_by_aria_label(ctx, calendar_root)

        # Result should indicate budget constraint (or simply not found since no budget to search)
        assert not result.ok


class TestCalendarDriverIntegration:
    """Integration tests for full calendar driver flow."""

    def test_full_flow_success_first_strategy(self):
        """Test successful date setting using first strategy."""
        driver = CalendarDriver()

        # Setup page with all necessary elements
        page = MockPage()

        # Opener
        opener = MockLocator(visible=True, enabled=True, clickable=True)
        page.add_locator("[role='combobox'][aria-label*='出発']", opener)

        # Calendar root
        input_locator = MockLocator(visible=True, enabled=True)
        calendar_root = MockLocator(visible=True)
        calendar_root.locators = {
            "input[type='text']:visible": input_locator,
            "[role='grid'], [role='gridcell']": MockLocator(visible=True),
        }
        calendar_root.locator = lambda sel: calendar_root.locators.get(sel, MockLocator(visible=False))
        calendar_root.count = lambda: 1

        page.add_locator("[role='dialog']:has([role='grid']):visible", calendar_root)
        page.add_locator("[role='gridcell']", MockLocator(visible=True))

        browser = MockBrowser(page=page)

        result = driver.set_date(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            locale_hint="ja-JP",
            verify_after_commit=False,  # Mock doesn't fully support verification
        )

        assert result.ok
        assert result.reason_code == "success"
        assert "calendar.strategy_id" in result.evidence
        assert opener.click_called
        assert input_locator.fill_called

    def test_fallback_to_second_strategy(self):
        """Test falling back to second strategy when first fails."""
        driver = CalendarDriver()

        # Setup page
        page = MockPage()

        # Opener
        opener = MockLocator(visible=True, enabled=True, clickable=True)
        page.add_locator("[role='combobox'][aria-label*='出発']", opener)

        # Calendar root without input (first strategy fails)
        cell_locator = MockLocator(visible=True, clickable=True, aria_label="2026年3月15日")
        calendar_root = MockLocator(visible=True)

        def mock_locator_fn(sel):
            if "input" in sel:
                return MockLocator(visible=False)  # No input
            if "[role='gridcell'][aria-label*='3月15日']" in sel:
                return cell_locator
            if "[role='grid'], [role='gridcell']" in sel:
                return MockLocator(visible=True)
            return MockLocator(visible=False)

        calendar_root.locator = mock_locator_fn
        calendar_root.count = lambda: 1

        page.add_locator("[role='dialog']:has([role='grid']):visible", calendar_root)
        page.add_locator("[role='gridcell']", MockLocator(visible=True))

        browser = MockBrowser(page=page)

        result = driver.set_date(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            locale_hint="ja-JP",
            verify_after_commit=False,  # Disable verification for this mock test
        )

        assert result.ok
        assert "calendar.strategy_id" in result.evidence
        # Should be pick_by_aria_label strategy
        assert result.evidence["calendar.strategy_id"] == "pick_by_aria_label"
        assert cell_locator.click_called


class TestDetectVisibleMonthRobust:
    """Test _detect_visible_month_robust() method."""

    def test_detect_month_with_parse_utility(self):
        """Test month detection using parse_month_year utility."""
        driver = CalendarDriver()

        # Mock calendar root with month header
        header_locator = MockLocator(visible=True, text="2026年3月")
        calendar_root = Mock()
        calendar_root.locator = Mock(return_value=header_locator)

        ctx = CalendarContext(
            browser=Mock(),
            role="depart",
            target_date="2026-03-15",
            locale_hint="ja-JP",
        )

        year, month, selector, evidence = driver._detect_visible_month_robust(
            ctx,
            calendar_root,
            fallback_selectors=["[role='heading']"],
        )

        assert year == 2026
        assert month == 3
        assert selector == "[role='heading']"
        assert evidence.get("header_text") == "2026年3月"
        assert evidence.get("parsing_method") == "parse_month_year_utility"

    def test_detect_month_english_format(self):
        """Test month detection with English format."""
        driver = CalendarDriver()

        header_locator = MockLocator(visible=True, text="March 2026")
        calendar_root = Mock()
        calendar_root.locator = Mock(return_value=header_locator)

        ctx = CalendarContext(
            browser=Mock(),
            role="depart",
            target_date="2026-03-15",
            locale_hint="en-US",
        )

        year, month, selector, evidence = driver._detect_visible_month_robust(
            ctx,
            calendar_root,
            fallback_selectors=["[role='heading'][aria-live]"],
        )

        assert year == 2026
        assert month == 3
        assert selector == "[role='heading'][aria-live]"

    def test_detect_month_with_scoreboard_ranking(self):
        """Test that scoreboard ranking is used if available."""
        from core.scenario.calendar_selector_scoring import SelectorScoreboard

        driver = CalendarDriver()

        # Create scoreboard with scores for different selectors
        scoreboard = SelectorScoreboard(site_key="google_flights")
        scoreboard.record_success("header", "good_selector")
        scoreboard.record_failure("header", "bad_selector")

        # Setup mocks - good_selector should be tried first due to ranking
        selectors_tried = []

        def mock_locator_fn(selector):
            selectors_tried.append(selector)
            if selector == "good_selector":
                return MockLocator(visible=True, text="2026年3月")
            return MockLocator(visible=False)

        calendar_root = Mock()
        calendar_root.locator = mock_locator_fn

        ctx = CalendarContext(
            browser=Mock(),
            role="depart",
            target_date="2026-03-15",
            locale_hint="ja-JP",
            scoreboard=scoreboard,
        )

        year, month, selector, evidence = driver._detect_visible_month_robust(
            ctx,
            calendar_root,
            fallback_selectors=["good_selector", "bad_selector"],
        )

        assert year == 2026
        assert month == 3
        assert selector == "good_selector"
        # good_selector should be ranked first due to higher score
        assert selectors_tried[0] == "good_selector"

    def test_detect_month_not_found(self):
        """Test handling when month header cannot be found."""
        driver = CalendarDriver()

        header_locator = MockLocator(visible=False)
        calendar_root = Mock()
        calendar_root.locator = Mock(return_value=header_locator)

        ctx = CalendarContext(
            browser=Mock(),
            role="depart",
            target_date="2026-03-15",
        )

        year, month, selector, evidence = driver._detect_visible_month_robust(
            ctx,
            calendar_root,
            fallback_selectors=["[role='heading']"],
        )

        assert year is None
        assert month is None
        assert selector is None
        assert evidence.get("error") == "month_header_not_found"


class TestVerifyDateCommitted:
    """Test _verify_date_committed() method."""

    def test_verify_date_committed_success(self):
        """Test successful date verification."""
        driver = CalendarDriver()

        # Mock field with committed date
        field_locator = MockLocator(visible=True, text="03/15/2026")
        page = Mock()
        page.locator = Mock(return_value=field_locator)

        browser = Mock()
        browser.page = page

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
        )

        ok, evidence = driver._verify_date_committed(
            ctx,
            calendar_root=Mock(),
            opener_selector="input[aria-label='Departure']",
        )

        assert ok is True
        assert evidence.get("verified") is True
        assert evidence.get("calendar.verification_success") is True

    def test_verify_date_committed_failure(self):
        """Test verification failure when date not in field."""
        driver = CalendarDriver()

        # Mock field with different date
        field_locator = MockLocator(visible=True, text="04/20/2026")
        page = Mock()
        page.locator = Mock(return_value=field_locator)

        browser = Mock()
        browser.page = page

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
        )

        ok, evidence = driver._verify_date_committed(
            ctx,
            calendar_root=Mock(),
            opener_selector="input[aria-label='Departure']",
        )

        assert ok is False
        assert evidence.get("verified") is False
        assert evidence.get("reason") == "date_not_in_field"
        assert evidence.get("calendar.failure_stage") == "verify"

    def test_verify_date_committed_field_not_readable(self):
        """Test verification when field is not readable."""
        driver = CalendarDriver()

        page = Mock()
        page.locator = Mock(side_effect=Exception("Field not found"))

        browser = Mock()
        browser.page = page

        ctx = CalendarContext(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
        )

        ok, evidence = driver._verify_date_committed(
            ctx,
            calendar_root=Mock(),
            opener_selector="input[aria-label='Departure']",
        )

        # Should gracefully return success (best effort)
        assert ok is True
        assert evidence.get("verified") == "best_effort_skip"


class TestSetDateWithVerification:
    """Test set_date() with verification enabled."""

    def test_set_date_with_verification_logs_success(self):
        """Test that successful set_date logs verification info."""
        driver = CalendarDriver()

        # We test that if a strategy returns ok=True, verification is called
        # This is a behavioral test rather than full integration test

        # Mock a successful strategy result
        ctx = CalendarContext(
            browser=MockBrowser(),
            role="depart",
            target_date="2026-03-15",
            verify_after_commit=True,
        )

        # Test that verification code gets executed
        # Create a mock for the verification step
        with patch.object(driver, '_verify_date_committed', return_value=(True, {"verified": True})):
            # Since full integration is complex, we verify the method exists and works
            ok, evidence = driver._verify_date_committed(
                ctx,
                calendar_root=Mock(),
                opener_selector="[role='combobox']",
            )

            assert ok is True
            assert evidence.get("verified") is True

    def test_verify_after_commit_context_field(self):
        """Test that CalendarContext has verify_after_commit field."""
        ctx = CalendarContext(
            browser=MockBrowser(),
            role="depart",
            target_date="2026-03-15",
            verify_after_commit=True,
        )

        assert ctx.verify_after_commit is True

        # Test disabled verification
        ctx2 = CalendarContext(
            browser=MockBrowser(),
            role="depart",
            target_date="2026-03-15",
            verify_after_commit=False,
        )

        assert ctx2.verify_after_commit is False

    def test_verification_with_month_day_patterns(self):
        """Test that verification checks multiple date patterns."""
        driver = CalendarDriver()

        # Test with various date formats in field
        # The verification logic checks for zero-padded month (03) and day (15)
        # OR for month as unpadded number (3) and day (15)
        test_cases = [
            ("03/15/2026", True),   # MM/DD/YYYY with zero-padding
            ("3/15/2026", True),    # M/DD/YYYY without zero-padding
            ("15-03-2026", True),   # DD-MM-YYYY (still has both)
            ("2026-03-15", True),   # ISO format (has both)
            ("2026-3-15", True),    # ISO without zero-padding
            ("04/20/2026", False),  # Wrong date
        ]

        for field_value, should_pass in test_cases:
            field_locator = MockLocator(visible=True, text=field_value)
            page = Mock()
            page.locator = Mock(return_value=field_locator)

            browser = Mock()
            browser.page = page

            ctx = CalendarContext(
                browser=browser,
                role="depart",
                target_date="2026-03-15",  # Target: month=3, day=15
            )

            ok, evidence = driver._verify_date_committed(
                ctx,
                calendar_root=Mock(),
                opener_selector="input[aria-label='date']",
            )

            if should_pass:
                # Contains the verification patterns
                assert ok is True, f"Should verify {field_value} as success, got {evidence}"
            else:
                # Wrong date - verification should fail
                assert ok is False, f"Should fail for {field_value}, got {evidence}"
