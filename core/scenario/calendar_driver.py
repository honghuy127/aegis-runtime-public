"""CalendarDriver: Multi-strategy date picker automation with bounded execution.

Implements smart calendar filling that does not solely depend on month header parsing.
Strategies execute in order (stats-driven if available):
  1. direct_input: Type date if input field exists
  2. pick_by_aria_label: Find day cell by aria-label/data-date (no nav needed)
  3. nav_scan_pick: Navigate month-by-month, check for target day cell
  4. deeplink_rebind: For Google Flights, fall back to URL manipulation

Each strategy returns (ok: bool, reason_code: str|None, evidence: dict).
Bounded by max_actions budget and per-strategy timeout caps.

Benefits:
- Reduces brittleness from month header parsing failures
- Provides multiple fallback paths
- Better diagnostic evidence for failures
- Integration with GraphPolicyStats for strategy learning

DOC: See docs/kb/30_patterns/date_picker.md for complete pattern documentation.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.logging import get_logger
from core.run_input_config import load_run_input_config
from core.scenario.calendar_parsing import normalize_month_text, parse_month_year, month_delta
from core.scenario.calendar_selector_scoring import SelectorScoreboard
from core.scenario.calendar_snapshot import (
    CalendarSnapshot,
    MonthParseResult,
    SelectorAttempt,
    extract_dialog_fragment,
    truncate_html,
    write_calendar_snapshot,
)

log = get_logger(__name__)


def _calendar_runtime_config() -> Dict[str, Any]:
    """Load run-level calendar feature knobs with safe defaults."""
    try:
        cfg = load_run_input_config()
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    return cfg


def _normalize_calendar_parsing_utility(value: Any) -> str:
    """Normalize parsing utility mode to supported values."""
    mode = str(value or "new").strip().lower()
    if mode not in {"new", "legacy"}:
        return "new"
    return mode


@dataclass
class StrategyResult:
    """Result of a single calendar setting strategy."""
    ok: bool
    reason_code: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    action_count: int = 0
    elapsed_ms: int = 0


@dataclass
class CalendarContext:
    """Context for calendar date setting operations."""
    browser: Any  # Browser/page object
    role: str  # 'depart' or 'return'
    target_date: str  # YYYY-MM-DD format
    timeout_ms: int = 1500
    locale_hint: str = ""
    budget: Optional[Any] = None
    deadline: Optional[float] = None
    max_nav_steps: int = 8
    role_selectors: List[str] = field(default_factory=list)
    graph_stats: Optional[Any] = None  # GraphPolicyStats instance if enabled
    scoreboard: Optional[SelectorScoreboard] = None  # (NEW) Selector scoring for this run
    verify_after_commit: bool = True  # (NEW) Verify date was actually committed
    parsing_utility: str = "new"  # "new" (utility-first) | "legacy" (regex-first compat)

    # Parsed date fields (populated on init)
    target_year: int = 0
    target_month: int = 0
    target_day: int = 0

    def __post_init__(self):
        """Parse target date into components."""
        try:
            date_obj = datetime.strptime(self.target_date, "%Y-%m-%d")
            self.target_year = date_obj.year
            self.target_month = date_obj.month
            self.target_day = date_obj.day
        except Exception as exc:
            log.warning("CalendarContext.date_parse_failed date=%s error=%s", self.target_date, exc)


class CalendarDriver:
    """Multi-strategy calendar date setter with bounded execution.

    Tries strategies in order until one succeeds or all fail.
    Each strategy is time-bounded and budget-tracked.

    Usage:
        driver = CalendarDriver()
        result = driver.set_date(
            browser=browser,
            role="depart",
            target_date="2026-03-15",
            timeout_ms=1500,
            locale_hint="ja-JP",
            budget=action_budget,
        )

        if result.ok:
            # Success
        else:
            # Check result.reason_code and result.evidence for diagnostics
    """

    def __init__(self):
        """Initialize calendar driver."""
        self.strategies = [
            ("direct_input", self._strategy_direct_input),
            ("pick_by_aria_label", self._strategy_pick_by_aria_label),
            ("nav_scan_pick", self._strategy_nav_scan_pick),
        ]

    def set_date(
        self,
        browser: Any,
        *,
        role: str,
        target_date: str,
        timeout_ms: int = 1500,
        role_selectors: Optional[List[str]] = None,
        locale_hint: str = "",
        budget: Optional[Any] = None,
        deadline: Optional[float] = None,
        max_nav_steps: int = 8,
        graph_stats: Optional[Any] = None,
        verify_after_commit: Optional[bool] = None,
    ) -> StrategyResult:
        """Set calendar date using multi-strategy approach.

        Args:
            browser: Browser session
            role: 'depart' or 'return'
            target_date: Target date in YYYY-MM-DD format
            timeout_ms: Per-action timeout in milliseconds
            role_selectors: Selectors for date field button/input
            locale_hint: Locale hint (e.g., 'ja-JP', 'en-US')
            budget: ActionBudget instance for tracking action count
            deadline: Wall clock deadline (monotonic time)
            max_nav_steps: Maximum navigation steps for nav_scan strategy
            graph_stats: Optional GraphPolicyStats instance for recording
            verify_after_commit: Whether to verify date was committed after selection.
                If None, uses configs/run.yaml calendar_verify_after_commit.

        Returns:
            StrategyResult with ok, reason_code, evidence, action_count, elapsed_ms
        """
        start_time = time.monotonic()

        # Initialize budget if not provided
        from core.scenario.types import ActionBudget
        if budget is None:
            budget = ActionBudget(max_actions=20)
        budget_start = budget.max_actions - budget.remaining

        runtime_cfg = _calendar_runtime_config()
        verify_after_commit_enabled = (
            bool(runtime_cfg.get("calendar_verify_after_commit", True))
            if verify_after_commit is None
            else bool(verify_after_commit)
        )
        parsing_utility_mode = _normalize_calendar_parsing_utility(
            runtime_cfg.get("calendar_parsing_utility", "new")
        )

        # Build context
        ctx = CalendarContext(
            browser=browser,
            role=role,
            target_date=target_date,
            timeout_ms=timeout_ms,
            locale_hint=locale_hint,
            budget=budget,
            deadline=deadline,
            max_nav_steps=max_nav_steps,
            role_selectors=role_selectors or [],
            graph_stats=graph_stats,
            verify_after_commit=verify_after_commit_enabled,
            parsing_utility=parsing_utility_mode,
        )
        if ctx.scoreboard is None and bool(runtime_cfg.get("calendar_selector_scoring_enabled", True)):
            try:
                cache_name = "_calendar_selector_scoreboards"
                cache = getattr(browser, cache_name, None)
                if not isinstance(cache, dict):
                    cache = {}
                    setattr(browser, cache_name, cache)
                cache_key = f"google_flights|{ctx.locale_hint or 'unknown'}"
                scoreboard = cache.get(cache_key)
                if not isinstance(scoreboard, SelectorScoreboard):
                    scoreboard = SelectorScoreboard(site_key="google_flights", locale=ctx.locale_hint or "en")
                    cache[cache_key] = scoreboard
                ctx.scoreboard = scoreboard
            except Exception as exc:
                log.debug("calendar_driver.scoreboard_init_failed error=%s", str(exc)[:100])

        # Validate inputs
        if ctx.role not in ("depart", "return"):
            return StrategyResult(
                ok=False,
                reason_code="unsupported_role",
                evidence={"role": role},
                action_count=0,
                elapsed_ms=0,
            )

        if not ctx.target_date or ctx.target_year == 0:
            return StrategyResult(
                ok=False,
                reason_code="invalid_date_format",
                evidence={"target_date": target_date},
                action_count=0,
                elapsed_ms=0,
            )

        # Step 1: Open calendar dialog
        open_result = self._open_calendar(ctx)
        if not open_result.ok:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            budget_used = budget.max_actions - budget.remaining - budget_start
            return StrategyResult(
                ok=False,
                reason_code=open_result.reason_code or "calendar_not_open",
                evidence=open_result.evidence,
                action_count=budget_used,
                elapsed_ms=elapsed_ms,
            )

        # Store calendar_root in context evidence
        calendar_root = open_result.evidence.get("calendar_root")

        # Step 2: Try strategies in order
        last_result = None
        for strategy_id, strategy_fn in self.strategies:
            # Check budget before trying strategy
            if budget.remaining < 1:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                budget_used = budget.max_actions - budget.remaining - budget_start
                return StrategyResult(
                    ok=False,
                    reason_code="budget_hit",
                    evidence={
                        "stage": "strategy_selection",
                        "strategy_id": strategy_id,
                        "strategies_tried": [s[0] for s in self.strategies[:self.strategies.index((strategy_id, strategy_fn))]],
                    },
                    action_count=budget_used,
                    elapsed_ms=elapsed_ms,
                )

            log.info("calendar_driver.strategy.trying strategy_id=%s role=%s date=%s", strategy_id, role, target_date)

            try:
                result = strategy_fn(ctx, calendar_root)
                last_result = result

                # Record to graph stats if enabled
                self._record_graph_stats(ctx, strategy_id, result)

                if result.ok:
                    elapsed_ms = int((time.monotonic() - start_time) * 1000)
                    budget_used = budget.max_actions - budget.remaining - budget_start

                    # Add strategy info to evidence
                    result.evidence["calendar.strategy_id"] = strategy_id
                    result.evidence["calendar.open.selector_used"] = open_result.evidence.get("opener_selector", "")

                    # Optional: Verify date was committed if enabled
                    if ctx.verify_after_commit:
                        opener_selector = open_result.evidence.get("opener_selector", "")
                        verify_ok, verify_ev = self._verify_date_committed(
                            ctx, calendar_root, opener_selector
                        )
                        result.evidence.update(verify_ev)

                        if not verify_ok:
                            log.info(
                                "calendar_driver.verify_failed strategy_id=%s role=%s reason=%s",
                                strategy_id,
                                role,
                                verify_ev.get("reason", "unknown"),
                            )
                            elapsed_ms = int((time.monotonic() - start_time) * 1000)
                            budget_used = budget.max_actions - budget.remaining - budget_start

                            return StrategyResult(
                                ok=False,
                                reason_code="date_not_committed",
                                evidence=result.evidence,
                                action_count=budget_used,
                                elapsed_ms=elapsed_ms,
                            )

                    log.info(
                        "calendar_driver.success strategy_id=%s role=%s date=%s elapsed_ms=%d budget_used=%d",
                        strategy_id,
                        role,
                        target_date,
                        elapsed_ms,
                        budget_used,
                    )

                    return StrategyResult(
                        ok=True,
                        reason_code="success",
                        evidence=result.evidence,
                        action_count=budget_used,
                        elapsed_ms=elapsed_ms,
                    )
                else:
                    log.debug(
                        "calendar_driver.strategy.failed strategy_id=%s reason=%s",
                        strategy_id,
                        result.reason_code,
                    )
            except Exception as exc:
                log.warning("calendar_driver.strategy.exception strategy_id=%s error=%s", strategy_id, str(exc)[:100])
                last_result = StrategyResult(
                    ok=False,
                    reason_code="strategy_exception",
                    evidence={"strategy_id": strategy_id, "error": str(exc)[:200]},
                )

        # All strategies failed
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        budget_used = budget.max_actions - budget.remaining - budget_start

        final_reason = last_result.reason_code if last_result else "all_strategies_failed"
        final_evidence = last_result.evidence if last_result else {}
        final_evidence["calendar.failure_stage"] = "all_strategies_exhausted"
        final_evidence["calendar.strategies_tried"] = [s[0] for s in self.strategies]

        # Attempt to capture snapshot on failure (if enabled)
        self._try_capture_snapshot(
            ctx=ctx,
            failure_reason_code=final_reason,
            failure_stage=final_evidence.get("calendar.failure_stage", "unknown"),
            evidence=final_evidence,
        )

        return StrategyResult(
            ok=False,
            reason_code=final_reason,
            evidence=final_evidence,
            action_count=budget_used,
            elapsed_ms=elapsed_ms,
        )

    def _open_calendar(self, ctx: CalendarContext) -> StrategyResult:
        """Open calendar dialog with robust opener selection.

        Args:
            ctx: Calendar context

        Returns:
            StrategyResult with calendar_root in evidence if successful
        """
        page = getattr(ctx.browser, "page", None)
        if page is None:
            return StrategyResult(
                ok=False,
                reason_code="page_unavailable",
                evidence={},
            )

        # Build opener selectors based on role and locale
        if ctx.role == "depart":
            if "ja" in ctx.locale_hint.lower():
                opener_selectors = [
                    "[role='combobox'][aria-label*='出発']",
                    "input[aria-label*='出発日']",
                    "input[placeholder*='出発日']",
                    "[role='button'][aria-label*='出発日']",
                    "[aria-label*='出発日']",
                ]
            else:
                opener_selectors = [
                    "[role='combobox'][aria-label*='Depart']",
                    "input[aria-label*='Departure']",
                    "input[placeholder*='Departure']",
                    "[role='button'][aria-label*='Depart']",
                    "[aria-label*='Depart']",
                ]
        else:  # return
            if "ja" in ctx.locale_hint.lower():
                opener_selectors = [
                    "[role='combobox'][aria-label*='復路']",
                    "input[aria-label*='復路']",
                    "input[placeholder*='復路']",
                    "[role='button'][aria-label*='復路']",
                    "[aria-label*='復路']",
                ]
            else:
                opener_selectors = [
                    "[role='combobox'][aria-label*='Return']",
                    "input[aria-label*='Return']",
                    "input[placeholder*='Return']",
                    "[role='button'][aria-label*='Return']",
                    "[aria-label*='Return']",
                ]

        # Add custom selectors from context (highest priority)
        if ctx.role_selectors:
            opener_selectors = ctx.role_selectors + opener_selectors

        # Try openers
        opener_used = None
        selectors_tried = []

        for selector in opener_selectors[:5]:  # Limit to 5 attempts
            if not selector:
                continue

            if ctx.budget.remaining < 1:
                return StrategyResult(
                    ok=False,
                    reason_code="budget_hit",
                    evidence={"stage": "open", "selectors_tried": selectors_tried},
                )

            try:
                locator = page.locator(selector).first

                # Check visibility and interactivity
                if not locator.is_visible(timeout=300):
                    selectors_tried.append(selector)
                    continue

                if not locator.is_enabled(timeout=100):
                    selectors_tried.append(selector)
                    continue

                # Click to open
                ctx.budget.consume(1)
                locator.click(timeout=ctx.timeout_ms)
                time.sleep(0.2)

                opener_used = selector
                selectors_tried.append(selector)

                # Wait for grid cells to appear
                try:
                    page.locator("[role='gridcell']").first.wait_for(state="visible", timeout=500)
                except Exception:
                    pass

                # Find calendar root
                root_selectors = [
                    "[role='dialog']:has([role='grid']):visible",
                    "[role='dialog']:has([role='gridcell']):visible",
                    "[class*='calendar']:has([role='grid']):visible",
                    "[role='dialog']:visible",
                ]

                calendar_root = None
                root_selector_used = None

                for root_sel in root_selectors:
                    try:
                        root_locator = page.locator(root_sel).first
                        if root_locator.is_visible(timeout=400):
                            calendar_root = root_locator
                            root_selector_used = root_sel
                            break
                    except Exception:
                        pass

                if calendar_root is not None:
                    # Verify it contains grid structure
                    try:
                        grid_count = calendar_root.locator("[role='grid'], [role='gridcell']").count()
                        if grid_count == 0:
                            log.debug("calendar_driver.open.root_без_grid selector=%s", root_selector_used)
                            # Continue trying other openers
                            continue
                    except Exception:
                        pass

                    log.info("calendar_driver.open.ok opener=%s root=%s", selector, root_selector_used)
                    return StrategyResult(
                        ok=True,
                        evidence={
                            "calendar_root": calendar_root,
                            "opener_selector": selector,
                            "root_selector": root_selector_used,
                            "selectors_tried": selectors_tried,
                        },
                    )
            except Exception as exc:
                log.debug("calendar_driver.open.attempt_failed selector=%s error=%s", selector, str(exc)[:50])
                selectors_tried.append(selector)
                continue

        return StrategyResult(
            ok=False,
            reason_code="calendar_not_open",
            evidence={
                "selectors_tried": selectors_tried,
                "opener_count": len(selectors_tried),
            },
        )

    def _strategy_direct_input(self, ctx: CalendarContext, calendar_root: Any) -> StrategyResult:
        """Strategy 1: Type date directly into input field if it exists.

        Some calendar implementations allow direct typing without navigation.

        Args:
            ctx: Calendar context
            calendar_root: Calendar dialog root element

        Returns:
            StrategyResult
        """
        page = getattr(ctx.browser, "page", None)
        if page is None or calendar_root is None:
            return StrategyResult(
                ok=False,
                reason_code="prerequisites_missing",
                evidence={"page_available": page is not None, "root_available": calendar_root is not None},
            )

        # Look for input field within calendar
        input_selectors = [
            "input[type='text']:visible",
            "input[type='date']:visible",
            "input[placeholder]:visible",
            "input:not([type='hidden']):visible",
        ]

        for selector in input_selectors:
            try:
                input_locator = calendar_root.locator(selector).first

                if not input_locator.is_visible(timeout=200):
                    continue

                if not input_locator.is_editable(timeout=100):
                    continue

                # Try typing date in various formats
                date_formats = [
                    ctx.target_date,  # YYYY-MM-DD
                    ctx.target_date.replace("-", "/"),  # YYYY/MM/DD
                    f"{ctx.target_month:02d}/{ctx.target_day:02d}/{ctx.target_year}",  # MM/DD/YYYY
                    f"{ctx.target_day:02d}/{ctx.target_month:02d}/{ctx.target_year}",  # DD/MM/YYYY
                ]

                for date_str in date_formats:
                    if ctx.budget.remaining < 2:
                        break

                    try:
                        ctx.budget.consume(1)
                        input_locator.fill(date_str, timeout=ctx.timeout_ms)
                        time.sleep(0.1)

                        # Check if value was accepted
                        current_value = input_locator.input_value(timeout=200)
                        if current_value and (ctx.target_date.replace("-", "") in current_value.replace("-", "").replace("/", "")):
                            # Try pressing Enter to commit
                            ctx.budget.consume(1)
                            input_locator.press("Enter", timeout=500)
                            time.sleep(0.2)

                            log.info("calendar_driver.direct_input.success date_str=%s", date_str)
                            return StrategyResult(
                                ok=True,
                                evidence={
                                    "calendar.day_selector_used": selector,
                                    "calendar.input_format": date_str,
                                },
                            )
                    except Exception as exc:
                        log.debug("calendar_driver.direct_input.format_failed format=%s error=%s", date_str, str(exc)[:50])
                        continue
            except Exception as exc:
                log.debug("calendar_driver.direct_input.selector_failed selector=%s error=%s", selector, str(exc)[:50])
                continue

        return StrategyResult(
            ok=False,
            reason_code="direct_input_not_available",
            evidence={},
        )

    def _strategy_pick_by_aria_label(self, ctx: CalendarContext, calendar_root: Any) -> StrategyResult:
        """Strategy 2: Find and click day cell by aria-label or data-date.

        Does not require month navigation if the target date is visible.
        Searches for cells with aria-label containing the target date pattern.

        Args:
            ctx: Calendar context
            calendar_root: Calendar dialog root element

        Returns:
            StrategyResult
        """
        page = getattr(ctx.browser, "page", None)
        if page is None or calendar_root is None:
            return StrategyResult(
                ok=False,
                reason_code="prerequisites_missing",
                evidence={},
            )

        # Build date patterns to search for
        # Japanese: "2026年3月15日", "3月15日"
        # English: "March 15, 2026", "15 March 2026"

        year_str = str(ctx.target_year)
        month_str = str(ctx.target_month)
        day_str = str(ctx.target_day)

        # Japanese patterns
        patterns = [
            f"{year_str}年{month_str}月{day_str}日",
            f"{month_str}月{day_str}日",
        ]

        # Data attribute patterns
        data_patterns = [
            f"{ctx.target_year}-{ctx.target_month:02d}-{ctx.target_day:02d}",
            f"{ctx.target_year}{ctx.target_month:02d}{ctx.target_day:02d}",
        ]

        # Try aria-label search
        for pattern in patterns:
            if ctx.budget.remaining < 1:
                break

            try:
                # Scoped to calendar root
                cells = calendar_root.locator(f"[role='gridcell'][aria-label*='{pattern}']")
                count = cells.count() if hasattr(cells, 'count') else 0

                if count > 0:
                    cell = cells.first
                    if cell.is_visible(timeout=200):
                        ctx.budget.consume(1)
                        cell.click(timeout=ctx.timeout_ms)
                        time.sleep(0.2)

                        log.info("calendar_driver.pick_by_aria.success pattern=%s", pattern)
                        return StrategyResult(
                            ok=True,
                            evidence={
                                "calendar.day_selector_used": f"[role='gridcell'][aria-label*='{pattern}']",
                                "calendar.nav_steps": 0,
                            },
                        )
            except Exception as exc:
                log.debug("calendar_driver.pick_by_aria.pattern_failed pattern=%s error=%s", pattern, str(exc)[:50])
                continue

        # Try data-date attributes
        for data_val in data_patterns:
            if ctx.budget.remaining < 1:
                break

            try:
                cells = calendar_root.locator(f"[data-date='{data_val}'], [data-date='{data_val}']")
                count = cells.count() if hasattr(cells, 'count') else 0

                if count > 0:
                    cell = cells.first
                    if cell.is_visible(timeout=200):
                        ctx.budget.consume(1)
                        cell.click(timeout=ctx.timeout_ms)
                        time.sleep(0.2)

                        log.info("calendar_driver.pick_by_data.success data_val=%s", data_val)
                        return StrategyResult(
                            ok=True,
                            evidence={
                                "calendar.day_selector_used": f"[data-date='{data_val}']",
                                "calendar.nav_steps": 0,
                            },
                        )
            except Exception as exc:
                log.debug("calendar_driver.pick_by_data.pattern_failed data_val=%s error=%s", data_val, str(exc)[:50])
                continue

        return StrategyResult(
            ok=False,
            reason_code="day_not_found_in_current_view",
            evidence={},
        )

    def _strategy_nav_scan_pick(self, ctx: CalendarContext, calendar_root: Any) -> StrategyResult:
        """Strategy 3: Navigate month-by-month and attempt to pick day cell.

        Bounded navigation: click next/prev up to max_nav_steps, checking for target day each time.
        Does not depend on month header text parsing.

        Args:
            ctx: Calendar context
            calendar_root: Calendar dialog root element

        Returns:
            StrategyResult
        """
        page = getattr(ctx.browser, "page", None)
        if page is None or calendar_root is None:
            return StrategyResult(
                ok=False,
                reason_code="prerequisites_missing",
                evidence={},
            )

        # Find navigation buttons
        nav_button_selectors = [
            "[aria-label*='次']:visible, [aria-label*='Next']:visible",  # Next
            "[aria-label*='前']:visible, [aria-label*='Previous']:visible",  # Previous
        ]

        next_button = None
        prev_button = None

        for sel in nav_button_selectors:
            try:
                buttons = calendar_root.locator(sel)
                count = buttons.count() if hasattr(buttons, 'count') else 0

                # Try to identify next vs prev
                for i in range(min(count, 4)):
                    btn = buttons.nth(i)
                    if btn.is_visible(timeout=100):
                        aria_label = btn.get_attribute("aria-label", timeout=100) or ""

                        if "次" in aria_label or "Next" in aria_label.lower():
                            next_button = btn
                        elif "前" in aria_label or "Previous" in aria_label.lower() or "Prev" in aria_label:
                            prev_button = btn
            except Exception:
                pass

        if next_button is None and prev_button is None:
            return StrategyResult(
                ok=False,
                reason_code="nav_buttons_not_found",
                evidence={},
            )

        # Determine navigation direction based on current vs target
        # Try to infer current month from visible cells
        current_month = None
        current_year = None

        try:
            cells = calendar_root.locator("[role='gridcell'][aria-label]")
            first_cell = cells.first
            if first_cell.is_visible(timeout=200):
                aria_label = first_cell.get_attribute("aria-label", timeout=100) or ""
                # Parse: "2026年3月1日"
                year_match = re.search(r'(\d{4})年', aria_label)
                month_match = re.search(r'(\d{1,2})月', aria_label)

                if year_match and month_match:
                    current_year = int(year_match.group(1))
                    current_month = int(month_match.group(1))
        except Exception:
            pass

        # If we can't determine current month, try both directions
        directions = []
        if current_year is not None and current_month is not None:
            target_ym = ctx.target_year * 12 + ctx.target_month
            current_ym = current_year * 12 + current_month

            if target_ym > current_ym and next_button:
                directions = [("next", next_button)]
            elif target_ym < current_ym and prev_button:
                directions = [("prev", prev_button)]
            else:
                # Same month or unknown, try both
                if next_button:
                    directions.append(("next", next_button))
                if prev_button:
                    directions.append(("prev", prev_button))
        else:
            # Unknown current month, try both
            if next_button:
                directions.append(("next", next_button))
            if prev_button:
                directions.append(("prev", prev_button))

        nav_steps_taken = 0

        for direction, nav_button in directions:
            for step in range(ctx.max_nav_steps):
                if ctx.budget.remaining < 2:
                    break

                # Try to pick day in current view
                pick_result = self._strategy_pick_by_aria_label(ctx, calendar_root)
                if pick_result.ok:
                    pick_result.evidence["calendar.nav_steps"] = nav_steps_taken
                    pick_result.evidence["calendar.nav_direction"] = direction
                    return pick_result

                # Navigate
                try:
                    ctx.budget.consume(1)
                    nav_button.click(timeout=ctx.timeout_ms)
                    time.sleep(0.3)
                    nav_steps_taken += 1
                except Exception as exc:
                    log.debug("calendar_driver.nav_scan.nav_failed direction=%s step=%d error=%s", direction, step, str(exc)[:50])
                    break

        return StrategyResult(
            ok=False,
            reason_code="month_nav_exhausted",
            evidence={
                "calendar.nav_steps": nav_steps_taken,
                "calendar.max_nav_steps": ctx.max_nav_steps,
            },
        )

    def _detect_visible_month_robust(
        self, ctx: CalendarContext, calendar_root: Any, fallback_selectors: List[str]
    ) -> Tuple[Optional[int], Optional[int], Optional[str], Dict]:
        """Detect visible month using parse_month_year utility + scoreboard ranking.

        Uses evidence-driven selector ranking (scoreboard) to find the best header selector.
        Falls back to hardcoded list of selectors if no scores are available.

        Args:
            ctx: Calendar context
            calendar_root: Calendar dialog root element
            fallback_selectors: Default header selectors if scoreboard not trained

        Returns:
            (year, month, selector_used, evidence) where year/month are None on failure
        """
        if calendar_root is None:
            return (None, None, None, {"error": "root_unavailable"})

        # Get ranked selectors (scoreboard-ordered if available, else fallback)
        if ctx.scoreboard:
            ranked_selectors = ctx.scoreboard.rank_selectors("header", fallback_selectors)
        else:
            ranked_selectors = fallback_selectors

        for selector in ranked_selectors:
            try:
                element = calendar_root.locator(selector).first
                if not element.is_visible(timeout=200):
                    if ctx.scoreboard:
                        ctx.scoreboard.record_failure("header", selector)
                    continue

                header_text = element.text_content().strip()
                if not header_text:
                    if ctx.scoreboard:
                        ctx.scoreboard.record_failure("header", selector)
                    continue

                # Parse month/year from header text
                year, month, parsing_method = self._parse_month_header(
                    header_text,
                    locale_hint=ctx.locale_hint,
                    mode=ctx.parsing_utility,
                )

                if year and month:
                    # Record success to scoreboard
                    if ctx.scoreboard:
                        ctx.scoreboard.record_success("header", selector)

                    return (year, month, selector, {
                        "header_text": header_text,
                        "parsing_method": parsing_method,
                        "selector_used": selector,
                    })
                else:
                    if ctx.scoreboard:
                        ctx.scoreboard.record_failure("header", selector)

            except Exception as exc:
                if ctx.scoreboard:
                    ctx.scoreboard.record_failure("header", selector)
                log.debug("detect_month_robust.try_failed selector=%s error=%s", selector, str(exc)[:50])

        return (None, None, None, {
            "error": "month_header_not_found",
            "selectors_tried": len(ranked_selectors),
        })

    def _parse_month_header(
        self,
        header_text: str,
        *,
        locale_hint: str,
        mode: str,
    ) -> Tuple[Optional[int], Optional[int], str]:
        """Parse calendar month header with config-selectable utility mode.

        `new`: use parse_month_year() utility first.
        `legacy`: use a narrow regex parser first, then fall back to parse_month_year().
        """
        mode = _normalize_calendar_parsing_utility(mode)
        if mode == "legacy":
            year, month = self._parse_month_header_legacy_compat(header_text, locale_hint=locale_hint)
            if year and month:
                return year, month, "legacy_regex"
            year, month = parse_month_year(header_text, locale=locale_hint)
            if year and month:
                return year, month, "legacy_regex_fallback_utility"
            return None, None, "legacy_regex"

        year, month = parse_month_year(header_text, locale=locale_hint)
        if year and month:
            return year, month, "parse_month_year_utility"
        return None, None, "parse_month_year_utility"

    def _parse_month_header_legacy_compat(
        self,
        header_text: str,
        *,
        locale_hint: str,
    ) -> Tuple[Optional[int], Optional[int]]:
        """Small regex parser used as a legacy-first compatibility path."""
        text = normalize_month_text(header_text or "")
        if not text:
            return (None, None)

        is_japanese = "ja" in str(locale_hint or "").lower()
        if is_japanese:
            m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text)
            if m:
                year = int(m.group(1))
                month = int(m.group(2))
                if 2000 <= year <= 2100 and 1 <= month <= 12:
                    return (year, month)

        m = re.search(r"(\d{4})[/\-](\d{1,2})", text)
        if m:
            year = int(m.group(1))
            month = int(m.group(2))
            if 2000 <= year <= 2100 and 1 <= month <= 12:
                return (year, month)

        return (None, None)

    def _verify_date_committed(
        self, ctx: CalendarContext, calendar_root: Any, opener_selector: str
    ) -> Tuple[bool, Dict]:
        """Verify date was actually committed after dialog close.

        Re-reads the date input field after calendar closes and checks if the target
        date is reflected in the field value.

        Args:
            ctx: Calendar context with target_month, target_day
            calendar_root: Calendar root element (for reading field if needed)
            opener_selector: Selector for date field/button to read value from

        Returns:
            (ok: bool, evidence: dict)
        """
        page = getattr(ctx.browser, "page", None)
        if page is None:
            return (True, {"verified": "best_effort_skip", "reason": "page_unavailable"})

        try:
            # Try to read committed value from the opener field
            if opener_selector:
                try:
                    field_elem = page.locator(opener_selector).first
                    if field_elem.is_visible(timeout=300):
                        # Try multiple ways to read the value
                        value = field_elem.get_attribute("value") or ""
                        if not value:
                            value = field_elem.text_content() or ""
                        if not value:
                            value = field_elem.get_attribute("aria-label") or ""

                        value = (value or "").strip()

                        # Fuzzy check: does value contain target month and day?
                        target_month_str = str(ctx.target_month).zfill(2)
                        target_day_str = str(ctx.target_day).zfill(2)

                        # Check various formats (YYYY-MM-DD, MM/DD/YYYY, DD-MM, etc)
                        check_patterns = [
                            target_month_str in value and target_day_str in value,
                            f"{ctx.target_month}" in value and f"{ctx.target_day}" in value,
                        ]

                        if any(check_patterns):
                            return (True, {
                                "verified": True,
                                "committed_value": value[:50],
                                "method": "field_read",
                                "calendar.verification_success": True,
                            })
                        else:
                            return (False, {
                                "verified": False,
                                "committed_value": value[:50],
                                "reason": "date_not_in_field",
                                "calendar.failure_stage": "verify",
                                "calendar.verification_failed": True,
                            })
                except Exception as exc:
                    log.debug("verify_date_committed.field_read_failed error=%s", str(exc)[:50])
                    # Continue to next verification method
                    pass

            # Fallback: best effort (assume success if we can't verify)
            return (True, {
                "verified": "best_effort_skip",
                "reason": "field_not_readable",
                "calendar.verification_skipped": True,
            })

        except Exception as exc:
            log.debug("verify_date_committed.failed error=%s", str(exc)[:50])
            return (False, {
                "verified": False,
                "exception": str(exc)[:50],
                "calendar.failure_stage": "verify",
                "calendar.verification_exception": True,
            })

    def _record_graph_stats(self, ctx: CalendarContext, strategy_id: str, result: StrategyResult) -> None:
        """Record strategy attempt to graph stats if enabled.

        Args:
            ctx: Calendar context
            strategy_id: Strategy identifier
            result: Strategy result
        """
        if ctx.graph_stats is None:
            return

        try:
            outcome = "ok" if result.ok else "soft_fail"
            reason_code = result.reason_code or ("success" if result.ok else "unknown")

            ctx.graph_stats.record_transition(
                run_id=getattr(ctx.browser, "run_id", "unknown"),
                attempt=0,
                turn=0,
                step_index=0,
                site="google_flights",  # TODO: could make this configurable
                page_kind="calendar_dialog",
                locale=ctx.locale_hint or "unknown",
                role=ctx.role,
                action="set_date",
                selector="calendar_driver",
                strategy_id=strategy_id,
                outcome=outcome,
                reason_code=reason_code,
                elapsed_ms=result.elapsed_ms,
            )
        except Exception as exc:
            log.debug("calendar_driver.graph_stats_record_failed error=%s", exc)

    def _try_capture_snapshot(
        self,
        ctx: CalendarContext,
        failure_reason_code: str,
        failure_stage: str,
        evidence: Dict[str, Any],
    ) -> None:
        """Attempt to capture a calendar state snapshot on failure.

        Captures HTML fragment, selector attempts, and metadata for KB authoring.
        Stores snapshot as JSON (and optional MD) in run artifacts directory.

        Args:
            ctx: Calendar context
            failure_reason_code: Reason code for failure
            failure_stage: Stage where failure occurred
            evidence: Evidence dictionary with selector/parsing details
        """
        try:
            runtime_cfg = _calendar_runtime_config()
            if not bool(runtime_cfg.get("calendar_snapshot_on_failure", True)):
                return
            snapshot_max_chars = int(runtime_cfg.get("calendar_snapshot_max_chars", 120000) or 120000)
            snapshot_max_chars = max(1000, snapshot_max_chars)
            snapshot_write_md = bool(runtime_cfg.get("calendar_snapshot_write_md", False))
            fragment_max_chars = max(1000, min(60000, snapshot_max_chars))

            # Get run_id from browser context (if available)
            run_id = getattr(ctx.browser, "run_id", "unknown")
            run_dir = Path("storage/runs") / run_id if run_id != "unknown" else None

            if run_dir is None or not run_dir.exists():
                log.debug("calendar_snapshot.skip_no_run_dir run_id=%s", run_id)
                return

            # Attempt to get HTML for snapshot
            html_content = ""
            html_source = "none"
            try:
                # Try to get HTML from browser
                if hasattr(ctx.browser, "content"):
                    html_content = ctx.browser.content()
                elif hasattr(ctx.browser, "page") and hasattr(ctx.browser.page, "content"):
                    html_content = ctx.browser.page.content()
            except Exception as e:
                log.debug("calendar_snapshot.html_fetch_failed error=%s", str(e)[:100])

            # If no HTML, try to read from scenario.last_html
            if not html_content:
                try:
                    last_html_file = Path("storage/runs") / run_id / "last_html.html"
                    if last_html_file.exists():
                        html_content = last_html_file.read_text(encoding="utf-8", errors="ignore")
                        html_source = "file"
                except Exception as e:
                    log.debug("calendar_snapshot.last_html_read_failed error=%s", str(e)[:100])

            # Extract dialog fragment if possible
            if html_content:
                fragment, source = extract_dialog_fragment(html_content, max_chars=fragment_max_chars)
                html_truncated, was_truncated = truncate_html(fragment, max_chars=snapshot_max_chars)
            else:
                html_truncated, was_truncated = "", False
                source = "none"

            # Extract month header candidate texts from evidence
            month_headers = []
            for key in evidence:
                if "month_header" in key.lower() or "header_text" in key.lower():
                    val = evidence.get(key)
                    if isinstance(val, str) and val:
                        month_headers.append(val)

            # Extract selector attempts from evidence
            attempts = []
            selectors_tried = evidence.get("selectors_tried", [])
            for i, selector in enumerate(selectors_tried):
                attempt = SelectorAttempt(
                    step=f"strategy_attempt_{i}",
                    selector=selector,
                    action="find",
                    outcome=evidence.get(f"selector_{i}_outcome", "unknown"),
                    visible=evidence.get(f"selector_{i}_visible", False),
                    enabled=evidence.get(f"selector_{i}_enabled", False),
                )
                attempts.append(attempt)

            # Extract month parsing result if available
            month_parse = None
            if evidence.get("calendar.header_parse_ok") is not None:
                month_parse = MonthParseResult(
                    ok=evidence.get("calendar.header_parse_ok", False),
                    year=evidence.get("calendar.parsed_year"),
                    month=evidence.get("calendar.parsed_month"),
                    source_text=evidence.get("calendar.header_text"),
                    parsing_method=evidence.get("calendar.parsing_method"),
                )

            # Build snapshot
            snapshot = CalendarSnapshot(
                run_id=run_id,
                site="google_flights",  # TODO: make configurable
                role=ctx.role,
                locale=ctx.locale_hint,
                target_date=ctx.target_date,
                strategy_used=evidence.get("calendar.strategy_id", "unknown"),
                failure_reason_code=failure_reason_code,
                failure_stage=failure_stage,
                month_header_texts=month_headers,
                month_parse=month_parse,
                selector_attempts=attempts,
                html_fragment=html_truncated,
                html_source=source,
                stdout_log_path=f"storage/runs/{run_id}/stdout.log" if run_id != "unknown" else None,
                run_dir_path=str(run_dir),
                last_html_path=f"storage/runs/{run_id}/last_html.html" if run_id != "unknown" else None,
            )

            # Write snapshot (JSON + optional MD)
            json_path, md_path = write_calendar_snapshot(
                snapshot=snapshot,
                run_dir=run_dir,
                include_md=snapshot_write_md,
            )

            # Attach snapshot path to evidence for propagation
            evidence["calendar.snapshot_path"] = str(json_path)
            evidence["calendar.snapshot_id"] = json_path.name

            log.info("calendar_snapshot.captured snapshot_id=%s role=%s reason=%s",
                    json_path.name, ctx.role, failure_reason_code)

        except Exception as exc:
            log.warning("calendar_snapshot.capture_failed error=%s", str(exc)[:200])
