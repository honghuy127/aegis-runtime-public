"""
Phase D: Calendar month navigation.

Extracted from: gf_set_date() month navigation loop (lines ~1800-2000)

Handles:
- Month/year comparison
- Navigation button detection (prev/next)
- Month advancement iteration
- Boundary checking
"""

from typing import Tuple, Optional, Dict, Any
from playwright.async_api import Page


def navigate_to_target_month(
    page: Page,
    current_year: int,
    current_month: int,
    target_year: int,
    target_month: int,
    max_nav_attempts: int = 13,
    timeout_value: int = 3000,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Navigate calendar to target month/year.

    Args:
        page: Playwright page
        current_year, current_month: Current calendar display
        target_year, target_month: Target month to reach
        max_nav_attempts: Maximum navigation clicks allowed
        timeout_value: Timeout for each navigation click

    Returns:
        (success, evidence_dict) tuple
        - success: True if reached target month
        - evidence_dict: Navigation attempts and final state
    """
    nav_attempts = 0
    nav_evidence = {
        "attempts": [],
        "reached_target": False,
        "final_year": current_year,
        "final_month": current_month,
    }

    while (current_year, current_month) != (target_year, target_month) and nav_attempts < max_nav_attempts:
        # Determine navigation direction
        if (current_year, current_month) < (target_year, target_month):
            direction = "next"
        else:
            direction = "prev"

        # Try to find navigation button and click
        nav_button_selector = f"button[aria-label*='{direction}']"
        try:
            nav_button = page.locator(nav_button_selector).first
            if nav_button.is_visible(timeout=min(timeout_value, 1000)):
                nav_button.click(timeout=timeout_value)
                nav_attempts += 1

                # Update current month (simplified)
                if direction == "next":
                    current_month += 1
                    if current_month > 12:
                        current_month = 1
                        current_year += 1
                else:
                    current_month -= 1
                    if current_month < 1:
                        current_month = 12
                        current_year -= 1

                nav_evidence["attempts"].append({
                    "direction": direction,
                    "new_month": current_month,
                    "new_year": current_year,
                })
            else:
                break
        except Exception as exc:
            nav_evidence["attempts"].append({
                "direction": direction,
                "error": str(exc),
            })
            break

    nav_evidence["reached_target"] = (current_year, current_month) == (target_year, target_month)
    nav_evidence["final_year"] = current_year
    nav_evidence["final_month"] = current_month
    nav_evidence["nav_attempts"] = nav_attempts

    return (nav_evidence["reached_target"], nav_evidence)


if __name__ == "__main__":
    pass
