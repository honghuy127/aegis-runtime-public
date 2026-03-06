"""Fill guard helpers for Google Flights scenario.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations


def _is_fillable_element(browser, selector: str) -> tuple[bool, str]:
    """Check if element matched by selector is fillable (INPUT/TEXTAREA/contenteditable).

    Returns:
        (is_fillable, reason_if_not_fillable)
    """
    page = getattr(browser, "page", None)
    if page is None:
        return True, ""  # Cannot verify, assume fillable

    try:
        locator = page.locator(selector).first
        element_info = locator.evaluate(
            """
            el => {
                const tagName = el.tagName.toUpperCase();
                const isContentEditable = el.contentEditable === 'true';
                const ariaLabel = el.getAttribute('aria-label') || '';
                return {
                    tagName: tagName,
                    isContentEditable: isContentEditable,
                    ariaLabel: ariaLabel,
                    isFillable: tagName === 'INPUT' || tagName === 'TEXTAREA' || isContentEditable
                };
            }
            """
        )
        if element_info.get("isFillable"):
            return True, ""
        else:
            tag = element_info.get("tagName", "unknown")
            aria = element_info.get("ariaLabel", "")[:50]  # truncate
            return False, f"non_fillable_element:tag={tag},aria={aria}"
    except Exception:
        return True, ""  # If check fails, proceed to attempt fill