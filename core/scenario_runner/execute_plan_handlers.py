"""Action handler helpers extracted from scenario_runner.execute_plan."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple


def should_skip_return_fill_after_depart_failure(
    *,
    role: Optional[str],
    step_optional: bool,
    step_trace: List[Dict[str, Any]],
) -> bool:
    if role != "return" or not step_optional:
        return False
    for item in reversed(step_trace):
        if not isinstance(item, dict):
            continue
        if item.get("action") != "fill":
            continue
        if str(item.get("role", "") or "").strip().lower() != "depart":
            continue
        status_value = str(item.get("status", "") or "").strip().lower()
        if status_value in {
            "soft_fail",
            "month_nav_exhausted",
            "calendar_not_open",
            "date_picker_unverified",
            "route_core_before_date_fill_unverified",
        }:
            return True
        fill_meta = item.get("fill_commit")
        if isinstance(fill_meta, dict) and not bool(fill_meta.get("ok")):
            return True
    return False


def has_recent_skyscanner_date_failure_in_turn(
    *,
    site_key: Optional[str],
    step_trace: List[Dict[str, Any]],
) -> bool:
    """Detect local Skyscanner date-fill failures that should short-circuit search/wait."""
    if str(site_key or "").strip().lower() != "skyscanner":
        return False
    failure_tokens = {
        "calendar_not_open",
        "month_nav_exhausted",
        "month_header_not_found",
        "month_nav_buttons_not_found",
        "day_not_found",
        "verify_mismatch",
        "date_picker_unverified",
        "route_core_before_date_fill_unverified",
        "skyscanner_date_fill_failed",
    }
    for item in reversed(step_trace or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("action", "") or "").strip().lower() != "fill":
            continue
        role = str(item.get("role", "") or "").strip().lower()
        if role not in {"depart", "return"}:
            continue
        status_text = str(item.get("status", "") or "").strip().lower()
        error_text = str(item.get("error", "") or "").strip().lower()
        fill_commit = item.get("fill_commit", {})
        fill_reason = ""
        if isinstance(fill_commit, dict):
            fill_reason = str(fill_commit.get("reason", "") or "").strip().lower()
        combined = " ".join(
            part for part in (status_text, error_text, fill_reason) if part
        )
        if status_text == "soft_fail":
            return True
        if any(token in combined for token in failure_tokens):
            return True
    return False


def soft_skip_after_recent_date_failure(
    *,
    action: Optional[str],
    selectors: List[str],
    has_recent_date_failure: bool,
    selectors_look_search_submit_fn: Callable[[List[str]], bool],
    selectors_look_post_search_wait_fn: Callable[[List[str]], bool],
) -> Optional[str]:
    if not has_recent_date_failure:
        return None
    if action == "click" and selectors_look_search_submit_fn(selectors):
        return "skip_search_after_local_date_fail"
    if action == "wait" and selectors_look_post_search_wait_fn(selectors):
        return "skip_wait_after_local_date_fail"
    return None


def optional_click_visibility_soft_skip(
    *,
    browser: Any,
    action: Optional[str],
    step_optional: bool,
    selectors: List[str],
    check_selector_visibility_fn: Callable[[Any, List[str]], bool],
    idx: int,
    step_start: float,
    log: Any,
) -> Optional[Dict[str, Any]]:
    if action != "click" or not step_optional:
        return None
    if check_selector_visibility_fn(browser, selectors):
        return None
    log.info(
        "scenario.step.click_optional_visibility_skip selectors=%s",
        selectors[:3] if selectors else [],
    )
    elapsed_ms = int((time.monotonic() - step_start) * 1000)
    log.info(
        "scenario.step.end step_index=%d action=%s status=soft_fail elapsed_ms=%d",
        idx,
        action or "unknown",
        elapsed_ms,
    )
    return {
        "elapsed_ms": elapsed_ms,
        "status": "soft_fail",
        "error": "selector_not_visible",
        "trace": {
            "index": idx,
            "action": action,
            "selectors": list(selectors),
            "used_selector": None,
            "status": "soft_fail",
            "error": "selector_not_visible",
        },
    }


def run_generic_click_action(
    *,
    browser: Any,
    site_key: str,
    selectors: List[str],
    remaining_step_timeout_ms_fn: Callable[[], Optional[int]],
    safe_click_first_match_fn: Callable[..., Tuple[Optional[Exception], Optional[str]]],
) -> Tuple[Optional[Exception], Optional[str]]:
    return safe_click_first_match_fn(
        browser,
        selectors,
        timeout_ms=remaining_step_timeout_ms_fn(),
        require_clickable=True,
        site_key=site_key,
    )


def run_generic_fill_or_wait_action(
    *,
    exec_ctx: Any,
    action: Optional[str],
    selectors: List[str],
    value: Any,
    remaining_step_timeout_ms_fn: Callable[[], Optional[int]],
) -> Tuple[Optional[Exception], Optional[str]]:
    return exec_ctx.run_step_action(
        action,
        selectors,
        value=value,
        timeout_ms=remaining_step_timeout_ms_fn(),
    )
