"""
Phase F: Date field verification.

Extracted from: gf_set_date() verification logic (lines ~2200-2350)

Handles:
- Reading date field value after selection
- Comparing with expected date
- Verification retry logic
- Evidence tracking
"""

from typing import Tuple, Optional, Dict, Any
from playwright.async_api import Page


def verify_date_field(
    page: Page,
    role_key: str,
    target_date: str,
    date_field_selector: str,
    timeout_value: int = 2000,
    max_verify_attempts: int = 3,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Verify that date field shows expected date after selection.

    Args:
        page: Playwright page
        role_key: "depart" or "return"
        target_date: Expected date string (YYYY-MM-DD)
        date_field_selector: Selector for date field to read
        timeout_value: Timeout for field reads
        max_verify_attempts: Maximum verification attempts

    Returns:
        (verified, evidence_dict) tuple
        - verified: True if field matches target date
        - evidence_dict: Verification attempts and final state
    """
    evidence = {
        "role": role_key,
        "target_date": target_date,
        "attempts": [],
        "verified": False,
        "field_value": None,
    }

    for attempt in range(max_verify_attempts):
        try:
            field_locator = page.locator(date_field_selector)
            field_value = field_locator.input_value(timeout=timeout_value)

            evidence["attempts"].append({
                "attempt": attempt,
                "field_value": field_value,
            })

            # Simple check: Does field contain target date components?
            target_parts = target_date.split("-")
            if all(part in field_value for part in target_parts):
                evidence["verified"] = True
                evidence["field_value"] = field_value
                return (True, evidence)

        except Exception as exc:
            evidence["attempts"].append({
                "attempt": attempt,
                "error": str(exc),
            })
            continue

    evidence["field_value"] = evidence["attempts"][-1].get("field_value") if evidence["attempts"] else None
    return (False, evidence)


if __name__ == "__main__":
    pass
