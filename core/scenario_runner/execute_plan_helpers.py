"""Execute plan micro-helpers extracted from core/scenario_runner.py.

Move-only extraction to reduce scenario_runner.py complexity.
No behavior changes.
"""

import time
from typing import Any, Callable, Dict, List, Optional, Tuple


def trace_latest_fill_selector_in_plan(role: str, step_trace: List[Dict[str, Any]]) -> str:
    """Return most recent fill selector used for one role in this plan execution.

    Args:
        role: Fill role to search for (origin, dest, depart, return)
        step_trace: List of step execution records

    Returns:
        Selector string if found, empty string otherwise
    """
    target = str(role or "").strip().lower()
    if target not in {"origin", "dest", "depart", "return"}:
        return ""
    for item in reversed(step_trace):
        if not isinstance(item, dict):
            continue
        if item.get("action") != "fill":
            continue
        if str(item.get("role", "") or "").strip().lower() != target:
            continue
        selector = str(item.get("used_selector", "") or "").strip()
        if selector:
            return selector
        selectors = item.get("selectors")
        if isinstance(selectors, list) and selectors:
            return str(selectors[0] or "").strip()
    return ""


def run_step_action_with_fallback(
    browser,
    action_name: str,
    selector_candidates,
    *,
    value=None,
    timeout_ms=None,
) -> Tuple[Optional[Exception], Optional[str]]:
    """Execute one action across selector candidates until one succeeds.

    Args:
        browser: BrowserSession instance
        action_name: Action type (fill, click, wait)
        selector_candidates: List of selectors to try
        value: Value for fill actions
        timeout_ms: Optional timeout in milliseconds

    Returns:
        (error, used_selector) tuple - (None, selector) on success, (error, None) on failure
    """
    last_error = None
    for selector in selector_candidates:
        try:
            if action_name == "fill":
                browser.fill(selector, value, timeout_ms=timeout_ms)
            elif action_name == "click":
                browser.click(selector, timeout_ms=timeout_ms)
            elif action_name == "wait":
                browser.wait(selector, timeout_ms=timeout_ms)
            return None, selector
        except Exception as exc:
            if isinstance(exc, (TimeoutError, KeyboardInterrupt)):
                raise
            last_error = exc
    return last_error, None


def get_current_page_url_for_commit(browser, evidence_url: str = "") -> str:
    """Get current page URL with evidence_url fallback.

    Args:
        browser: BrowserSession instance
        evidence_url: Optional fallback URL from evidence context

    Returns:
        Current page URL or empty string
    """
    if evidence_url:
        return evidence_url
    try:
        page_obj = getattr(browser, "page", None)
        page_url = getattr(page_obj, "url", "") if page_obj is not None else ""
        if callable(page_url):
            page_url = page_url()
        return str(page_url or "")
    except Exception:
        return ""


def get_step_wall_clock_cap_ms(
    action_name: str,
    site_key: str,
    get_threshold_fn: Callable,
    threshold_site_value_fn: Callable,
) -> int:
    """Get wall clock cap for step action based on thresholds.

    Args:
        action_name: Action type (click, fill, wait)
        site_key: Site identifier
        get_threshold_fn: Function to retrieve threshold values
        threshold_site_value_fn: Function to get site-specific threshold

    Returns:
        Wall clock cap in milliseconds
    """
    action_key = {
        "click": "scenario_step_wall_clock_cap_ms_click",
        "fill": "scenario_step_wall_clock_cap_ms_fill",
        "wait": "scenario_step_wall_clock_cap_ms_wait",
        "wait_msec": "scenario_step_wall_clock_cap_ms_wait",
    }
    key = action_key.get(str(action_name or "").strip().lower(), "scenario_step_wall_clock_cap_ms_default")
    fallback = int(get_threshold_fn(key, get_threshold_fn("scenario_step_wall_clock_cap_ms", 45_000)))
    return int(threshold_site_value_fn(key, site_key, fallback))


def calculate_remaining_step_timeout_ms(
    step_deadline: Optional[float],
    effective_step_timeout_ms: Optional[int],
    raise_if_timed_out_fn: Callable[[str], None],
) -> Optional[int]:
    """Calculate remaining timeout considering budget deadline.

    Args:
        step_deadline: Monotonic time deadline (None if no deadline)
        effective_step_timeout_ms: Base timeout in milliseconds
        raise_if_timed_out_fn: Callback to raise timeout error (unused, kept for signature compatibility)

    Returns:
        Remaining timeout in milliseconds, or None if no limit
    """
    if step_deadline is None:
        return effective_step_timeout_ms
    remaining_ms = int((step_deadline - time.monotonic()) * 1000)
    # Return minimum viable timeout instead of raising error here
    # The wall clock guard (_raise_if_step_timed_out) is responsible for raising timeout errors
    if remaining_ms <= 0:
        return 1
    if effective_step_timeout_ms is None:
        return max(1, remaining_ms)
    return max(1, min(int(effective_step_timeout_ms), int(remaining_ms)))
