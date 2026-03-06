"""Google Flights step trace analysis helpers.

Move-only extraction from core/scenario_runner.py.
No behavior changes.
"""

from typing import Any, Callable, Dict, List


def has_recent_google_date_failure_in_trace(
    site_key: str,
    step_trace: List[Dict[str, Any]]
) -> bool:
    """Check if recent date fill failures occurred in Google Flights step trace.

    Scans step trace for date fill (depart/return) failures including:
    - calendar_not_open, month_nav_exhausted, day_not_found, verify_mismatch, etc.

    Args:
        site_key: Site identifier
        step_trace: List of step execution records

    Returns:
        True if recent date fill failure detected
    """
    if (site_key or "").strip().lower() != "google_flights":
        return False
    for item in reversed(step_trace or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("action", "") or "").strip().lower() != "fill":
            continue
        role_name = str(item.get("role", "") or "").strip().lower()
        if role_name not in {"depart", "return"}:
            continue
        status_value = str(item.get("status", "") or "").strip().lower()
        if status_value in {
            "soft_fail",
            "calendar_not_open",
            "month_header_not_found",
            "month_nav_buttons_not_found",
            "day_not_found",
            "verify_mismatch",
            "date_picker_unverified",
            "month_nav_exhausted",
            "route_core_before_date_fill_unverified",
        }:
            return True
        fill_meta = item.get("fill_commit")
        if isinstance(fill_meta, dict) and not bool(fill_meta.get("ok")):
            reason = str(fill_meta.get("reason", "") or "").strip().lower()
            if reason in {
                "calendar_not_open",
                "month_nav_buttons_not_found",
                "month_nav_exhausted",
                "day_not_found",
                "date_picker_unverified",
                "verify_mismatch",
            }:
                return True
    return False


def has_google_date_done_clicked_in_trace(
    step_trace: List[Dict[str, Any]],
    locale_hint: str,
    get_tokens_fn: Callable,
    prioritize_tokens_fn: Callable,
    selector_candidates_fn: Callable,
) -> bool:
    """Check if Google Flights date picker was closed via done button.

    Analyzes step trace for evidence of date picker close interaction:
    - Done button clicks
    - Date fill commit evidence with close_method

    Args:
        step_trace: List of step execution records
        locale_hint: Locale hint for token prioritization
        get_tokens_fn: Function to get knowledge tokens
        prioritize_tokens_fn: Function to prioritize tokens by locale
        selector_candidates_fn: Function to extract selector candidates

    Returns:
        True if evidence of done button click found
    """
    done_tokens = prioritize_tokens_fn(
        get_tokens_fn("google_date_done_keywords"),
        locale_hint=locale_hint,
    )
    if not done_tokens:
        done_tokens = ["done", "完了"]
    for item in reversed(step_trace or []):
        if not isinstance(item, dict):
            continue
        action_kind = str(item.get("action", "") or "").strip().lower()
        status_kind = str(item.get("status", "") or "").strip().lower()
        if action_kind == "click" and status_kind == "ok":
            blob = " ".join(
                selector_candidates_fn(item.get("used_selector") or item.get("selectors"))
            ).lower()
            if any(str(token or "").strip().lower() in blob for token in done_tokens):
                return True
            continue
        if action_kind != "fill":
            continue
        if str(item.get("role", "") or "").strip().lower() not in {"depart", "return"}:
            continue
        fill_commit = item.get("fill_commit")
        if not isinstance(fill_commit, dict) or not bool(fill_commit.get("ok")):
            continue
        fill_evidence = fill_commit.get("evidence")
        if not isinstance(fill_evidence, dict):
            continue
        close_method = str(fill_evidence.get("verify.close_method", "") or "").strip().lower()
        close_scope = str(fill_evidence.get("calendar.close_scope", "") or "").strip().lower()
        if close_method == "done_button":
            return True
        if close_scope and close_scope not in {"escape", "unknown"}:
            return True
    return False
