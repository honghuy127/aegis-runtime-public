"""Calendar day selection helpers for Google Flights.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.browser import wall_clock_remaining_ms
from core.scenario.gf_helpers.date_tokens import (
    _google_date_display_tokens,
    _should_filter_date_token,
)
from core.scenario.gf_helpers.helpers import _dedupe_compact_selectors


def select_calendar_day_impl(
    *,
    calendar_root,
    target_date: str,
    target_year: int,
    target_month: int,
    target_day: int,
    role_key: str,
    parsing_method: Optional[str],
    nav_steps: int,
    parsed_year: Optional[int],
    parsed_month: Optional[int],
    logger,
    date_field_selector: str,
    budget,
    budget_used_start: int,
    deadline,
    budget_check,
    budgeted_timeout,
    try_date_input_fallback,
) -> Dict[str, Any]:
    """Click target day in calendar and return result dict."""
    # Step 4: Click target day (scoped to calendar root)
    if not budget_check(1):
        return {
            "ok": False,
            "reason": "budget_hit",
            "evidence": {"stage": "day_click", "nav_steps": nav_steps},
            "selector_used": date_field_selector,
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }

    day_clicked = False
    day_click_selectors = [
        f"[role='gridcell'][data-iso='{target_date}']:not([aria-disabled='true'])",
        f"[data-iso='{target_date}'][role='gridcell']:not([aria-disabled='true'])",
        f"[role='gridcell'][aria-label*='{target_year}年{target_month}月{target_day}日']:not([aria-disabled='true'])",
        f"[role='gridcell'][aria-label*='{target_month}月{target_day}日']:not([aria-disabled='true'])",
        f"[role='button'][aria-label*='{target_year}年{target_month}月{target_day}日']:not([aria-disabled='true'])",
        f"[role='button'][aria-label*='{target_month}月{target_day}日']:not([aria-disabled='true'])",
        f"button[aria-label*='{target_year}年{target_month}月{target_day}日']:not([aria-disabled='true'])",
        f"button[aria-label*='{target_month}月{target_day}日']:not([aria-disabled='true'])",
        f"div[aria-label*='{target_year}年{target_month}月{target_day}日']:not([aria-disabled='true'])",
        f"div[aria-label*='{target_month}月{target_day}日']:not([aria-disabled='true'])",
    ]
    for token in _google_date_display_tokens(target_date):
        if not token or _should_filter_date_token(token):
            continue
        day_click_selectors.extend(
            [
                f"[role='gridcell'][aria-label*='{token}']:not([aria-disabled='true'])",
                f"[role='button'][aria-label*='{token}']:not([aria-disabled='true'])",
                f"button[aria-label*='{token}']:not([aria-disabled='true'])",
                f"div[aria-label*='{token}']:not([aria-disabled='true'])",
            ]
        )
    day_click_selectors = _dedupe_compact_selectors(day_click_selectors, max_items=32)

    for day_sel in day_click_selectors:
        try:
            day_locator = calendar_root.locator(day_sel).first
            if day_locator.is_visible(timeout=200):
                day_locator.click(timeout=budgeted_timeout())
                day_clicked = True
                logger.info(
                    "gf_set_date.day_click.ok role=%s day=%d nav_steps=%d",
                    role_key,
                    target_day,
                    nav_steps,
                )
                time.sleep(0.15)
                break
        except Exception as exc:
            logger.debug(
                "gf_set_date.day_click.attempt_failed selector=%s error=%s",
                day_sel,
                str(exc)[:50],
            )
            continue

    if not day_clicked:
        typed_fallback_success = try_date_input_fallback("calendar_day_not_found")
        if typed_fallback_success:
            return typed_fallback_success
        logger.warning(
            "gf_set_date.day_click.fail role=%s target_day=%d/%d/%d",
            role_key,
            target_year,
            target_month,
            target_day,
        )
        return {
            "ok": False,
            "reason": "calendar_day_not_found",
            "evidence": {
                "calendar.target_date": target_date,
                "calendar.target_year": target_year,
                "calendar.target_month": target_month,
                "calendar.target_day": target_day,
                "calendar.nav_steps": nav_steps,
                "calendar.parsing_method": parsing_method or "unknown",
                "ui.day_selectors_tried": len(day_click_selectors),
                "calendar.month_parsed": (
                    f"{parsed_year}-{int(parsed_month):02d}"
                    if parsed_year is not None and parsed_month is not None
                    else None
                ),
                "calendar.day_parsed": target_day,
                "time.deadline_ms_remaining": wall_clock_remaining_ms(deadline) or -1,
                "budget.actions_used": budget.max_actions - budget.remaining - budget_used_start,
            },
            "selector_used": date_field_selector,
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }

    return {
        "ok": True,
        "day_clicked": True,
        "day_selectors_tried": len(day_click_selectors),
    }
