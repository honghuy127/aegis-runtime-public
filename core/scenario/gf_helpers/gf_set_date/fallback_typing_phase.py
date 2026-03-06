"""
Phase G: Fallback typing when calendar fails.

Extracted from: gf_set_date() fallback path (lines ~1480-1500 pre-calendar)

Handles:
- Typing date string directly when calendar unavailable
- Fuzzy matching date formats
- Fallback evidence tracking
"""

from typing import Tuple, Optional, Dict, Any
from playwright.async_api import Page


def attempt_typing_fallback(
    page: Page,
    target_date: str,
    date_field_selector: str,
    locale_hint: Optional[str] = None,
    timeout_value: int = 3000,
    max_attempts: int = 2,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Fallback: Type date string directly into date field.

    Args:
        page: Playwright page
        target_date: Date string to type (YYYY-MM-DD format)
        date_field_selector: Selector for date field to type into
        locale_hint: Locale hint for date format conversion
        timeout_value: Timeout for field operations
        max_attempts: Maximum type attempts

    Returns:
        (success, evidence_dict) tuple
        - success: True if typing succeeded
        - evidence_dict: Typing attempts and final state
    """
    evidence = {
        "target_date": target_date,
        "locale_hint": locale_hint,
        "attempts": [],
        "typed": False,
        "date_formats_tried": [],
    }

    # Convert target date to various formats for locale support
    date_formats = []
    if target_date:
        # YYYY-MM-DD -> expected format variations
        parts = target_date.split("-")
        if len(parts) == 3:
            year, month, day = parts
            date_formats = [
                f"{year}/{month}/{day}",  # Google Flights prefers this
                f"{month}/{day}/{year}",  # US format
                target_date,  # Original YYYY-MM-DD
            ]

    evidence["date_formats_tried"] = date_formats

    for attempt in range(max_attempts):
        for date_format in date_formats:
            try:
                field = page.locator(date_field_selector)
                field.click(timeout=timeout_value)
                field.clear()
                field.fill(date_format)
                field.focus()

                evidence["attempts"].append({
                    "attempt": attempt,
                    "format_tried": date_format,
                    "success": True,
                })
                evidence["typed"] = True
                return (True, evidence)

            except Exception as exc:
                evidence["attempts"].append({
                    "attempt": attempt,
                    "format_tried": date_format,
                    "error": str(exc),
                })
                continue

    evidence["typed"] = False
    return (False, evidence)


if __name__ == "__main__":
    pass
