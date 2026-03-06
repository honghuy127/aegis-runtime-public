"""Date typing fallback helpers for Google Flights scenario.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from core.scenario.gf_helpers.fill_guards import _is_fillable_element


def _google_date_typing_fallback(
    browser,
    target_date: str,
    role_key: str,
    role_selectors,
    result: dict,
    timeout_fn: Callable[[], int],
    logger,
    *,
    preferred_selectors: Optional[list] = None,
    deadline: Optional[float] = None,
    max_attempts: int = 2,
    date_formats: Optional[list] = None,
) -> dict:
    """Fallback to typing date when picker fails. Only uses with post-fill validation.

    Safety checks:
    - Verify element is actually fillable before attempting typing
    - Requires real input element to be present and visible
    - Includes post-fill verification to confirm date was accepted
    """
    result["typed_fallback"] = True
    result["reason"] = "typing_fallback"

    def _deadline_exceeded(stage: str) -> bool:
        if deadline is None:
            return False
        if time.monotonic() > deadline:
            logger.warning("gf.date.cap.hit stage=%s", stage)
            result["reason"] = f"cap_hit_{stage}"
            return True
        return False

    # Build a compact, input-only selector list (never type into combobox containers).
    selector_candidates = []
    for selector in (preferred_selectors or []):
        if isinstance(selector, str) and selector.strip():
            selector_candidates.append(selector)
    for selector in (role_selectors or []):
        if not isinstance(selector, str) or not selector.strip():
            continue
        lowered = selector.lower()
        if "role='combobox'" in lowered and "input" not in lowered:
            continue
        if "role=\"combobox\"" in lowered and "input" not in lowered:
            continue
        selector_candidates.append(selector)

    selector_candidates = [s for s in selector_candidates if isinstance(s, str) and s.strip()]

    # Check if any selector is actually fillable before attempting typing
    fillable_selector = None
    for selector in selector_candidates:
        is_fillable, skip_reason = _is_fillable_element(browser, selector)
        if is_fillable:
            fillable_selector = selector
            break

    if not fillable_selector:
        result["reason"] = "typing_skipped_no_fillable_input"
        result["ok"] = False
        logger.warning(
            "scenario.google_date_fallback role=%s date=%s skipped_no_fillable_input=true",
            role_key,
            target_date,
        )
        return result

    # Try typing into date field with increased timeout for typing phase
    typed_ok = False
    last_error = None
    selector_used = None
    # Use 3000ms for typing fallback to give extra time for date input
    typing_timeout_ms = 3000
    format_candidates = [str(value) for value in (date_formats or [target_date]) if value]
    attempt_limit = max(1, int(max_attempts))
    attempts = 0

    # Prefer focused input when available
    page = getattr(browser, "page", None)
    focused_input = False
    if page is not None and hasattr(page, "evaluate"):
        try:
            if _deadline_exceeded("typed"):
                return result
            focused_input = bool(
                page.evaluate(
                    "() => {const e = document.activeElement; return e && (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA');}",
                    timeout=200,
                )
            )
            if focused_input:
                selector_used = ":focus"
                logger.info("gf.date.input_found selector=:focus")
        except Exception:
            focused_input = False

    selector_pool = []
    if focused_input:
        selector_pool.append(":focus")
    selector_pool.extend([s for s in selector_candidates if s != ":focus"])
    selector_pool = selector_pool[:2]

    for fmt in format_candidates:
        for selector in selector_pool:
            if attempts >= attempt_limit:
                break
            if _deadline_exceeded("typed"):
                return result
            attempts += 1
            try:
                if selector == ":focus":
                    if page is not None and hasattr(page, "keyboard"):
                        page.keyboard.press("ControlOrMeta+A")
                        page.keyboard.press("Delete")
                        page.keyboard.type(fmt)
                else:
                    browser.fill(selector, fmt, timeout_ms=typing_timeout_ms)
                typed_ok = True
                selector_used = selector
                time.sleep(0.15)
                break
            except Exception as exc:
                last_error = str(exc)[:200]
                logger.debug(
                    "scenario.google_date_fallback.selector_failed role=%s selector=%s error=%s",
                    role_key,
                    selector[:80] if len(selector) > 80 else selector,
                    last_error,
                )
                continue
        if typed_ok:
            break

    if not typed_ok:
        result["reason"] = "typing_failed"
        result["ok"] = False
        result["last_error"] = last_error
        logger.warning(
            "scenario.google_date_fallback role=%s date=%s typed_ok=false error=%s",
            role_key,
            target_date,
            last_error or "unknown",
        )
        return result

    result["selector_used"] = selector_used

    # Post-fill verification: Read back field value to confirm date was accepted
    date_commit_verified = False
    verified_value = None
    verify_selectors = (role_selectors or []) + [
        f"[aria-label*='{role_key}']",
        f"input[aria-label*='{role_key}']",
    ]

    page = getattr(browser, "page", None)
    for selector in verify_selectors:
        try:
            if page is not None:
                locator = page.locator(selector).first
                field_value = locator.input_value(timeout=500) or locator.get_attribute("value", timeout=500) or ""
                # Accept if typed date appears in field, or if field contains date-like pattern
                if target_date in field_value or _date_fuzzy_match(field_value, target_date):
                    date_commit_verified = True
                    verified_value = field_value[:40]
                    break
        except Exception:
            continue

    result["committed"] = date_commit_verified
    result["date_commit_verified"] = date_commit_verified
    result["ok"] = date_commit_verified
    result["reason"] = "typed_verified" if date_commit_verified else "typed_unverified"

    if verified_value:
        result["verified_value"] = verified_value

    logger.warning(
        "scenario.google_date_fallback role=%s date=%s committed=%s verified=%s skipped_no_fillable_input=false",
        role_key,
        target_date,
        result["committed"],
        result["date_commit_verified"],
    )
    return result


def _date_fuzzy_match(field_value: str, target_date: str) -> bool:
    """Check if field value contains the target date in various formats."""
    if not field_value or not target_date:
        return False

    # Extract date components from target (YYYY-MM-DD format)
    import re
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", target_date)
    if not match:
        return False

    year, month, day = match.groups()
    # Remove leading zeros for flexible matching
    month_alt = str(int(month))
    day_alt = str(int(day))

    # Check various patterns
    patterns = [
        f"{year}-{month}-{day}",  # 2026-03-15
        f"{year}/{month}/{day}",  # 2026/03/15
        f"{month}/{day}/{year}",  # 03/15/2026
        f"{day}/{month}/{year}",  # 15/03/2026
        f"{year}年{month_alt}月{day_alt}日",  # Japanese format
    ]

    for pattern in patterns:
        if pattern in field_value:
            return True

    return False
