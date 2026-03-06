"""Phase: day selection for Google date picker.

Move-only extraction from core/scenario/google_flights.py.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from core.scenario.gf_helpers.date_tokens import _google_date_display_tokens
from core.scenario.gf_helpers.helpers import _dedupe_compact_selectors


def build_depart_day_selectors(target_date: str) -> List[str]:
    """Build bounded day selectors for depart flow."""
    day_selectors = [f"button[data-iso='{target_date}']"]
    for token in _google_date_display_tokens(target_date):
        token_q = str(token or "").replace("'", "\\'")
        if not token_q:
            continue
        day_selectors.extend(
            [
                f"button[aria-label*='{token_q}']",
                f"[role='gridcell'][aria-label*='{token_q}']",
                f"td[role='gridcell'][aria-label*='{token_q}']",
            ]
        )
    return _dedupe_compact_selectors(day_selectors, max_items=10)


def build_return_day_selectors(
    *,
    locale_hint: str,
    year: int,
    month: int,
    day: int,
) -> List[str]:
    """Build day selectors for return flow, honoring locale formats."""
    locale_is_ja = str(locale_hint or "").lower().startswith("ja")
    date_selectors: List[str] = []
    if locale_is_ja:
        date_selectors.extend(
            [
                f"[role='button'][aria-label*='{year}年{month}月{day}日']",
                f"[role='gridcell'][aria-label*='{year}年{month}月{day}日']",
                f"[aria-label*='{month}月{day}日']",
                f"button:has-text('{day}日')",
            ]
        )
    else:
        import datetime as date_mod

        month_name = date_mod.date(year, month, day).strftime("%B")
        month_abbr = date_mod.date(year, month, day).strftime("%b")
        date_selectors.extend(
            [
                f"[role='button'][aria-label*='{month_name} {day}, {year}']",
                f"[role='gridcell'][aria-label*='{month_name} {day}']",
                f"[role='button'][aria-label*='{month_abbr} {day}']",
                f"button:has-text('{day}')",
            ]
        )
    return date_selectors


def click_depart_day(
    page,
    *,
    day_selectors: List[str],
    max_day_click_attempts: int,
    deadline_exceeded,
    budgeted_timeout_fn,
    logger,
    result: dict,
) -> Optional[bool]:
    """Attempt bounded day clicks for depart flow."""
    day_clicked = False
    for attempt in range(max_day_click_attempts):
        if deadline_exceeded("day"):
            return None
        selector = day_selectors[min(attempt, len(day_selectors) - 1)]
        try:
            if page is not None:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=200):
                    locator.click(timeout=budgeted_timeout_fn())
                    day_clicked = True
                    result["picker_used"] = True
                    logger.info("gf.date.day.click selector=%s", selector)
                    time.sleep(0.15)
                    break
        except Exception:
            continue
    return day_clicked


def click_return_day(
    page,
    *,
    date_selectors: List[str],
    budgeted_timeout_fn,
    logger,
    result: dict,
) -> Tuple[bool, Optional[str]]:
    """Click date in calendar for return flow."""
    date_clicked = False
    click_method = None
    for selector in date_selectors:
        try:
            if page is not None:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=500):
                    locator.click(timeout=budgeted_timeout_fn())
                    date_clicked = True
                    result["picker_used"] = True
                    click_method = "calendar_click"
                    time.sleep(0.15)
                    break
        except Exception:
            continue
    return date_clicked, click_method
