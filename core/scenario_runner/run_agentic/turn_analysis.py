"""Turn trace analysis helpers for run_agentic_scenario."""

from __future__ import annotations

from typing import Any, Callable, Dict, List


def analyze_turn_trace(
    *,
    step_trace: Any,
    browser: Any,
    last_html: str,
    site_key: str,
    attempt: int,
    turn_idx: int,
    planner_notes: List[str],
    merge_planner_notes_fn: Callable[..., List[str]],
    step_trace_memory_hint_fn: Callable[[Any], str],
    google_step_trace_local_date_open_failure_fn: Callable[[Any], Dict[str, Any]],
    google_should_suppress_force_bind_after_date_failure_fn: Callable[[Any], Dict[str, Any]],
    debug_exploration_mode_fn: Callable[[], str],
    selector_candidates_fn: Callable[[Any], List[str]],
    prioritize_tokens_fn: Callable[..., List[str]],
    get_tokens_fn: Callable[[str, str], List[str]],
    current_mimic_locale_fn: Callable[[], str],
    logger: Any,
) -> Dict[str, Any]:
    """Analyze turn step trace and derive route/date signal features."""
    try:
        last_html = browser.content() or last_html
        if last_html:
            logger.info(
                "scenario.last_html.update source=after_plan_execution html_len=%d",
                len(last_html),
            )
    except Exception as exc:
        logger.debug("Failed to capture last_html after plan execution: %s", exc)

    trace_memory_hint = ""
    if isinstance(step_trace, list):
        ok_count = sum(
            1 for item in step_trace if isinstance(item, dict) and item.get("status") == "ok"
        )
        soft_fail_count = sum(
            1 for item in step_trace if isinstance(item, dict) and item.get("status") == "soft_fail"
        )
        logger.info(
            "scenario.turn.trace attempt=%s turn=%s steps=%s ok=%s soft_fail=%s",
            attempt + 1,
            turn_idx + 1,
            len(step_trace),
            ok_count,
            soft_fail_count,
        )
        trace_memory_hint = step_trace_memory_hint_fn(step_trace)
        if trace_memory_hint:
            planner_notes = merge_planner_notes_fn(
                planner_notes,
                [trace_memory_hint],
            )
            logger.info(
                "scenario.turn.trace_memory site=%s attempt=%s turn=%s hint=%s",
                site_key,
                attempt + 1,
                turn_idx + 1,
                trace_memory_hint,
            )

    route_fill_mismatch_events = [
        item
        for item in (step_trace or [])
        if isinstance(item, dict) and item.get("status") == "route_fill_mismatch"
    ]
    gf_date_failure_events = [
        item
        for item in (step_trace or [])
        if isinstance(item, dict)
        and item.get("action") == "fill"
        and str(item.get("role", "") or "").strip().lower() in {"depart", "return"}
        and bool(
            item.get("status")
            in {
                "calendar_not_open",
                "month_header_not_found",
                "month_nav_buttons_not_found",
                "day_not_found",
                "verify_mismatch",
                "date_picker_unverified",
                "month_nav_exhausted",
                "route_core_before_date_fill_unverified",
            }
        )
    ]
    google_local_date_open_failure = (
        google_step_trace_local_date_open_failure_fn(step_trace)
        if site_key == "google_flights"
        else {"matched": False, "reason": ""}
    )
    google_force_bind_suppression = (
        google_should_suppress_force_bind_after_date_failure_fn(step_trace)
        if site_key == "google_flights"
        else {"use": False, "reason": ""}
    )
    debug_exploration_mode = debug_exploration_mode_fn()
    super_deep_exploration = (
        site_key == "google_flights" and debug_exploration_mode == "super_deep"
    )

    google_trace_dest_selector = ""
    google_trace_dest_committed = False
    google_trace_dest_commit_reason = ""
    google_trace_suggestion_used = False
    google_trace_date_done_clicked = False
    google_trace_date_picker_seen = False
    done_tokens = prioritize_tokens_fn(
        get_tokens_fn("actions", "done"),
        locale_hint=current_mimic_locale_fn(),
    )
    if site_key == "google_flights":
        for item in reversed(step_trace or []):
            if not isinstance(item, dict):
                continue
            if (
                not google_trace_dest_selector
                and item.get("action") == "fill"
                and str(item.get("role", "") or "").strip().lower() == "dest"
            ):
                google_trace_dest_selector = str(item.get("used_selector", "") or "").strip()
                commit_meta = (
                    item.get("fill_commit", {})
                    if isinstance(item.get("fill_commit"), dict)
                    else {}
                )
                google_trace_dest_committed = bool(commit_meta.get("committed", False))
                google_trace_dest_commit_reason = str(commit_meta.get("reason", "") or "")
                google_trace_suggestion_used = bool(commit_meta.get("suggestion_used", False))
            if (
                item.get("action") == "fill"
                and str(item.get("role", "") or "").strip().lower() in {"depart", "return"}
            ):
                google_trace_date_picker_seen = True
                fill_commit = (
                    item.get("fill_commit", {})
                    if isinstance(item.get("fill_commit"), dict)
                    else {}
                )
                fill_evidence = (
                    fill_commit.get("evidence", {})
                    if isinstance(fill_commit.get("evidence"), dict)
                    else {}
                )
                close_method = str(fill_evidence.get("verify.close_method", "") or "").strip().lower()
                close_scope = str(fill_evidence.get("calendar.close_scope", "") or "").strip().lower()
                if close_method == "done_button":
                    google_trace_date_done_clicked = True
                elif close_scope and close_scope not in {"escape", "unknown"}:
                    google_trace_date_done_clicked = True
            if item.get("action") == "click" and item.get("status") == "ok":
                blob = " ".join(
                    selector_candidates_fn(item.get("used_selector") or item.get("selectors"))
                ).lower()
                if any(str(token or "").strip().lower() in blob for token in done_tokens):
                    google_trace_date_done_clicked = True
            if (
                google_trace_dest_selector
                and google_trace_date_done_clicked
                and google_trace_date_picker_seen
            ):
                break

    return {
        "last_html": last_html,
        "planner_notes": planner_notes,
        "trace_memory_hint": trace_memory_hint,
        "route_fill_mismatch_events": route_fill_mismatch_events,
        "gf_date_failure_events": gf_date_failure_events,
        "google_local_date_open_failure": google_local_date_open_failure,
        "google_force_bind_suppression": google_force_bind_suppression,
        "debug_exploration_mode": debug_exploration_mode,
        "super_deep_exploration": super_deep_exploration,
        "google_trace_dest_selector": google_trace_dest_selector,
        "google_trace_dest_committed": google_trace_dest_committed,
        "google_trace_dest_commit_reason": google_trace_dest_commit_reason,
        "google_trace_suggestion_used": google_trace_suggestion_used,
        "google_trace_date_done_clicked": google_trace_date_done_clicked,
        "google_trace_date_picker_seen": google_trace_date_picker_seen,
    }
