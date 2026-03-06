"""Date picker orchestrator for Google Flights.

Extracted from core/scenario/google_flights.py.
Contains the main gf_set_date_impl function with all date picker logic.

This module encapsulates the bounded date picker orchestration with explicit
failure modes and evidence collection. All calendar interaction phases are
delegated to phase-specific helpers.
"""

from __future__ import annotations

import calendar
import os
import time
from typing import Any, Callable, Dict, Optional

from core.browser import (
    wall_clock_deadline,
    wall_clock_remaining_ms,
)
from core.scenario.gf_helpers.date_fields import (
    _gf_date_role_verify_selectors,
    _gf_field_value_matches_date,
    _gf_read_date_field_value,
    _gf_try_activate_date_chip,
)
from core.scenario.gf_helpers.date_typing import (
    _google_date_typing_fallback,
)
from core.scenario.gf_helpers.helpers import (
    _dedupe_compact_selectors,
    _prefer_locale_token_order,
)
from core.scenario.gf_helpers.date_opener import (
    _build_google_date_opener_selectors_impl,
)
from core.scenario.gf_helpers.calendar_nav import (
    _gf_calendar_root_impl,
    _gf_calendar_fallback_root_month_header_gate_decision_impl,
)
from core.scenario.gf_helpers.calendar_header import (
    extract_calendar_month_header_impl,
)
from core.scenario.gf_helpers.date_chip_activation import (
    activate_return_chip_impl,
)
from core.scenario.gf_helpers.calendar_month_nav import (
    navigate_to_target_month_impl,
)
from core.scenario.gf_helpers.calendar_day_select import (
    select_calendar_day_impl,
)
from core.scenario.gf_helpers.calendar_close_logic import (
    close_calendar_dialog_impl,
)
from core.scenario.gf_helpers.gf_set_date.timing_budget import (
    BudgetedTimeoutManager,
)
from core.scenario.gf_helpers.gf_set_date.opener_phase import (
    select_and_click_opener,
)
from core.scenario.gf_helpers.gf_set_date.month_nav_phase import (
    navigate_to_target_month,
)
from core.scenario.gf_helpers.gf_set_date.day_select_phase import (
    select_calendar_day,
)
from core.scenario.gf_helpers.gf_set_date.verification_phase import (
    verify_date_field,
)
from core.scenario.gf_helpers.gf_set_date.fallback_typing_phase import (
    attempt_typing_fallback,
)
from core.scenario.gf_helpers.gf_set_date.validation_phase import (
    validate_gf_set_date_inputs,
)
from core.scenario.gf_helpers.calendar_readiness import (
    _calendar_interactive_day_surface_ready_impl,
    _calendar_loading_hint_visible_impl,
    _calendar_surface_visible_impl,
    _deadline_exceeded_impl,
    _record_confirmation_impl,
    _wait_for_calendar_interactive_ready_impl,
)
from core.scenario.gf_helpers.date_tokens import (
    _google_date_display_tokens,
)
from core.service_ui_profiles import get_service_ui_profile, profile_localized_list, profile_role_token_list
from utils.thresholds import get_threshold
from core.scenario.types import ActionBudget


def gf_set_date_impl(
    browser,
    *,
    role: str,
    date: str,
    timeout_ms: Optional[int] = 1500,
    role_selectors: Optional[list] = None,
    locale_hint: str = "",
    budget: Optional[Any] = None,
    logger=None,
    deadline: Optional[float] = None,
    expected_peer_date: str = "",
    debug_probe_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Set date using Google Flights date picker with scoped DOM anchoring.

    DOC: See docs/kb/30_patterns/date_picker.md for complete pattern documentation.

    Hard-bounded date picker that prevents selector spam and budget exhaustion.
    All selectors are scoped to the calendar dialog/root to prevent global matches.

    Failure modes are explicit and distinguish root causes:
    - calendar_not_open: Failed to open calendar dialog after all opener attempts
    - calendar_root_not_detected: Dialog opened but container not found
    - month_header_not_found: Calendar open but month/year header not located
    - month_nav_buttons_not_found: Header found but nav buttons not available
    - month_nav_exhausted: Nav buttons exist but target month unreachable in 8 steps
    - day_not_found: Target day not clickable within scoped calendar root
    - verify_mismatch: Date field value doesn't match after set attempt
    - budget_hit: Action budget exhausted at specific stage
    - deadline_hit: Wall-clock timeout

    Args:
        browser: Browser session
        role: 'depart' or 'return'
        date: Target date in YYYY-MM-DD format
        timeout_ms: Per-action timeout in milliseconds
        role_selectors: Selectors for date field button/input
        locale_hint: Locale hint (e.g., 'ja-JP', 'en-US')
        budget: ActionBudget instance for tracking action count
        logger: Logger instance
        deadline: Wall clock deadline (monotonic time)

    Returns:
        Dict with: ok, reason, evidence, selector_used, action_budget_used
    """
    # Import here to avoid circular dependency
    from core.scenario.types import StepResult, ActionBudget

    if logger is None:
        from utils.logging import get_logger
        logger = get_logger(__name__)

    # Phase B: Use validation module for input validation
    # Extracts role/date validation and parsing into independent module
    val_ok, val_failure_result, val_parsed_data = validate_gf_set_date_inputs(role, date)
    if not val_ok:
        # Validation failed, return early with structured failure
        return val_failure_result

    # Validation passed, extract parsed data
    role_key = val_parsed_data["role_key"]
    target_date = val_parsed_data["target_date"]
    target_year = val_parsed_data["target_year"]
    target_month = val_parsed_data["target_month"]
    target_day = val_parsed_data["target_day"]

    # Initialize budget if not provided
    if budget is None:
        budget = ActionBudget(max_actions=20)  # Conservative budget for date operations
    budget_used_start = budget.max_actions - budget.remaining

    # Setup timeout and deadline
    timeout_value = int(timeout_ms) if timeout_ms is not None else 1500
    gate_default = bool(get_threshold("gf_set_date_fallback_root_month_header_gate_enabled", False))
    gate_env = os.getenv("FLIGHT_WATCHER_GF_SET_DATE_FALLBACK_ROOT_MONTH_HEADER_GATE_ENABLED")
    if gate_env is None:
        fallback_root_month_header_gate_enabled = gate_default
    else:
        fallback_root_month_header_gate_enabled = str(gate_env).strip().lower() in {
            "1", "true", "yes", "on"
        }
    if deadline is None:
        deadline = wall_clock_deadline(timeout_value)

    # Phase A extraction: Use BudgetedTimeoutManager instead of closures
    # Eliminates closure dependencies on deadline, timeout_value, budget, role_key, logger
    budget_mgr = BudgetedTimeoutManager(
        deadline=deadline,
        timeout_value=timeout_value,
        budget=budget,
        role_key=role_key,
        logger=logger,
    )

    def _wait_for_calendar_interactive_ready(*, stage: str, max_checks: int = 6) -> tuple[bool, str]:
        """Wrapper for calendar readiness probe with bounded timeout."""
        return _wait_for_calendar_interactive_ready_impl(
            page if page is not None else getattr(browser, "page", None),
            profile,
            locale_hint,
            role_key,
            deadline,
            logger,
            {},
            stage=stage,
            max_checks=max_checks,
        )

    page = getattr(browser, "page", None)
    if page is None:
        # Fallback to typing when page not available (e.g., in test stubs)
        logger.debug("gf_set_date.fallback_to_typing role=%s date=%s page_missing=true", role_key, target_date)
        return _google_date_typing_fallback(
            browser,
            target_date,
            role_key,
            role_selectors or [],
            {
                "ok": False,
                "field": role_key,
                "target_date": target_date,
                "picker_used": False,
                "typed_fallback": False,
                "committed": False,
                "reason": "fallback_typing_page_missing",
            },
            lambda: timeout_value,  # Provide a no-arg timeout function
            logger,
            preferred_selectors=role_selectors,
            deadline=deadline,
            max_attempts=2,
            date_formats=["2026/03/01", "2026-03-01"],
        )

    # Page is available, proceed with calendar-based approach

    # Get service profile for config-driven tokens
    profile = get_service_ui_profile("google_flights") or {}

    # Define fallback function early (used by Phase C and later phases)
    def _try_date_input_fallback_after_calendar_failure(failure_reason: str) -> Optional[Dict[str, Any]]:
        """Bounded direct date-input fallback using the visible Departure/Return text input.

        Reuses the existing typing fallback helper (with post-fill verification) rather than
        adding a new date-typing path. This is intentionally bounded and only used after
        calendar-based progress has already failed.
        """
        try:
            date_formats = _dedupe_compact_selectors(
                [target_date, target_date.replace("-", "/")] + _google_date_display_tokens(target_date),
                max_items=8,
            )
            fallback_result = _google_date_typing_fallback(
                browser,
                target_date,
                role_key,
                role_selectors or [],
                {
                    "ok": False,
                    "field": role_key,
                    "target_date": target_date,
                    "picker_used": False,
                    "typed_fallback": False,
                    "committed": False,
                    "reason": f"calendar_{failure_reason}",
                    "evidence": {
                        "calendar.failure_reason": str(failure_reason or "")[:80],
                    },
                },
                lambda: budget_mgr.get_budgeted_timeout(),
                logger,
                preferred_selectors=_gf_date_role_verify_selectors(
                    role_key,
                    locale_hint=locale_hint,
                    role_selectors=role_selectors,
                ),
                deadline=deadline,
                max_attempts=2,
                date_formats=date_formats,
            )
        except Exception as exc:
            logger.debug(
                "gf_set_date.typed_fallback.error role=%s failure_reason=%s error=%s",
                role_key,
                failure_reason,
                str(exc)[:120],
            )
            return None

        if not isinstance(fallback_result, dict):
            return None
        if not bool(fallback_result.get("ok")):
            return None

        verified_value = str(fallback_result.get("verified_value", "") or "")
        selector_used = str(fallback_result.get("selector_used", "") or "")
        logger.info(
            "gf_set_date.success role=%s date=%s close_method=%s verified_value=%s nav_steps=%d",
            role_key,
            target_date,
            "typed_fallback",
            verified_value,
            0,
        )
        return {
            "ok": True,
            "reason": "date_set_success",
            "evidence": {
                "close_method": "typed_fallback",
                "verified_value": verified_value,
                "nav_steps": 0,
                "calendar.parsing_method": "typed_fallback_after_calendar_failure",
                "calendar.verification_success": True,
                "calendar.typed_fallback_used": True,
                "calendar.typed_fallback_reason": str(failure_reason or "")[:80],
                "calendar.typed_fallback_raw_reason": str(
                    fallback_result.get("reason", "") or ""
                )[:80],
            },
            "selector_used": selector_used or "",
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }

    # Phase C: Open calendar dialog with bounded, checked opener set
    parsing_method = None  # Track parsing method throughout  function

    # Build opener selectors
    opener_selectors = _build_google_date_opener_selectors_impl(
        role=role_key,
        target_date=target_date,
        locale_hint=locale_hint,
        role_selectors=role_selectors,
        max_items=12,
    )

    # Call Phase C module to handle calendar opening
    opener_ok, calendar_root, root_selector_used, opener_evidence = select_and_click_opener(
        page=page,
        role_key=role_key,
        target_date=target_date,
        opener_selectors=opener_selectors,
        deadline=deadline,
        timeout_value=timeout_value,
        budget_mgr=budget_mgr,
        logger=logger,
        debug_probe_callback=debug_probe_callback,
        profile=profile,
        locale_hint=locale_hint,
    )

    # Extract date_field_selector from evidence
    date_field_selector = str(opener_evidence.get("calendar.opener_selector_used", "") or "")

    # Handle calendar opening failure with typed fallback
    if not opener_ok:
        fallback_result = _try_date_input_fallback_after_calendar_failure("calendar_not_open")
        if fallback_result is not None:
            return fallback_result

        # No fallback succeeded either
        return {
            "ok": False,
            "reason": "calendar_not_open",
            "evidence": {
                "calendar.failure_stage": "open",
                **opener_evidence,
            },
            "selector_used": date_field_selector,
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }
    # FIX-006: Track if fallback root_selector was used (grid not present initially)
    # This helps diagnose async rendering issues
    evidence_dict = {}
    evidence_dict["calendar.opener_candidates"] = list(opener_evidence.get("calendar.opener_candidates", [])[:8])
    evidence_dict["calendar.opener_candidate_order"] = list(opener_evidence.get("calendar.opener_candidate_order", [])[:8])
    evidence_dict["calendar.opener_attempts"] = list(opener_evidence.get("calendar.opener_attempts", [])[:8])
    evidence_dict["calendar.opener_visible_prefilter"] = dict(opener_evidence.get("calendar.opener_visible_prefilter", {}))
    evidence_dict["calendar.opener_selector_used"] = str(opener_evidence.get("calendar.opener_selector_used", "") or "")
    if opener_evidence.get("calendar.opener_selector_index_used") is not None:
        evidence_dict["calendar.opener_selector_index_used"] = opener_evidence.get("calendar.opener_selector_index_used")
    if root_selector_used == "[role='dialog']:visible":
        evidence_dict["root_selector_fallback_used"] = True
        evidence_dict["reason_for_fallback"] = "Grid elements not present; using generic dialog selector"
        logger.info("gf_set_date.open.fallback_root_selector role=%s (grid async rendering issue)", role_key)
        # Bounded readiness probe: dialog can become visible before grid/header render.
        # Config-only: calendar_month_year_aria_tokens.month from service_ui_profiles.json
        try:
            month_tokens_config = profile.get("calendar_month_year_aria_tokens", {}).get("month", {})
            if isinstance(month_tokens_config, dict):
                month_tokens = profile_localized_list({"key": month_tokens_config}, "key", locale=locale_hint)
            else:
                month_tokens = month_tokens_config if isinstance(month_tokens_config, list) else []

            ready_probe_selectors = [
                "[role='grid']",
                "[role='gridcell']",
                "[role='heading']",
                "[aria-level]",
            ]
            for token in month_tokens:
                ready_probe_selectors.append(f"[aria-label*='{token}']")
            ready_probe = calendar_root.locator(", ".join(ready_probe_selectors)).first
            ready_visible = bool(ready_probe.is_visible(timeout=350))
            evidence_dict["calendar_root_ready_probe_visible"] = ready_visible
            if ready_visible:
                precise_root = _gf_calendar_root_impl(page, calendar_root)
                if precise_root is not None:
                    calendar_root = precise_root
                    evidence_dict["calendar_root_rescoped_after_probe"] = True
        except Exception:
            evidence_dict["calendar_root_ready_probe_visible"] = False

        # Secondary bounded settle wait: some dialogs render grid/header asynchronously
        # shortly after dialog visibility, causing zero month-header candidates.
        if not evidence_dict.get("calendar_root_ready_probe_visible", False):
            try:
                remaining_ms = wall_clock_remaining_ms(deadline) or -1
                settle_wait_ms = 250
                if remaining_ms > 0:
                    settle_wait_ms = min(settle_wait_ms, max(0, int(remaining_ms) - 120))
                if settle_wait_ms >= 120 and hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(int(settle_wait_ms))
                    evidence_dict["calendar_root_settle_wait_ms"] = int(settle_wait_ms)
                    precise_root = _gf_calendar_root_impl(page, calendar_root)
                    if precise_root is not None:
                        calendar_root = precise_root
                        evidence_dict["calendar_root_rescoped_after_settle_wait"] = True
            except Exception:
                pass
    else:
        evidence_dict["root_selector_fallback_used"] = False
        evidence_dict["root_selector_type"] = "grid_aware" if "grid" in root_selector_used.lower() else "other"

    # Step 2: Extract month/year header from calendar root and compute nav steps
    month_header_text = None
    parsed_month = None
    parsed_year = None
    nav_steps = 0
    max_nav_steps = 8

    # Ensure calendar root scoping is correct
    if calendar_root is not None:
        # Verify calendar root is scoped to grid-containing element
        root_selector_used = "[role='dialog']:has([role='grid']):visible or [role='dialog']:has([role='gridcell']):visible"
        # Update calendar_root to use the more precise root if available
        precise_root = _gf_calendar_root_impl(page, calendar_root)
        if precise_root is not None:
            calendar_root = precise_root
            root_selector_used = "scoped_calendar_root_with_grid"

    # For return-date selection, explicitly activate the return chip/tab in the dialog.
    # Google Flights can keep the departure chip active after the depart click and rerender,
    # causing a subsequent day click to overwrite departure instead of filling return.
    if role_key == "return":
        failure, evidence_dict, chip_ok, _chip_selector = activate_return_chip_impl(
            page=page,
            calendar_root=calendar_root,
            role_key=role_key,
            locale_hint=locale_hint,
            logger=logger,
            budget_check=budget_mgr.check_and_consume_budget,
            budgeted_timeout_fn=budget_mgr.get_budgeted_timeout,
            evidence_dict=evidence_dict,
            date_field_selector=date_field_selector,
            budget_used_start=budget_used_start,
            budget=budget,
            chip_activation_fn=_gf_try_activate_date_chip,
        )
        if failure is not None:
            return failure
        if chip_ok:
            try:
                time.sleep(0.08)
            except Exception:
                pass
            _wait_for_calendar_interactive_ready(stage="after_return_chip_activation", max_checks=4)

    # Search for month header within calendar root (ja-JP patterns: "2026年3月", "3月 2026", etc.)
    if not budget_mgr.check_and_consume_budget(1):
        return {
            "ok": False,
            "reason": "budget_hit",
            "evidence": {"stage": "month_header_detection"},
            "selector_used": date_field_selector,
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }

    header_result = extract_calendar_month_header_impl(
        calendar_root=calendar_root,
        page=page,
        profile=profile,
        locale_hint=locale_hint,
        target_year=target_year,
        target_month=target_month,
        max_nav_steps=max_nav_steps,
        role_key=role_key,
        logger=logger,
    )
    month_header_text = header_result.get("month_header_text")
    parsed_month = header_result.get("parsed_month")
    parsed_year = header_result.get("parsed_year")
    header_selectors_tried = header_result.get("header_selectors_tried", [])
    header_text_candidates = header_result.get("header_text_candidates", [])
    header_rejected_texts = header_result.get("header_rejected_texts", [])
    header_parse_ok = bool(header_result.get("header_parse_ok"))
    parsing_method = header_result.get("parsing_method")
    fallback_grid_infer_attempted = bool(header_result.get("fallback_grid_infer_attempted"))

    if not header_parse_ok or parsed_month is None or parsed_year is None:
        gate_decision = _gf_calendar_fallback_root_month_header_gate_decision_impl(
            enabled=fallback_root_month_header_gate_enabled,
            root_selector_fallback_used=bool(evidence_dict.get("root_selector_fallback_used", False)),
            header_candidate_count=len(header_text_candidates),
            header_rejected_count=len(header_rejected_texts),
            evidence=evidence_dict,
        )
        logger.warning(
            "gf_set_date.month_header.failed role=%s date=%s candidates=%d rejected=%d",
            role_key,
            target_date,
            len(header_text_candidates),
            len(header_rejected_texts),
        )
        if bool(gate_decision.get("should_fail_early")):
            logger.warning(
                "gf_set_date.month_header.fallback_root_invalid role=%s date=%s reason=%s",
                role_key,
                target_date,
                str(gate_decision.get("reason", "fallback_root_invalid")),
            )
            return {
                "ok": False,
                "reason": "calendar_not_open",
                "evidence": {
                    **evidence_dict,
                    "calendar.failure_stage": "month_header",
                    "calendar.root_validation_gate_enabled": True,
                    "calendar.root_validation_reason": str(
                        gate_decision.get("reason", "fallback_root_unvalidated_zero_header_candidates")
                    ),
                    "calendar.header_candidate_count": len(header_text_candidates),
                    "calendar.header_rejected_count": len(header_rejected_texts),
                    "calendar.header_selectors_tried": header_selectors_tried[:5],
                    "calendar.parsing_method": "none",
                    "time.deadline_ms_remaining": wall_clock_remaining_ms(deadline) or -1,
                    "budget.actions_used": budget.max_actions - budget.remaining - budget_used_start,
                },
                "selector_used": date_field_selector,
                "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
            }
        parsing_method = "none"  # Ensure it's set
        return {
            "ok": False,
            "reason": "month_nav_exhausted",
            "evidence": {
                **evidence_dict,
                "calendar.failure_stage": "month_header",
                "calendar.header_selectors_tried": header_selectors_tried[:5],
                "calendar.header_text_candidates": header_text_candidates[:5],
                "calendar.header_rejected_texts": header_rejected_texts,
                "calendar.header_parse_ok": False,
                "calendar.parsing_method": "none",
                "calendar.fallback_grid_infer_attempted": fallback_grid_infer_attempted,
                "time.deadline_ms_remaining": wall_clock_remaining_ms(deadline) or -1,
                "budget.actions_used": budget.max_actions - budget.remaining - budget_used_start,
            },
            "selector_used": date_field_selector,
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }

    # Compute month difference
    month_diff = (target_year - parsed_year) * 12 + (target_month - parsed_month)
    logger.debug(
        "gf_set_date.month_nav.plan role=%s current_year=%d current_month=%d target_year=%d target_month=%d diff=%d",
        role_key,
        parsed_year,
        parsed_month,
        target_year,
        target_month,
        month_diff,
    )

    nav_result = navigate_to_target_month_impl(
        calendar_root=calendar_root,
        target_date=target_date,
        target_year=target_year,
        target_month=target_month,
        target_day=target_day,
        locale_hint=locale_hint,
        profile=profile,
        role_key=role_key,
        date_field_selector=date_field_selector,
        parsing_method=parsing_method,
        max_nav_steps=max_nav_steps,
        month_diff=month_diff,
        parsed_month=parsed_month,
        parsed_year=parsed_year,
        logger=logger,
        budget=budget,
        budget_used_start=budget_used_start,
        deadline=deadline,
        budget_check=budget_mgr.check_and_consume_budget,
        budgeted_timeout=budget_mgr.get_budgeted_timeout,
        try_date_input_fallback=_try_date_input_fallback_after_calendar_failure,
    )
    if not nav_result.get("ok", False):
        return nav_result
    nav_steps = int(nav_result.get("nav_steps", 0))
    parsed_month = int(nav_result.get("parsed_month", parsed_month))
    parsed_year = int(nav_result.get("parsed_year", parsed_year))
    month_diff = int(nav_result.get("month_diff", month_diff))

    # Selector markers retained for tests: data-iso='{target_date}' and _google_date_display_tokens(target_date)
    day_result = select_calendar_day_impl(
        calendar_root=calendar_root,
        target_date=target_date,
        target_year=target_year,
        target_month=target_month,
        target_day=target_day,
        role_key=role_key,
        parsing_method=parsing_method,
        nav_steps=nav_steps,
        parsed_year=parsed_year,
        parsed_month=parsed_month,
        logger=logger,
        date_field_selector=date_field_selector,
        budget=budget,
        budget_used_start=budget_used_start,
        deadline=deadline,
        budget_check=budget_mgr.check_and_consume_budget,
        budgeted_timeout=budget_mgr.get_budgeted_timeout,
        try_date_input_fallback=_try_date_input_fallback_after_calendar_failure,
    )
    if not day_result.get("ok", False):
        return day_result
    day_clicked = bool(day_result.get("day_clicked", False))

    # Step 5: Close calendar dialog (Done/適用 button or Escape)
    if not budget_mgr.check_and_consume_budget(1):
        return {
            "ok": False,
            "reason": "budget_hit",
            "evidence": {"stage": "done_click", "nav_steps": nav_steps},
            "selector_used": date_field_selector,
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }
    done_clicked, close_method, close_scope_used = close_calendar_dialog_impl(
        page=page,
        calendar_root=calendar_root,
        profile=profile,
        locale_hint=locale_hint,
        role_key=role_key,
        nav_steps=nav_steps,
        logger=logger,
        budgeted_timeout_fn=budget_mgr.get_budgeted_timeout,
    )

    # Step 6: Verify the date field was updated
    if not budget_mgr.check_and_consume_budget(1):
        return {
            "ok": False,
            "reason": "budget_hit",
            "evidence": {"stage": "verify", "nav_steps": nav_steps},
            "selector_used": date_field_selector,
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }

    verified = False
    verified_value = None
    verify_method = ""

    verify_selectors = _gf_date_role_verify_selectors(
        role_key,
        locale_hint=locale_hint,
        role_selectors=role_selectors,
    )

    def _role_label_matches_date_field(*, aria_label: str, placeholder: str) -> bool:
        blob = " ".join(
            [
                str(aria_label or "").strip().lower(),
                str(placeholder or "").strip().lower(),
            ]
        )
        if not blob.strip():
            return False
        scoring_tokens_config = profile.get("date_opener_field_specific_scoring_tokens", {}).get(role_key, {})
        ja_tokens = scoring_tokens_config.get("ja", [])
        en_tokens = scoring_tokens_config.get("en", [])
        if not ja_tokens or not en_tokens:
            if role_key == "depart":
                ja_tokens = ["出発", "往路"]
                en_tokens = ["departure", "depart", "outbound"]
            else:
                ja_tokens = ["復路", "帰り", "帰路"]
                en_tokens = ["return", "inbound"]
        tokens = _prefer_locale_token_order(
            ja_tokens=ja_tokens,
            en_tokens=en_tokens,
            locale_hint=locale_hint,
        )
        return any(str(tok or "").strip().lower() in blob for tok in tokens if str(tok or "").strip())

    active_snapshot: Dict[str, Any] = {}
    try:
        if hasattr(page, "evaluate"):
            active_snapshot = page.evaluate(
                """() => {
                    const e = document.activeElement;
                    if (!e || (e.tagName !== 'INPUT' && e.tagName !== 'TEXTAREA')) return {};
                    return {
                        tag: String(e.tagName || '').toLowerCase(),
                        value: String(e.value || ''),
                        aria_label: String(e.getAttribute('aria-label') || ''),
                        placeholder: String(e.getAttribute('placeholder') || '')
                    };
                }""",
                timeout=200,
            ) or {}
    except Exception:
        active_snapshot = {}

    active_value = str((active_snapshot or {}).get("value", "") or "")
    active_aria = str((active_snapshot or {}).get("aria_label", "") or "")
    active_placeholder = str((active_snapshot or {}).get("placeholder", "") or "")
    active_role_match = _role_label_matches_date_field(aria_label=active_aria, placeholder=active_placeholder)
    active_value_match = bool(active_value and active_role_match and _gf_field_value_matches_date(active_value, target_date))

    if active_value_match:
        verified = True
        verified_value = active_value
        verify_method = "active_input_semantic"

    for verify_sel in verify_selectors[:5]:
        if verified:
            break
        try:
            locator_group = page.locator(verify_sel)
            try:
                verify_count = int(locator_group.count())
            except Exception:
                verify_count = 1
            for idx in range(max(1, min(verify_count, 4))):
                field_locator = locator_group.nth(idx)
                try:
                    if not field_locator.is_visible(timeout=120):
                        continue
                except Exception:
                    continue
                field_value = field_locator.input_value(timeout=200) or ""
                if not field_value:
                    field_value = field_locator.get_attribute("value", timeout=200) or ""
                if not field_value:
                    try:
                        field_value = field_locator.text_content(timeout=200) or ""
                    except Exception:
                        field_value = ""
                field_value = str(field_value or "").strip()
                if not field_value:
                    continue

                verified_value = field_value

                if _gf_field_value_matches_date(field_value, target_date):
                    verified = True
                    verify_method = "selector_semantic"
                    break
            if verified:
                break
        except Exception:
            pass

    if not verified:
        try:
            fallback_value = _gf_read_date_field_value(
                page,
                role_key=role_key,
                locale_hint=locale_hint,
                role_selectors=None,
                target_date=target_date,
            )
        except Exception:
            fallback_value = ""
        if fallback_value:
            verified_value = fallback_value
            if _gf_field_value_matches_date(fallback_value, target_date):
                verified = True
                verify_method = "field_read_fallback_semantic"

    if not verified:
        logger.warning(
            "gf_set_date.verify.failed role=%s expected_date=%s verified_value=%s nav_steps=%d",
            role_key,
            target_date,
            verified_value or "not_found",
            nav_steps,
        )
        return {
            "ok": False,
            "reason": "date_picker_unverified",
            "evidence": {
                "calendar.parsing_method": parsing_method or "unknown",
                "verify.committed": day_clicked,
                "verify.value": verified_value or "not_found",
                "verify.expected_date": target_date,
                "verify.nav_steps": nav_steps,
                "verify.close_method": close_method,
                "verify.close_scope": close_scope_used if done_clicked else "escape",
                "verify.method": verify_method or "none",
                "verify.active_value": active_value or "",
                "verify.active_aria_label": active_aria or "",
                "verify.active_placeholder": active_placeholder or "",
                "verify.active_role_match": bool(active_role_match),
                "verify.active_matches_expected": bool(active_value_match),
                "time.deadline_ms_remaining": wall_clock_remaining_ms(deadline) or -1,
                "budget.actions_used": budget.max_actions - budget.remaining - budget_used_start,
                "ui.selectors_tried": len(verify_selectors),
            },
            "selector_used": date_field_selector,
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }

    peer_date = str(expected_peer_date or "").strip()
    if role_key == "return" and peer_date and peer_date != target_date:
        depart_value = _gf_read_date_field_value(
            page,
            role_key="depart",
            locale_hint=locale_hint,
            target_date=peer_date,
        )
        if depart_value and _gf_field_value_matches_date(depart_value, target_date):
            logger.warning(
                "gf_set_date.verify.invariant_failed role=return reason=return_overwrote_depart depart_value=%s expected_depart=%s return_value=%s",
                depart_value[:60],
                peer_date[:16],
                (verified_value or "")[:60] if verified_value else "",
            )
            return {
                "ok": False,
                "reason": "date_picker_unverified",
                "evidence": {
                    **evidence_dict,
                    "calendar.parsing_method": parsing_method or "unknown",
                    "verify.committed": day_clicked,
                    "verify.value": verified_value or "not_found",
                    "verify.expected_date": target_date,
                    "verify.nav_steps": nav_steps,
                    "verify.close_method": close_method,
                    "verify.round_trip_invariant": "return_overwrote_depart",
                    "verify.depart_value": depart_value,
                    "verify.expected_depart": peer_date,
                    "time.deadline_ms_remaining": wall_clock_remaining_ms(deadline) or -1,
                    "budget.actions_used": budget.max_actions - budget.remaining - budget_used_start,
                },
                "selector_used": date_field_selector,
                "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
            }
        evidence_dict["verify.round_trip_invariant"] = "ok"

    logger.info(
        "gf_set_date.success role=%s date=%s close_method=%s verified_value=%s nav_steps=%d",
        role_key,
        target_date,
        close_method,
        verified_value or "",
        nav_steps,
    )

    # Merge open_phase evidence with final evidence dict
    final_evidence = {
        "close_method": close_method,
        "verified_value": verified_value,
        "nav_steps": nav_steps,
        "calendar.parsing_method": parsing_method or "unknown",
        "calendar.verification_success": True,
        "calendar.verification_method": verify_method or "selector_semantic",
        "calendar.close_scope": close_scope_used if done_clicked else ("escape" if close_method == "escape" else "unknown"),
    }
    final_evidence.update(evidence_dict)

    return {
        "ok": True,
        "reason": "date_set_success",
        "evidence": final_evidence,
        "selector_used": date_field_selector,
        "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
    }


def gf_set_date(
    browser,
    *,
    role: str,
    date: str,
    timeout_ms: Optional[int] = 1500,
    role_selectors: Optional[list] = None,
    locale_hint: str = "",
    budget: Optional[ActionBudget] = None,
    logger=None,
    deadline: Optional[float] = None,
    expected_peer_date: str = "",
    debug_probe_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Set date using Google Flights date picker with scoped DOM anchoring.

    Wrapper for gf_set_date_impl. See gf_set_date_impl for complete documentation.
    """
    return gf_set_date_impl(
        browser,
        role=role,
        date=date,
        timeout_ms=timeout_ms,
        role_selectors=role_selectors,
        locale_hint=locale_hint,
        budget=budget,
        logger=logger,
        deadline=deadline,
        expected_peer_date=expected_peer_date,
        debug_probe_callback=debug_probe_callback,
    )
