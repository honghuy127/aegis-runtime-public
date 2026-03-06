"""Phase: calendar opener for Google date picker (depart flow).

Move-only extraction from core/scenario/google_flights.py.
"""

from __future__ import annotations

import time
from typing import List, Tuple


def open_depart_calendar(
    browser,
    page,
    *,
    open_selectors: List[str],
    calendar_surface_selectors: List[str],
    max_open_attempts: int,
    deadline_exceeded,
    budgeted_timeout_fn,
    logger,
    result: dict,
) -> Tuple[bool, str]:
    """Open calendar dialog for depart flow."""
    calendar_open = False
    open_selector_used = ""
    for _attempt in range(max_open_attempts):
        if deadline_exceeded("open"):
            return False, open_selector_used
        for selector in open_selectors:
            try:
                browser.click(selector, timeout_ms=budgeted_timeout_fn())
                open_selector_used = selector
                time.sleep(0.15)
                for surface_sel in calendar_surface_selectors:
                    try:
                        if page is not None:
                            surface = page.locator(surface_sel).first
                            if surface.is_visible(timeout=200):
                                calendar_open = True
                                result["selector_used"] = selector
                                logger.info("gf.date.open.ok selector=%s", selector)
                                break
                    except Exception:
                        continue
                if calendar_open:
                    break
            except Exception:
                continue
        if calendar_open:
            break
    return calendar_open, open_selector_used
