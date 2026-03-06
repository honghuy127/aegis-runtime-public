"""Google Flights date picker flow.

Move-only extraction from core/scenario/google_flights.py.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.browser import wall_clock_deadline
from core.scenario.gf_helpers.calendar_readiness import (
    _calendar_interactive_day_surface_ready_impl,
    _calendar_loading_hint_visible_impl,
    _calendar_surface_visible_impl,
    _deadline_exceeded_impl,
    _record_confirmation_impl,
    _wait_for_calendar_interactive_ready_impl,
)
from core.scenario.gf_helpers.date_fields import (
    _gf_date_role_verify_selectors,
    _gf_field_value_matches_date,
    _gf_read_date_field_value,
    _gf_try_activate_date_chip,
)
from core.scenario.gf_helpers.date_opener import _build_google_date_opener_selectors_impl
from core.scenario.gf_helpers.date_tokens import _google_date_display_tokens
from core.scenario.gf_helpers.helpers import _dedupe_compact_selectors
from core.service_ui_profiles import get_service_ui_profile, profile_localized_list

from .commit_phase import build_legacy_done_selectors, click_done_or_apply, commit_depart_date
from .day_select_phase import (
    build_depart_day_selectors,
    build_return_day_selectors,
    click_depart_day,
    click_return_day,
)
from .fallback_typing_phase import attempt_typing_fallback
from .month_nav_phase import navigate_depart_months, navigate_return_months
from .opener_phase import open_depart_calendar
from .timeout import get_budgeted_timeout
from .verify_phase import verify_date_field_value


def google_fill_date_via_picker(
    browser,
    *,
    role: str,
    value: str,
    timeout_ms: Optional[int],
    role_selectors,
    locale_hint: str = "",
    logger,
    deadline: Optional[float] = None,
    expected_peer_date: str = "",
) -> Dict[str, Any]:
    """Fill date using Google Flights date picker with calendar selection.

    Strategy:
    1. Click date field to open calendar
    2. Find and click the exact date element in calendar
    3. For round trip return date: repeat for second date
    4. Click "Done/完了" button if present
    5. Fallback to typing if picker selection fails (with validation)

    Args:
        browser: Browser session
        role: Field role ('depart', 'return')
        value: Date string in YYYY-MM-DD format
        timeout_ms: Timeout in milliseconds
        role_selectors: Selectors for the date field button/input
        locale_hint: Locale for date formatting (e.g., "ja-JP", "en-US")
        logger: Logger instance
        deadline: Wall clock deadline

    Returns:
        Dict with keys: ok, picker_used, typed_fallback, committed, reason
    """
    role_key = str(role or "").strip().lower()
    target_date = str(value or "").strip()

    result: Dict[str, Any] = {
        "ok": False,
        "field": role_key,
        "target_date": target_date,
        "picker_used": False,
        "typed_fallback": False,
        "committed": False,
        "date_commit_verified": False,
        "selector_used": "",
        "reason": "not_attempted",
        "action_confirmations": [],
    }

    if role_key not in {"depart", "return"}:
        result["reason"] = "unsupported_role"
        return result
    if not target_date:
        result["reason"] = "empty_value"
        return result

    # Parse date for calendar selection
    try:
        from datetime import datetime

        date_obj = datetime.strptime(target_date, "%Y-%m-%d")
        year = date_obj.year
        month = date_obj.month
        day = date_obj.day
    except Exception:
        result["reason"] = "invalid_date_format"
        return result

    timeout_value = int(timeout_ms) if timeout_ms is not None else 1200
    deadline = deadline or wall_clock_deadline(timeout_value)
    local_deadline = float(deadline) if deadline is not None else time.monotonic() + (timeout_value / 1000.0)

    def _budgeted_timeout() -> int:
        return get_budgeted_timeout(deadline, timeout_value)

    page = getattr(browser, "page", None)

    # Get service profile for config-driven tokens
    profile = get_service_ui_profile("google_flights") or {}

    # Wrapper functions delegating to calendar_readiness implementations
    def _deadline_exceeded(stage: str) -> bool:
        return _deadline_exceeded_impl(stage, local_deadline, logger, result)

    def _record_confirmation(stage: str, ok: bool, **meta) -> None:
        return _record_confirmation_impl(stage, ok, logger, result, **meta)

    def _calendar_surface_visible() -> bool:
        return _calendar_surface_visible_impl(page)

    def _calendar_loading_hint_visible() -> bool:
        return _calendar_loading_hint_visible_impl(page, profile)

    def _calendar_interactive_day_surface_ready() -> bool:
        return _calendar_interactive_day_surface_ready_impl(page, profile, locale_hint)

    def _wait_for_calendar_interactive_ready(*, stage: str, max_checks: int = 6) -> tuple[bool, str]:
        return _wait_for_calendar_interactive_ready_impl(
            page, profile, locale_hint, role_key, local_deadline, logger, result,
            stage=stage, max_checks=max_checks
        )

    if role_key == "depart":
        # HARD CAP: bounded depart-date handling with local deadline guard to prevent selector spam.
        max_open_attempts = 3
        max_month_nav = 8
        max_day_click_attempts = 3
        max_typed_fallback_attempts = 2

        open_selectors = _build_google_date_opener_selectors_impl(
            role="depart",
            target_date=target_date,
            locale_hint=locale_hint,
            role_selectors=role_selectors,
            max_items=10,
        )
        calendar_surface_selectors = [
            "[role='dialog']",
            "[role='grid']",
            "[class*='calendar']",
        ]
        month_nav_config = profile.get("calendar_month_nav_button_labels", {})
        next_labels_config = month_nav_config.get("next", {})
        if isinstance(next_labels_config, dict):
            next_labels = profile_localized_list({"key": next_labels_config}, "key", locale=locale_hint)
        else:
            next_labels = next_labels_config if isinstance(next_labels_config, list) else []
        if not next_labels:
            next_labels = ["Next", "Next month"]

        next_month_selectors = []
        for label in next_labels:
            next_month_selectors.extend(
                [
                    f"[aria-label*='{label}']",
                    f"button[aria-label*='{label}']",
                    f"[role='button'][aria-label*='{label}']",
                ]
            )

        day_selectors = build_depart_day_selectors(target_date)

        done_selectors = build_legacy_done_selectors(profile)

        depart_fallback_selectors = _gf_date_role_verify_selectors(
            "depart",
            locale_hint=locale_hint,
            role_selectors=role_selectors,
        )
        preferred_depart_selectors = list(depart_fallback_selectors or [])
        if preferred_depart_selectors:
            preferred_depart_selectors = preferred_depart_selectors[:6]
        if ":focus" not in preferred_depart_selectors:
            preferred_depart_selectors.append(":focus")

        calendar_open, open_selector_used = open_depart_calendar(
            browser,
            page,
            open_selectors=open_selectors,
            calendar_surface_selectors=calendar_surface_selectors,
            max_open_attempts=max_open_attempts,
            deadline_exceeded=_deadline_exceeded,
            budgeted_timeout_fn=_budgeted_timeout,
            logger=logger,
            result=result,
        )

        if not calendar_open:
            logger.warning(
                "gf.date.open.fail selector=%s attempts=%d",
                open_selector_used or "",
                max_open_attempts,
            )
            return attempt_typing_fallback(
                browser,
                target_date=target_date,
                role_key=role_key,
                role_selectors=role_selectors,
                result=result,
                timeout_fn=_budgeted_timeout,
                logger=logger,
                preferred_selectors=preferred_depart_selectors,
                deadline=local_deadline,
                max_attempts=max_typed_fallback_attempts,
                date_formats=["2026/03/01", "2026-03-01"],
            )

        _record_confirmation("open_calendar", calendar_open, selector=open_selector_used or "")

        nav_steps = navigate_depart_months(
            page,
            day_selectors=day_selectors,
            next_month_selectors=next_month_selectors,
            max_month_nav=max_month_nav,
            deadline_exceeded=_deadline_exceeded,
            budgeted_timeout_fn=_budgeted_timeout,
            logger=logger,
        )
        if nav_steps is None:
            return result

        day_clicked = click_depart_day(
            page,
            day_selectors=day_selectors,
            max_day_click_attempts=max_day_click_attempts,
            deadline_exceeded=_deadline_exceeded,
            budgeted_timeout_fn=_budgeted_timeout,
            logger=logger,
            result=result,
        )
        if day_clicked is None:
            return result

        if not day_clicked:
            logger.warning("gf.date.day.fail selector=%s", day_selectors[0])
            return attempt_typing_fallback(
                browser,
                target_date=target_date,
                role_key=role_key,
                role_selectors=role_selectors,
                result=result,
                timeout_fn=_budgeted_timeout,
                logger=logger,
                preferred_selectors=preferred_depart_selectors,
                deadline=local_deadline,
                max_attempts=max_typed_fallback_attempts,
                date_formats=["2026/03/01", "2026-03-01"],
            )

        done_clicked, commit_method, deadline_hit = commit_depart_date(
            page,
            done_selectors=done_selectors,
            deadline_exceeded=_deadline_exceeded,
            budgeted_timeout_fn=_budgeted_timeout,
            logger=logger,
        )
        if deadline_hit:
            return result

        if _deadline_exceeded("verify"):
            return result
        verified, verified_value = verify_date_field_value(
            page,
            role_key=role_key,
            target_date=target_date,
            locale_hint=locale_hint,
            role_selectors=role_selectors,
        )
        _record_confirmation(
            "verify_date_value",
            verified,
            role=role_key,
            close_method=commit_method,
            calendar_open=_calendar_surface_visible(),
        )

        logger.info("gf.date.verify %s value=%s", "ok" if verified else "failed", verified_value or "")
        result["committed"] = verified
        result["ok"] = verified
        result["reason"] = "date_picker_success" if verified else "date_picker_unverified"
        result["date_commit_verified"] = verified
        return result

    # Step 1: Click date field to open calendar
    date_field_clicked = False
    for selector in (role_selectors or []):
        try:
            browser.click(selector, timeout_ms=_budgeted_timeout())
            date_field_clicked = True
            result["selector_used"] = selector
            time.sleep(0.2)
            break
        except Exception:
            continue

    if not date_field_clicked:
        result["reason"] = "date_field_click_failed"
        logger.warning(
            "scenario.google_date_picker.failed role=%s date=%s reason=date_field_click_failed",
            role_key,
            target_date,
        )
        return attempt_typing_fallback(
            browser,
            target_date=target_date,
            role_key=role_key,
            role_selectors=role_selectors,
            result=result,
            timeout_fn=_budgeted_timeout,
            logger=logger,
        )

    calendar_ready, calendar_ready_reason = _wait_for_calendar_interactive_ready(stage="after_field_click")
    result.setdefault("evidence", {})["calendar_interactive_ready_after_open"] = bool(calendar_ready)
    if not calendar_ready and calendar_ready_reason not in {"not_interactive", "loading_persisted"}:
        logger.info(
            "scenario.google_date_picker.calendar_ready_wait role=%s ok=%s reason=%s",
            role_key,
            calendar_ready,
            calendar_ready_reason,
        )

    if role_key == "return":
        try:
            chip_ok, chip_selector = _gf_try_activate_date_chip(
                page,
                role_key="return",
                locale_hint=locale_hint,
                timeout_ms=min(300, _budgeted_timeout()),
            )
        except Exception:
            chip_ok, chip_selector = False, ""
        _record_confirmation(
            "activate_return_chip",
            chip_ok,
            selector=(chip_selector[:80] if chip_selector else ""),
        )
        if chip_ok:
            try:
                time.sleep(0.08)
            except Exception:
                pass
            _wait_for_calendar_interactive_ready(stage="after_return_chip_activation", max_checks=4)

    date_selectors = build_return_day_selectors(
        locale_hint=locale_hint,
        year=year,
        month=month,
        day=day,
    )

    month_nav_attempts = 0
    max_month_nav_attempts = 24

    month_nav_config = profile.get("calendar_month_nav_button_labels", {})
    next_labels_config = month_nav_config.get("next", {})
    prev_labels_config = month_nav_config.get("prev", {})
    if isinstance(next_labels_config, dict):
        next_labels = profile_localized_list({"key": next_labels_config}, "key", locale=locale_hint)
    else:
        next_labels = next_labels_config if isinstance(next_labels_config, list) else []
    if isinstance(prev_labels_config, dict):
        prev_labels = profile_localized_list({"key": prev_labels_config}, "key", locale=locale_hint)
    else:
        prev_labels = prev_labels_config if isinstance(prev_labels_config, list) else []
    if not next_labels:
        next_labels = ["Next", "Next month"]
    if not prev_labels:
        prev_labels = ["Previous", "Previous month"]

    next_month_selectors = [
        "[aria-label*='Next'][aria-label*='month']",
        "[role='button'][title*='Next']",
    ]
    for label in next_labels:
        next_month_selectors.extend(
            [
                f"[aria-label*='{label}']",
                f"button[aria-label*='{label}']",
                f"[role='button'][aria-label*='{label}']",
            ]
        )
    prev_month_selectors = [
        "[aria-label*='Previous'][aria-label*='month']",
        "[role='button'][title*='Previous']",
    ]
    for label in prev_labels:
        prev_month_selectors.extend(
            [
                f"[aria-label*='{label}']",
                f"button[aria-label*='{label}']",
                f"[role='button'][aria-label*='{label}']",
            ]
        )

    month_nav_attempts, _current_month_log = navigate_return_months(
        page,
        date_selectors=date_selectors,
        next_month_selectors=next_month_selectors,
        prev_month_selectors=prev_month_selectors,
        max_month_nav_attempts=max_month_nav_attempts,
        budgeted_timeout_fn=_budgeted_timeout,
        logger=logger,
        wait_for_calendar_interactive_ready=_wait_for_calendar_interactive_ready,
    )

    date_clicked, click_method = click_return_day(
        page,
        date_selectors=date_selectors,
        budgeted_timeout_fn=_budgeted_timeout,
        logger=logger,
        result=result,
    )

    if not date_clicked:
        result["reason"] = "calendar_day_not_found"
        logger.warning(
            "scenario.google_date_picker.failed role=%s date=%s reason=calendar_day_not_found month_nav_attempts=%d",
            role_key,
            target_date,
            month_nav_attempts,
        )
        return attempt_typing_fallback(
            browser,
            target_date=target_date,
            role_key=role_key,
            role_selectors=role_selectors,
            result=result,
            timeout_fn=_budgeted_timeout,
            logger=logger,
        )

    _record_confirmation("day_click", True, role=role_key, calendar_open=_calendar_surface_visible())

    _wait_for_calendar_interactive_ready(stage="after_day_click", max_checks=4)

    done_clicked = False
    done_selector_used = ""
    picker_open_before_commit = _calendar_surface_visible()
    if picker_open_before_commit:
        _record_confirmation("picker_open_after_day_click", True, role=role_key)

    if _deadline_exceeded("commit"):
        return result

    if picker_open_before_commit or role_key == "return":
        try:
            done_clicked, done_selector_used = click_done_or_apply(
                page,
                locale_hint=locale_hint,
                budgeted_timeout_fn=_budgeted_timeout,
                profile=profile,
            )
            if done_clicked:
                click_method = "calendar_click_with_done"
                time.sleep(0.1)
        except Exception:
            done_clicked = False
            done_selector_used = ""

    if not done_clicked and _calendar_surface_visible():
        try:
            if page is not None and hasattr(page, "keyboard"):
                page.keyboard.press("Enter")
                click_method = click_method or "calendar_click_then_enter"
                time.sleep(0.08)
        except Exception:
            try:
                if page is not None and hasattr(page, "keyboard"):
                    page.keyboard.press("Escape")
                    click_method = click_method or "calendar_click_then_escape"
                    time.sleep(0.08)
            except Exception:
                pass

    picker_open_after_commit = _calendar_surface_visible()
    _record_confirmation(
        "commit_ui_close",
        not picker_open_after_commit,
        role=role_key,
        done_clicked=done_clicked,
        done_selector=done_selector_used or "",
        method=click_method or "calendar_click",
    )

    if _deadline_exceeded("verify"):
        return result
    verified, verified_value = verify_date_field_value(
        page,
        role_key=role_key,
        target_date=target_date,
        locale_hint=locale_hint,
        role_selectors=role_selectors,
    )
    result["date_done_clicked"] = done_clicked
    result["date_commit_verified"] = bool(verified and (done_clicked or not picker_open_after_commit))
    _record_confirmation(
        "verify_date_value",
        verified,
        role=role_key,
        verified_value=(verified_value[:40] if verified_value else ""),
    )

    if not verified or (role_key == "return" and picker_open_after_commit and not done_clicked):
        result["committed"] = False
        result["ok"] = False
        result["reason"] = "date_picker_unverified"
        logger.warning(
            "scenario.google_date_picker.verify_failed role=%s date=%s verified=%s picker_open_after_commit=%s done_clicked=%s value=%s",
            role_key,
            target_date,
            verified,
            picker_open_after_commit,
            done_clicked,
            (verified_value or "")[:60],
        )
        return result

    peer_date = str(expected_peer_date or "").strip()
    if role_key == "return" and peer_date and peer_date != target_date:
        depart_value = _gf_read_date_field_value(page, role_key="depart", locale_hint=locale_hint)
        if depart_value and _gf_field_value_matches_date(depart_value, target_date):
            result.setdefault("evidence", {})["verify.round_trip_invariant"] = "return_overwrote_depart"
            result["evidence"]["verify.depart_value"] = depart_value[:60]
            result["evidence"]["verify.expected_depart"] = peer_date
            result["evidence"]["verify.return_value"] = (verified_value or "")[:60]
            result["committed"] = False
            result["ok"] = False
            result["reason"] = "date_picker_unverified"
            _record_confirmation(
                "round_trip_invariant",
                False,
                role=role_key,
                depart_value=(depart_value[:40] if depart_value else ""),
                expected_depart=peer_date,
            )
            logger.warning(
                "scenario.google_date_picker.invariant_failed role=return reason=return_overwrote_depart depart_value=%s expected_depart=%s return_value=%s",
                depart_value[:60],
                peer_date[:16],
                (verified_value or "")[:60],
            )
            return result
        _record_confirmation(
            "round_trip_invariant",
            True,
            role=role_key,
            expected_depart=peer_date,
        )

    result["committed"] = True
    result["ok"] = True
    result["reason"] = "date_picker_success"

    logger.info(
        "scenario.google_date_picker.success role=%s date=%s method=%s done_clicked=%s verified=%s",
        role_key,
        target_date,
        click_method or "calendar_click",
        done_clicked,
        verified,
    )
    logger.info(
        "scenario.google_date_picker role=%s date=%s picker_used=%s committed=%s",
        role_key,
        target_date,
        result["picker_used"],
        result["committed"],
    )

    return result
