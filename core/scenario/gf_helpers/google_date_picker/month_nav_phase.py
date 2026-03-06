"""Phase: month navigation for Google date picker.

Move-only extraction from core/scenario/google_flights.py.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple


def navigate_depart_months(
    page,
    *,
    day_selectors: List[str],
    next_month_selectors: List[str],
    max_month_nav: int,
    deadline_exceeded,
    budgeted_timeout_fn,
    logger,
) -> Optional[int]:
    """Navigate months for depart flow until target day visible."""
    nav_steps = 0
    while nav_steps < max_month_nav:
        if deadline_exceeded("nav"):
            return None
        day_visible = False
        for selector in day_selectors:
            try:
                if page is not None:
                    locator = page.locator(selector).first
                    if locator.is_visible(timeout=150):
                        day_visible = True
                        break
            except Exception:
                continue
        if day_visible:
            break
        moved = False
        for selector in next_month_selectors:
            try:
                if page is not None:
                    nav = page.locator(selector).first
                    if nav.is_visible(timeout=150):
                        nav.click(timeout=budgeted_timeout_fn())
                        time.sleep(0.15)
                        moved = True
                        nav_steps += 1
                        break
            except Exception:
                continue
        if not moved:
            break

    logger.info("gf.date.nav steps=%d", nav_steps)
    return nav_steps


def navigate_return_months(
    page,
    *,
    date_selectors: List[str],
    next_month_selectors: List[str],
    prev_month_selectors: List[str],
    max_month_nav_attempts: int,
    budgeted_timeout_fn,
    logger,
    wait_for_calendar_interactive_ready,
) -> Tuple[int, Optional[str]]:
    """Navigate months for return flow to reach target month/day."""
    month_nav_attempts = 0
    current_month_log = None
    try:
        if page is not None:
            month_header_selectors = [
                "[role='heading']",
                ".calendar-header",
                "[class*='month']",
                "[aria-label*='month']",
            ]
            for hdr_sel in month_header_selectors:
                try:
                    hdr_locator = page.locator(hdr_sel).first
                    if hdr_locator.is_visible(timeout=300):
                        header_text = hdr_locator.text_content(timeout=300) or ""
                        current_month_log = header_text[:50]
                        break
                except Exception:
                    continue

        while month_nav_attempts < max_month_nav_attempts:
            month_nav_attempts += 1

            date_found_in_month = False
            for selector in date_selectors:
                try:
                    if page is not None:
                        locator = page.locator(selector).first
                        if locator.is_visible(timeout=300):
                            date_found_in_month = True
                            break
                except Exception:
                    continue

            if date_found_in_month:
                logger.info(
                    "scenario.google_date_picker.month_nav_success month_found=%s nav_attempts=%d current_month=%s",
                    True,
                    month_nav_attempts - 1,
                    current_month_log or "unknown",
                )
                break

            nav_attempted = False
            for next_sel in next_month_selectors:
                try:
                    if page is not None:
                        nav_locator = page.locator(next_sel).first
                        if nav_locator.is_visible(timeout=300):
                            nav_locator.click(timeout=budgeted_timeout_fn())
                            time.sleep(0.2)
                            wait_for_calendar_interactive_ready(stage="after_month_nav", max_checks=4)
                            nav_attempted = True
                            break
                except Exception:
                    continue

            if not nav_attempted and month_nav_attempts >= 2:
                break

    except Exception as nav_exc:
        logger.debug(
            "scenario.google_date_picker.month_nav_exception error=%s",
            str(nav_exc)[:100],
        )

    return month_nav_attempts, current_month_log
