"""Google Flights location field fill operation helpers."""

from __future__ import annotations

import time
from typing import Any, Optional


def _perform_fill_sequence(
    browser,
    fill_chain: list[str],
    target_value: str,
    result: dict,
    *,
    deadline: Optional[float],
    budgeted_timeout_fn,
    skip_fillable_check: bool = False,
) -> tuple[bool, str]:
    """Attempt fill on each selector in fill_chain with fillability guards.

    Iterates through fill_chain and attempts:
    1. Click selector
    2. Clear field (Ctrl+A, Backspace)
    3. Fill with target value
    4. Break on success or continue on failure

    Args:
        browser: Browser session
        fill_chain: List of selectors to try for fill
        target_value: Value to fill into the field
        result: Shared result dict for tracking evidence_errors
        deadline: Wall clock deadline for timeout enforcement
        budgeted_timeout_fn: Callable that returns remaining timeout in ms
        skip_fillable_check: If True, skip _is_fillable_element guard (for fallback)

    Returns:
        Tuple of (success: bool, fill_selector: str)
        - success: True if fill succeeded
        - fill_selector: The selector that was successfully filled (or "" if failed)

    Side Effects:
        Updates result["evidence_errors"] list with skip reasons when elements are skipped
    """
    from core.scenario.gf_helpers.fill_guards import _is_fillable_element

    fill_selector = ""
    fill_error = None

    for selector in fill_chain:
        # Fillability guard: skip elements that are hidden/disabled/readonly
        if not skip_fillable_check:
            is_fillable, skip_reason = _is_fillable_element(browser, selector)
            if not is_fillable:
                errors = result.get("evidence_errors")
                if not isinstance(errors, list):
                    errors = []
                errors.append(skip_reason)
                result["evidence_errors"] = errors[:6]  # Keep recent skips
                continue

        # Attempt fill on this selector
        try:
            # Click to focus
            browser.click(selector, timeout_ms=budgeted_timeout_fn())
        except Exception:
            pass

        try:
            # Clear field
            page = getattr(browser, "page", None)
            keyboard = getattr(page, "keyboard", None) if page is not None else None
            if keyboard is not None and hasattr(keyboard, "press"):
                keyboard.press("ControlOrMeta+A")
                keyboard.press("Backspace")
        except Exception:
            pass

        # Perform fill
        try:
            browser.fill(selector, target_value, timeout_ms=budgeted_timeout_fn())
            fill_selector = selector
            fill_error = None
            return True, fill_selector
        except Exception as exc:
            fill_error = exc
            continue

    # All selectors in the chain failed
    return False, fill_selector


def _is_field_accessible_for_typing(
    browser,
    fill_selector: str,
    result: dict,
) -> bool:
    """Check if a field is accessible (visible, not hidden) for type_active.

    Performs visibility checks:
    1. offsetParent !== null (not display:none)
    2. computed style display !== "none"

    Args:
        browser: Browser session
        fill_selector: Selector for the fill field
        result: Shared result dict for tracking evidence errors

    Returns:
        bool: True if field is accessible for typing, False if hidden

    Side Effects:
        Updates result["evidence_errors"] if field is detected as hidden
    """
    page = getattr(browser, "page", None)
    if page is None or not fill_selector:
        # Can't verify without page; assume accessible
        return True

    try:
        locator = page.locator(fill_selector).first
        # Check if field is hidden (display:none, visibility:hidden, offsetParent===null, etc.)
        is_hidden = locator.evaluate(
            "el => el.offsetParent === null || window.getComputedStyle(el).display === 'none'"
        )
        if is_hidden:
            # Field is hidden; record evidence
            errors = result.get("evidence_errors", [])
            if not isinstance(errors, list):
                errors = []
            errors.append("field_is_hidden")
            result["evidence_errors"] = errors[:6]
            return False
    except Exception:
        # If verification fails, assume accessible and proceed
        pass

    return True


def _attempt_type_active_with_refocus(
    browser,
    target_value: str,
    result: dict,
    *,
    max_attempts: int = 2,
    budgeted_timeout_fn=None,
) -> bool:
    """Attempt type_active with bounded refocus retry on failure.

    Tries type_active up to max_attempts times, clearing and refocusing on retry.

    Args:
        browser: Browser session (must support type_active method)
        target_value: Value to type into active element
        result: Shared result dict for tracking attempt count
        max_attempts: Maximum number of type_active attempts (default 2)
        budgeted_timeout_fn: Callable returning remaining timeout in ms

    Returns:
        bool: True if type_active succeeded, False if all attempts failed

    Side Effects:
        - Sets result["committed"] = True on success
        - Sets result["reason"] = "type_active_commit" on success
        - Sets result["type_attempt"] = attempt_idx + 1 on success
        - Appends "type_active_attempt_1_failed" to evidence_errors on first attempt failure
    """
    if not hasattr(browser, "type_active"):
        return False

    if budgeted_timeout_fn is None:
        budgeted_timeout_fn = lambda: 1200  # Default fallback timeout

    for attempt_idx in range(max_attempts):
        try:
            # On retry (attempt_idx > 0), refocus: clear and reset field state
            if attempt_idx > 0:
                try:
                    page = getattr(browser, "page", None)
                    keyboard = getattr(page, "keyboard", None) if page else None
                    if keyboard and hasattr(keyboard, "press"):
                        keyboard.press("Control+A")  # Select all
                        keyboard.press("Delete")  # Clear field
                        # Pause for field state update
                        time.sleep(0.05)
                except Exception:
                    pass

            # Perform type_active
            browser.type_active(target_value, timeout_ms=budgeted_timeout_fn())
            result["committed"] = True
            result["reason"] = "type_active_commit"
            result["type_attempt"] = attempt_idx + 1
            return True

        except Exception as type_exc:
            # Log first attempt failure for diagnostics
            if attempt_idx == 0:
                errors = result.get("evidence_errors", [])
                if not isinstance(errors, list):
                    errors = []
                errors.append("type_active_attempt_1_failed")
                result["evidence_errors"] = errors[:6]
            continue

    return False
