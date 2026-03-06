"""
Phase E: Calendar day selection.

Extracted from: gf_set_date() day selection logic (lines ~2050-2150)

Handles:
- Day selector building by target date
- Day button clicking
- Selection verification
- Evidence tracking
"""

from typing import Tuple, Optional, Dict, Any, List
from playwright.async_api import Page


def select_calendar_day(
    page: Page,
    target_day: int,
    calendar_root_selector: str,
    timeout_value: int = 3000,
    max_attempts: int = 3,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Select a specific day in calendar.

    Args:
        page: Playwright page
        target_day: Day number to select (1-31)
        calendar_root_selector: Selector for calendar root element
        timeout_value: Timeout per click
        max_attempts: Maximum click attempts per day

    Returns:
        (success, evidence_dict) tuple
        - success: True if day was clicked
        - evidence_dict: Selection attempts and final state
    """
    evidence = {
        "target_day": target_day,
        "attempts": [],
        "day_clicked": False,
        "day_selector_used": "",
    }

    # Build candidate selectors for day
    day_selectors = [
        f"{calendar_root_selector} button[aria-label='{target_day}']",
        f"{calendar_root_selector} [role='button'][aria-label*='{target_day}']",
        f"{calendar_root_selector} td[data-date='{target_day}'] button",
    ]

    for attempt in range(max_attempts):
        for selector in day_selectors:
            try:
                locator = page.locator(selector)
                if locator.is_visible(timeout=min(timeout_value, 1500)):
                    locator.click(timeout=timeout_value)
                    evidence["day_click_attempts"] = len(evidence["attempts"])
                    evidence["day_clicked"] = True
                    evidence["day_selector_used"] = selector
                    evidence["day_click_success"] = True
                    return (True, evidence)
            except Exception as exc:
                evidence["attempts"].append({
                    "selector": selector,
                    "attempt": attempt,
                    "error": str(exc),
                })
                continue

    evidence["day_click_success"] = False
    return (False, evidence)


if __name__ == "__main__":
    pass
