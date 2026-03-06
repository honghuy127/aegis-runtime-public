"""Calendar month navigation helpers for Google Flights.

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


def navigate_to_target_month_impl(
    *,
    calendar_root,
    target_date: str,
    target_year: int,
    target_month: int,
    target_day: int,
    locale_hint: str,
    profile: Dict[str, Any],
    role_key: str,
    date_field_selector: str,
    parsing_method: Optional[str],
    max_nav_steps: int,
    month_diff: int,
    parsed_month: int,
    parsed_year: int,
    logger,
    budget,
    budget_used_start: int,
    deadline,
    budget_check,
    budgeted_timeout,
    try_date_input_fallback,
) -> Dict[str, Any]:
    """Navigate calendar to target month using next/prev controls.

    Returns dict with ok flag, nav steps, and updated parsed month/year. On failure,
    returns the same structure as gf_set_date failures (ok False + reason/evidence).
    """
    # Step 3: Navigate to target month (scoped to calendar root)
    month_found = False

    # Locate nav buttons within calendar root
    next_button = None
    prev_button = None
    nav_buttons_found = False

    if not budget_check(1):
        return {
            "ok": False,
            "reason": "budget_hit",
            "evidence": {"stage": "month_nav_buttons_detection"},
            "selector_used": date_field_selector,
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }

    # Build month navigation button selectors from config
    # Prefer exact semantic month-nav controls first (confirmed in current Google Flights UI).
    # Keep multilingual fallbacks and broader patterns after the exact anchors.
    month_nav_config = profile.get("calendar_month_nav_button_labels", {})
    next_labels_config = month_nav_config.get("next", {})
    prev_labels_config = month_nav_config.get("prev", {})

    next_ja_labels = next_labels_config.get("ja", ["次の月", "次"])
    next_en_labels = next_labels_config.get("en", ["Next", "next month"])
    prev_ja_labels = prev_labels_config.get("ja", ["前の月", "前"])
    prev_en_labels = prev_labels_config.get("en", ["Previous", "previous month"])

    next_button_selectors = []
    # Exact matches first (EN)
    for label in next_en_labels:
        if label.lower() == "next":
            next_button_selectors.extend([
                f"button[aria-label='{label}']:not([aria-hidden='true'])",
                f"[role='button'][aria-label='{label}']:not([aria-hidden='true'])",
            ])
    # JA labels
    for label in next_ja_labels:
        next_button_selectors.append(f"[aria-label*='{label}']:not([aria-hidden='true'])")
    # EN partial matches
    for label in next_en_labels:
        next_button_selectors.extend([
            f"[aria-label*='{label}' i]:not([aria-hidden='true'])",
            f"button[aria-label*='{label}' i]:not([aria-hidden='true'])",
        ])
    # Observed structural selector (fallback)
    next_button_selectors.append("button[jsname='KpyLEe']:not([aria-hidden='true'])")

    prev_button_selectors = []
    # Exact matches first (EN)
    for label in prev_en_labels:
        if label.lower() == "previous":
            prev_button_selectors.extend([
                f"button[aria-label='{label}']:not([aria-hidden='true'])",
                f"[role='button'][aria-label='{label}']:not([aria-hidden='true'])",
            ])
    # JA labels
    for label in prev_ja_labels:
        prev_button_selectors.append(f"[aria-label*='{label}']:not([aria-hidden='true'])")
    # EN partial matches
    for label in prev_en_labels:
        prev_button_selectors.extend([
            f"[aria-label*='{label}' i]:not([aria-hidden='true'])",
            f"button[aria-label*='{label}' i]:not([aria-hidden='true'])",
        ])
    # Observed structural selector (fallback)
    prev_button_selectors.append("button[jsname='ux1Cpc']:not([aria-hidden='true'])")

    next_button_selector_used = ""
    prev_button_selector_used = ""

    for next_sel in next_button_selectors:
        try:
            nb = calendar_root.locator(next_sel).first
            if nb.is_visible(timeout=200):
                next_button = nb
                next_button_selector_used = next_sel
                nav_buttons_found = True
                break
        except Exception:
            pass

    for prev_sel in prev_button_selectors:
        try:
            pb = calendar_root.locator(prev_sel).first
            if pb.is_visible(timeout=200):
                prev_button = pb
                prev_button_selector_used = prev_sel
                nav_buttons_found = True
                break
        except Exception:
            pass

    if not nav_buttons_found:
        typed_fallback_success = try_date_input_fallback("month_nav_buttons_not_found")
        if typed_fallback_success:
            return typed_fallback_success
        logger.warning(
            "gf_set_date.month_nav.buttons_not_found role=%s date=%s current_month=%d/%d",
            role_key,
            target_date,
            parsed_month,
            parsed_year,
        )
        return {
            "ok": False,
            "reason": "month_nav_buttons_not_found",
            "evidence": {
                "calendar.failure_stage": "month_nav_buttons_detection",
                "date": target_date,
                "current_month": parsed_month,
                "current_year": parsed_year,
                "target_month": target_month,
                "target_year": target_year,
                "calendar.nav_next_selectors_tried": next_button_selectors[:6],
                "calendar.nav_prev_selectors_tried": prev_button_selectors[:6],
            },
            "selector_used": date_field_selector,
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }

    logger.debug(
        "gf_set_date.month_nav.buttons_detected role=%s next=%s prev=%s",
        role_key,
        next_button_selector_used or "none",
        prev_button_selector_used or "none",
    )

    # Perform bounded month navigation
    nav_steps = 0
    while nav_steps < max_nav_steps and not month_found:
        if not budget_check(1):
            return {
                "ok": False,
                "reason": "budget_hit",
                "evidence": {
                    "stage": "month_nav",
                    "nav_steps": nav_steps,
                    "current_month": parsed_month,
                    "current_year": parsed_year,
                },
                "selector_used": date_field_selector,
                "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
            }

        # Check if target day is now visible within calendar root (scoped search)
        day_selectors = [
            f"[role='gridcell'][data-iso='{target_date}']:not([aria-disabled='true'])",
            f"[data-iso='{target_date}'][role='gridcell']:not([aria-disabled='true'])",
            f"[role='gridcell'][aria-label*='{target_year}年{target_month}月{target_day}日']:not([aria-disabled='true'])",
            f"[role='gridcell'][aria-label*='{target_month}月{target_day}日']:not([aria-disabled='true'])",
            f"[role='button'][aria-label*='{target_year}年{target_month}月{target_day}日']:not([aria-disabled='true'])",
            f"[role='button'][aria-label*='{target_month}月{target_day}日']:not([aria-disabled='true'])",
            f"button[aria-label*='{target_year}年{target_month}月{target_day}日']:not([aria-disabled='true'])",
            f"button[aria-label*='{target_month}月{target_day}日']:not([aria-disabled='true'])",
            f"div[aria-label*='{target_month}月{target_day}日']:not([aria-disabled='true'])",
        ]
        for token in _google_date_display_tokens(target_date):
            if not token or _should_filter_date_token(token):
                continue
            day_selectors.extend(
                [
                    f"[role='gridcell'][aria-label*='{token}']:not([aria-disabled='true'])",
                    f"[role='button'][aria-label*='{token}']:not([aria-disabled='true'])",
                    f"button[aria-label*='{token}']:not([aria-disabled='true'])",
                    f"div[aria-label*='{token}']:not([aria-disabled='true'])",
                ]
            )
        day_selectors = _dedupe_compact_selectors(day_selectors, max_items=24)

        day_visible = False
        # Check all bounded day selector variants; button-based day cells are common.
        for day_sel in day_selectors:
            try:
                day_locator = calendar_root.locator(day_sel).first
                if day_locator.is_visible(timeout=150):
                    day_visible = True
                    month_found = True
                    break
            except Exception:
                pass

        if day_visible:
            logger.info(
                "gf_set_date.month_nav.found steps=%d parsed_month=%d parsed_year=%d",
                nav_steps,
                parsed_month,
                parsed_year,
            )
            break

        # Navigate: determine direction and click button
        if month_diff > 0:
            # Navigate forward
            if next_button is None:
                break
            try:
                next_button.click(timeout=budgeted_timeout())
                nav_steps += 1
                parsed_month += 1
                if parsed_month > 12:
                    parsed_month = 1
                    parsed_year += 1
                time.sleep(0.15)
                logger.debug(
                    "gf_set_date.month_nav.forward_click nav_steps=%d new_month=%d/%d",
                    nav_steps,
                    parsed_month,
                    parsed_year,
                )
            except Exception as exc:
                logger.debug("gf_set_date.month_nav.click_failed error=%s", str(exc)[:50])
                break
            month_diff -= 1
        else:
            # Navigate backward
            if prev_button is None:
                break
            try:
                prev_button.click(timeout=budgeted_timeout())
                nav_steps += 1
                parsed_month -= 1
                if parsed_month < 1:
                    parsed_month = 12
                    parsed_year -= 1
                time.sleep(0.15)
                logger.debug(
                    "gf_set_date.month_nav.backward_click nav_steps=%d new_month=%d/%d",
                    nav_steps,
                    parsed_month,
                    parsed_year,
                )
            except Exception as exc:
                logger.debug("gf_set_date.month_nav.click_failed error=%s", str(exc)[:50])
                break
            month_diff += 1

    if not month_found:
        typed_fallback_success = try_date_input_fallback("month_nav_exhausted")
        if typed_fallback_success:
            return typed_fallback_success
        logger.warning(
            "gf_set_date.month_nav.exhausted role=%s nav_steps=%d max=%d final_month=%d/%d target=%d/%d",
            role_key,
            nav_steps,
            max_nav_steps,
            parsed_month,
            parsed_year,
            target_month,
            target_year,
        )
        return {
            "ok": False,
            "reason": "month_nav_exhausted",
            "evidence": {
                "calendar.nav_steps": nav_steps,
                "calendar.max_nav_steps": max_nav_steps,
                "calendar.current_month": parsed_month,
                "calendar.current_year": parsed_year,
                "calendar.target_month": target_month,
                "calendar.target_year": target_year,
                "calendar.parsing_method": parsing_method or "unknown",
                "time.deadline_ms_remaining": wall_clock_remaining_ms(deadline) or -1,
                "budget.actions_used": budget.max_actions - budget.remaining - budget_used_start,
            },
            "selector_used": date_field_selector,
            "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
        }

    return {
        "ok": True,
        "nav_steps": nav_steps,
        "parsed_month": parsed_month,
        "parsed_year": parsed_year,
        "month_diff": month_diff,
    }
