"""Google Flights smart_escalation functions."""

from typing import Any, Callable, Dict, List, Optional, Tuple
from utils.logging import get_logger

log = get_logger(__name__)

from core.scenario.types import StepResult
from core.scenario_runner.google_flights.service_runner_bridge import (
    _google_step_trace_local_date_open_failure,
    _google_form_text_looks_instructional_noise,
    _google_form_text_looks_date_like,
)
from core.scenario_runner.google_flights.route_bind import (
    _google_turn_fill_success_corroborates_route_bind,
)
import core.scenario_runner as sr

def _google_route_fill_smart_escalation_skip_reason(
    step_trace,
    *,
    error_message: str = "",
    browser=None,
) -> str:
    """Return skip reason when latest Google route fill failed for deterministic local budget reasons.

    These failures come from the bounded combobox helper itself (for example activation-vs-commit
    budget allocation) and are not improved by expensive planner/VLM retries in the same attempt.
    """
    step_trace_list = step_trace if isinstance(step_trace, list) else []

    latest_route_fill_item = None
    latest_fill_commit = {}
    latest_route_unverified_item = None
    latest_route_unverified_fill_commit = {}
    for item in reversed(step_trace_list):
        if not isinstance(item, dict):
            continue
        if str(item.get("action", "") or "").strip().lower() != "fill":
            continue
        role = str(item.get("role", "") or "").strip().lower()
        if role not in {"origin", "dest"}:
            continue

        fill_commit = item.get("fill_commit")
        if not isinstance(fill_commit, dict):
            continue
        if bool(fill_commit.get("ok")):
            continue
        fill_reason = str(fill_commit.get("reason", "") or "").strip()
        if fill_reason == "combobox_fill_failed":
            latest_route_fill_item = item
            latest_fill_commit = fill_commit
            break
        if (
            latest_route_unverified_item is None
            and fill_reason.startswith("combobox_fill_unverified_")
        ):
            latest_route_unverified_item = item
            latest_route_unverified_fill_commit = fill_commit

    def _combobox_failure_stage_from_sources(*sources) -> str:
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in ("combobox.failure_stage", "failure_stage"):
                value = str(source.get(key, "") or "").strip().lower()
                if value:
                    return value
            nested = source.get("combobox_debug")
            if isinstance(nested, dict):
                value = str(nested.get("failure_stage", "") or "").strip().lower()
                if value:
                    return value
        return ""

    failure_stage = ""
    if latest_route_fill_item is not None:
        failure_stage = _combobox_failure_stage_from_sources(
            latest_fill_commit.get("evidence"),
            latest_route_fill_item.get("evidence"),
            latest_fill_commit,
        )
        if failure_stage in {
            "deadline_activation_budget",
            "deadline_activation_check",
        }:
            return f"google_route_fill_{failure_stage}"
        if failure_stage in {"activation_failed", "no_input_found"}:
            return f"google_route_fill_{failure_stage}"

    def _combobox_verify_ok_from_sources(*sources) -> bool:
        for source in sources:
            if not isinstance(source, dict):
                continue
            nested = source.get("combobox_debug")
            if isinstance(nested, dict) and bool(nested.get("verify_ok")):
                return True
            if bool(source.get("verify_ok")):
                return True
        return False

    if latest_route_unverified_item is not None:
        unverified_evidence = latest_route_unverified_fill_commit.get("evidence")
        if not isinstance(unverified_evidence, dict):
            unverified_evidence = {}
        postcheck_reason = str(
            unverified_evidence.get("verify.postcheck_reason", "") or ""
        ).strip().lower()
        observed_origin = str(
            unverified_evidence.get("verify.observed_origin", "") or ""
        ).strip()
        observed_dest_raw = str(
            unverified_evidence.get("verify.observed_dest_raw", "") or ""
        ).strip()
        contaminated_value = observed_origin or observed_dest_raw
        contamination_flag = bool(unverified_evidence.get("verify.postcheck_contamination"))
        if (
            postcheck_reason in {"origin_mismatch", "dest_mismatch"}
            and (
                contamination_flag
                or _google_form_text_looks_instructional_noise(contaminated_value)
            )
        ):
            try:
                combobox_debug = dict(
                    getattr(browser, "_last_google_flights_combobox_debug", {}) or {}
                )
            except Exception:
                combobox_debug = {}
            if _combobox_verify_ok_from_sources(
                latest_route_unverified_fill_commit,
                latest_route_unverified_item,
                {"combobox_debug": combobox_debug},
            ):
                return "google_route_fill_postcheck_helper_contamination"
        if (
            postcheck_reason in {"origin_mismatch", "dest_mismatch"}
            and _google_form_text_looks_date_like(contaminated_value)
        ):
            try:
                combobox_debug = dict(
                    getattr(browser, "_last_google_flights_combobox_debug", {}) or {}
                )
            except Exception:
                combobox_debug = {}
            if _combobox_verify_ok_from_sources(
                latest_route_unverified_fill_commit,
                latest_route_unverified_item,
                {"combobox_debug": combobox_debug},
            ):
                return "google_route_fill_postcheck_cross_field_date"

    msg = str(error_message or "")
    msg_looks_like_route_combobox_fail = (
        "combobox_fill_failed" in msg
        and "action=fill" in msg
        and any(f"role={role}" in msg for role in ("origin", "dest"))
    )
    if latest_route_fill_item is None and not msg_looks_like_route_combobox_fail:
        return ""
    try:
        combobox_debug = dict(getattr(browser, "_last_google_flights_combobox_debug", {}) or {})
    except Exception:
        combobox_debug = {}
    failure_stage = str(combobox_debug.get("failure_stage", "") or "").strip().lower()
    if failure_stage in {"deadline_activation_budget", "deadline_activation_check"}:
        return f"google_route_fill_{failure_stage}"
    if failure_stage in {"activation_failed", "no_input_found"}:
        return f"google_route_fill_{failure_stage}"
    return ""


def _google_search_commit_smart_escalation_skip_reason(
    step_trace,
    *,
    error_message: str = "",
) -> str:
    """Return skip reason for repeated deterministic local Google search-commit failures."""
    msg = str(error_message or "")
    if "results_not_ready_after_turn_limit" not in msg:
        return ""
    trace = step_trace if isinstance(step_trace, list) else []
    fill_ok = _google_turn_fill_success_corroborates_route_bind(trace)
    if not bool(fill_ok.get("ok")):
        return ""
    search_click_fail_count = 0
    search_no_transition_count = 0
    for item in trace:
        if not isinstance(item, dict):
            continue
        if str(item.get("action", "") or "").strip().lower() != "click":
            continue
        selectors = item.get("selectors")
        if not sr._selectors_look_search_submit(selectors):
            continue
        status = str(item.get("status", "") or "").strip().lower()
        if status not in {"soft_fail", "route_fill_mismatch"}:
            continue
        err = str(item.get("error", "") or "")
        if "action_deadline_exceeded_before_click" in err:
            search_click_fail_count += 1
        if "search_commit_no_results_transition" in err:
            search_no_transition_count += 1
    if search_click_fail_count >= 2:
        return "google_search_commit_click_deadline"
    if search_no_transition_count >= 2:
        return "google_search_commit_no_results_transition"
    return ""
